// rust_enumerator is a small, dependency-light companion program for source_differential.py's
// independent Rust oracle (G3/TC-590). It reads every `.rs` file under a directory, recursively,
// using the ONE new pinned dependency the audit's own design table already expects -- the `syn`
// crate (`syn::parse_file`, real Rust syntax, never this project's own tree-sitter engine, never
// a committed fixture) -- and prints one JSON array to stdout describing each public symbol it
// found. source_differential.py's `enumerate_rust` runs this inside a pinned `rust:1.98.1` Docker
// container (never directly on the host -- a freshly-built, unsigned native .exe is blocked by
// this host's Windows Application Control policy; see the REWORK note in plans/TC-590.yaml and
// the .cargo/config.toml next to this file for why the container build never touches the network
// either) and parses this program's stdout.
//
// Usage:
//
//   cargo run --release -- <library-dir>
//
// Scope, matching enumerate_python's/enumerate_go's own scope (type-level public API only):
//   - a top-level `pub struct X { ... }` is a symbol (kind "struct"); its own `pub` NAMED fields
//     become "property" members. A tuple struct's unnamed fields and any non-pub field are out
//     of scope, matching enumerate_go's own unexported/anonymous-field exclusion.
//   - a top-level `pub enum X { ... }` is a symbol (kind "enum"); every variant becomes an
//     "enum_value" member. A variant with an explicit discriminant (`B = 2`) reports that exact
//     literal as its enum_value; one with no explicit discriminant reports null ("not checked"),
//     matching enumerate_python's own Enum-member convention -- this enumerator names and counts
//     every variant exactly, it never merely counts them.
//   - a top-level `pub trait X { ... }` is a symbol (kind "trait"); every function the trait
//     declares becomes a "method" member (a trait's own fn items are always public once the
//     trait itself is pub -- Rust forbids a visibility qualifier inside a trait body).
//   - every `impl X { ... }` and `impl SomeTrait for X { ... }` block anywhere under the tree
//     contributes its functions as "method" members of X (an inherent impl's fn needs its own
//     explicit `pub`; a trait impl's fn is always public once the trait is, and Rust forbids a
//     visibility qualifier there, so none is ever present to check). Matches the Go companion's
//     own cross-file method-attachment pattern (a type's methods routinely split across files).
//   - a method's receiver (`&self`/`&mut self`/`self`/none) is never one of its own params
//     (matches enumerate_python's own self/cls exclusion); a method with NO receiver is reported
//     `is_static: true` (an associated function, e.g. a `new` constructor), matching this
//     project's own baked-fixture convention of a `"static"` modifier on exactly that case.
//   - only top-level items in each file are read (mirrors enumerate_python's own module-level-
//     only scope and the Go companion's own top-level-only scope): a type declared only inside
//     an inline, nested `mod` block (as opposed to its own `mod foo;` file, which this program
//     reads like any other file) is out of this card's declared scope, not a defect.
//
// Deliberate, disclosed limitation (the design table's own fidelity note, "Full
// (macro-expansion-blind, disclosed)"): `syn::parse_file` parses REAL Rust SYNTAX, never
// macro-EXPANDED output. A `pub struct`/`pub enum`/`pub trait` produced only inside a
// `macro_rules!` expansion (e.g. `make_struct!(Foo);`) is syntactically just an opaque macro
// invocation to this reader -- it is correctly, silently absent from this program's output,
// never crashed on, and never fabricated. tests/release/test_source_differential.py proves this
// limitation is real, not merely asserted here.
//
// Output JSON shape, one array of:
//
//   {
//     "name": "Document",
//     "kind": "struct" | "enum" | "trait",
//     "members": [
//       {"name": "Save", "kind": "method", "params": [{"name": "path"}], "is_static": false, "enum_value": null},
//       {"name": "Title", "kind": "property", "params": [], "is_static": false, "enum_value": null},
//       {"name": "A", "kind": "enum_value", "params": [], "is_static": false, "enum_value": "1"}
//     ]
//   }

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use syn::punctuated::Punctuated;
use syn::token::Comma;
use syn::{Expr, Fields, FnArg, ImplItem, Item, Lit, Pat, TraitItem, Type, UnOp, Visibility};

#[derive(Debug, Clone)]
struct Param {
    name: String,
}

#[derive(Debug, Clone)]
struct Member {
    name: String,
    kind: &'static str, // "method" | "property" | "enum_value"
    params: Vec<Param>,
    is_static: bool,
    enum_value: Option<String>,
}

