Sealed-candidate commits for the furnished pilots (G2/TC-217).

Each <pilot>.CURRENT file holds exactly one 40-hex commit: the sealed candidate
that repository-presenter's candidates/<repository>/CURRENT names for that
pilot's repository. Source of each value: the pilot's committed furnished page
tests/fixtures/furnished/<pilot>/pages/_index.md, field doc_source_commit.

Mapping (pilot -> repository):
  pdf_net        -> aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET
  pdf_java       -> aspose-pdf-foss/Aspose.PDF-FOSS-for-Java
  pdf_go         -> aspose-pdf-foss/Aspose-PDF-FOSS-for-Go
  slides_python  -> aspose-slides-foss/Aspose.Slides-FOSS-for-Python
  cells_rust     -> aspose-cells-foss/Aspose.Cells-FOSS-for-Rust

These are vendored so tests/infra/test_generate_furnished_page.py always runs,
on any machine, without a repository-presenter checkout. Where a real checkout
is present, the test also asserts these values equal its candidates/<repo>/CURRENT.
Refresh them only from a new sealed candidate, and update the furnished page
in the same change.