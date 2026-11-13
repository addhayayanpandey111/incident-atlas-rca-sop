# Verification record

Verified locally on Windows 11, Python 3.14.6, SQLite WAL. Dependencies were installed into the ignored `.deps/` folder. The standard installation paths remain documented in the README.

## Executed checks

| Check | Observed outcome |
| --- | --- |
| `python scripts/check.py` | 35 regression tests passed, 22/22 reused regression query cases passed |
| `python evaluation/run.py --split development` | 12/12 development cases passed |
| `python scripts/benchmark.py --documents 3000 --queries 30` | Search p95 234.978 ms, two-type suggestions p95 332.754 ms |
| `node scripts/browser_smoke.cjs` using installed headless Edge | Library, ID search, source dialog, cited chat, RCA/SOP switch, review queue and mobile overflow checks passed; no page JavaScript errors |
| `node --check frontend/app.js` | JavaScript syntax accepted |
| `docker compose config --quiet` | Compose configuration accepted |
| `python scripts/smoke_http.py` against the local server | 16 concurrent HTTP imports created one version; edit, stale-write rejection, cited answer and deletion all passed |
| GitHub Actions, Python 3.13 / Ubuntu | Regression suite and Docker Compose/PostgreSQL HTTP lifecycle checks both passed; [verified run](https://github.com/addhayayanpandey111/incident-atlas-rca-sop/actions/runs/43449971220) |

Raw evidence is in `evaluation/results/`. Browser screenshots are `docs/library-desktop.png` and `docs/library-mobile.png`; both were visually inspected. The smoke script uses a real running server at `127.0.0.1:8000`.

The regression suite includes 24 concurrent repeated ingestions, canonical/alias ownership, version and deletion behavior, stale revision rejection, mixed-source splitting, secret redaction, exact missing-ID non-substitution, RCA/SOP isolation, exact ordered SOP preservation, unsupported questions, session separation, topic switches, admin gates, optional-model hallucinated-key rejection, model outage fallback, and folder synchronization.

## Interpretation limits

- The first holdout run was 19/22. Its failed raw outputs are retained in `initial-holdout-*`. Fixes used those failures, so the latest 22/22 is a **regression result**, not fresh unseen accuracy.
- All 34 query cases are author-created synthetic fixtures. There was no separately supplied query dataset in the workspace, no independent annotation, and no real incident corpus.
- Scale latency measures direct service calls over a generated 3,000-document corpus, one client and 30 samples per operation. It excludes user-network latency, concurrent engineer traffic, model latency and container overhead.
- The optional LLM integration was tested with mocked responses and outages. No live API key/provider was used.
- Docker CLI is installed but its Linux engine was unavailable, so the Compose/PostgreSQL stack was **not run locally**. After publishing, the GitHub Actions job successfully built and started the full stack on Ubuntu and passed the real HTTP concurrent-ingestion/lifecycle checks. This closes the initial container-verification gap; it does not establish a production load SLA.
- The built-in in-app browser tool failed to initialize; headless installed Edge through Playwright provided the successful UI verification.
- The installed Starlette test client reports a deprecation warning for its `httpx` adapter. This did not fail tests. A future dependency refresh should move the test harness to the recommended adapter after compatibility verification.

## Explicit cuts

This is a working local prototype with an optional constrained GenAI evidence selector. Trained semantic embeddings, model-based legacy extraction, production identity/ACLs, broad multilingual routing, OCR, full wiki connectors, physical erasure/retention and production load validation are not implemented. See `DESIGN.md` for the rationale and next steps.
