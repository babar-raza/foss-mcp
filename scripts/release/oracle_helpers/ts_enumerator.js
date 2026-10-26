// ts_enumerator.js is a small companion program for source_differential.py's independent
// TypeScript oracle (G3/TC-591). It uses the REAL TypeScript Compiler API (ts.createProgram
// plus its own TypeChecker -- never a regex, never this project's own tree-sitter grammar,
// which would make the oracle circular) to read every exported class/interface reachable from
// a library's own declared public entry point and prints one JSON array to stdout describing
// each symbol it found. source_differential.py's enumerate_typescript runs this with `node`
// (the already-trusted host interpreter that runs a JS program -- no fresh native binary is
// ever built or executed), so, unlike enumerate_go/enumerate_rust (G3/TC-589/TC-590/TC-596),
// this never needs Docker containerization to work around this host's Windows Application
// Control policy: WDAC blocks a freshly-built, unsigned native .exe, and `node` running a .js
// file is neither -- see source_differential.py's own module docstring.
//
// Usage:
//   node ts_enumerator.js <library-dir> <typescript-package-dir>
//
// <typescript-package-dir> is the absolute path to the already-installed `typescript` npm
// package's own directory (resolved once by the Python side, TYPESCRIPT_NPM_PACKAGE_DIR in
// source_differential.py), so this script never installs its own node_modules or reaches the
// network -- `require(typescript-package-dir)` resolves through that package's own
// package.json "main" field exactly like any other node_modules import.
//
// SCOPE (read this before changing it -- it was NOT the first design tried):
//   A first version of this script walked every `*.ts` file under <library-dir>/src and
//   collected every module-local `export`ed class/interface, matching enumerate_go's own
//   "every exported symbol in every file" rule. For Go that rule IS the language's whole
//   notion of public API (capitalised = exported = public, full stop). TypeScript has an
//   extra layer Go does not: a symbol can be `export`ed from an internal file (reachable via
//   a deep import) without being part of the PACKAGE's own intended public surface, which is
//   only what is reachable from its declared entry point (package.json's "types"/"main").
//   Proven directly against this card's own pinned pilot (aspose-pdf-foss/
//   Aspose.PDF-FOSS-for-TypeScript @ 155bfc7a33f0ba23fb4252b6ba201828b02a5b9d): the naive
//   "every file's own exports" rule found 776 class/interface declarations, nearly DOUBLE the
//   pilot's own baked api_surface.json fixture's entire type count across every kind (541,
//   of which only 337 are class/interface/abstract_class at all) -- almost all of the excess
//   came from internal, file-local-only interfaces (e.g. small per-module option bags) that
//   are genuinely exported at the TypeScript LANGUAGE level but never re-exported through the
//   package's own curated `src/index.ts` barrel, so they are not part of its real public API.
//   Scoping to symbols reachable from the package's own declared entry point instead found
//   45 classes + 289 interfaces = 334 -- within 3 of the fixture's own 337, i.e. this measures
//   the right thing. This script therefore enumerates the PACKAGE's public surface (entry
//   point reachability, read with checker.getExportsOfModule on the entry file alone, which
//   already transitively resolves every `export * from`/`export { X } from` hop on its own),
//   never merely the TypeScript language's own per-file export keyword.
//
// Entry point resolution: package.json's "types" (preferred), then "typings", then "main",
// mapped from its build-output form (conventionally a sibling of `src/`, e.g. "dist/index.js"
// -> "src/index.ts" -- this pilot's own tsconfig.build.json mirrors rootDir "src" onto outDir
// "dist" 1:1, which is the common pattern) onto the real `.ts` source tree; if no such file
// exists (an unusual layout, or no package.json at all -- every offline unit test's own tiny
// synthetic fixture has neither), this script falls back to the original "every file's own
// module-local export" rule over the whole src/ tree instead of silently reporting nothing,
// printing one honest note to stderr when it does.
//
// Scope, matching enumerate_python/enumerate_go/enumerate_rust's own scope exactly
// (type-level public API only), independent of which of the two strategies above resolved
// the candidate symbol list:
//   - every `*.ts` file (never `*.d.ts`) directly under <library-dir>/src, recursively,
//     skipping any nested test/examples/docs/scripts directory (mirrors go_enumerator.go's
//     own skipDirs; this exact pilot keeps those entirely outside src/ already, so this is
//     defense in depth, not something this pilot currently needs) is a candidate for the
//     Program itself, so every cross-file reference the type checker needs stays resolvable;
//   - a class's own members are read from the type checker's own apparent-type properties:
//     checker.getPropertiesOfType on the class's declared (instance) type for instance
//     members, and again on the class symbol's own "static side" type (the constructor
//     function type returned by checker.getTypeOfSymbolAtLocation) for static members, minus
//     the synthetic "prototype" property every class's static side carries. An interface has
//     no static side, so every interface member is read from its declared type alone and is
//     always reported with is_static false;
//   - this getPropertiesOfType-based read is also what gives a correct, SANE overload-set
//     result for free, with no extra bookkeeping: TypeScript's own checker already merges
//     every overload signature plus its implementation into ONE symbol per member name (
//     confirmed directly: a 3-declaration overload set reads back as exactly one "members"
//     entry), so an overloaded method is never duplicated and never dropped. This script picks
//     that merged symbol's own IMPLEMENTATION declaration (the one with a function body) for
//     its reported parameter list when one exists (the real, callable signature), and falls
//     back to the member's first declared signature when there is no implementation (every
//     interface method, and an abstract/ambient class method);
//   - a member explicitly modified `private` is excluded (checked with
//     ts.getCombinedModifierFlags on its representative declaration, which also correctly
//     reads a `private` constructor-parameter property). TypeScript refuses to compile a
//     `private` member inside an interface at all, so every interface member is always public
//     by construction; a class member with NO visibility modifier is public by TypeScript's
//     own default -- this script never treats "no modifier" as anything other than public,
//     which the card driving this file calls out as the single most important rule here;
//   - a parameter's "has_default" is true ONLY when its own AST node carries an `initializer`
//     (a real default VALUE, e.g. `count: number = 1`). A `?` optional marker
//     (`param.questionToken`) is a completely different thing and must never set has_default
//     -- confirmed directly: `optionalOnly(a?: string)` reads back has_default=false, exactly
//     matching the real pilot's own baked fixture, which keeps "optional" and "default" as two
//     separate fields for exactly this reason. A synthetic `this: T` pseudo-parameter (TS's
//     own explicit-this-typing syntax) is never reported as a real parameter.
//
// Output JSON shape, one array of:
//   {
//     "name": "Document",
//     "kind": "class" | "interface",
//     "members": [
//       {"name": "Save", "kind": "method", "params": [{"name": "path", "has_default": false}], "is_static": false},
//       {"name": "Title", "kind": "property", "params": [], "is_static": false}
//     ]
//   }

