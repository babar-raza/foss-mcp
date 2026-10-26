// JavaEnumerator is a small, dependency-free companion program for source_differential.py's
// independent Java oracle (G3/TC-592). It reads every public top-level class/interface/enum
// declaration under a directory, recursively, using the JDK's OWN Compiler Tree API
// (com.sun.source.tree.* / com.sun.source.util.*, part of the jdk.compiler module's
// unconditionally-exported public surface -- confirmed directly on this host: it compiles and
// runs with no `--add-exports` flag at all, unlike `com.sun.tools.javac.*`'s own internals),
// stopped at the PARSE phase only (JavacTask.parse(), never .analyze()/.generate()) -- never
// this project's own tree-sitter engine, never a committed fixture. source_differential.py's
// enumerate_java runs this with the JDK's single-file source-launcher (`java
// JavaEnumerator.java <library-dir>`, supported since Java 11) and parses the JSON array it
// prints to stdout.
//
// Usage:
//   java JavaEnumerator.java <library-dir>
//
// Why the parse phase, and why that is enough: the card driving this file bounds it to
// "parse-phase only" -- reading the source's own declared API surface from its syntax tree,
// never requiring the library to fully compile/resolve (a real library's own third-party
// dependencies are routinely absent from this program's classpath, since it is never given
// one). JavacTask.parse() proves this directly: confirmed on this host against a file that
// imports and references a package that does not exist anywhere (`import
// com.totally.nonexistent.ExternalDependency;`, used as a field/parameter/return type) --
// parse() returns the file's full syntax tree with zero exceptions and zero diagnostics,
// because parsing never resolves a single name. Only a genuine SYNTAX error (not a semantic
// one) produces an ERROR-kind diagnostic, which this program treats as "this file fails to
// parse" and skips, matching enumerate_python/enumerate_go/enumerate_rust's own "skip a file
// that fails to parse" rule exactly -- never a partial or fabricated reading of a broken file.
//
// Why NOT `--add-exports jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED` or similar: that
// flag exists to reach javac's own IMPLEMENTATION internals (com.sun.tools.javac.*), which this
// program never needs. com.sun.source.* is the JDK's own, intentionally public "Compiler Tree
// API" (the same API javac itself hands to annotation processors and the API many
// IDEs/build-tool plugins use), unconditionally exported by the jdk.compiler module -- no
// module flag of any kind is required, confirmed empirically on this host's installed javac
// 21.0.11 before this file was written.
//
// SCOPE (read this before changing it):
//   - a file is scanned only when no path component is one of SKIPPED_DIR_NAMES below (tests,
//     test, examples, docs, target, build, out, node_modules, .git) -- this mirrors
//     enumerate_go/enumerate_rust's own skip set, and in particular means a real Maven layout's
//     src/test/java is excluded by the literal "test" path component, with no Maven-specific
//     logic needed;
//   - a top-level class/interface/enum declaration (one of CompilationUnitTree's own
//     getTypeDecls()) is a candidate symbol, and so is a NESTED one, flattened into the same
//     flat symbol list by its own simple name -- but only when every enclosing type down to it
//     is itself public, mirroring real Java accessibility (an outside caller can never even
//     name a nested type whose enclosing type it cannot name). This is NOT the same choice
//     enumerate_go/enumerate_rust make (both top-level-only): confirmed directly against the
//     real pinned pdf/java pilot before this file was written -- a top-level-only reading
//     silently produced ZERO symbol for roughly a fifth of the pilot's own distinct public type
//     names (e.g. a settings class's own nested status enum), because they are declared as
//     public static nested types, a routine Java API pattern this card's own two reference
//     languages do not share;
//   - a candidate is a real symbol only when its own modifiers literally include `public` --
//     package-private (no modifier) is excluded, exactly like a non-exported Go name or a
//     non-pub Rust item;
//   - inside a CLASS or ENUM body, a method/field member is public ONLY when its own modifiers
//     literally include `public`. A `protected` method (or a package-private one, no modifier
//     at all) is NOT public and must be excluded -- this is the card's own named defect case,
//     and it is enforced by this exact rule, never merely "not private";
//   - inside an INTERFACE body, a member is public unless its own modifiers literally include
//     `private` (Java 9+ private interface methods are the only non-public interface member
//     kind the language allows -- an interface field can never be `private` at all, and an
//     abstract/default/static interface method carries no implicit `public` modifier at the
//     Tree-API level even though the language makes it public by default, confirmed directly
//     against a hand-written interface fixture: `void read(String);` parses back with an EMPTY
//     modifier set, not `[public]` -- so this program treats "not explicitly private" as the
//     interface member's own public test, never "modifiers contains public");
//   - a constructor (`<init>`) is never reported as a method, matching every other enumerator's
//     own exclusion of constructors from "methods";
//   - an ENUM CONSTANT is distinguished from a genuine field by POSITION, never by name or by
//     its initializer's tree kind alone (both would be fooled by a field such as `private final
//     Logger log = new Logger();`, which also has a NEW_CLASS initializer): javac synthesises
//     an enum constant's own "type" tree at the exact same source offset as the constant's own
//     name (there is no real type token in the source -- `OK(0)` names no type at all), so
//     `getStartPosition(variable) == getStartPosition(variable.getType())` is true for every
//     enum constant and false for every real field declaration (which always has a real,
//     separately-positioned type token before its name). Confirmed directly against both a
//     plain enum (no constructor args) and one with explicit int constructor args, and against
//     a genuine field inside the same enum body;
//   - an enum constant's own constructor arguments (when it has any -- `OK(0)`, not bare `OK`)
//     are read EXACTLY, via the synthesised NEW_CLASS initializer's own argument list, each
//     argument's literal source text joined by a comma, never merely counted. A constant with
//     no constructor arguments reports enum_value as null ("not checked"), matching
//     enumerate_rust's own "no explicit discriminant" convention for the same reason: Java has
//     no language-level analogue of Rust's implicit-successor discriminant, so there is no
//     value to assert when none was written;
//   - a parameter's "has_default" is always false -- Java has no default parameter values,
//     matching enumerate_go/enumerate_rust's own identical, honest default;
//   - a field's/method's "is_static" is read from its own modifiers' literal `static` keyword,
//     with one deliberate exception: every interface field is reported is_static=true even
//     without an explicit `static` keyword, because JLS 9.3 makes every interface field
//     implicitly `public static final` -- the Tree API itself does not synthesise that implicit
//     flag (confirmed directly: `int MAX = 100;` inside an interface parses back with an EMPTY
//     modifier set), so this program fills in that one piece of real Java semantics itself
//     rather than reporting a literally-false "not static" for something the language itself
//     guarantees is static.
//
// Output JSON shape, one array of:
//   {
//     "name": "Document",
//     "kind": "class" | "enum",
//     "members": [
//       {"name": "save", "kind": "method", "params": [{"name": "path"}], "is_static": false, "enum_value": null},
//       {"name": "title", "kind": "property", "params": [], "is_static": false, "enum_value": null},
//       {"name": "OK", "kind": "enum_value", "params": [], "is_static": false, "enum_value": "0"}
//     ]
//   }

