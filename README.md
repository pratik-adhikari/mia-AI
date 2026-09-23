# MIA — deterministic product data to AAS

MIA turns traceable product evidence into a validated Asset Administration
Shell (AAS) environment. The backend is Python. AI can propose unfamiliar
semantic mappings, but Python owns extraction rules, mapping acceptance,
template resolution, AAS compilation, validation, and deployment gates.

The current end-to-end path targets IDTA Digital Nameplate 3.0.1. The same
template loader and compiler are also tested against nested elements from IDTA
Technical Data 2.0.1, so the backend is not a hand-written one-template JSON
generator.

## Run locally

For hobby/research development, the default deployment is now the local stack rather than Vercel.
Long Crawl4AI and agent runs execute in a persistent worker process and are not constrained by a
serverless function duration.

### Recommended: one-command Docker stack

Requirements: Git and Docker with Compose.

```bash
git clone --recurse-submodules git@github.com:pratik-adhikari/mia-AI.git
cd mia-AI
cp .env.example .env.local
```

Set at least `OPENROUTER_API_KEY` in `.env.local`. Local testing uses the shared local account by
default; set `auth.enabled` to `true` in the root `config.json` to test Clerk authentication.
Authentication can only be disabled in local mode. Restart the stack after changing the file;
image rebuilds are not required.

```json
{
  "auth": {
    "enabled": false
  }
}
```

Apply a change with `docker compose restart frontend backend` (or restart the native dev processes).
With local authentication disabled, `/login` and `/signup` redirect to the workspace.

Then start everything:

```bash
make up
```

This starts:

```text
frontend   http://127.0.0.1:3000
backend    http://127.0.0.1:8000
worker     persistent resumable Crawl4AI/research worker
storage    Docker volume using the local filesystem adapter
```

The local Compose stack uses host networking so the browser-based crawler shares the host's
working outbound route. Backend and frontend listeners bind to localhost in Compose; their ports
remain configurable through `BACKEND_PORT` and `FRONTEND_PORT`.

Useful commands:

```bash
make logs     # follow frontend + backend + worker
make studio   # run the backend workflow in LangGraph Studio for debugging
make down     # stop services; durable Docker volume is kept
```

`make studio` starts the local LangGraph Agent Server used by FastAPI graph runs and prints the
LangGraph Studio URL. Backend graph calls go through the official LangGraph SDK, so Studio shows
the same live executions rather than a separate copy of the workflow. The Agent Server uses the
same local MIA data volume for catalogue and artifacts, and its graph checkpoint store has a
separate Docker volume. New local threads use Agent Server checkpoints; threads created before
this integration continue on their existing SQLite checkpoints. The Studio browser interface is
hosted by LangSmith and connects to the local server. Opening Studio does not start a workflow;
submitting a product request can call the configured model and external product sites.
The local Agent Server runs with LangGraph's `--allow-blocking` development option because MIA's
SQLite catalogue and artifact storage perform synchronous filesystem operations. This option is
limited to the local Agent Server image; it is not part of the production backend image.

The local Compose frontend also has a **Debug** control in the workspace header. It opens a
side panel that renders the compiled Agent Server topology and highlights node task events from
the selected conversation. With no conversation selected, it still shows the graph topology;
after a conversation is selected, it shows that local process's live or recent run state. This
control is enabled only in the local Compose build and is absent from the Vercel frontend.

When `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` is set in `.env.local`, the local backend,
worker, and Agent Server send traces to LangSmith. Without those settings, graph state remains
available through local Agent Server/Studio without LangSmith run tracing.

The current Clerk SDK constraint requires `cryptography<49`, while LangGraph API 0.14 requires
`cryptography>=50`; the local Agent Server therefore resolves API 0.13.3, which currently logs a
critical-support warning. Revisit this pin when the Clerk dependency can move to a compatible
cryptography range.