'use strict';

const fs = require('fs');
const path = require('path');

const SKIP_DIR_NAMES = new Set(['tests', 'test', 'examples', 'docs', 'scripts', 'node_modules', '.git']);
const BUILD_OUTPUT_DIR_NAMES = new Set(['dist', 'lib', 'build', 'out']);

function listSourceFiles(root) {
  const out = [];
  const stack = [root];
  while (stack.length > 0) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (err) {
      continue; // an unreadable directory is skipped, never fatal
    }
    for (const entry of entries) {
      if (SKIP_DIR_NAMES.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
        continue;
      }
      if (entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts')) {
        out.push(full);
      }
    }
  }
  out.sort();
  return out;
}

/** The package's own declared public entry point, mapped onto its real `.ts` source (see this
 * file's own "SCOPE" docstring above for why this matters); `null` when none can be found, in
 * which case the caller falls back to a full-tree, per-file export scan. */
function resolveEntryFile(libraryDir) {
  let pkg = {};
  try {
    pkg = JSON.parse(fs.readFileSync(path.join(libraryDir, 'package.json'), 'utf-8'));
  } catch (err) {
    return null;
  }
  const declared = pkg.types || pkg.typings || pkg.main;
  if (!declared || typeof declared !== 'string') return null;
  const parts = declared.split('/');
  if (BUILD_OUTPUT_DIR_NAMES.has(parts[0])) parts[0] = 'src';
  const withoutExt = parts.join('/').replace(/\.d\.ts$|\.(ts|js|mjs|cjs)$/, '');
  const candidate = path.join(libraryDir, withoutExt + '.ts');
  if (fs.existsSync(candidate)) return candidate;
  const fallback = path.join(libraryDir, 'src', 'index.ts');
  return fs.existsSync(fallback) ? fallback : null;
}