import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.ModifiersTree;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.Trees;

import javax.lang.model.element.Modifier;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class JavaEnumerator {

    private static final Set<String> SKIPPED_DIR_NAMES = Set.of(
            "tests", "test", "examples", "docs", "target", "build", "out", "node_modules", ".git"
    );

    /** One parsed top-level type, carrying everything symbolToJson needs: the type's own tree,
     * the compilation unit it came from, and the SourcePositions used to tell a real field apart
     * from an enum constant (see this file's own module docstring). */
    private static final class ParsedType {
        final ClassTree classTree;
        final CompilationUnitTree unit;
        final SourcePositions positions;

        ParsedType(ClassTree classTree, CompilationUnitTree unit, SourcePositions positions) {
            this.classTree = classTree;
            this.unit = unit;
            this.positions = positions;
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length != 1) {
            System.err.println("usage: java JavaEnumerator.java <library-dir>");
            System.exit(2);
        }
        List<Path> files = listJavaFiles(Path.of(args[0]));
        List<String> symbolJsons = new ArrayList<>();
        for (Path file : files) {
            for (ParsedType parsed : parsePublicTopLevelTypes(file)) {
                symbolJsons.add(symbolToJson(parsed));
            }
        }
        System.out.println("[" + String.join(",", symbolJsons) + "]");
    }

    /** Every *.java file under root, recursively, skipping any path component named in
     * SKIPPED_DIR_NAMES -- never a fixture, never a cached build artefact. Sorted for a
     * deterministic symbol order. */
    private static List<Path> listJavaFiles(Path root) throws IOException {
        if (!Files.isDirectory(root)) {
            return List.of();
        }
        try (Stream<Path> walk = Files.walk(root)) {
            return walk
                    .filter(Files::isRegularFile)
                    .filter(p -> p.getFileName().toString().endsWith(".java"))
                    .filter(p -> {
                        for (Path part : root.relativize(p)) {
                            if (SKIPPED_DIR_NAMES.contains(part.toString())) {
                                return false;
                            }
                        }
                        return true;
                    })
                    .sorted()
                    .collect(Collectors.toList());
        }
    }

    /** Parses one file, PARSE PHASE ONLY (never .analyze()/.generate() -- see this file's own
     * module docstring for why that is both sufficient and deliberate). Returns every public
     * top-level class/interface/enum declaration it found, or an empty list when the file has a
     * genuine syntax error (never a partial/fabricated reading of a broken file). */
    private static List<ParsedType> parsePublicTopLevelTypes(Path file) throws IOException {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        StandardJavaFileManager fileManager = compiler.getStandardFileManager(null, null, null);
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        Iterable<? extends JavaFileObject> units =
                fileManager.getJavaFileObjectsFromFiles(Arrays.asList(file.toFile()));
        JavacTask task = (JavacTask) compiler.getTask(null, fileManager, diagnostics, null, null, units);

        List<ParsedType> found = new ArrayList<>();
        try {
            Trees trees = Trees.instance(task);
            SourcePositions positions = trees.getSourcePositions();
            for (CompilationUnitTree unit : task.parse()) {
                boolean hasSyntaxError = diagnostics.getDiagnostics().stream()
                        .anyMatch(d -> d.getKind() == Diagnostic.Kind.ERROR);
                if (hasSyntaxError) {
                    continue; // skip this file entirely: never half-read a broken file
                }
                for (Tree decl : unit.getTypeDecls()) {
                    if (decl instanceof ClassTree) {
                        collectPublicTypesRecursively((ClassTree) decl, unit, positions, found);
                    }
                }
            }
        } catch (RuntimeException e) {
            return List.of(); // a parser-internal failure on a malformed file: skip, never crash
        } finally {
            fileManager.close();
        }
        return found;
    }

    /** Collects `node` itself (when it is public and a class/interface/enum) AND, only when
     * `node` is public, recurses into its own member types too -- a NESTED class/interface/enum
     * is part of the real public API exactly when the full chain down to it is public, matching
     * real Java accessibility (an outside caller can never even name a nested type whose
     * enclosing type it cannot name). This flattens nested public types into the same flat
     * symbol list top-level types use, by their own simple name, matching the real baked
     * fixture's own observed behaviour -- confirmed directly against the real pinned pdf/java
     * pilot: roughly a fifth of its own distinct type names (e.g. an enum named inside its
     * owning class, such as a settings class's own nested status enum) are public STATIC nested
     * declarations, never top-level ones, and are silently and completely absent from a
     * top-level-only reading (not merely mis-counted -- absent from every category this card's
     * diff categories can even express, since the diff engine only ever walks the oracle's own
     * symbol list). A non-public node is never recursed into: whatever public members it
     * declares are still unreachable from outside the library. */
    private static void collectPublicTypesRecursively(
            ClassTree node, CompilationUnitTree unit, SourcePositions positions, List<ParsedType> out) {
        if (!isPublic(node.getModifiers())) {
            return;
        }
        if (isCandidateKind(node)) {
            out.add(new ParsedType(node, unit, positions));
        }
        for (Tree member : node.getMembers()) {
            if (member instanceof ClassTree) {
                collectPublicTypesRecursively((ClassTree) member, unit, positions, out);
            }
        }
    }

    private static boolean isCandidateKind(ClassTree classTree) {
        Tree.Kind kind = classTree.getKind();
        return kind == Tree.Kind.CLASS || kind == Tree.Kind.INTERFACE || kind == Tree.Kind.ENUM;
    }

    private static boolean isPublic(ModifiersTree modifiers) {
        return modifiers.getFlags().contains(Modifier.PUBLIC);
    }

    private static boolean isStatic(ModifiersTree modifiers) {
        return modifiers.getFlags().contains(Modifier.STATIC);
    }

    private static boolean isPrivate(ModifiersTree modifiers) {
        return modifiers.getFlags().contains(Modifier.PRIVATE);
    }

    private static String symbolToJson(ParsedType parsed) {
        ClassTree classTree = parsed.classTree;
        boolean isEnum = classTree.getKind() == Tree.Kind.ENUM;
        boolean isInterface = classTree.getKind() == Tree.Kind.INTERFACE;
        List<String> memberJsons = new ArrayList<>();

        for (Tree member : classTree.getMembers()) {
            if (member instanceof MethodTree) {
                MethodTree methodTree = (MethodTree) member;
                if (methodTree.getName().toString().equals("<init>")) {
                    continue; // constructors are never reported as methods
                }
                if (!isMemberPublic(isInterface, methodTree.getModifiers())) {
                    continue;
                }
                memberJsons.add(methodMemberJson(methodTree, isStatic(methodTree.getModifiers())));
            } else if (member instanceof VariableTree) {
                VariableTree variableTree = (VariableTree) member;
                if (isEnum && isEnumConstant(parsed, variableTree)) {
                    memberJsons.add(enumConstantJson(variableTree));
                    continue;
                }
                if (!isMemberPublic(isInterface, variableTree.getModifiers())) {
                    continue;
                }
                boolean fieldIsStatic = isInterface || isStatic(variableTree.getModifiers());
                memberJsons.add(propertyMemberJson(variableTree, fieldIsStatic));
            }
        }

        return "{\"name\":\"" + jsonEscape(classTree.getSimpleName().toString()) + "\","
                + "\"kind\":\"" + (isEnum ? "enum" : "class") + "\","
                + "\"members\":[" + String.join(",", memberJsons) + "]}";
    }

    /** A CLASS/ENUM member is public only when explicitly marked `public` -- protected and
     * package-private are both excluded (the card's own named defect case). An INTERFACE member
     * is public unless explicitly marked `private` -- the Tree API never synthesises an implicit
     * `public` modifier for an interface member, so "not private" is the correct public test
     * there (see this file's own module docstring). */
    private static boolean isMemberPublic(boolean isInterface, ModifiersTree modifiers) {
        return isInterface ? !isPrivate(modifiers) : isPublic(modifiers);
    }

    /** True exactly when `variable` is a real enum constant of `parsed`'s own enum, never a
     * genuine field that merely happens to have a `new Something()` initializer -- see this
     * file's own module docstring for why position, not the initializer's tree kind, is the
     * right test. */
    private static boolean isEnumConstant(ParsedType parsed, VariableTree variable) {
        long variableStart = parsed.positions.getStartPosition(parsed.unit, variable);
        long typeStart = parsed.positions.getStartPosition(parsed.unit, variable.getType());
        return variableStart == typeStart;
    }

    private static String methodMemberJson(MethodTree methodTree, boolean isStatic) {
        List<String> params = new ArrayList<>();
        for (var param : methodTree.getParameters()) {
            params.add("{\"name\":\"" + jsonEscape(param.getName().toString()) + "\"}");
        }
        return "{\"name\":\"" + jsonEscape(methodTree.getName().toString()) + "\","
                + "\"kind\":\"method\","
                + "\"params\":[" + String.join(",", params) + "],"
                + "\"is_static\":" + isStatic + ","
                + "\"enum_value\":null}";
    }

    private static String propertyMemberJson(VariableTree variableTree, boolean isStatic) {
        return "{\"name\":\"" + jsonEscape(variableTree.getName().toString()) + "\","
                + "\"kind\":\"property\","
                + "\"params\":[],"
                + "\"is_static\":" + isStatic + ","
                + "\"enum_value\":null}";
    }

    private static String enumConstantJson(VariableTree variableTree) {
        String enumValue = constructorArgsLiteral(variableTree.getInitializer());
        return "{\"name\":\"" + jsonEscape(variableTree.getName().toString()) + "\","
                + "\"kind\":\"enum_value\","
                + "\"params\":[],"
                + "\"is_static\":false,"
                + "\"enum_value\":" + (enumValue == null ? "null" : "\"" + jsonEscape(enumValue) + "\"") + "}";
    }

    /** The exact, joined source text of an enum constant's own constructor arguments (`OK(0)` ->
     * "0"; `Pair(1, 2)` -> "1,2"), or null when the constant takes none (`OK` -> null, "not
     * checked" -- matching enumerate_rust's own convention for a variant with no explicit
     * discriminant). */
    private static String constructorArgsLiteral(ExpressionTree initializer) {
        if (!(initializer instanceof NewClassTree)) {
            return null;
        }
        List<? extends ExpressionTree> arguments = ((NewClassTree) initializer).getArguments();
        if (arguments.isEmpty()) {
            return null;
        }
        return arguments.stream().map(Object::toString).collect(Collectors.joining(","));
    }

    private static String jsonEscape(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}
