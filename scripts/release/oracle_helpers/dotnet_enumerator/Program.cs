// DotNetEnumerator is a small companion console program for source_differential.py's independent
// .NET oracle (G3/TC-593). It reads every public class/struct/interface/enum declaration under a
// directory, recursively, using Roslyn's OWN parser (Microsoft.CodeAnalysis.CSharp,
// CSharpSyntaxTree.ParseText) at the SYNTAX/PARSE phase only -- never a SemanticModel, never a
// Compilation, never symbol resolution against the library's own external references (a real
// library's own third-party dependencies are routinely absent from this program's own build, and
// are never needed: every fact this program reports -- a declared modifier, a parameter's own
// default-value syntax, a member's own simple name -- is read straight off the syntax tree, the
// same parse-phase discipline TC-592's JavaEnumerator.java applies via JavacTask.parse()) -- never
// this project's own tree-sitter engine, never a committed fixture.
//
// source_differential.py's enumerate_dotnet runs this with `dotnet run --project
// DotNetEnumerator.csproj --no-restore -c Release -- <library-dir>` and parses the JSON array it
// prints to stdout.
//
// Usage:
//   dotnet run --project DotNetEnumerator.csproj --no-restore -c Release -- <library-dir>
//
// Why this needs no Docker, unlike enumerate_go/enumerate_rust: `dotnet run`/`dotnet <dll>` JIT-
// compiles and executes MANAGED IL through the already-trusted `dotnet` host -- it never writes a
// freshly-built, unsigned NATIVE .exe to disk (the exact case this host's Windows Application
// Control policy blocks; see source_differential.py's Go/Rust docstrings). Confirmed directly
// before this program was written (G3/TC-593), the same way TC-591/TC-592 confirmed it for
// node/java: a plain `dotnet build` + `dotnet run` of this exact project on this host completed
// with no Code Integrity / WDAC event of any kind.
//
// SCOPE (read this before changing it):
//   - a file is scanned only when no path component (case-insensitively) is one of
//     SkippedDirNames below (tests, test, examples, docs, target, build, out, node_modules,
//     .git, bin, obj) -- mirrors enumerate_go/enumerate_rust/enumerate_java's own skip set,
//     extended with "bin"/"obj" (this project's own build output, which would otherwise be
//     re-parsed as if it were library source the moment it is built once);
//   - a top-level type declaration (class/struct/interface/record/enum) is a candidate only when
//     its own modifiers literally include `public` -- C#'s own default for an unmarked top-level
//     type is `internal`, never public, so "no modifier" must exclude it, exactly like a Go name
//     that starts lowercase or a non-`pub` Rust item. A NESTED type is flattened into the same
//     flat symbol list by its own simple name, by the same rule TC-592 established for Java
//     (public STATIC nested enums/classes are a routine pattern in these wrapper APIs), but ONLY
//     when every enclosing type down to it is ALSO explicitly `public` -- a nested type is never
//     even inspected when its own enclosing type was excluded, matching real C# accessibility
//     (an outside caller can never name a nested type whose enclosing type it cannot name) and
//     JavaEnumerator.java's own `collectPublicTypesRecursively` exactly;
//   - inside a CLASS/STRUCT/RECORD body, a method/property/field member is public ONLY when its
//     own modifiers literally include `public` -- C#'s own default for an unmarked class member
//     is `private`, so "no modifier" must exclude it;
//   - inside an INTERFACE body, a member is public UNLESS its own modifiers literally include
//     `private` (C# 8+ allows a `private` default-implementation interface method; every other
//     interface member -- including one with NO modifier at all, the ordinary case for an
//     abstract interface method/property -- is public by the language's own default). This is
//     the exact opposite of the class/struct rule directly above, and the card's own named
//     defect case: get both right, they are easy to invert by mistake;
//   - a constructor is never reported as a method, matching every other enumerator's own
//     exclusion of constructors from "methods"; indexers, operators and events are out of this
//     card's scope and are never reported either (a genuinely new scope decision, not a defect --
//     recorded here honestly rather than silently guessed at);
//   - a member's "is_static" is read from its own modifiers' literal `static` keyword only -- no
//     implicit-static synthesis is applied for .NET (unlike JavaEnumerator.java's own JLS 9.3
//     interface-field exception): a genuine C# interface cannot declare an instance OR static
//     FIELD at all (only properties/methods/events/indexers, plus C# 11's own `static abstract`
//     members, which already carry an explicit `static` keyword in source), so there is no
//     language-level implicit case here needing a manual fix, confirmed directly against the
//     language spec before this program was written;
//   - a parameter's "has_default" comes straight from the syntax tree's own EqualsValueClause
//     (`ParameterSyntax.Default is not null`) -- never a `?`-nullable marker, which carries no
//     default value of its own;
//   - a `record`/`record class`/`record struct` is treated exactly like a `class`/`struct`:
//     kind "class", and ONLY its explicitly-written members are read. A positional record's own
//     primary-constructor parameters (`public record Point(int X, int Y);`) are a deliberate
//     scope exclusion: the implicit init-only properties they generate are a SEMANTIC fact (the
//     compiler synthesises them during binding), not a syntactic one, and synthesising them here
//     would cross this card's own parse-phase-only line -- recorded as a known, deliberate gap,
//     not a defect, exactly the discipline the card demands;
//   - a C# method/property name is NEVER unique within one type on its own: overloading is
//     routine in these wrapper APIs (the live pdf/net pilot's own Document/PdfFileInfo classes
//     declare several same-named `Save`/`BindXml`-style overloads with different parameter
//     lists). diff_symbol/diff_language key BOTH the oracle's own member list AND the baked
//     fixture's own `methods`/`properties` lists by bare name only (`{m["name"]: m for m in
//     ...}` in source_differential.py itself) -- a pre-existing design limit of the whole oracle
//     system this card inherits from TC-566, not something this card introduces or must fix.
//     This program therefore keeps exactly ONE declaration per (enclosing type, member name):
//     the FIRST one encountered in file-then-declaration order, deterministically, and reports
//     that one's own parameter list/static flag -- never one entry per overload, and never a
//     fabricated merge of several overloads' signatures into one. A `partial class`/`partial
//     struct`/`partial interface` split across several files is merged the same deterministic
//     way: every file's own members for the same (kind, name) symbol are folded into one symbol
//     entry, first-member-name-wins on a collision, so a partial declaration's members are never
//     silently dropped just because they are declared in a different file than the first one
//     seen.
//
// Output JSON shape, one array of:
//   {
//     "name": "Document",
//     "kind": "class" | "enum",
//     "members": [
//       {"name": "Save", "kind": "method", "params": [{"name": "path", "has_default": false}], "is_static": false, "enum_value": null},
//       {"name": "FileName", "kind": "property", "params": [], "is_static": false, "enum_value": null},
//       {"name": "Ok", "kind": "enum_value", "params": [], "is_static": false, "enum_value": "0"}
//     ]
//   }

