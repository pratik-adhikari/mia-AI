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

Requirements: Git, Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20.19+,
and npm. Docker is optional.

```bash
git clone --recurse-submodules git@github.com:frankgeorge/mia-dpp.git
cd mia-dpp
make install
make crawl-setup
make dev
```

Open `http://127.0.0.1:3000`. `Ctrl-C` stops both processes started by
`make dev`.

The deterministic `/api/dpp` capability does not need an API key. The interactive
workspace does: copy `.env.example` to `.env.local` and set `OPENROUTER_API_KEY`. Crawl4AI uses
locally installed Chromium to render pages.

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

## Local deployment

```bash
make docker-build
make up
make down
```

The frontend runs on port 3000 and the Python API on port 8000 by default.
Override them with `FRONTEND_PORT`, `BACKEND_PORT`, and `API_URL` when needed.

Local durable data is written under `.mia-data/`. On Vercel, configure PostgreSQL plus Vercel Blob;
MIA intentionally refuses ephemeral production persistence.

Current limits are explicit: generic website ingestion recognizes common
schema.org Product data and labelled specification tables. A generated
Crawl4AI site schema must be reviewed and fixture-tested before it is committed
as trusted extraction data. Automatic PDF fact mapping is not yet implemented,
and Digital Nameplate's external
Address Information drop-in is structurally present but reported as not deeply
validated. No OPC-UA, MQTT, PLC, telemetry, or time-series path is included.
