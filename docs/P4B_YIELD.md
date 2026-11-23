# P4B example-yield measurement (G3/TC-866, REQ-G3-014, decision gate DG-3)

Measured 2026-11-23 by `scripts/release/measure_sample_yield.py`. Question: of the example programs an upstream
project ships itself, how many can be turned into verified examples by the product's own verifier? The plan assumed
about 70%. This document reports what was measured, not the assumption.

## Decision

**DG-3 for cells/python: `proceed`.** 27 of 32 measured samples verified, 84.4%, against the 70% rule
(`yield_decision(27, 32) == "proceed"`). No revision of the cells/python minimum is proposed and no floor is
lowered. The stricter reading in "What the 84.4% does and does not mean" gives 25 of 30, 83.3%, which is also a
`proceed`. The figure is a measurement of 32 files from one project; it does not by itself say that the corpus gap
(10 of 30) closes (see the caveats).

## Method

| Item | Value |
| --- | --- |
| Pilot | cells/python |
| Upstream repository | `aspose-cells-foss/Aspose.Cells-FOSS-for-Python` |
| Pin (infra/helm/foss-mcp/values.yaml) | `4f6768a7b349a1309644f456eb43bc35f70c16d7` (checked out exactly; `git rev-parse HEAD` matched) |
| Sample directory | `examples/` (32 files, 520 015 bytes; repository licence MIT; no bundled font, image or logo) |
| Ingestion image | `foss-mcp-ingestion:regen-p4b`, id `sha256:b26c3cf071d0fa053349ce7ac8d1bc2d26d675f6132985cae76a25daea65c606`, built from the TC-866 worktree (main `7813c4ef`); toolchain labels: dotnet 8.0.426 + 10.0.402, uv 0.13.0 / python 3.12.15, node 24.13.1, go 1.26.4, rust 1.98.1 |
| Library environment | the product's own `prepare_python_library` (venv + `pip install -e` of the pinned tree, with network); the venv interpreter reported `Python 3.13.16`; installed: `aspose_cells_foss` (editable, pinned commit), `olefile==0.47`, `pycryptodome==4.6.0` |
| Staging and run step | the product's own `verify_python_example` (`example_verifier.py`): staging of repository files a sample names, `provide_fixtures` (`example_fixtures.py`), the bounded 120 s run, the import-only rejection; plus `example_artifacts.artifact_problems` on every file a run wrote. Imported inside the image, not reimplemented |
| Isolation | one fresh container per sample, `--network none`, 4 GB memory, 2 CPUs, 512 pids; a writable copy of the set-up environment on a tmpfs, so a sample that writes under the repository (`examples/outputfiles`) neither fails on a read-only tree nor leaves files for the next sample. Nothing from the host environment (no token, no endpoint) is passed to a container |
| Reproducibility | two complete runs (set-up included) gave identical classifications and reasons for all 32 samples. Time per sample: mean 0.9 s, max 2.1 s, 29-30 s for all 32 (wall 43-44 s per run including the set-up container) |

Classes (each sample exactly once): `verified-ran-produced-output` (exit 0 and the run printed output or wrote a
valid file), `verified-ran-exit0` (exit 0, nothing printed, no file), `rejected-compile`,
`rejected-missing-fixture` (names the file), `rejected-runtime` (first error line),
`rejected-needs-network-or-credentials`, `rejected-no-statements`. A sample whose licence or bundled assets forbid
republication is `excluded` with the reason and reported beside the denominator; **none was excluded here** (the
repository is MIT and `examples/` bundles no binary asset).

## Per-sample result

