# Venture Twin Agent

A persistent venture-building agent for the **Google All Things Agentic Hackathon — Collaborative Partner track**.

> **Before you put money into a business, do the homework. Then keep doing it as reality changes.**

Venture Twin turns an idea into a structured, evidence-backed venture state rather than a one-off business-plan document. It asks for the founder's real constraints, decomposes the business into assumptions, searches for evidence that can kill those assumptions, models the economics, blocks irreversible steps while critical facts are weak, and turns surviving ventures into an execution roadmap covering registration, tax, licensing, suppliers, service providers and launch dependencies.

The same venture state persists after the first decision. If rent changes, a competitor appears, a supplier quote arrives, or operating data replaces an estimate, the agent mutates the relevant assumption and re-runs the downstream model. A background monitor re-checks evidence on a schedule (weekly by default) and automatically queues targeted re-research when it goes stale, so the venture stays current without the founder having to remember to ask.

Venture Twin is jurisdiction-agnostic, not Kenya-specific: currency, country, subdivision and locale are explicit intake fields that flow through every specialist's mandate, the source-authority gate and the financial model's units. The canonical demo below uses Kenya; the system has also been run live end-to-end against a US venture (Austin, Texas) with Gemini/OpenRouter + Tavily grounded search.

## What is implemented

- Structured founder intake: capital, protected reserve, income target, location, country, currency, loss tolerance, launch horizon, time commitment and experience — collected progressively, not as one giant form.
- Persistent **assumption ledger** with confidence, impact weight, dependency invalidation and evidence provenance.
- Adversarial research interface with two modes:
  - `offline`: deterministic demo fixtures clearly labelled as non-factual demo data.
  - `live`: 5 specialist roles (finance, market, regulatory, execution, adversary) run against one canonical venture twin, using either Gemini with Google Search grounding or OpenRouter + Tavily retrieval.
- A deterministic **evidence ledger** that admits, rejects or flags-as-contradictory model-proposed evidence — regulatory claims require a recognized official-authority source URL across both "gov" and "go.&lt;country-code&gt;" domain conventions, and unsourced or ungrounded high-confidence claims are downgraded rather than trusted.
- Reproducible Monte Carlo cash/runway stress test. It reports the probability of satisfying the explicit modeled conditions, **not a fake universal "business success probability"**.
- Decision engine: `needs_data`, `reject`, `conditional`, `approve`.
- Capital-commitment gates that remain locked while critical assumptions are weak.
- Execution roadmap with official-source requirements for registration/tax/licensing research.
- Material-change events that mutate assumptions and re-underwrite the venture.
- Decision history: the system remembers why a prior configuration was approved, conditional or rejected.
- **Sandbox experiments**: thousands of Monte Carlo runs against a disposable, deep-copied scenario (rent shocks, demand shocks, competitor entry) that can never contaminate canonical evidence.
- **Forking**: branch a venture at a meaningful decision point (a different location or format) without losing the parent's history; only jurisdiction-sensitive evidence is invalidated in the child.
- **Long-horizon monitor**: a persistent, configurable cron schedule per venture that flags stale evidence and re-queues research automatically.
- **Founder model & weekly recommendation**: a deterministic aggregation of everything you've built (capital, loss tolerance, time commitment, interests, decision history) that the agent learns from — and a self-running recommendation agent that researches in your interest direction and returns a numbered, reasoned "what to build next", rendered as a compact window above your venture list.
- **Two-tier durable memory**: venture-scoped memory + cross-session user memory. When you reveal a lasting preference or constraint, the working agent writes it to `remember_user_fact` so every future venture is fitted to you — and never flatters or re-asks what you already told it.
- **Narrated tabs**: the Position and Model tabs lead with plain-prose explanations of the decision and the model (what drives cash, where the risk is, what "12-month survival" actually means) with the charts as supporting artifacts — not a wall of unexplained numbers.
- **Multimodal upload**: drop images, PDFs or .docx into the same chat — Gemini reads images/PDFs natively, and .docx is text-extracted — so a lease photo, a contract or a supplier quote lands as usable context, not a dead attachment.
- **Copilot-style working todos**: the agent keeps a live, checkable "Working plan" checklist (create / update / tick done) that persists across sessions, exactly like a coding agent's task list — the founder can tick items too.
- Append-only event log, specialist reports, contradiction records and validation tasks — a full audit trail behind every decision.
- Google ADK agent with tools that read and mutate the structured venture twin end-to-end (see "Why this is agentic" below).
- FastAPI web/API surface and a responsive hackathon demo UI.
- SQLite local persistence and Neon/Postgres cloud persistence.
- Cloud Run-ready Dockerfile.
- Automated tests (141) and GitHub Actions CI, including a machine-readable 100-question flagship acceptance contract (`app/acceptance.py`) that CI enforces stays fully covered.

