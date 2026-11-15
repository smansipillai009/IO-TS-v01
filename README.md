# Sky Detective backend (MVP)

FastAPI service between the Next.js frontend and NASA/IRSA. Search SPHEREx observations, read real cutouts,
align them to one shared grid, QA the alignment, and return a manifest of evidence.
It reports **potential moving sources and brightness changes**, never confirmed discoveries.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional; defaults work
uvicorn app.main:app --reload --port 8000     # docs at http://localhost:8000/docs
```
Frontend: `.env.local` needs `NEXT_PUBLIC_USE_MOCK=0` and `BACKEND_URL=http://localhost:8000` (name lookup and spectrum flags are now on).

## DO THIS FIRST: the data spike (Task 3)
Open `notebooks/01-irsa-query.ipynb` and run it top to bottom for Barnard's Star. It follows IRSA's official tutorial
(TAP query on `spherex.artifact` / `spherex.plane`, cutout = `uri?center=RA,DECd&size=DEG`) and adds the one thing the app
depends on that the tutorial steps skip: the **wavelength at the target pixel**. Then confirm the backend's own code agrees:
```bash
python scripts/day1_spike.py "Barnard's Star"
```
The query and cutout URL now come from the tutorial. **Still unverified** (I could not reach IRSA while writing this):

| Assumption | Check in the notebook | Where to fix |
|---|---|---|
| `time_bounds_lower` is MJD | "SANITY" cell | `services/irsa.py` `search()` |
| Wavelength = WCS key `W` on IMAGE, via `pixel_to_world(x, y)` (WCS-WAVE ext.) | "wavelength at the target pixel" cell | `science/fits_io.py` `wavelength_at` |
| Header keys `DETECTOR`, `MJD-AVG` (primary vs IMAGE header) | "Header metadata" cell | `science/fits_io.py` `open_cutout` |
| Pixel scale about 6.15"/px; image unit (BUNIT) | WCS + image cells | `config.py`, `science/spectrum.py` |
| `FLAGS` non-zero = bad (all bits masked) | "Quality" cell prints the bits actually set | `.env` `FLAG_MASK_BITS` |

A frame whose wavelength cannot be read is **skipped and listed** in `processing.skipped`; it is never given a guessed value.

## Working offline / on a laptop (when IRSA is flaky)
Colab shares IP addresses, which IRSA can throttle; a laptop usually gets through. Record real cutouts once, then run with no IRSA at all:
```bash
# 1. the notebook's query cell saves data/reports/irsa_observations.csv
python scripts/download_cutouts.py --ra 269.452 --dec 4.693 --n 12      # re-run until all files are present
# 2. build an offline archive from the real files
python scripts/build_fixture.py --fits data/raw --out data/fixtures
# 3. serve it (PowerShell: $env:FIXTURE_DIR="data/fixtures"; uvicorn app.main:app --port 8000)
FIXTURE_DIR=data/fixtures uvicorn app.main:app --port 8000
```
Search the cutout centre with radius <= half the cutout width (0.1 deg cutouts -> `radius=0.05`). The data is real SPHEREx data;
only the archive lookup is skipped. Cutouts can also be downloaded one at a time with `curl`/the browser and dropped into `data/raw`.

## What the frontend calls (the investigation contract)
`INVESTIGATION_MODE=live` (default) runs the real pipeline for the coordinates the user sent. `static` serves only the stored demo field.

| Call | Behaviour |
|---|---|
| `POST /api/investigations` `{ra_deg, dec_deg, fov_deg, max_frames}` | `fov_deg` is the FULL width of the square (radius = fov/2). Returns `{investigation_id, status, coordinates_used}`. Idempotent: same inputs + release + pipeline version = same id |
| `GET /api/investigations/{id}` | Poll. `status`: `queued` -> `finding_observations` -> `downloading` -> `processing` -> `complete` or `failed` (`error`, `error_code`: `no_observations`, `download_failed`, `invalid_request`, `processing_failed`). Complete = `frames`, `difference_frames`, `warnings`, `grid`, `groups`, `processing`, `data_mode` |
| `GET /products/{id}/{name}.png[?stretch=linear\|asinh\|zscale]` | Whitelisted PNG serving (default stretch asinh) |
| `GET /api/spectrum`, `/api/catalog/{gaia\|wise\|simbad}`, `/api/resolve` | Optional features; `request_id` = the investigation id |

`data_mode` is `development_fixture` only for the synthetic test archive; real or recorded SPHEREx files are `archive_data`.
Difference images are always `validation_status: "not_validated"`: nothing here validates them scientifically.