#[derive(Debug, Clone)]
struct Symbol {
    name: String,
    kind: &'static str, // "struct" | "enum" | "trait"
    members: Vec<Member>,
}

// Mirrors enumerate_python's own tests/test/examples/docs skip list, plus target/vendor/.git,
// which never carry a library's own public API.
const SKIP_DIRS: &[&str] = &["tests", "test", "examples", "docs", "target", "vendor", ".git"];

fn is_skipped_dir(name: &str) -> bool {
    SKIP_DIRS.contains(&name)
}

fn collect_rs_files(root: &Path, out: &mut Vec<PathBuf>) {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if is_skipped_dir(name) {
                    continue;
                }
            }
            collect_rs_files(&path, out);
            continue;
        }
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let is_build_script = path
            .file_name()
            .and_then(|n| n.to_str())
            .map(|n| n == "build.rs" || n.ends_with("_test.rs"))
            .unwrap_or(false);
        if !is_build_script {
            out.push(path);
        }
    }
}

fn is_pub(vis: &Visibility) -> bool {
    matches!(vis, Visibility::Public(_))
}

fn base_type_name(ty: &Type) -> Option<String> {
    match ty {
        Type::Path(type_path) => type_path.path.segments.last().map(|segment| segment.ident.to_string()),
        Type::Reference(reference) => base_type_name(&reference.elem),
        Type::Group(group) => base_type_name(&group.elem),
        Type::Paren(paren) => base_type_name(&paren.elem),
        _ => None,
    }
}

/// `self`/`&self`/`&mut self` is never a param (matches enumerate_python's self/cls exclusion).
/// Returns (has_receiver, the real params in order).
fn split_receiver_and_params(inputs: &Punctuated<FnArg, Comma>) -> (bool, Vec<Param>) {
    let mut has_receiver = false;
    let mut params = Vec::new();
    for input in inputs {
        match input {
            FnArg::Receiver(_) => has_receiver = true,
            FnArg::Typed(typed) => {
                let name = match typed.pat.as_ref() {
                    Pat::Ident(pat_ident) => pat_ident.ident.to_string(),
                    _ => String::new(), // a complex/destructuring pattern: rare in a public API
                };
                params.push(Param { name });
            }
        }
    }
    (has_receiver, params)
}

fn literal_discriminant(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Lit(expr_lit) => match &expr_lit.lit {
            Lit::Int(lit_int) => Some(lit_int.base10_digits().to_string()),
            _ => None,
        },
        Expr::Unary(unary) if matches!(unary.op, UnOp::Neg(_)) => {
            literal_discriminant(&unary.expr).map(|digits| format!("-{digits}"))
        }
        _ => None,
    }
}