function paramName(param) {
  return param.name && typeof param.name.getText === 'function' ? param.name.getText() : String(param.name);
}

function isThisParam(ts, param) {
  return ts.isIdentifier(param.name) && param.name.text === 'this';
}

function representativeDeclaration(declarations) {
  const withBody = declarations.find((d) => d.body !== undefined);
  return withBody || declarations[0];
}

function readParams(ts, decl) {
  if (!decl || !decl.parameters) return [];
  const params = [];
  for (const param of decl.parameters) {
    if (isThisParam(ts, param)) continue;
    params.push({ name: paramName(param), has_default: param.initializer !== undefined });
  }
  return params;
}

function isPrivate(ts, decl) {
  if (!decl) return false;
  return (ts.getCombinedModifierFlags(decl) & ts.ModifierFlags.Private) !== 0;
}

/** Every member of `type` (an instance or a static-side type), filtered/shaped per this file's
 * own docstring above. `isStatic` is just the literal flag this function stamps on every
 * member it emits -- callers invoke it once for the instance side and once (classes only) for
 * the static side. */
function membersFromType(ts, checker, type, { isStatic }) {
  const members = [];
  for (const propertySymbol of checker.getPropertiesOfType(type)) {
    if (propertySymbol.name === 'prototype' || propertySymbol.name === '__constructor') continue;
    const declarations = propertySymbol.getDeclarations() || [];
    if (declarations.length === 0) continue;
    const decl = representativeDeclaration(declarations);
    if (isPrivate(ts, decl)) continue;
    const isMethod = (propertySymbol.flags & ts.SymbolFlags.Method) !== 0;
    members.push({
      name: propertySymbol.name,
      kind: isMethod ? 'method' : 'property',
      params: isMethod ? readParams(ts, decl) : [],
      is_static: isStatic,
    });
  }
  return members;
}

function symbolFromClassOrInterface(ts, checker, resolvedSymbol, declaration) {
  const isClass = ts.isClassDeclaration(declaration);
  const instanceType = checker.getDeclaredTypeOfSymbol(resolvedSymbol);
  let members = membersFromType(ts, checker, instanceType, { isStatic: false });
  if (isClass) {
    const staticType = checker.getTypeOfSymbolAtLocation(resolvedSymbol, declaration);
    members = members.concat(membersFromType(ts, checker, staticType, { isStatic: true }));
  }
  return {
    name: declaration.name ? declaration.name.getText() : resolvedSymbol.name,
    kind: isClass ? 'class' : 'interface',
    members,
  };
}

function resolveAlias(ts, checker, symbol) {
  if ((symbol.flags & ts.SymbolFlags.Alias) === 0) return symbol;
  try {
    return checker.getAliasedSymbol(symbol);
  } catch (err) {
    return symbol; // an unresolvable alias is reported as itself, never fatal
  }
}

function classOrInterfaceDeclaration(ts, declarations, { requireSourceFile } = {}) {
  return declarations.find(
    (d) =>
      (ts.isClassDeclaration(d) || ts.isInterfaceDeclaration(d)) &&
      (!requireSourceFile || d.getSourceFile() === requireSourceFile)
  );
}

/** Strategy 1 (preferred): every class/interface reachable from the package's own declared
 * entry point. checker.getExportsOfModule on the entry file alone already transitively
 * resolves every `export *`/named re-export hop, so this needs no manual file-graph walk. */
