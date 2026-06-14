<div align="center">

# PageServe

**Self-hosted, vectorless document RAG — as a REST API, Python SDK, CLI, and MCP server.**

Turn a folder of PDFs into a question-answering API with page-level citations.
No chunking, no embeddings, no vector database — PageServe builds an LLM-generated
*document structure tree* and navigates it to fetch exactly the pages that answer a question.

[![Python](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-e11d48.svg)](LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-e11d48.svg)](#contributing)

[Quick start](#quick-start) · [Configuration](#configuration) · [SDK](#python-sdk) · [REST API](#rest-api) · [MCP](#mcp-server) · [Admin UI](#admin-ui)

</div>

> **Naming.** **PageServe** is the project. `pageserve` (lowercase) is the package,
> CLI command, and Docker service name. PageServe wraps
> [PageIndex OSS](https://github.com/VectifyAI/PageIndex) — the
> [`pageindex_src/`](pageindex_src/) directory is a **verbatim clone and must never be edited**;
> all custom code lives in [`app/`](app/) and wraps it.

---

## Why PageServe

Classic RAG splits documents into chunks, embeds them, and runs a similarity search —
which loses document structure and returns fragments without reliable provenance.
PageServe instead asks an LLM to build a **table-of-contents tree** for each document,
then walks that tree to read the precise pages a question needs.

- **Accurate & traceable** — every answer carries page-level citations (`p.22, 24`).
- **Vectorless** — no embedding model and no vector store to operate.
- **Multi-tenant** — projects isolate documents, API keys, and members.
- **Two auth layers** — JWT for the Admin UI, public/secret key pairs for agents & the SDK.
- **Async indexing** — a RAM-adaptive ARQ queue with retries and live progress over SSE.
- **Batteries included** — REST API, Python SDK (sync + async), CLI, and an MCP server.
- **Self-hosted** — runs entirely on your infrastructure against any OpenAI-compatible LLM.

## Architecture

```
                         ┌──────────────────────────────────────────┐
   Browser  ──/ui──▶     │            PageServe (FastAPI)            │
   Agent/SDK ─/v1─▶      │   /auth   /admin   /v1   /ui   /health    │
                         └───────┬───────────────────────┬──────────┘
                                 │                        │
                        ┌────────▼────────┐      ┌────────▼─────────┐
                        │   PostgreSQL    │      │      Redis       │
                        │ users · docs ·  │      │  cache + ARQ     │
                        │ structure ·     │      │  job queue       │
                        │ pages (JSONB)   │      └────────┬─────────┘
                        └─────────────────┘               │
                                                  ┌────────▼─────────┐
                                                  │   ARQ worker     │
                                                  │ index_document   │
                                                  │ (RAM-adaptive)   │
                                                  └──────────────────┘
```

Full technical docs live in [`docs_internal/`](docs_internal/) (00–12).

## Quick start

PageServe ships as a Docker Compose stack (PostgreSQL + Redis + API + worker).

```bash
# 1. Configure
cp .env.example .env
openssl rand -hex 32        # paste the output into JWT_SECRET in .env

# 2. Launch the whole stack
docker compose up -d

# 3. Open it up
open http://localhost:8000/ui      # Admin UI
open http://localhost:8000/docs    # Swagger / OpenAPI
curl http://localhost:8000/health  # Health check
```

The API container runs database migrations and seeds the default admin
(`ADMIN_EMAIL` / `ADMIN_PASSWORD`) on startup. **Change the admin password right
after your first login.**

From there: create a project → upload a PDF → wait for indexing → create an API key →
query it from the [Playground](#admin-ui), the [SDK](#python-sdk), or [`curl`](#rest-api).

## Configuration

Set these in `.env` (see [`.env.example`](.env.example)):

| Variable | Required | Description |
|---|:---:|---|
| `POSTGRES_PASSWORD` | ✅ | Database password. |
| `LLM_BASE_URL` | ✅ | OpenAI-compatible endpoint (e.g. `http://vllm:8000/v1`). |
| `LLM_MODEL` | ✅ | Model name served by `LLM_BASE_URL`. |
| `ADMIN_EMAIL` | ✅ | Email for the seeded admin account. |
| `ADMIN_PASSWORD` | ✅ | Initial admin password — change it after first login. |
| `JWT_SECRET` | ✅ | Secret for signing access/refresh tokens (`openssl rand -hex 32`). |
| `MAX_FILE_SIZE_MB` | — | Upload limit. Empty → auto-detected from host RAM. |
| `WORKER_MAX_JOBS` | — | Concurrent indexing jobs. Empty → auto-detected. |
| `RATE_LIMIT_PER_MINUTE` | — | Per-key rate limit. `0` disables it. |

## REST API

The agent-facing endpoints live under `/v1` and use key-pair auth. Full reference at
`http://localhost:8000/docs`.

```bash
# Upload a PDF
curl -X POST http://localhost:8000/v1/documents \
  -u "pk_live_xxx:sk_live_xxx" \
  -F "file=@report.pdf"

# Query it (with page-level citations)
curl -X POST http://localhost:8000/v1/query \
  -u "pk_live_xxx:sk_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{"doc_id": "DOC_UUID", "question": "Summarize chapter 2"}'

# Retrieve — get the relevant ORIGINAL passages, with no synthesized answer
curl -X POST http://localhost:8000/v1/retrieve \
  -u "pk_live_xxx:sk_live_xxx" \
  -H "Content-Type: application/json" \
  -d '{"doc_id": "DOC_UUID", "question": "probation terms"}'
```

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/documents` | Upload a PDF (multipart). |
| `GET` | `/v1/documents` | List documents. |
| `GET` | `/v1/documents/{id}/structure` | Document structure tree. |
| `GET` | `/v1/documents/{id}/pages/{pages}` | Raw page contents. |
| `POST` | `/v1/query` | RAG query — synthesized answer with citations. |
| `POST` | `/v1/query/stream` | Answer query as an SSE stream. |
| `POST` | `/v1/retrieve` | Retrieve relevant original passages — no answer synthesis. |

**`/v1/query` vs `/v1/retrieve`** — `query` runs the full agent loop and returns a written
answer plus its sources; `retrieve` runs a single tree-navigation call per document and returns
the **raw page content** of the relevant sections (cheaper, no answer). Use `retrieve` when you
want to feed passages into your own model or show source text directly. Response shape:

```jsonc
{
  "doc_ids": ["..."], "question": "probation terms", "elapsed_ms": 820, "cached": false,
  "results": [
    { "doc_id": "...", "sections": [
      { "title": "Probation", "node_id": "0006", "page_start": 24, "page_end": 25,
        "pages": [ {"page": 24, "content": "..."}, {"page": 25, "content": "..."} ] }
    ]}
  ]
}
```

## Python SDK

```bash
pip install pageserve            # core SDK
pip install "pageserve[all]"     # + MCP server + CLI
```

```python
from pageserve import PageServeClient

client = PageServeClient(
    base_url   = "http://localhost:8000",
    public_key = "pk_live_xxx",
    secret_key = "sk_live_xxx",
)

# Upload and wait for indexing to finish
doc = client.upload("./report.pdf", wait=True)

# Query with page-level citations
res = client.query(doc.doc_id, "What was Q4 revenue?")
print(res.answer)        # the answer
print(res.page_refs)     # [22, 24]
print(res.sources)       # "Annual Report 2024 p.22, 24"
```

An `AsyncPageServeClient` is also available for querying many documents concurrently.

## MCP server

Plug PageServe directly into an agent (Claude Desktop, Cursor, …) over the Model Context Protocol:

```bash
export PAGESERVE_URL=http://localhost:8000
export PAGESERVE_PUBLIC_KEY=pk_live_xxx
export PAGESERVE_SECRET_KEY=sk_live_xxx
pageserve mcp
```

Exposed tools: `list_documents`, `query_document`, `get_page_content`, `get_document_structure`.

## CLI

```bash
export PAGESERVE_URL=http://localhost:8000
export PAGESERVE_PUBLIC_KEY=pk_live_xxx
export PAGESERVE_SECRET_KEY=sk_live_xxx

pageserve list                                    # list documents
pageserve query DOC_UUID "what are the probation terms?"
```

## Admin UI

A multi-page admin console served at `/ui`:

- **Overview** — usage metrics and document status at a glance.
- **Projects** — create projects, upload PDFs, watch indexing progress live.
- **Playground** — run answer/search queries with streamed tool steps and citations.
- **API Keys** — issue and revoke public/secret key pairs per project.
- **Users** & **Audit Log** — account management and a full activity trail (admin only).

Each screen is a standalone, bookmarkable URL. Navigation state is preserved via query
string + `sessionStorage`, the access token lives only in memory (silently refreshed on
load), and a light/dark theme is persisted in `localStorage`. All shared behaviour lives
in [`ui/assets/core.js`](ui/assets/core.js); pages only define their own Alpine component.

## Authentication

| Surface | Credentials | Endpoints |
|---|---|---|
| Admin UI (browser) | email + password → JWT (access 1h, refresh 7d) | `/auth/*`, `/admin/*` |
| Agents / SDK | public key + secret key (`pk_*` / `sk_*`) | `/v1/*` |

Pass key pairs via HTTP Basic (`base64("pk:sk")`) or the `X-PageServe-Public-Key` /
`X-PageServe-Secret-Key` headers. **Scopes:** `read` (list/get/query) and `write`
(upload/delete/reindex/manage keys & webhooks). The public key is safe to expose; if a
secret key leaks, revoke it immediately.

## Local development

```bash
source .venv/bin/activate
pip install -r requirements.txt

# Requires a reachable Postgres + Redis; export the same env vars as .env
export DATABASE_URL=postgresql+asyncpg://pageserve:pass@localhost:5432/pageserve
export REDIS_URL=redis://localhost:6379/0
export LLM_BASE_URL=... LLM_MODEL=... ADMIN_EMAIL=... ADMIN_PASSWORD=... JWT_SECRET=...

uvicorn app.main:app --reload      # API (runs Alembic migrations on boot)
arq worker.WorkerSettings          # indexing worker (separate process)
```

The Python codebase is fully type-annotated and `ruff check` passes clean.

## Project structure

```
app/
  config.py            adaptive settings (RAM-based limits)
  db/                  models.py, session.py
  auth/                jwt.py, api_key.py, password.py, deps.py
  routes/              auth, admin_*, documents, keys, query, stats, webhooks
  services/            pageindex_wrapper, indexer, rag, cache, audit, webhook, seed
worker.py              ARQ worker settings + tasks
migrations/            Alembic migrations
ui/                    multi-page Admin UI (each screen = its own URL)
  assets/core.js       shared shell: theme, auth, API client, sidebar, icons
  assets/styles.css    design system (Tailwind + Inter, red/zinc, dark mode)
pageindex_src/         PageIndex OSS clone — do not edit
docs_internal/         technical deep-dives (00–12)
```

## Dependency notes

- `asyncpg==0.30.0` — 0.29 fails to build on Python 3.13.
- `httpx==0.28.1` — required by `litellm==1.83.7`.
- `bcrypt` is used directly (not via `passlib`, which is unmaintained and breaks on bcrypt ≥ 4.1).

## Contributing

Contributions are welcome! Please:

1. Open an issue to discuss substantial changes before starting.
2. Keep edits inside `app/`, `ui/`, `worker.py`, and `migrations/` — **never** touch `pageindex_src/`.
3. Run `ruff check` and ensure the app boots (`docker compose up`) before opening a PR.
4. Write clear commit messages and describe the change in the PR body.

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.

## Acknowledgements

PageServe is built on top of [PageIndex](https://github.com/VectifyAI/PageIndex) by
[Vectify AI](https://vectify.ai/) — the reasoning-based, vectorless document indexing
engine that makes page-level retrieval possible.