## Canonical demo

Founder input:

- Business: neighbourhood supermarket/minimart
- Location: Ruiru, Kiambu County, Kenya
- Available capital: KES 1.8m
- Protected reserve: KES 150k
- Desired owner income: KES 120k/month
- Maximum acceptable loss: KES 600k

The local demo deliberately treats `transactions_per_day` as a weak critical assumption. The result should therefore stay conditional/rejected rather than quietly promoting an invented footfall number into truth. The location/lease gate remains locked until the critical uncertainty is resolved.

## Architecture

```mermaid
flowchart LR
    U[Founder / Web UI] --> API[FastAPI on Cloud Run]
    API --> ROOT[ADK root agent — conversational front door]
    ROOT --> TOOLS[Structured venture tools]
    TOOLS --> ENG[Deterministic underwriting + dependency engine]
    ROOT -->|run_underwriting| ORCH[SpecialistOrchestrator]
    ORCH --> FIN[ADK finance agent]
    ORCH --> MKT[ADK market agent]
    ORCH --> REG[ADK regulatory agent]
    ORCH --> EXE[ADK execution agent]
    ORCH --> ADV[ADK adversary agent]
    FIN & MKT & REG & EXE & ADV -->|search_web, browse_page_for_details| WEB[Live web]
    FIN & MKT & REG & EXE & ADV --> LEDGER[EvidenceLedger — deterministic admissibility gate]
    LEDGER --> ENG
    ENG --> DB[(Neon Postgres)]
    TOOLS --> DB
    DB --> WATCH[Persistent venture state]
    WATCH --> ENG
    ENG --> GATES[Execution gates / roadmap]
    GATES --> U
```

Six ADK agents in total: one conversational root agent the founder talks to, and five specialist sub-agents (finance, market, regulatory, execution, adversary) it delegates research to — each with its own model instance, its own role-scoped instruction, and real `search_web`/`browse_page_for_details` tools, not one model role-playing five personas through a prompt swap. Every specialist only *proposes* candidate evidence; `EvidenceLedger` is the same deterministic admissibility gate regardless of which agent produced a claim, so multi-agent orchestration adds real autonomous research depth without weakening the anti-fabrication guarantee. Toggle with `SPECIALIST_MODE=agentic` (the real multi-agent path) vs `SPECIALIST_MODE=orchestrated` (the original single-completion-per-role path, kept as a tested fallback). Local development swaps Postgres for SQLite and can swap live research for deterministic fixtures. The domain model and decision engine stay identical either way.

## Why this is agentic

The product is intentionally not a chat wrapper, and it is intentionally not one model either. The
ADK **root agent** the founder talks to has 12 tools that read and mutate durable venture state —
nothing consequential happens by the model editing state directly:

- `plan_venture_intake` — progressive intake; ask only the next materially-relevant question.
- `create_venture` — persist a new venture twin from typed founder + jurisdiction fields.
- `inspect_venture` — reload durable state for a returning founder instead of trusting chat memory.
- `run_underwriting` — checkpointed specialist research, evidence synthesis, deterministic underwriting.
- `add_founder_evidence` — admit a founder-supplied fact and re-underwrite.
- `apply_material_change` — apply a changed real-world fact and re-underwrite.
- `fork_configuration` — branch a materially different configuration without losing parent history.
- `run_sandbox_experiment` — stress-test assumptions in a disposable scenario that never becomes evidence.
- `inspect_audit_trail` — read back events, contradictions, specialist reports and validation tasks.
- `complete_execution_step` — advance an unlocked, founder-approved roadmap gate.
- `search_web` — live web search for competitors, suppliers, professionals, pricing, registration authorities.
- `browse_page_for_details` — headless-render one specific URL (Playwright, SSRF-guarded) and read its actual
  content; a search snippet alone is never treated as evidence.

