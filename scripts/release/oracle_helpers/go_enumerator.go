// go_enumerator is a small, dependency-free companion program for source_differential.py's
// independent Go oracle (G3/TC-589). It reads every exported top-level struct/interface
// declaration under a directory, recursively, using Go's OWN standard library (go/parser +
// go/ast, nothing else, no new project dependency -- never this project's tree-sitter engine,
// never a committed fixture), and prints one JSON array to stdout describing each symbol it
// found. source_differential.py's enumerate_go runs this with `go run` and parses that output.
//
// Usage:
//
//	go run go_enumerator.go <library-dir>
//
// Scope, matching enumerate_python's own scope exactly (type-level API only):
//   - a top-level `type X struct { ... }` or `type X interface { ... }` is a symbol only when X
//     is exported (name starts with an uppercase letter, per go/ast.IsExported);
//   - a struct's own exported fields become "property" members (an anonymous/embedded field,
//     i.e. one with no explicit name, is out of this card's declared scope and is skipped);
//   - an interface's own methods become "method" members, read straight from the interface body;
//   - every top-level func declaration with a receiver (value or pointer -- both are unwrapped
//     to the same bare receiver type name) and an exported name becomes a "method" member of the
//     struct/interface its receiver names, wherever in the package that func is declared (methods
//     of one type are routinely split across several files in a real Go package);
//   - bare package-level functions (no receiver) are never enumerated -- out of scope, same as
//     enumerate_python never reading module-level functions.
//
// Two fields the oracle never needs from Go are always emitted as their one honest value: Go has
// no default parameter values, so a param's "has_default" is always false (the Python side
// defaults a bare param object to that already); Go methods always require a receiver, so no
// method is ever "static" and every member's "is_static" is always false.
//
// Output JSON shape, one array of:
//
//	{
//	  "name": "Document",
//	  "kind": "struct" | "interface",
//	  "members": [
//	    {"name": "Save", "kind": "method", "params": [{"name": "path"}], "is_static": false},
//	    {"name": "Title", "kind": "property", "params": [], "is_static": false}
//	  ]
//	}
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type param struct {
	Name string `json:"name"`
}

type member struct {
	Name     string  `json:"name"`
	Kind     string  `json:"kind"`
	Params   []param `json:"params"`
	IsStatic bool    `json:"is_static"`
}

type symbol struct {
	Name    string   `json:"name"`
	Kind    string   `json:"kind"`
	Members []member `json:"members"`
}

// skipDirs mirrors enumerate_python's own skip list (tests/test/examples/docs), plus vendor and
// .git, which never carry the library's own public API.
var skipDirs = map[string]bool{
	"tests": true, "test": true, "examples": true, "docs": true, "vendor": true, ".git": true,
}

func isSkippedPath(path string) bool {
	for _, part := range strings.Split(filepath.ToSlash(path), "/") {
		if skipDirs[part] {
			return true
		}
	}
	return false
}

func receiverTypeName(recv *ast.FieldList) string {
	if recv == nil || len(recv.List) != 1 {
		return ""
	}
	expr := recv.List[0].Type
	if star, ok := expr.(*ast.StarExpr); ok {
		expr = star.X
	}
	if ident, ok := expr.(*ast.Ident); ok {
		return ident.Name
	}
	return ""
}

func funcParams(t *ast.FuncType) []param {
	out := []param{}
	if t == nil || t.Params == nil {
		return out
	}
	for _, field := range t.Params.List {
		if len(field.Names) == 0 {
			out = append(out, param{Name: ""}) // an unnamed parameter; rare, still counted
			continue
		}
		for _, name := range field.Names {
			out = append(out, param{Name: name.Name})
		}
	}
	return out
}

func structFields(t *ast.StructType) []member {
	out := []member{}
	if t.Fields == nil {
		return out
	}
	for _, field := range t.Fields.List {
		if len(field.Names) == 0 {
			continue // an embedded/anonymous field: out of this card's declared scope
		}
		for _, name := range field.Names {
			if !ast.IsExported(name.Name) {
				continue
			}
			out = append(out, member{Name: name.Name, Kind: "property", Params: []param{}, IsStatic: false})
		}
	}
	return out
}

func interfaceMethods(t *ast.InterfaceType) []member {
	out := []member{}
	if t.Methods == nil {
		return out
	}
	for _, m := range t.Methods.List {
		if len(m.Names) == 0 {
			continue // an embedded interface: out of this card's declared scope
		}
		for _, name := range m.Names {
			if !ast.IsExported(name.Name) {
				continue
			}
			funcType, _ := m.Type.(*ast.FuncType)
			out = append(out, member{Name: name.Name, Kind: "method", Params: funcParams(funcType), IsStatic: false})
		}
	}
	return out
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: go_enumerator <library-dir>")
		os.Exit(2)
	}
	root := os.Args[1]

	kinds := map[string]string{}     // exported type name -> "struct" | "interface"
	fields := map[string][]member{}  // exported type name -> its own exported fields/interface methods
	methods := map[string][]member{} // exported receiver type name -> methods declared anywhere in the package

	fset := token.NewFileSet()
	walkErr := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if info.IsDir() {
			if path != root && isSkippedPath(path) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") || isSkippedPath(path) {
			return nil
		}
		file, perr := parser.ParseFile(fset, path, nil, 0)
		if perr != nil {
			return nil // a file that fails to parse is skipped, never fatal -- mirrors enumerate_python
		}
		for _, decl := range file.Decls {
			switch d := decl.(type) {
			case *ast.GenDecl:
				if d.Tok != token.TYPE {
					continue
				}
				for _, spec := range d.Specs {
					ts, ok := spec.(*ast.TypeSpec)
					if !ok || !ast.IsExported(ts.Name.Name) {
						continue
					}
					switch t := ts.Type.(type) {
					case *ast.StructType:
						kinds[ts.Name.Name] = "struct"
						fields[ts.Name.Name] = append(fields[ts.Name.Name], structFields(t)...)
					case *ast.InterfaceType:
						kinds[ts.Name.Name] = "interface"
						fields[ts.Name.Name] = append(fields[ts.Name.Name], interfaceMethods(t)...)
					}
				}
			case *ast.FuncDecl:
				if d.Recv == nil || !ast.IsExported(d.Name.Name) {
					continue
				}
				receiver := receiverTypeName(d.Recv)
				if receiver == "" {
					continue
				}
				methods[receiver] = append(methods[receiver], member{
					Name: d.Name.Name, Kind: "method", Params: funcParams(d.Type), IsStatic: false,
				})
			}
		}
		return nil
	})
	if walkErr != nil {
		fmt.Fprintln(os.Stderr, "go_enumerator: walk failed:", walkErr)
		os.Exit(1)
	}

	names := make([]string, 0, len(kinds))
	for name := range kinds {
		names = append(names, name)
	}
	sort.Strings(names)

	symbols := make([]symbol, 0, len(names))
	for _, name := range names {
		members := append([]member{}, fields[name]...)
		members = append(members, methods[name]...)
		symbols = append(symbols, symbol{Name: name, Kind: kinds[name], Members: members})
	}

	if err := json.NewEncoder(os.Stdout).Encode(symbols); err != nil {
		fmt.Fprintln(os.Stderr, "go_enumerator: encode failed:", err)
		os.Exit(1)
	}
}