### Which frames are shown (important)
A SPHEREx exposure has a different wavelength at the same sky position each time, so the oldest N frames are N different
wavelengths and cannot be subtracted. The backend therefore downloads a pool spread across the whole time range
(`MAX_POOL`, default 40), reads each frame's wavelength at the target pixel, and shows `max_frames` of them: the wavelength
groups with the most epochs first, spread across time. `processing.selection` and a warning say how many readable
observations were not shown. The first request for a new target downloads up to 40 cutouts, so run `precache_demo.py` /
`save-demo.mjs` before the demo.

## Pipeline API (older contract, still served: used by tests and `precache_demo.py`)
| Endpoint | Notes |
|---|---|
| `GET /api/resolve?name=` | `{name, ra, dec}`. Demo targets work offline; others via CDS Sesame |
| `GET /api/search?ra&dec&radius[&start&end&detector]` | Validates, queries IRSA (cached 6 h), starts the job, returns `{request_id, center, radius_deg, ...}`. **Idempotent**: same inputs + release + pipeline version = same `request_id` |
| `GET /api/manifest/{id}?stretch=asinh\|linear\|zscale` | `pending/processing` (+`stage`,`progress`) / `complete` / `failed` (+`error`). Complete = `grid`, `groups`, `frames`, `difference_frames`, `processing` |
| `GET /api/products/{id}/{stretch}/{name}.png` | Whitelisted PNG serving only |
| `POST /api/jobs/combine` | `{request_id, op: add\|subtract\|median, frames[]}`; refuses frames from different groups |
| `GET /api/spectrum?request_id&ra&dec&aperture_arcsec` | One point per cutout, each with its own wavelength and time |
| `GET /api/catalog/{gaia\|wise\|simbad}?ra&dec&radius` | `{sources:[{id,ra,dec,label}]}` |
| `GET /api/health`, `GET /api/releases` | Health; which release/pipeline version the cache is keyed on |

PNG contract: north up, east left, FITS origin bottom-left (`processing.png_orientation`). Difference PNG: red = brighter in the later frame, blue = fainter.

## Your MVP checklist, and where each item lives
| Item | Where |
|---|---|
| Validate RA and Dec | `validation.py` |
| Limit radius / number of frames | `validation.py`, `services/irsa.py` `select_observations` (also a transfer-size budget) |
| Query IRSA, return observation metadata | `services/irsa.py`, `/api/search` |
| Download cutout | `IrsaArchive.fetch_cutout` (retry, SSRF guard, cached on disk) |
| Save processed image | `science/pipeline.py` + `science/render.py` |
| Return manifest | `/api/manifest/{id}` |
| Handle errors | `errors.py` + handlers in `main.py`: readable `detail` string for every failure |

## Design decisions (from the plan review)
- **Wavelength is per pixel, not per observation.** Frames are grouped by detector **and** wavelength at the target pixel (`science/grouping.py`, mirrors `groups.ts`). Only same-group frames are ever differenced or combined.
- **Release-aware.** `DATA_RELEASE` and `PIPELINE_VERSION` are inside the request id and every cache key. A new IRSA release = change one env var; nothing stale is reused, nothing else changes.
- **One process.** SQLite + disk + a thread pool. Postgres/Redis/Celery are not needed for the MVP; `JobRunner.submit` is the one seam to replace.
- **Failure-tolerant.** Archive down -> stale cached search is served; failed frames are skipped with reasons; interrupted jobs are marked failed on restart.
- **Honest QA.** Alignment status comes from a measured residual (good <= 0.5 px, degraded <= 1.5, else failed; `unknown` when there is too little structure). Failed frames are never differenced. `unknown` is never shown as good.

## Tests
```bash
SKYDETECTIVE_NO_AUTOAPP=1 pytest -q
```
`test_core.py`, `test_irsa_select.py`: pure logic. `test_api.py`: full HTTP flow on synthetic FITS (`tests/make_fixture.py`: fixed stars, one mover, 3 epochs at 1.28 um plus one at 2.0 um that must land in a separate group).

## Deploy / demo safety
- `docker build -t sky-api . && docker run -p 8000:8000 -v $PWD/data:/data sky-api`
- Before the demo: `python scripts/precache_demo.py` then, in the frontend, `node scripts/save-demo.mjs http://localhost:8000`.
- If IRSA is down on stage: `FIXTURE_DIR=...` serves a recorded archive from disk.

## Deliberately NOT in the MVP
Per-detector stacking (frontend hides `stack` mode), track linking, Celery/Postgres/S3, AI features, FITS export,
per-star alignment (Gaia centroids), flag-bit decoding, photometric scaling before differencing, within-pass blink grouping.