`run_underwriting` delegates to `SpecialistOrchestrator`, which — in `SPECIALIST_MODE=agentic` — runs **five
separate ADK sub-agents** (`app/agentic_research.py`), one per specialist role, each with its own model
instance, its own role-scoped instruction and source policy, and the same `search_web`/`browse_page_for_details`
tools. A specialist's output is a proposal, never a write: `EvidenceLedger` is the same deterministic
admissibility gate (official-source domain check, contradiction detection, confidence downgrade on
unsourced claims) regardless of whether a single completion or a multi-step agentic turn produced the
claim. This is a reversible toggle, not a rewrite — `SPECIALIST_MODE=orchestrated` keeps the original,
still-tested single-completion-per-role providers in `app/research.py` as a fallback.

The system can be invoked asynchronously through analysis jobs. A production deployment can schedule recurring re-research calls (for example with Cloud Scheduler/Pub/Sub) without changing the core state model; a lightweight version of this already runs in-process via the monitor cron in `app/monitor.py`.

## Local setup

### Prerequisites

- Python 3.11+
- `uv`

### Install

```bash
cp .env.example .env
uv sync --extra dev
```

The default `.env` settings use:

```env
DATABASE_BACKEND=sqlite
RESEARCH_MODE=offline
GEMINI_MODEL=gemini-3.7-flash
```

Start the application:

```bash
uv run uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080`.

### Run the ADK agent playground

The repository contains an ADK `app/agent.py` entrypoint and `agents-cli-manifest.yaml`.

```bash
uvx google-agents-cli setup --skip-auth
uvx google-agents-cli playground
```

For live model calls, add a Gemini API key or configure Google Cloud authentication as documented by Google ADK.

## Live grounded research

Two live providers are supported behind the same `ResearchProvider` interface, so the model can be swapped without touching the domain model, evidence gate or decision engine.

**Production target — Gemini with Google Search grounding:**

```env
RESEARCH_MODE=live
RESEARCH_PROVIDER=gemini
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-3.7-flash
```

**Development mode — OpenRouter + Tavily.** Used during build-out to avoid spending Gemini 3.7 Flash tokens on every iteration; swap `RESEARCH_PROVIDER` back to `gemini` for the submission/production run without any other code change:

```env
RESEARCH_MODE=live
RESEARCH_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=google/gemini-3.5-flash-lite
TAVILY_API_KEY=your_key
```

Then restart the API. Live analysis uses grounded web search (Google Search or Tavily). The prompt explicitly prioritizes evidence against the business case and forbids invented licences, fees, laws, suppliers, providers, prices and URLs.

Both live providers downgrade a claimed source to low confidence when the claimed URL does not match a URL the search step actually returned — the model's self-rated confidence is never trusted on its own.

## Postgres (Neon) mode

Provision a Postgres database (Neon or otherwise), then set:

```env
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://user:password@host/dbname
```

The Postgres repository stores three tables — `ventures`, `jobs`, and an append-only `state_records` table keyed by `(kind, id)` that holds every event, workflow checkpoint, specialist report, contradiction, fork, sandbox experiment, validation task and monitor schedule (see `app/repository.py`, `app/state.py`). Neon's pooled connection string provides the pooling layer, so no separate application-level connection pool is needed on a scale-to-zero Cloud Run instance.

## Test it

```bash
uv run ruff check app tests
uv run pytest --cov=app --cov-report=term-missing
```

The suite tests the actual deterministic engine, API, persistence and ADK import path. It includes failure-oriented cases: inadequate capital kills the current configuration, weak demand evidence prevents approval, irreversible roadmap steps remain locked, and a changed rent quote recomputes profitability.

