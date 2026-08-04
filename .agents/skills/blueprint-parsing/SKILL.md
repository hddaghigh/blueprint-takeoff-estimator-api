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

3. **Text extraction is sentence-scoped regex, not whole-document regex.**
   `src/extraction.py::extract_quantities_from_text` splits on sentence
   boundaries and takes the first number in the same sentence as a category
   keyword. This avoids accidentally pairing a wall count with a door's
   dimension. Reuse this function from both the text pipeline and the PDF
   text fallback rather than writing a second regex scanner.

4. **PDF pipeline priority order: text first, vector geometry second.**
   `takeoff_from_pdf` must attempt the keyword+number sentence match across
   all scanned pages before falling back to vector-geometry heuristics
   (summed line length for walls, rect count for doors, page area for
   flooring). Never skip straight to the vector fallback if real text
   quantities are present — text-embedded dimensions are more reliable than
   geometric heuristics.

5. **Heuristic constants must be named, documented, and surfaced in
   `metadata`.** The photo pipeline's pixel-to-square-foot calibration
   (`PHOTO_PIXELS_PER_SQFT`) is a placeholder MVP heuristic, not a measured
   camera calibration. Any pipeline that uses a similar heuristic must:
   - define it as a named module-level constant with a comment explaining it,
   - include a `note` field in the response `metadata` saying so.
   This keeps heuristic estimates from being silently presented as
   ground-truth measurements downstream in the estimate engine.

6. **Large files must not block the event loop.** PDFs are scanned with a
   hard page cap (`MAX_PDF_PAGES`) to keep the endpoint responsive; if you
   raise or remove that cap, add a timing regression test.

7. **Every pipeline change needs a real-asset test.** Add or update a test in
   `tests/test_takeoffs.py` that uploads the actual file under `assets/`
   (or a new real fixture asset committed the same way, via
   `bootstrap_assets.py`) — not a synthetic/mocked payload — through
   `multipart/form-data`.
