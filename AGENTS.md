# AGENTS.md

## Commands (run these first, in this order)

```bash
# 1. Create and activate the virtual environment
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Fetch real assets (scope.txt, sample_blueprint.pdf, sample_site_photo.jpg)
python bootstrap_assets.py

# 4. Run the API locally
uvicorn src.main:app --reload --port 8000

# 5. Lint
python -m py_compile src/**/*.py   # or: pip install ruff && ruff check .

# 6. Run the full test suite (must be 100% green before any commit/deploy)
python -m pytest -v
```

## Project layout

```
src/
  main.py          FastAPI app, router registration, /health
  config.py        Filesystem paths (assets dir, database path)
  schemas.py       Pydantic request/response models
  extraction.py     Shared regex quantity extraction (text + PDF pipelines)
  database/
    ddc_cwicr.json  Local DDC-CWICR rate table (material/labor rates per category)
    loader.py       Cached JSON loader
  routes/
    takeoffs.py     Pipelines A (text), B (photo), C (drawing-pdf)
    estimates.py    Pipeline D (cost estimation sync)
tests/              pytest + httpx.AsyncClient integration tests
assets/             Real downloaded reference assets (git-ignored except scope.txt)
bootstrap_assets.py Isolated setup script that fetches real assets over the network
```

## Rules for agents working in this repo

- **Never commit fabricated or placeholder data.** `assets/sample_blueprint.pdf` and
  `assets/sample_site_photo.jpg` must be real files fetched by `bootstrap_assets.py`;
  do not hand-write stub binary files under those names.
- **All takeoff quantities must be derived from real extracted data** (regex matches
  on real text, real Pillow pixel data, or real pdfplumber vector/text geometry).
  Do not introduce `random.random()`-style fabricated numbers into a response.
  Documented calibration heuristics (e.g. the photo pipeline's
  `PHOTO_PIXELS_PER_SQFT` constant) are acceptable as long as they are clearly
  labeled in code and in the response `metadata`.
  See [.agents/skills/blueprint-parsing/SKILL.md](.agents/skills/blueprint-parsing/SKILL.md)
  for the specific rules governing the multi-modal parsing pipelines.
- **Every new endpoint or pipeline needs a corresponding pytest test** that uploads
  a real file via `multipart/form-data` (not a mock) before it is considered done.
- **Do not merge/deploy with a failing or skipped test.** `python -m pytest` must
  exit 0 with all tests passing before running formatting/commit/push steps.
- **DDC-CWICR rates live only in `src/database/ddc_cwicr.json`.** Don't hardcode
  material/labor rates anywhere else; add new categories there and they'll flow
  through both the takeoff and estimate endpoints automatically.
