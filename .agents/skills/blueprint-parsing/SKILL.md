# SKILL: blueprint-parsing

## Purpose

Rules for any agent that reads, extends, or debugs the multi-modal takeoff
pipelines in `src/routes/takeoffs.py` (text, photo, drawing-pdf).

## Task rules

1. **Read before you infer.** Every pipeline must open and actually parse the
   real input (text stream, decoded image pixel matrix, or pdfplumber page
   objects). Never return a takeoff item that wasn't computed from bytes that
   were actually read from the uploaded/bundled file.

2. **Category vocabulary is fixed and lives in one place.** The only valid
   takeoff categories are the keys of `src/database/ddc_cwicr.json`
   (`walls`, `doors`, `flooring`). If a pipeline needs a new category, add the
   CWICR row to that file first — do not invent a category string in a router
   that has no matching rate entry.

3. **Text extraction is sentence-scoped and unit-aware.**
   `src/extraction.py::extract_matches` splits on sentence boundaries and,
   inside a sentence, pairs each keyword with the nearest number whose unit
   fits the category (walls = length, flooring = area, doors = count).
   Dimensions ("8-inch", 3'-0") and part codes ("08-1100-0045") are never
   quantities. Reuse it from both the text and PDF pipelines rather than
   writing a second scanner, and add a case to `tests/test_extraction.py`
   for any new phrasing you support.

4. **PDF takeoff items come only from text callouts.** Shape counts and page
   size have no reliable meaning (rect count != doors, sheet area != floor
   area), so they must never become takeoff items. Vector lengths/counts and
   embedded-image counts are reported in `metadata` for reference, as
   paper-space at 1:1 (864 PDF points = 1 ft; no drawing scale applied).
   Anything that limits the result (page cap, scanned/image-only pages,
   skipped pages, no matching callout) must appear in `metadata.warnings`,
   never be dropped silently. OCR is not supported.

5. **Heuristic constants must be named, documented, and surfaced in
   `metadata`.** The photo pipeline's pixel-to-square-foot calibration
   (`PHOTO_PIXELS_PER_SQFT`) is a placeholder MVP heuristic, not a measured
   camera calibration. Any pipeline that uses a similar heuristic must:
   - define it as a named module-level constant with a comment explaining it,
   - include a `note` field in the response `metadata` saying so.
   This keeps heuristic estimates from being silently presented as
   ground-truth measurements downstream in the estimate engine.

6. **Large files must not block the event loop.** PDFs are scanned in a
   worker thread with a hard page cap (`MAX_PDF_PAGES`, reported in
   `metadata.warnings` when hit); if you raise or remove that cap, add a
   timing regression test.

7. **Every pipeline change needs a real-asset test.** Add or update a test in
   `tests/test_takeoffs.py` that uploads the actual file under `assets/`
   (or a new real fixture asset committed the same way, via
   `bootstrap_assets.py`) — not a synthetic/mocked payload — through
   `multipart/form-data`.
