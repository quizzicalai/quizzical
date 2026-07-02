# quafel (quizzical)

quafel is "The Personality Quiz for Everything": type any topic — Myers-Briggs,
Hogwarts Houses, Famous Elephants — and an AI agent generates a personality
quiz for it on the spot, interviews you with adaptive questions, and writes a
shareable profile of who you are within that topic.

Live at [quafel.com](https://quafel.com). The brand name is always lowercase
("quafel") in user-facing copy.

## How it works

1. `POST /quiz/start` — resolves the topic. Popular topics are served
   instantly from **pre-computed topic packs** (Postgres-backed, Redis-cached);
   everything else goes to a live LangGraph agent that writes a synopsis and a
   cast of character archetypes.
2. `POST /quiz/proceed` / `POST /quiz/next` — baseline questions, then
   adaptive questions generated per-answer in a background agent run.
3. `GET /quiz/status/{id}` — the FE polls for the next unseen question or the
   final profile. Live quiz state sits in Redis with durable snapshots in
   Postgres, so an expired cache rehydrates instead of dead-ending.
4. Character/answer art is generated via FAL and served from Azure Blob.

## Stack

| Layer     | Tech                                                                    |
|-----------|-------------------------------------------------------------------------|
| Frontend  | React 18 + TypeScript + Vite + Tailwind + Zustand (`frontend/`)         |
| Backend   | FastAPI + LangGraph agent + SQLAlchemy (async) (`backend/app/`)         |
| Data      | PostgreSQL (durable state, topic packs) + Redis (live state, caches)    |
| Images    | FAL (generation) + Azure Blob (hosting)                                 |
| Deploy    | Azure Container Apps (API), Azure Static Web Apps (FE), Cloudflare edge |
| Infra     | Bicep templates in `infrastructure/`                                    |

## Run locally

Docker (everything: FE on :3000, API on :8000, Postgres, Redis):

```bash
docker compose up --build
```

Or run the pieces directly:

```bash
# Backend (Python 3.12; deps are managed with Poetry / pyproject.toml)
cd backend
poetry install --with dev        # or: pip install -e . + the dev dependency-group
uvicorn app.main:app --reload    # http://localhost:8000, needs Postgres+Redis up

# Frontend
cd frontend
npm install
npm run dev                      # http://localhost:5173, proxies /api to :8000
```

Configuration comes from `backend/appconfig.local.yaml` plus environment
variables / `.env` (secrets: LLM + FAL keys, Turnstile, DB/Redis URLs).
Production reads the same settings from Azure Key Vault. With
`APP_ENVIRONMENT=local` and no keys set, Turnstile is bypassed and paid
integrations stay inert.

## Tests

```bash
# Backend (from backend/; local env keeps the fail-closed prod gates off)
APP_ENVIRONMENT=local LOG_TO_FILE=false pytest tests/unit tests/security -q
APP_ENVIRONMENT=local LOG_TO_FILE=false pytest tests/integration -q

# Frontend unit tests (Vitest)
cd frontend && npm run test:run

# Frontend component tests (Playwright CT; collects src/** and tests/ct/**)
cd frontend && npm run test-ct -- --project=chromium

# Agent-quality evals (offline dry-run is free + deterministic)
cd evals && python -m quizzical_evals.cli run --dry-run --reps 8
```

Lint: `ruff check app tests` (backend), `npm run lint` (frontend).

## Deploy

GitHub Actions in `.github/workflows/`:

- `api-deploy.yml` — builds the backend image, verifies Key Vault config,
  Trivy-scans, deploys to Azure Container Apps, live-smokes, auto-rolls-back.
- `azure-static-web-apps-*.yml` — FE build + deploy to Static Web Apps
  (previews per PR; note the Free-tier cap of 3 staging environments).
- `seed-prod-packs.yml` / `nightly-promote.yml` — signed starter-pack imports
  and the nightly user-quiz → topic-pack promotion pipeline.
- `prod-smoke.yml` — scheduled black-box checks against production.

## Repository map

- `backend/` — FastAPI app (`app/`), tests, DB init SQL (`db/init/`), and
  operational scripts (`scripts/`: pack building, image backfills, smokes).
- `frontend/` — React app, Vitest specs alongside sources, Playwright CT
  specs in `tests/ct/`, e2e in `tests/e2e/`.
- `evals/` — statistical evaluation harness for the agent's per-function LLM
  calls (see `evals/methodology.md`).
- `infrastructure/` — Bicep modules for the Azure footprint.
- `specifications/` — design docs (`backend-design.MD`, `frontend-design.MD`,
  `azure-infrastructure.md`), audits (`specifications/audit/`), and plans.

## Key documents

- Backend design: `specifications/backend-design.MD`
- Frontend design: `specifications/frontend-design.MD`
- Azure infrastructure: `specifications/azure-infrastructure.md`
- Launch audits & punch lists: `specifications/audit/`
- Eval methodology: `evals/methodology.md`