function enumerateFromEntryPoint(ts, checker, entrySourceFile) {
  const moduleSymbol = checker.getSymbolAtLocation(entrySourceFile);
  if (!moduleSymbol) return null;
  const symbols = [];
  const seenNames = new Set();
  for (const exported of checker.getExportsOfModule(moduleSymbol)) {
    const resolved = resolveAlias(ts, checker, exported);
    const declaration = classOrInterfaceDeclaration(ts, resolved.getDeclarations() || []);
    if (!declaration) continue;
    const symbol = symbolFromClassOrInterface(ts, checker, resolved, declaration);
    const dedupeKey = symbol.kind + ':' + symbol.name;
    if (seenNames.has(dedupeKey)) continue;
    seenNames.add(dedupeKey);
    symbols.push(symbol);
  }
  return symbols;
}

/** Strategy 2 (fallback): every module-local export, file by file, attributing each symbol to
 * the file that physically declares it so a barrel re-export is never double-counted. Used
 * only when the package's entry point cannot be resolved (see this file's own "SCOPE"
 * docstring) -- every offline synthetic test fixture takes this path, since none of them ships
 * a package.json. */
function enumerateEveryFileExport(ts, checker, files, program) {
  const symbols = [];
  const seenNames = new Set();
  for (const file of files) {
    const sourceFile = program.getSourceFile(file);
    if (!sourceFile) continue;
    const moduleSymbol = checker.getSymbolAtLocation(sourceFile);
    if (!moduleSymbol) continue;
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const resolved = resolveAlias(ts, checker, exported);
      const declaration = classOrInterfaceDeclaration(ts, resolved.getDeclarations() || [], {
        requireSourceFile: sourceFile,
      });
      if (!declaration) continue;
      const symbol = symbolFromClassOrInterface(ts, checker, resolved, declaration);
      const dedupeKey = symbol.kind + ':' + symbol.name;
      if (seenNames.has(dedupeKey)) continue;
      seenNames.add(dedupeKey);
      symbols.push(symbol);
    }
  }
  return symbols;
}

function main() {
  const [, , libraryDir, typescriptPackageDir] = process.argv;
  if (!libraryDir || !typescriptPackageDir) {
    process.stderr.write('usage: node ts_enumerator.js <library-dir> <typescript-package-dir>\n');
    process.exit(2);
  }

  let ts;
  try {
    ts = require(typescriptPackageDir);
  } catch (err) {
    process.stderr.write(
      'ts_enumerator: could not require the typescript package at ' + typescriptPackageDir + ': ' + err + '\n'
    );
    process.exit(1);
  }

  const srcDir = path.join(libraryDir, 'src');
  const files = fs.existsSync(srcDir) ? listSourceFiles(srcDir) : [];
  if (files.length === 0) {
    process.stdout.write('[]\n');
    return;
  }

  const compilerOptions = {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.NodeNext,
    moduleResolution: ts.ModuleResolutionKind.NodeNext,
    strict: false,
    skipLibCheck: true,
    noEmit: true,
    types: [],
  };

  const program = ts.createProgram(files, compilerOptions);
  const checker = program.getTypeChecker();

  const entryFile = resolveEntryFile(libraryDir);
  const entrySourceFile = entryFile ? program.getSourceFile(entryFile) : undefined;

  let symbols = entrySourceFile ? enumerateFromEntryPoint(ts, checker, entrySourceFile) : null;
  if (symbols === null) {
    process.stderr.write(
      'ts_enumerator: no resolvable package entry point under ' +
        libraryDir +
        '; falling back to a full source-tree scan of every file\'s own module-local exports ' +
        '(see this file\'s own "SCOPE" docstring -- this may overcount versus the package\'s ' +
        'own curated public surface)\n'
    );
    symbols = enumerateEveryFileExport(ts, checker, files, program);
  }
  symbols.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

  process.stdout.write(JSON.stringify(symbols) + '\n');
}

main();