using System.Text.Json.Nodes;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DotNetEnumerator;

internal static class Program
{
    private static readonly HashSet<string> SkippedDirNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "tests", "test", "examples", "docs", "target", "build", "out", "node_modules", ".git", "bin", "obj",
    };

    private static int Main(string[] args)
    {
        if (args.Length != 1)
        {
            Console.Error.WriteLine("usage: dotnet run --project DotNetEnumerator.csproj -- <library-dir>");
            return 2;
        }

        var root = new DirectoryInfo(args[0]);
        var symbols = new SymbolTable();
        foreach (var file in ListCSharpFiles(root))
        {
            CollectFromFile(file, symbols);
        }

        var array = new JsonArray();
        foreach (var symbol in symbols.OrderedSymbols())
        {
            array.Add(symbol.ToJson());
        }

        Console.WriteLine(array.ToJsonString());
        return 0;
    }

    /// Every *.cs file under root, recursively, skipping any path component named in
    /// SkippedDirNames -- never a fixture, never this project's own build output. Sorted for a
    /// deterministic symbol/member order (needed for the first-declaration-wins overload rule
    /// documented in this file's own module docstring above).
    private static List<FileInfo> ListCSharpFiles(DirectoryInfo root)
    {
        var found = new List<FileInfo>();
        if (!root.Exists)
        {
            return found;
        }

        void Walk(DirectoryInfo dir)
        {
            foreach (var sub in dir.GetDirectories())
            {
                if (SkippedDirNames.Contains(sub.Name))
                {
                    continue;
                }
                Walk(sub);
            }
            foreach (var file in dir.GetFiles("*.cs"))
            {
                found.Add(file);
            }
        }

        Walk(root);
        found.Sort((a, b) => string.CompareOrdinal(a.FullName, b.FullName));
        return found;
    }

    /// Parses one file, SYNTAX/PARSE PHASE ONLY (CSharpSyntaxTree.ParseText -- never a
    /// SemanticModel/Compilation; see this file's own module docstring for why that is both
    /// sufficient and deliberate). A file with a genuine syntax error (an ERROR-severity
    /// diagnostic) is skipped entirely, never half-read -- matching every other enumerator's own
    /// "skip a file that fails to parse" rule exactly.
    private static void CollectFromFile(FileInfo file, SymbolTable symbols)
    {
        string text;
        try
        {
            text = File.ReadAllText(file.FullName);
        }
        catch (IOException)
        {
            return;
        }

        var tree = CSharpSyntaxTree.ParseText(text, path: file.FullName);
        if (tree.GetDiagnostics().Any(d => d.Severity == DiagnosticSeverity.Error))
        {
            return; // a genuine syntax error: skip this file entirely, never a partial reading
        }

        var compilationUnit = (CompilationUnitSyntax)tree.GetRoot();
        foreach (var member in compilationUnit.Members)
        {
            WalkTopLevel(member, symbols);
        }
    }

    /// A namespace (block-scoped or file-scoped) carries no visibility of its own -- descend into
    /// its members unconditionally, looking for top-level type declarations.
    private static void WalkTopLevel(MemberDeclarationSyntax member, SymbolTable symbols)
    {
        switch (member)
        {
            case BaseNamespaceDeclarationSyntax ns:
                foreach (var inner in ns.Members)
                {
                    WalkTopLevel(inner, symbols);
                }
                break;
            case EnumDeclarationSyntax enumDecl:
                CollectEnum(enumDecl, symbols);
                break;
            case TypeDeclarationSyntax typeDecl:
                CollectType(typeDecl, symbols);
                break;
        }
    }

    /// `node` is a candidate exactly when its own modifiers literally include `public` -- the
    /// opposite-of-default-internal/private rule documented in this file's own module docstring.
    /// A non-public node is never recursed into, and none of its own nested members are ever
    /// inspected: an outside caller can never name a nested type whose enclosing type it cannot
    /// name, matching JavaEnumerator.java's own `collectPublicTypesRecursively` exactly.
    private static void CollectType(TypeDeclarationSyntax node, SymbolTable symbols)
    {
        if (!HasModifier(node.Modifiers, "public"))
        {
            return;
        }

        bool isInterface = node is InterfaceDeclarationSyntax;
        var symbol = symbols.GetOrAdd(node.Identifier.Text, "class");

        foreach (var member in node.Members)
        {
            switch (member)
            {
                case EnumDeclarationSyntax nestedEnum:
                    CollectEnum(nestedEnum, symbols);
                    break;
                case TypeDeclarationSyntax nestedType:
                    CollectType(nestedType, symbols);
                    break;
                case ConstructorDeclarationSyntax:
                    break; // constructors are never reported as methods
                case MethodDeclarationSyntax method:
                    if (IsMemberPublic(isInterface, method.Modifiers))
                    {
                        symbol.AddMember(
                            method.Identifier.Text,
                            "method",
                            ParamsOf(method.ParameterList),
                            HasModifier(method.Modifiers, "static"));
                    }
                    break;
                case PropertyDeclarationSyntax property:
                    if (IsMemberPublic(isInterface, property.Modifiers))
                    {
                        symbol.AddMember(
                            property.Identifier.Text,
                            "property",
                            new List<(string, bool)>(),
                            HasModifier(property.Modifiers, "static"));
                    }
                    break;
                case FieldDeclarationSyntax field:
                    if (IsMemberPublic(isInterface, field.Modifiers))
                    {
                        bool isStatic = HasModifier(field.Modifiers, "static");
                        foreach (var variable in field.Declaration.Variables)
                        {
                            symbol.AddMember(variable.Identifier.Text, "property", new List<(string, bool)>(), isStatic);
                        }
                    }
                    break;
            }
        }
    }

    private static void CollectEnum(EnumDeclarationSyntax node, SymbolTable symbols)
    {
        if (!HasModifier(node.Modifiers, "public"))
        {
            return;
        }

        var symbol = symbols.GetOrAdd(node.Identifier.Text, "enum");
        foreach (var constant in node.Members)
        {
            string? value = constant.EqualsValue?.Value.ToString();
            symbol.AddEnumValue(constant.Identifier.Text, value);
        }
    }

    /// A CLASS/STRUCT/RECORD member is public only when explicitly marked `public`. An INTERFACE
    /// member is public unless explicitly marked `private` -- the exact opposite default, and the
    /// card's own named defect case (see this file's own module docstring).
    private static bool IsMemberPublic(bool isInterface, SyntaxTokenList modifiers) =>
        isInterface ? !HasModifier(modifiers, "private") : HasModifier(modifiers, "public");

    private static bool HasModifier(SyntaxTokenList modifiers, string keyword) =>
        modifiers.Any(m => m.Text == keyword);

    private static List<(string Name, bool HasDefault)> ParamsOf(ParameterListSyntax parameterList)
    {
        var result = new List<(string, bool)>();
        foreach (var parameter in parameterList.Parameters)
        {
            result.Add((parameter.Identifier.Text, parameter.Default is not null));
        }
        return result;
    }
}