fn discriminant_value(discriminant: &Option<(syn::token::Eq, Expr)>) -> Option<String> {
    let (_, expr) = discriminant.as_ref()?;
    literal_discriminant(expr)
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() != 2 {
        eprintln!("usage: rust_enumerator <library-dir>");
        return ExitCode::from(2);
    }
    let root = PathBuf::from(&args[1]);

    let mut files = Vec::new();
    collect_rs_files(&root, &mut files);
    files.sort();

    // name -> "struct" | "enum" | "trait", set by the Item::Struct/Item::Enum/Item::Trait pass.
    let mut kinds: BTreeMap<String, &'static str> = BTreeMap::new();
    // name -> its own declared members: a struct's pub fields, an enum's variants, or a trait's
    // own fn items.
    let mut declared: BTreeMap<String, Vec<Member>> = BTreeMap::new();
    // name -> methods attached via any impl block anywhere in the tree (the Go companion's own
    // cross-file method-attachment pattern: a type's methods routinely split across files).
    let mut impl_methods: BTreeMap<String, Vec<Member>> = BTreeMap::new();

    for path in &files {
        let content = match fs::read_to_string(path) {
            Ok(content) => content,
            Err(_) => continue,
        };
        let file = match syn::parse_file(&content) {
            Ok(file) => file,
            Err(_) => continue, // a file that fails to parse is skipped, never fatal (matches Python/Go)
        };

        for item in file.items {
            match item {
                Item::Struct(item_struct) => {
                    if !is_pub(&item_struct.vis) {
                        continue;
                    }
                    let name = item_struct.ident.to_string();
                    kinds.insert(name.clone(), "struct");
                    let mut members = Vec::new();
                    if let Fields::Named(named) = &item_struct.fields {
                        for field in &named.named {
                            if !is_pub(&field.vis) {
                                continue;
                            }
                            if let Some(ident) = &field.ident {
                                members.push(Member {
                                    name: ident.to_string(),
                                    kind: "property",
                                    params: Vec::new(),
                                    is_static: false,
                                    enum_value: None,
                                });
                            }
                        }
                    }
                    declared.entry(name).or_default().extend(members);
                }
                Item::Enum(item_enum) => {
                    if !is_pub(&item_enum.vis) {
                        continue;
                    }
                    let name = item_enum.ident.to_string();
                    kinds.insert(name.clone(), "enum");
                    let mut members = Vec::new();
                    for variant in &item_enum.variants {
                        members.push(Member {
                            name: variant.ident.to_string(),
                            kind: "enum_value",
                            params: Vec::new(),
                            is_static: false,
                            enum_value: discriminant_value(&variant.discriminant),
                        });
                    }
                    declared.entry(name).or_default().extend(members);
                }
                Item::Trait(item_trait) => {
                    if !is_pub(&item_trait.vis) {
                        continue;
                    }
                    let name = item_trait.ident.to_string();
                    kinds.insert(name.clone(), "trait");
                    let mut members = Vec::new();
                    for trait_item in &item_trait.items {
                        if let TraitItem::Fn(trait_fn) = trait_item {
                            let (has_receiver, params) = split_receiver_and_params(&trait_fn.sig.inputs);
                            members.push(Member {
                                name: trait_fn.sig.ident.to_string(),
                                kind: "method",
                                params,
                                is_static: !has_receiver,
                                enum_value: None,
                            });
                        }
                    }
                    declared.entry(name).or_default().extend(members);
                }
                Item::Impl(item_impl) => {
                    let Some(type_name) = base_type_name(&item_impl.self_ty) else {
                        continue;
                    };
                    let is_trait_impl = item_impl.trait_.is_some();
                    for impl_item in &item_impl.items {
                        if let ImplItem::Fn(impl_fn) = impl_item {
                            // An inherent impl's fn needs its own `pub`; a trait impl's fn is
                            // always public once the trait itself is pub (Rust forbids a
                            // visibility qualifier there, so none is ever present to check).
                            if !is_trait_impl && !is_pub(&impl_fn.vis) {
                                continue;
                            }
                            let (has_receiver, params) = split_receiver_and_params(&impl_fn.sig.inputs);
                            impl_methods.entry(type_name.clone()).or_default().push(Member {
                                name: impl_fn.sig.ident.to_string(),
                                kind: "method",
                                params,
                                is_static: !has_receiver,
                                enum_value: None,
                            });
                        }
                    }
                }
                _ => {} // includes Item::Macro: a macro invocation is never expanded (see above)
            }
        }
    }

    let mut symbols = Vec::new();
    for (name, kind) in &kinds {
        let mut members = declared.remove(name).unwrap_or_default();
        if let Some(extra) = impl_methods.remove(name) {
            members.extend(extra);
        }
        symbols.push(Symbol {
            name: name.clone(),
            kind: *kind,
            members,
        });
    }

    print!("{}", render_json(&symbols));
    ExitCode::SUCCESS
}

// Hand-rolled JSON output -- the oracle's shape is small and fixed, and this keeps the companion
// crate's only new dependency exactly the one the design table already expects (`syn`), never an
// additional serde/serde_json footprint vendored into the repository for three struct fields.
fn escape_json(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn render_json(symbols: &[Symbol]) -> String {
    let mut out = String::from("[");
    for (symbol_index, symbol) in symbols.iter().enumerate() {
        if symbol_index > 0 {
            out.push(',');
        }
        out.push_str(&format!(
            "{{\"name\":\"{}\",\"kind\":\"{}\",\"members\":[",
            escape_json(&symbol.name),
            symbol.kind
        ));
        for (member_index, member) in symbol.members.iter().enumerate() {
            if member_index > 0 {
                out.push(',');
            }
            let params = member
                .params
                .iter()
                .map(|param| format!("{{\"name\":\"{}\"}}", escape_json(&param.name)))
                .collect::<Vec<_>>()
                .join(",");
            let enum_value = match &member.enum_value {
                Some(value) => format!("\"{}\"", escape_json(value)),
                None => "null".to_string(),
            };
            out.push_str(&format!(
                "{{\"name\":\"{}\",\"kind\":\"{}\",\"params\":[{}],\"is_static\":{},\"enum_value\":{}}}",
                escape_json(&member.name),
                member.kind,
                params,
                member.is_static,
                enum_value
            ));
        }
        out.push_str("]}");
    }
    out.push(']');
    out
}