| Sample | Classification | Reason | Seconds |
| --- | --- | --- | ---: |
| `__init__.py` | rejected-no-statements | only imports, comments or blank lines; nothing was run | 0.7 |
| `output_path_helper.py` | verified-ran-exit0 | exit 0, no output and no file written | 0.7 |
| `test_alignment_properties.py` | verified-ran-produced-output | printed output; unittest: ran 11, OK | 0.9 |
| `test_auto_filter.py` | verified-ran-exit0 | exit 0, no output and no file written; unittest: ran 14, OK | 0.9 |
| `test_border_settings.py` | verified-ran-produced-output | printed output; unittest: ran 10, OK | 0.9 |
| `test_cell_protection_locked.py` | verified-ran-produced-output | printed output; unittest: ran 3, OK | 0.9 |
| `test_cell_values.py` | verified-ran-produced-output | printed output; unittest: ran 7, OK | 0.9 |
| `test_comment_size.py` | verified-ran-produced-output | printed output; unittest: ran 5, OK | 0.8 |
| `test_comments.py` | verified-ran-produced-output | printed output; unittest: ran 7, OK | 0.8 |
| `test_conditional_formatting.py` | verified-ran-produced-output | printed output; unittest: ran 13, OK | 0.8 |
| `test_create_all_charts.py` | rejected-runtime | ModuleNotFoundError: No module named 'pytest' | 0.7 |
| `test_create_exceltable.py` | verified-ran-produced-output | printed output | 0.8 |
| `test_create_picture.py` | verified-ran-exit0 | exit 0, no output and no file written; unittest: ran 1, OK (skipped=1) | 1.5 |
| `test_create_shape.py` | verified-ran-produced-output | printed output | 0.8 |
| `test_create_sparkline.py` | verified-ran-produced-output | printed output | 0.8 |
| `test_csv_import_export.py` | rejected-runtime | AssertionError: FileNotFoundError not raised | 1.7 |
| `test_data_validation.py` | verified-ran-produced-output | wrote outputfiles/example_test_alert_styles.xlsx, outputfiles/example_test_all_operators.xlsx, outputfiles/example_test_custom.xlsx, outputfiles/example_test_date.xlsx, outputfiles/example_test_decima | 0.9 |
| `test_document_properties.py` | verified-ran-produced-output | wrote outputfiles/example_comprehensive_document_properties.xlsx, outputfiles/example_convenience_properties.xlsx, outputfiles/example_core_properties_basic.xlsx, outputfiles/example_core_properties_d | 0.9 |
| `test_encryption.py` | verified-ran-produced-output | printed output; unittest: ran 9, OK | 2.1 |
| `test_fill_settings.py` | verified-ran-produced-output | printed output; unittest: ran 2, OK | 0.8 |
| `test_font_settings.py` | verified-ran-produced-output | printed output; unittest: ran 13, OK | 0.9 |
| `test_hyperlinks.py` | verified-ran-produced-output | printed output; unittest: ran 9, OK | 0.9 |
| `test_manual_page_breaks.py` | verified-ran-exit0 | exit 0, no output and no file written; unittest: ran 1, OK | 0.9 |
| `test_merge_cells.py` | verified-ran-produced-output | wrote outputfiles/example_merge_cells_merged.xlsx, outputfiles/example_merge_cells_unmerged.xlsx; unittest: ran 1, OK | 0.9 |
| `test_number_formats.py` | verified-ran-produced-output | printed output; unittest: ran 5, OK | 0.9 |
| `test_print_area.py` | verified-ran-produced-output | wrote outputfiles/example_print_area_cleared_demo.xlsx, outputfiles/example_print_area_demo.xlsx; unittest: ran 2, OK | 0.9 |
| `test_workbook_protection.py` | verified-ran-produced-output | printed output; unittest: ran 3, OK | 0.9 |
| `test_worksheet_management.py` | verified-ran-produced-output | printed output; unittest: ran 12, OK | 0.9 |
| `test_worksheet_properties.py` | verified-ran-produced-output | printed output; unittest: ran 8, OK | 0.8 |
| `test_worksheet_protection.py` | verified-ran-produced-output | printed output; unittest: ran 3, OK | 0.9 |
| `test_xlsx_to_json.py` | rejected-missing-fixture | input/sales_report_comprehensive.xlsx | 0.8 |
| `test_xlsx_to_markdown.py` | rejected-missing-fixture | input/*.xlsx | 0.9 |

## Totals

| Class | Count |
| --- | ---: |
| verified-ran-produced-output | 23 |
| verified-ran-exit0 | 4 |
| rejected-compile | 0 |
| rejected-missing-fixture | 2 |
| rejected-runtime | 2 |
| rejected-needs-network-or-credentials | 0 |
| rejected-no-statements | 1 |
| excluded | 0 |

Verified 27 / denominator 32 (0 excluded of 32 found) = **84.4%**. Rule: `proceed` when verified/total >= 0.70,
else `revise`. Decision: **proceed**.

## What the 84.4% does and does not mean

- **The upstream samples are test modules, not tutorial scripts.** 30 of the 32 files are `test_*.py` (46 to 1 378
  lines): 26 define `unittest.TestCase` classes, and 4 (`test_create_all_charts.py`, `test_create_exceltable.py`,
  `test_create_shape.py`, `test_create_sparkline.py`) are plain scripts with a `__main__` function. The other two
  files are `__init__.py` (empty) and `output_path_helper.py` (a helper that 18 files import). For a `unittest`
  module exit 0 means "its own tests passed", which is a stronger statement than for a README snippet, but a
  published example would be a whole test module.
- **Two verified samples are not examples.** `output_path_helper.py` only defines two functions (exit 0, nothing
  exercised) and `test_create_picture.py` ran 1 test and skipped it (`OK (skipped=1)`: `input/picture/R.jpg` is not
  in the repository). Excluding both, the stricter figure is **25 of 30 test modules, 83.3%** (still `proceed`).
- **Two further verified samples ran silently** (`test_auto_filter.py`: 14 tests OK; `test_manual_page_breaks.py`:
  1 test OK; no stdout, no file). They count as `verified-ran-exit0`.
- **The rejections.** `test_create_all_charts.py` imports `pytest`, which is not a dependency of the library
  (`rejected-runtime`). `test_csv_import_export.py` fails its own assertion (`wb.load_csv('nonexistent_file.csv')`
  does not raise `FileNotFoundError` at this commit): a genuine library behaviour, recorded as `rejected-runtime`.
  `test_xlsx_to_json.py` and `test_xlsx_to_markdown.py` read an `input/` directory the repository does not ship
  (`rejected-missing-fixture`, naming `input/sales_report_comprehensive.xlsx` and `input/*.xlsx`). `__init__.py` is
  empty (`rejected-no-statements`).
- **The product's Python verifier judges by exit code alone** (`ran-exit0`); the produced-output / exit-0 split
  and the artefact check are this tool's. No written file failed the artefact validators in this run.
- **Environment sensitivity.** A first attempt that mounted the library tree read-only measured 10 of 32 (31.2%):
  18 samples failed with `OSError: Read-only file system: '/work/repo/examples/outputfiles'`, because the editable
  install makes `examples/output_path_helper.py` resolve inside the repository. That was a defect of the harness, not
  of the samples, and is discarded; the product's real build runs candidates against a writable repository copy,
  which the final runs reproduce.
- **Classification heuristics** (the offline tests cover each): a `SyntaxError`/`IndentationError` is
  `rejected-compile`; the verifier's `fixture_unavailable` status, a `FileNotFoundError` naming a file, or a test's
  own "input file ... does not exist" / "No XLSX files found in input directory" message is
  `rejected-missing-fixture` (verifier placeholders such as `{input_path}` are not file names); connection,
  name-resolution, API-key or credential messages are `rejected-needs-network-or-credentials` (none occurred);
  everything else is `rejected-runtime` with the first `SomeError: ...` line printed.
- **Scope.** Only the Python platform is measured. The other platforms' run steps need their own toolchain set-up and
  are out of scope for this slice.

## Sizing consequence for the example corpus

cells/python has 10 verified examples against a minimum of 30. At the pinned commit 25 test modules (27 by the rule)
verify, so upstream material that passes the verifier is not the limiting factor. What this measurement does not
settle is whether each whole test module is an acceptable published example (size, one example per module,
distinctness from the 10 already present): that is a corpus-design question for the follow-up cards. Because the
decision is `proceed`, no new minimum is proposed and the minimum of 30 for cells/python stands unchanged.

## The five next-largest gaps: upstream material (read-only listing, no execution)

Each pinned checkout was fetched at depth 1 at the commit named in `infra/helm/foss-mcp/values.yaml` and listed;
nothing was built or run.

| Pilot | Pin | Upstream sample material | Language | Shared helper / data needed |
| --- | --- | --- | --- | --- |
| pdf/java (`aspose-pdf-foss/Aspose.PDF-FOSS-for-Java`) | `736cedaa2922e4952f429364bdb9a4bbb1d97863` | No sample directory. 19 `docs/*.md` pages hold 192 fenced `java` blocks (0-22 per page); `src/test/java` has 293 files (91 JUnit classes in `org/aspose/pdf/tests`, 22 more in `tests/generation`) | Java (Maven, `pom.xml`) | No `src/test/resources`, so no data files; the library must be built with Maven |
| pdf/net (`aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET`) | `b7172877651413cff57a8bfe41fb8a8befb2406b` | No sample directory. 17 `docs/*.md` pages hold 210 fenced `csharp` blocks; `tests/` has 127 `.cs` files (xUnit project `Aspose.Pdf.Foss.Tests.csproj`) | C# (.NET) | The repository holds only 4 binary files (2 `docs/media` PNGs, `src/icon.png`, one font resource under `src/`), so no input document or image for a snippet; the test project references the library project |
| cells/rust (`aspose-cells-foss/Aspose.Cells-FOSS-for-Rust`) | `1a6004af47b1ef15385f9d36d381a8172428cc7e` | `samples/`: 14 programs (`basic`, `charts`, `comments`, `conditional_formatting`, `document_properties`, `hyperlinks_and_names`, `list_objects`, `loading`, `pagesetup`, `pictures`, `shapes`, `styles`, `validations`, `worksheet_settings`), 51-554 lines, each a `[[bin]]` in `Cargo.toml`, plus `samples/README.md` | Rust | One shared helper, `samples/common.rs` (`output_path`, a hand-encoded `sample_png_bytes`), declared by all 14 programs (`mod common`); writes under `output/samples`. No external data file, font or image |
| pdf/go (`aspose-pdf-foss/Aspose-PDF-FOSS-for-Go`) | `cdf43df10c8c565ecaa978428b1fe66ad6685f8d` | `_examples/`: 21 programs, one `main.go` per directory (21-3 195 lines): `acroform_build`, `acroform_read`, `ai_alt_text`, `ai_chat`, `ai_make_searchable`, `ai_summary`, `barcode_verify`, `blank_with_images`, `embed_font`, `feature_showcase`, `markdown_to_pdf`, `metadata`, `openall`, `pdf_to_markdown`, `render`, `set_password`, `showcase_preview`, `text_extraction`, `textsearch`, `toc`, `visualtest` | Go | 7 programs read repository data (`testdata/`, 46 files incl. `DejaVuSans.ttf`, `marketing.pdf`, `aspose-logo.png/.svg`; `fonts/` Arimo `.ttf`); `feature_showcase` bundles 7 images and 2 svg under `assets/` with a `CREDITS.md` (public-domain portraits: a per-asset licence check is needed before republishing). 5 programs (`ai_alt_text`, `ai_chat`, `ai_make_searchable`, `ai_summary`, `visualtest`) use `os.Getenv` or `net/http`, so likely need network or credentials |
| cells/net (`aspose-cells-foss/Aspose.Cells-FOSS-for-.NET`) | `941814fd138dc28cf47123d48194f329f283498d` | `samples/`: 15 sample projects, `Aspose.Cells_FOSS.Samples.<Name>/Program.cs` plus a `.csproj` each (Basic, Charts, Comments, ConditionalFormatting, DocumentProperties, HyperlinksAndNames, ListObjects, Loading, PageSetup, PdfConversion, Pictures, Shapes, Styles, Validations, WorksheetSettings), plus `samples/README.md` | C# (.NET) | One shared data file, `samples/files/sample.xlsx` (used by `Loading`); each project references the library project |

These counts come from `git ls-files` and from counting fenced code blocks; they are listings, not verification
results. None of the five was executed, so no yield is claimed for them.

## Reproduce

```
docker build -f Dockerfile.ingestion -t foss-mcp-ingestion:regen-p4b .
python scripts/release/measure_sample_yield.py measure --pilot cells/python --checkout <pinned clone> \
    --image foss-mcp-ingestion:regen-p4b --out-json run.json --out-md run.md
```

`python scripts/release/measure_sample_yield.py pin --pilot cells/python` prints the pin read from the Helm values.