/// One real member (method/property/enum value), as this program actually read it.
internal sealed class MemberInfo
{
    public required string Name { get; init; }
    public required string Kind { get; init; }
    public List<(string Name, bool HasDefault)> Params { get; init; } = new();
    public bool IsStatic { get; init; }
    public string? EnumValue { get; init; }

    public JsonObject ToJson()
    {
        var paramsArray = new JsonArray();
        foreach (var (name, hasDefault) in Params)
        {
            paramsArray.Add(new JsonObject { ["name"] = name, ["has_default"] = hasDefault });
        }
        return new JsonObject
        {
            ["name"] = Name,
            ["kind"] = Kind,
            ["params"] = paramsArray,
            ["is_static"] = IsStatic,
            ["enum_value"] = EnumValue,
        };
    }
}

/// One real class/struct/interface/enum, as this program actually read it, accumulated across
/// every file that contributes to it (a `partial` type may legitimately span several files; see
/// this file's own module docstring for the deterministic, first-declaration-wins merge rule this
/// class applies for both a `partial` split and a genuine method-overload set).
internal sealed class SymbolInfo
{
    public required string Name { get; init; }
    public required string Kind { get; init; }
    private readonly Dictionary<string, MemberInfo> _members = new();
    private readonly List<string> _order = new();