## Docker / Cloud Run

Build locally:

```bash
docker build -t venture-twin-agent .
docker run --rm -p 8080:8080 \
  -e RESEARCH_MODE=offline \
  -e DATABASE_BACKEND=sqlite \
  venture-twin-agent
```

Example Cloud Run deployment:

```bash
gcloud run deploy venture-twin-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars DATABASE_BACKEND=postgres,RESEARCH_MODE=live,RESEARCH_PROVIDER=gemini,GEMINI_MODEL=gemini-3.7-flash,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT \
  --set-secrets DATABASE_URL=database-url:latest,GEMINI_API_KEY=gemini-api-key:latest
```

Store the Gemini key and the Postgres connection string in Secret Manager rather than placing them in source control.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/intake` | Start progressive intake from a bare idea |
| `POST` | `/api/intake/{draft_id}` | Refine an in-progress intake draft |
| `POST` | `/api/ventures` | Create persistent venture state |
| `GET` | `/api/ventures` | List ventures |
| `GET` | `/api/ventures/{id}` | Read venture twin |
| `POST` | `/api/ventures/{id}/analysis` | Queue background underwriting |
| `POST` | `/api/ventures/{id}/analysis/sync` | Deterministic test/demo underwriting |
| `GET` | `/api/jobs/{id}` | Inspect asynchronous job state |
| `POST` | `/api/ventures/{id}/evidence` | Replace weak assumptions with evidence |
| `POST` | `/api/ventures/{id}/changes` | Apply a material real-world change and re-underwrite |
| `POST` | `/api/ventures/{id}/forks` | Branch a venture at a meaningful decision point |
| `GET` | `/api/ventures/{id}/forks` | List forks of a venture |
| `POST` | `/api/ventures/{id}/sandbox` | Run a disposable Monte Carlo scenario shock |
| `GET` | `/api/ventures/{id}/events` | Read the append-only event log |
| `GET` | `/api/ventures/{id}/timeline` | Read events as a timed timeline |
| `GET` | `/api/ventures/{id}/contradictions` | Read recorded conflicting evidence |
| `GET` | `/api/ventures/{id}/specialists` | Read specialist research reports |
| `GET` | `/api/ventures/{id}/validation` | Read outstanding real-world validation tasks |
| `POST` | `/api/ventures/{id}/roadmap/{step}/complete` | Advance an unlocked execution gate |
| `POST` | `/api/ventures/{id}/monitor` | Enable/configure the staleness monitor |
| `GET` | `/api/ventures/{id}/monitor` | Read the monitor schedule |
| `POST` | `/api/ventures/{id}/monitor/tick` | Force an immediate monitor check |
| `POST` | `/api/demo` | Create canonical Ruiru demo |
| `GET` | `/healthz` / `/readyz` | Liveness / readiness (DB + research runtime) |

## Truthfulness rules

The most important product behaviour is what it refuses to claim.

1. A model estimate is not verified evidence.
2. An illustrative/demo number is never presented as a current market fact for the founder's actual jurisdiction.
3. Laws, licences, fees and official procedures require official-source research.
4. A Monte Carlo output is described by the assumptions it tests; it is not a universal probability that a company "succeeds".
5. Irreversible legal and financial actions remain explicitly user-approved.
6. Unknowns are allowed to remain unknown. The agent should ask for a real-world validation task rather than manufacture certainty.

## Hackathon fit

**Track: Collaborative Partner.** The agent captures the founder's constraints, persistently mutates a structured venture model, actively challenges the user's thinking and adapts the roadmap as new evidence arrives. The implementation uses Gemini 3.7 Flash, Google ADK, and is Cloud Run/Neon Postgres ready.

## Current boundary

This repository is a functional hackathon MVP, not a licensed legal, accounting or investment-advice service. Live registration/regulatory research is built to surface official sources and execution dependencies; production use would require jurisdiction-specific review, authentication, authorization, audit logging and explicit approval flows around submissions/payments.