The overview groups the workflow into `evidence_and_coverage` and `aas_output` subgraphs. Open a
subgraph in Studio to inspect its node-level flow, including mapping review and research loops.

To run the workflow from Studio, provide `thread_id`, `user_id`, and `user_message` in the graph
input; for direct URL import, put the product page URL in `user_message`. The workflow can then
crawl the site and call the configured model, which may incur provider costs.

The worker polls durable background jobs and advances one bounded crawl batch at a time. Each batch
checkpoints the frontier, cursor, evidence artifact, mapping progress, and timing events, so a
restart resumes the same job instead of repeating completed source pages.

### Native development without Docker

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20.19+, npm, and Chromium.

```bash
make install
make crawl-setup
make dev
```

Open `http://127.0.0.1:3000`. `Ctrl-C` stops the frontend, backend, and local worker.

The deterministic `/api/dpp` capability does not need an API key. The interactive workspace does:
copy `.env.example` to `.env.local` and set `OPENROUTER_API_KEY`. Crawl4AI uses locally
installed Chromium to render pages.

The workspace is orchestrated by LangGraph. Product discovery, semantic mapping, and gap-driven
source research use focused PydanticAI agents; extraction, official-template resolution, mapping
acceptance, coverage, AAS compilation, validation, and deployment gates remain trusted Python.
Human review/value collection uses native graph interrupts.

```text
conversation -> product discovery -> extract -> deterministic map -> semantic map -> review
                                                         |                         |
                                                         +---- durable state ------+
                                                                    |
                                                          coverage -> research/build
```

Every attempt is persisted. A canonical product URL identifies a durable product record with
timestamped chat history, run events, artifacts, and versioned successful DPPs. Repeating a known
product URL reuses the latest successful DPP unless a refresh is requested. The `/products` UI
shows the accumulated catalogue.

## Production storage model

The current deployment target deliberately separates structured application state from file
storage. BaSyx publication is **not** part of the current milestone; the next publication artifact
to complete is a validated `.aasx` package.

### Supabase PostgreSQL — structured application state

Supabase is the durable database for data that MIA needs to query, relate, and restore:

- user-owned threads and chat messages
- products and processing runs
- background jobs and run events
- human review / mapping state
- LangGraph checkpoints
- DPP/AASX version records
- artifact metadata such as filename, content type, hash, size, product/run ownership, and storage URI

The production backend reads the database connection from `MIA_DATABASE_URL` (or
`DATABASE_URL`). The catalogue creates its schema and packaged migrations automatically, and
LangGraph creates its PostgreSQL checkpoint tables through its own setup path.

### Vercel Blob — durable artifact bytes

Vercel Blob stores the actual files generated or collected by MIA. These are not assumed to be
temporary; final artifacts can remain here as the durable file archive.

Typical Blob objects include:

- crawled HTML / Markdown
- downloaded source PDFs and product images
- extraction and evidence JSON
- mapping / coverage / validation reports
- generated AAS JSON
- generated `.aasx` packages
- later, generated DPP PDFs or other export files

Blob objects are private application artifacts by default. Supabase stores the metadata and the
relationship to the owning user, thread, product, and run; Blob stores the bytes.

```text
Supabase PostgreSQL                     Vercel Blob
-------------------                     -----------
thread                                  raw HTML
chat messages                           source PDFs
product                                 product images
run                                     evidence JSON
background job                          validation reports
artifact metadata  ------------------>  generated AAS JSON
DPP/AASX version                        generated .aasx
LangGraph checkpoint
```

On Vercel, connect one Blob store to the project. The runtime accepts either
`BLOB_STORE_ID` (OIDC/default client authentication) or the legacy
`BLOB_READ_WRITE_TOKEN`.

### Current milestone — AASX first

The immediate target is:

```text
product evidence
  -> mapping and human review
  -> official IDTA template resolution
  -> aas-core3.0 Environment
  -> validation
  -> package valid .aasx
  -> store .aasx in Vercel Blob
  -> register the version + artifact metadata in Supabase
  -> make the .aasx downloadable from the authenticated MIA workspace
```

BaSyx server deployment, public DPP hosting, and QR-code publication come only after this AASX
generation and persistence path is working reliably.

Run `make help` to see the short command list. The most useful checks are:

```bash
make test       # deterministic Python tests
make check      # standards, lint, types, tests, frontend build, Compose config
make smoke      # build, start, probe, and stop the Docker stack
```

## Deterministic path

```text
source data
  -> EvidenceRecord with provenance
  -> proposed mapping plus an explainable basis and review policy
  -> human-approved MappingSpecification
  -> pinned official IDTA template
  -> aas-core3.0 Environment
  -> AAS metamodel and IDTA template validation
  -> deployable artifact or explicit GapReport
```

Mappings state whether their basis is an exact deterministic rule, semantic
reasoning, or trusted human input. Ambiguity and weak signals require review;
MIA does not present handcrafted scores as statistical confidence.

## Repository map

```text
app/, components/                  Next.js workspace + durable product library
backend/src/mia_dpp/mia.py        small application/composition façade
backend/src/mia_dpp/workflow/     LangGraph state, routing, nodes, HITL
backend/src/mia_dpp/agents/       focused discovery, research, semantic agents
backend/src/mia_dpp/persistence/  products, runs, chat, events, DPP versions
backend/src/mia_dpp/storage/      filesystem / Vercel Blob artifact adapters
backend/src/mia_dpp/runtime/      SQLite/Postgres checkpoint composition
backend/src/mia_dpp/tools/web/    provenance-aware generic evidence extraction
backend/src/mia_dpp/tools/mapping/ mapping, coverage, review
backend/src/mia_dpp/aas/          official templates, compiler, validator
backend/src/mia_dpp/domain/       framework-neutral Pydantic concepts
backend/src/mia_dpp/integrations/ vendor-specific adapters
backend/tests/                     deterministic + workflow/persistence tests
standards/idta-submodel-templates/ unmodified, commit-pinned standards data
```

MIA does not copy upstream application source into its own package. `aas-core` and Crawl4AI stay
behind small MIA boundaries. LangGraph owns workflow ordering/checkpoints; PydanticAI is used only
inside reasoning-heavy nodes. See `docs/architecture.md` for the complete responsibility map and
`docs/deterministic-backend.md` for validation layers.

## Local deployment architecture

```text
Browser
  |
  +--> Next.js frontend :3000
  |       |
  |       +--> authenticated agent BFF
  |                |
  |                v
  +------------> FastAPI :8000
                   |
                   +--> LangGraph
                   +--> Crawl4AI seed extraction
                   +--> local artifact store
                   +--> SQLite by default, or PostgreSQL/Supabase when configured
                   |
                   v
              durable jobs
                   |
                   v
            persistent worker
                   |
                   +--> bounded parallel crawl batches
                   +--> incremental evidence
                   +--> incremental semantic mapping
                   +--> timing/run events
```

The frontend runs on port 3000 and the Python API on port 8000 by default. Override them with
`FRONTEND_PORT` and `BACKEND_PORT` when needed. Docker Compose stores local application data in
the named `mia-data` volume. Native `make dev` uses the normal `.mia-data/` paths.

Vercel-specific storage/workflow adapters remain in the codebase for a future hosted deployment,
but local development does not require Vercel Blob or Vercel Workflow.

Current limits are explicit: generic website ingestion recognizes common
schema.org Product data and labelled specification tables. A generated
Crawl4AI site schema must be reviewed and fixture-tested before it is committed
as trusted extraction data. Automatic PDF fact mapping is not yet implemented,
and Digital Nameplate's external
Address Information drop-in is structurally present but reported as not deeply
validated. No OPC-UA, MQTT, PLC, telemetry, or time-series path is included.