    public void AddMember(string name, string kind, List<(string Name, bool HasDefault)> parameters, bool isStatic)
    {
        string key = kind + ":" + name;
        if (_members.ContainsKey(key))
        {
            return; // first declaration wins: a partial-type re-declaration or a method overload
        }
        _members[key] = new MemberInfo { Name = name, Kind = kind, Params = parameters, IsStatic = isStatic };
        _order.Add(key);
    }

    public void AddEnumValue(string name, string? value)
    {
        string key = "enum_value:" + name;
        if (_members.ContainsKey(key))
        {
            return;
        }
        _members[key] = new MemberInfo { Name = name, Kind = "enum_value", EnumValue = value };
        _order.Add(key);
    }

    public JsonObject ToJson()
    {
        var membersArray = new JsonArray();
        foreach (var key in _order)
        {
            membersArray.Add(_members[key].ToJson());
        }
        return new JsonObject { ["name"] = Name, ["kind"] = Kind, ["members"] = membersArray };
    }
}

/// Every symbol this program has found so far, keyed by (kind, name) so a `partial` type's own
/// several declarations (always declared with the same kind) accumulate into one entry.
internal sealed class SymbolTable
{
    private readonly Dictionary<string, SymbolInfo> _symbols = new();
    private readonly List<string> _order = new();

    public SymbolInfo GetOrAdd(string name, string kind)
    {
        string key = kind + ":" + name;
        if (_symbols.TryGetValue(key, out var existing))
        {
            return existing;
        }
        var created = new SymbolInfo { Name = name, Kind = kind };
        _symbols[key] = created;
        _order.Add(key);
        return created;
    }

    public IEnumerable<SymbolInfo> OrderedSymbols() => _order.Select(key => _symbols[key]);
}
