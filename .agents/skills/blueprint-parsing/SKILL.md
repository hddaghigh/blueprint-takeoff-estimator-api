# SKILL: blueprint-parsing

## Purpose

Rules for any agent that reads, extends, or debugs the takeoff pipelines
(`src/pipelines.py`, `src/pdf_scan.py`, `src/cad.py`, and the routes in
`src/routes/`) for text, photos, PDF drawings and DXF/DWG drawings.

## Task rules

1. **Read before you infer.** Every item must come from something actually read
   from the input: text (layer or OCR), a schedule table, scaled geometry, CAD
   entities, or an opt-in vision reading. Never return a quantity from page size,
   pixel counts or shape counts alone.

2. **Provenance on every item.** Each `Finding` carries `basis`
   (`text|schedule|geometry|cad|vision`), `confidence` (`high|medium|low`) and
   `evidence`. Defaults: text layer/schedule/CAD = high; OCR text = medium (low
   when mean OCR confidence < 75); scaled wall pairing = medium; door-swing and
   room-area geometry = low; vision = low. Anything not `high` is listed in
   `review_reasons`, and the estimate endpoint flags it again.

3. **Category vocabulary is fixed and lives in one place.** The valid categories
   are the keys of `src/database/ddc_cwicr.json` (`walls`, `doors`, `flooring`).
   Add the CWICR row (with a real price) before using a new category anywhere,
   including the vision schema (built from those keys) and CAD layer rules.

4. **Text extraction is sentence-scoped and unit-aware.**
   `src/extraction.py::extract_matches` pairs each keyword with the nearest
   number whose unit fits the category (walls = length, flooring = area,
   doors = count) and converts feet-inches/metric via `src/measure/dimensions.py`.
   Dimensions ("8-inch", 3'-0") and part codes ("08-1100-0045") are never
   quantities. Reuse it everywhere; add a case to `tests/test_extraction.py`
   for any new phrasing. Line breaks are re-flowed by `_join_lines`: a new
   capitalised line is a new statement, and scale notes are blanked out so the
   `1'-0"` in `SCALE 1/4" = 1'-0"` is never read as a wall.
   OCR text is first repaired by `src/ocr/normalize.py::unglue` (missing spaces,
   e.g. `4doors and120linear`); it splits only runs made entirely of
   construction vocabulary, so part codes and dimensions are untouched. OCR
   merges overlapping detections keeping the LONGEST text, because engines
   sometimes clip a line's tail.

5. **Priority when sources disagree:** door schedule > text callout (text layer,
   then OCR) > geometry > vision for PDFs; CAD geometry > drawing notes for CAD.
   A disagreement is a warning plus a review reason, never a silent override.

6. **Scale is explicit or absent.** Geometry becomes real-world length/area only
   with exactly one scale note on the same page (`src/measure/scale.py`).
   Several scales, "not to scale", or none means paper-space only (864 PDF points
   = 1 ft) and no geometry items. Never borrow a scale from another page.

7. **Limits are reported, never silent.** Page cap, unread/scanned pages, no OCR
   engine, OCR failures, low OCR confidence, skipped pages, ambiguous scale and
   failed vision calls all go in `metadata.warnings`. OCR and vision must never
   raise: a missing or failing engine degrades to a warning.

8. **Vision is opt-in and private by construction.** It runs only when the request
   sets `use_vision=true` (it sends the image to Anthropic). Use structured
   outputs (`output_config.format`), never forced `tool_choice` (rejected by
   claude-sonnet-5-5), keep refusal fallbacks on, and only fill categories no
   other source found.

9. **Heavy work stays off the event loop.** PDF/photo/CAD processing runs in a
   worker thread; long documents go through `/api/v1/jobs/takeoffs`. Caps live in
   `src/config.py` and are read at call time so tests can patch them.

10. **Every change needs a real-file test that can fail.** Upload generated
    ground-truth PDFs (reportlab) or DXFs (ezdxf), or the real assets, via
    `multipart/form-data`. Replace OCR/Claude only at their own boundary
    (`ocr.engine.set_engine`, `vision.claude.set_client`). After adding logic,
    mutate one constant and confirm a test fails. Run `python -m scripts.benchmark`.

11. **CAD (`src/cad.py`).** expand nested blocks with their scale, count block
    arrays per cell, treat a door block as one unit (never expand it), let layer-0
    entities inherit the INSERT's layer, skip frozen/off/invisible entities, and
    report (never silently drop) xrefs, MLINE, paper-space viewports, depth/insert
    caps. A multi-floor / multi-scale drawing must be read one top-level block at a
    time (`include_blocks`), never summed. Room schedules (ROOM-block attributes)
    are deduplicated by room id; schedule areas are `schedule`/`medium` and must
    carry the warning that every scheduled room is included. Never infer what a
    layer means from its name alone: render or measure it (line lengths, pairing,
    extents) first. DWG is converted by a child process with a timeout; never import
    LibreDWG (GPLv3).
