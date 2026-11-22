# AGENTS.md

## Commands (run these first, in this order)

```bash
# 1. Create and activate the virtual environment
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 2. Install dependencies (dev file adds reportlab for test PDFs, and black)
pip install -r requirements-dev.txt

# 3. Fetch real assets (scope.txt, sample_blueprint.pdf, sample_site_photo.jpg)
python bootstrap_assets.py

# 4. Run the API locally
uvicorn src.main:app --reload --port 8000

# 5. Format / lint
python -m black src tests scripts
python -m black --check src tests scripts

# 6. Run the full test suite (must be 100% green before any commit/deploy)
python -m pytest -v

# 7. Before a RELEASE only, if you ship with Tesseract: its real-engine test
#    (needs the Tesseract binary). RapidOCR's real tests are in the default suite.
python -m pytest -m ocr_engine

# 8. Accuracy benchmark (synthetic ground truth; add real labelled drawings to it)
python -m scripts.benchmark
```

OCR: the default engine is RapidOCR (`rapidocr-onnxruntime`, installed by
`requirements.txt`, models bundled, no system install). Tesseract (a system
binary: `winget install UB-Mannheim.TesseractOCR`, `apt install tesseract-ocr`)
is optional via `OCR_ENGINE=tesseract`. Most tests fix the OCR engine
explicitly with a stand-in; `tests/test_ocr_real.py` runs the real RapidOCR.
Never pass keyword arguments to a RapidOCR call: it keeps them on the
instance and silently changes every later read.

## Project layout

```
src/
  main.py          FastAPI app, routers, /health (reports OCR/vision/DWG availability)
  config.py        Paths and runtime limits (env-overridable, read at call time)
  schemas.py       Pydantic models (TakeoffItem has basis/confidence/evidence)
  findings.py      Finding dataclass + build_items (adds CWICR code/unit)
  extraction.py    Unit-aware text quantity extraction (shared by every pipeline)
  pipelines.py     process_text/photo/pdf, detect_kind, run_takeoff, upload limit
  pdf_scan.py      PDF service: text layer, OCR, scale, geometry, schedules, vision
  cad.py           DXF/DWG takeoff: layers, nested blocks/arrays, units, converter selection
  jobs.py          In-memory background jobs
  measure/         dimensions.py (feet-inches, metric), scale.py (drawing scale)
  geometry/        paths.py (exact Bezier/polygon math), semantics.py, schedules.py
  ocr/             engine.py (selection, Tesseract, injectable), rapid.py (RapidOCR:
                   full page + bands, merge, orientation), normalize.py (spacing
                   repair), preprocess.py (deskew/binarize), rasterize.py
  vision/          claude.py (opt-in Anthropic vision)
  database/        ddc_cwicr.json (rates), layer_rules.json (CAD, LAYER_RULES_FILE), loader.py
examples/          layer_rules.german.example.json, layer_rules.jp-apartment.example.json
  routes/          takeoffs.py, estimates.py, jobs.py
tests/             pytest + httpx.AsyncClient, ground-truth PDFs/DXFs
scripts/           benchmark.py
assets/            Real downloaded reference assets (git-ignored except scope.txt)
bootstrap_assets.py  Fetches the real sample assets over the network
```

## Rules for agents working in this repo

- **Never commit fabricated or placeholder data.** `assets/sample_blueprint.pdf` and
  `assets/sample_site_photo.jpg` must be real files fetched by `bootstrap_assets.py`;
  do not hand-write stub binary files under those names. Do not invent rates:
  add categories to `ddc_cwicr.json` only with real prices.
- **Every takeoff quantity must come from something actually read** (text, OCR,
  schedule, scaled geometry, CAD entities, or an opt-in vision reading) and carry
  `basis`, `confidence` and `evidence`. Never derive a quantity from page size,
  pixel counts or shape counts alone. Heuristics (walls, doors, rooms) need a
  stated scale and must be medium/low confidence.
  See [.agents/skills/blueprint-parsing/SKILL.md](.agents/skills/blueprint-parsing/SKILL.md).
- **Limits are reported, never silent.** Page caps, unread/scanned pages, missing
  OCR, ambiguous scale and failed vision calls go in `metadata.warnings` (and
  `review_reasons` when a person should check the result).
- **Privacy:** the vision pass sends images to Anthropic. It runs only when a
  request sets `use_vision=true`; never make it default or automatic.
- **Every new endpoint or pipeline needs tests that upload a real file** via
  `multipart/form-data`. External engines (OCR, Claude) may be replaced only at
  their own boundary (`ocr.engine.set_engine`, `vision.claude.set_client`).
  Prove new logic can fail: mutate a constant and confirm a test catches it.
- **Do not merge/deploy with a failing or skipped test.** `python -m pytest` must
  exit 0 with all tests passing before running formatting/commit/push steps,
  and, if you ship with Tesseract, `python -m pytest -m ocr_engine` must pass.
- **Measure OCR changes by the end result** (do the right quantities come out of
  `extract_matches`?), never by eyeballing text. Compare strategies with a fresh
  engine per strategy, on pages of several sizes and degradations.
- **DDC-CWICR rates live only in `src/database/ddc_cwicr.json`.** Don't hardcode
  material/labor rates anywhere else.
