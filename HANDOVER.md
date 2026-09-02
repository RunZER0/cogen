# Cogen Engineering Handover

**Status timestamp:** 2026-08-25 18:48 EAT (Africa/Nairobi)

This document is the authoritative handover for the current Cogen build. Read it before changing architecture, merging branches, touching deployment, or interpreting the existing README. Some older documentation on `main` still describes an earlier Firestore-shaped MVP and is stale relative to the active work.

---

## 1. What we are building

Cogen is a **persistent adversarial venture-building agent**.

The product promise is:

> **Before you put money into a business, Cogen does the homework. Then it stays with the venture until it is running and keeps re-evaluating it as reality changes.**

This is not intended to be a generic business-plan generator, idea validator, chat wrapper, market-research bot, or one-shot financial calculator.

The product should take a founder from a vague business idea to an evidence-backed capital decision and then into execution. A founder should be able to say something like:

> “I have USD 85,000 and want to open a specialty coffee shop in Austin, Texas. I must keep USD 15,000 untouched, I need the business to eventually pay me USD 7,500/month, I have never run a café, I will not accept losing more than USD 30,000, and I want to launch within six months.”

Cogen should then:

1. Capture founder-specific constraints that cannot be researched externally.
2. Resolve the venture jurisdiction/currency explicitly.
3. Build a persistent Venture Twin.
4. Decompose the business into assumptions and dependencies.
5. Research evidence that can support **or kill** those assumptions.
6. Keep unknown material facts unknown rather than inventing them.
7. Reverse-model what must be true for the founder's income/capital constraints to work.
8. Stress-test the economics deterministically.
9. Identify the exact variables that can kill the current configuration.
10. Request the smallest useful real-world validation experiment where research cannot resolve a critical fact.
11. Say `REJECT` / “do not commit capital” when the evidence warrants it.
12. If the configuration fails, preserve what survives, change only necessary variables, and support forks/scenarios.
13. For surviving ideas, continue into registration, tax, permits, premises, suppliers, equipment, staffing, payments, insurance, and launch dependencies.
14. Persist decision history so future changes can explain why the recommendation changed.
15. Re-underwrite the venture when rent, demand, regulation, competitors, supplier terms, or actual operating data change.

### Product philosophy

The system must be adversarial. Do not flatter the founder and do not optimize for an encouraging answer. The goal is to prevent expensive facts being learned only after capital is committed.

The LLM is **not** the source of truth. Durable structured state is.

---

## 2. Hackathon context

Cogen is being built for the **Google All Things Agentic Hackathon**, Collaborative Partner track.

The intended Google stack is:

- Gemini
- Google ADK
- Cloud Run
- Google Cloud authentication via Workload Identity Federation
- Neon PostgreSQL as the durable Venture Twin database

Cloud Run is sufficient as the required Google Cloud infrastructure component; Firestore is not required.

---

## 3. Repository / branch state — do not skip this

Repository: `RunZER0/cogen`

### `main`

Current head at handover time:

`a8270cbf0667b0bec6f472e1409fc90d13c176ce`

Message:

`Diagnose and force Cloud Run public routing`

`main` contains the earlier flagship/live-stack work plus the latest Cloud Run/Docker routing diagnostics. Its README still contains stale Firestore references.

### Active implementation branch: `flagship/global-agent`

Current head before this handover document:

`130c11ad0cf5c982d4293de9b170d37dd81e1ca5`

Message:

`Run live proof directly through Google ADK Runner`

At handover time GitHub reports this branch is:

- **29 commits ahead of `main`**
- **3 commits behind `main`**
- status: **diverged**

**Do not force-update `main` to this branch.** Reconcile the three `main` commits carefully. Some Cloud Run/Docker fixes were manually copied onto the active branch, so a merge/rebase may surface overlapping changes.

### Other branches

- `flagship/runtime-hardening` — prior context/recovery/model-sustenance work; head observed at `90399fdbe8ba2619ddaf8d8e0e7d5afb22580949`.
- `flagship/live-stack` — earlier persistence/deployment refactor staging branch.
- `flagship/global-agent-proof` — historical proof branch.
- `handover/current-state` — docs-only branch containing this handover for safe review without triggering `main` deployment.

Treat `flagship/global-agent` as the active code state to continue from.

---

## 4. What is actually implemented on `flagship/global-agent`

### 4.1 Real Google ADK root agent

`app/agent.py` defines a real Google ADK `Agent` named `venture_underwriter` using the configured Gemini model.

Typed tools exposed to the root agent include:

- `plan_venture_intake`
- `create_venture`
- `inspect_venture`
- `run_underwriting`
- `add_founder_evidence`
- `apply_material_change`
- `fork_configuration`
- `run_sandbox_experiment`
- `inspect_audit_trail`
- `complete_execution_step`

The model does not edit the database directly. All consequential state changes go through tools/service code.

### 4.2 Important multi-agent truth

Do **not** describe the current specialist layer as five independent ADK agents.

The architecture is:

```text
Google ADK root agent
        |
        v
Typed Cogen tools
        |
        v
Canonical Venture Twin
        |
        +--> Finance specialist research role
        +--> Market specialist research role
        +--> Regulatory specialist research role
        +--> Execution specialist research role
        +--> Adversary specialist research role
        |
        v
Evidence admission / synthesis
        |
        v
Deterministic underwriting + gates
```

The five specialists are currently **scoped iterative Gemini research workers coordinated by deterministic orchestration**, not independent ReAct agents with competing memories. This was deliberate: avoid fake “multi-agent” theatre and keep one canonical state.

Each specialist gets a narrow mandate and candidate evidence. It cannot independently redefine truth or mutate the Venture Twin.

### 4.3 Persistent Venture Twin

The durable state includes founder constraints, assumptions, evidence, confidence/provenance, underwriting, roadmap, events, contradictions, specialist reports, validation tasks, workflow state, and forks/sandbox outputs.

Important design principle:

> **The database is long-term memory. The model gets bounded working context.**

### 4.4 Neon/Postgres persistence

The active branch supports:

- SQLite for local/offline mode
- PostgreSQL/Neon for production

`app/repository.py` implements `PostgresRepository` using `psycopg` and short-lived connections, appropriate for a Neon pooled URL and Cloud Run scale-to-zero.

Core tables currently auto-created by repository initialization:

- `ventures`
- `jobs`
- `state_records`

`state_records` supports idempotency keys and is used for durable workflow/event/specialist/validation state.

**Do not reintroduce Firestore unless there is a new reason.** The README/older architecture docs mentioning Firestore are stale.

### 4.5 Country-agnostic venture state

This was added after discovering Kenya-specific leakage (`KES`, “county licence”) in the earlier core.

The Venture Twin now explicitly carries:

- location text
- locality
- subdivision/state/province/region
- country
- currency
- locale

Starter monetary assumption units derive from the venture currency.

Regression tests cover at least:

- Austin, Texas, United States / USD
- Melbourne, Victoria, Australia / AUD
- Shenzhen, Guangdong, China / CNY

A US venture test fails if KES/Kenya/county-specific core wording leaks into the model.

The root-agent instruction explicitly says not to assume Kenya, the US, or any other jurisdiction from server context.

### 4.6 Context pruning / bounded working context

`app/context.py` implements role-specific bounded context.

Current default budget:

- max assumptions: **14**
- max evidence records: **12**
- max context characters: **9,000**

Materiality ranking considers role relevance, confidence risk, criticality/impact, staleness, and related evidence.

This is intentional: do not pass the complete conversation or full venture history to every model call.

### 4.7 Model sustainance / fallback

`app/model_runtime.py` contains `GeminiModelRouter`.

Current configured behavior is:

- primary model from `GEMINI_MODEL`
- optional fallback from `GEMINI_FALLBACK_MODEL`
- bounded attempts per model
- health counters: last model used, failures by model, fallbacks used, total calls

The currently configured IDs in the branch/workflows are:

- `gemini-3.7-flash`
- fallback `gemini-3.6-flash`

**Important:** do not assume these IDs are valid merely because they are configured. The immediate blocker is that a successful Gemini response has not yet been proven. Directly verify model availability first (see Section 10).

### 4.8 Recovery / idempotency

`app/workflow.py` implements durable workflow phases:

```text
PLAN
RESEARCH
SYNTHESIS
UNDERWRITE
VALIDATION
MONITOR
COMPLETE
```

Workflow states include retry/failure handling. Completed phases are persisted.

Research is more granular: specialist reports are themselves checkpoints. On resume, roles already completed for the same workflow are skipped.

Intended behavior:

```text
Finance ✓
Market ✓
process dies

restart
Finance -> skip
Market -> skip
Regulatory -> continue
Execution -> continue
Adversary -> continue
```

There are tests for crash/resume and specialist-level recovery.

A prior recovery implementation exposed a dedupe bug where newly seen findings were accidentally suppressed before persistence. That bug was fixed; the current branch unit tests are green.

### 4.9 Sandbox / forks

Cogen supports hypothetical forks and sandbox experiments without allowing simulated values to masquerade as observed evidence.

Use forks for meaningful alternative configurations (location/format/etc.). Use sandbox runs for shocks/sensitivity analysis.

### 4.10 Deterministic modelling

The LLM is used for research, interpretation, hypothesis generation, and synthesis. Deterministic code should own:

- financial calculations
- dependency propagation
- simulation
- gate logic
- status transitions

Monte Carlo outputs must never be presented as a universal “probability this business succeeds.” They only describe the probability of satisfying the explicitly modelled conditions under the current assumptions.

---

## 5. Evidence / truthfulness contract

These are product invariants, not optional prompt style:

1. A model estimate is not verified evidence.
2. Founder claims are tagged as founder claims, not external evidence.
3. Unsupported material claims may remain unknown.
4. Official legal/regulatory/tax/permit claims require current official-source evidence from the relevant jurisdiction.
5. Supplier/provider/pricing claims should be tied to actual sources serving the market.
6. Search-grounded results still pass through evidence admission rules.
7. Contradictory evidence is retained/flagged rather than silently overwritten.
8. Simulated/sandbox values never become real-world evidence.
9. Irreversible capital/legal actions remain user-approved.
10. If a critical fact cannot be researched, create the smallest useful real-world validation task instead of inventing certainty.

---

## 6. Tests — last known green state

Latest clean test workflow for `flagship/global-agent` before handover:

GitHub Actions run ID:

`32853920652`

Result:

- Python 3.11: success
- Python 3.13: success
- lint: success
- tests: success

The live proof setup also printed:

`45 passed, 2 warnings`

So the deterministic/local architecture is currently green.

Do not confuse that with a successful live Gemini/ADK E2E. That is **not** proven yet.

---

## 7. Google Cloud infrastructure already configured

Google Cloud project:

- Project ID: `cogen-506607`
- Project number: `257066508186`
- Region: `us-central1`

Artifact Registry:

- Repository: `cogen`
- URI: `us-central1-docker.pkg.dev/cogen-506607/cogen`

Service accounts:

- deployer: `cogen-github-deployer@cogen-506607.iam.gserviceaccount.com`
- runtime: `cogen-runtime@cogen-506607.iam.gserviceaccount.com`

Deployer roles configured:

- `roles/run.admin`
- `roles/artifactregistry.writer`

Deployer can act as runtime account via:

- `roles/iam.serviceAccountUser` on `cogen-runtime`

Workload Identity Federation:

- pool: `github`
- provider: `cogen-github`
- provider resource:
  `projects/257066508186/locations/global/workloadIdentityPools/github/providers/cogen-github`
- trust restricted to repository: `RunZER0/cogen`

GitHub repository secrets are expected to be configured already:

- `GEMINI_API_KEY`
- `DATABASE_URL`

Do not print or move these values into source control.

GCP identifiers/region were also added as GitHub variables, although the deployment workflow currently hard-codes the non-secret GCP identifiers to remove variable-injection failure as a deployment risk.

---

## 8. Cloud Run deployment state

Cloud Run deployment/authentication progressed far enough to prove:

- GitHub Actions execution works
- GitHub OIDC -> Google WIF authentication works
- Artifact Registry authentication works
- Docker builds/pushes work
- Cloud Run deploy command succeeds
- runtime/deployer service accounts work

A real Docker packaging bug was found and fixed:

Old image build installed the Python project before copying the `app/` package, allowing the wrong top-level `app` package to be resolved.

The corrected Dockerfile copies Cogen before installation, uses `python -m uvicorn`, and contains build-time assertions that `app.main` resolves inside `/app/app/` and exposes `/healthz` and `/readyz`.

### Remaining Cloud Run issue

`main` deployment workflow run:

`32831284717`

ended in failure after deployment because the public Cloud Run front door returned 404 during probing.

The app image itself had already been proven to contain the correct routes.

The active branch modifies `.github/workflows/deploy.yml` to make authenticated Cloud Run proxy probing authoritative and treat the public URL as an additional check, but that active branch has not yet been safely reconciled into `main` and deployed.

Do not spend time debugging Cloud Run public routing until the simpler Gemini connectivity issue below is resolved.

---

## 9. Live Gemini / ADK testing: exact truth

This section is critical because earlier discussion temporarily overstated what had been proven.

### What has NOT been proven

As of this handover:

- no successful direct Gemini response has been confirmed
- no completed ADK root-agent turn has been confirmed
- no Gemini-chosen Cogen tool call has been confirmed
- no live Gemini-grounded specialist research has been confirmed
- no Austin live underwriting result has been confirmed

### Live attempt 1 — Google Agents CLI

Workflow run:

`32853441820`

The CLI successfully:

- installed dependencies
- started a local ADK server
- accepted the Austin prompt
- entered the agent run

Then after about two minutes the `google-agents-cli` SSE client failed with a local HTTP read timeout:

`HTTPConnectionPool(host='127.0.0.1', port=18080): Read timed out`

No successful tool-call trace was captured before timeout.

This showed the CLI wrapper is a poor proof harness for a long Cogen underwriting operation.

### Live attempt 2 — direct Google ADK Runner

Workflow run:

`32853920964`

The proof was changed to use Google ADK `Runner.run_async()` directly in:

`scripts/live_adk_proof.py`

The log showed:

`Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.`

Then **no ADK event was emitted for 900 seconds**. The proof's own 15-minute `asyncio.wait_for(..., timeout=900)` cancelled the root node.

Result:

- Gemini/ADK model execution path was entered.
- A successful model response was **not** observed.
- It is not known whether the problem is model ID availability, API/key behavior, SDK transport, a hanging model request, or some other upstream issue.

Do not debug the full agent first. Isolate Gemini connectivity.

---

## 10. IMMEDIATE NEXT STEP — start here

The next engineer should **not** run the full Austin agent again first.

### Step 1: prove one direct Gemini response

Create a tiny one-purpose workflow/script using the same repository secret and current `google-genai` SDK:

```python
import os
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
response = client.models.generate_content(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.7-flash"),
    contents="Reply exactly COGEN_GEMINI_OK",
)
print(response.text)
```

Pass condition:

```text
COGEN_GEMINI_OK
```

Put a short hard timeout around this probe.

If it fails/hangs:

1. verify the model ID with current Google model documentation/API listing;
2. test a known available Gemini model explicitly;
3. ensure only one API key environment variable is being used (ADK logged both `GOOGLE_API_KEY` and `GEMINI_API_KEY` and chose `GOOGLE_API_KEY`);
4. test the same call outside ADK;
5. inspect exception/transport output;
6. do not touch Cogen business logic until this succeeds.

### Step 2: prove a direct grounded research call

After plain Gemini works, call the exact provider path in `GeminiGroundedResearchProvider` on one narrow query such as:

> “Find the current official City of Austin page governing food enterprise permits and return only grounded source metadata.”

Pass condition: real model response + grounding metadata/source URL.

### Step 3: prove a trivial ADK agent tool call

Use the real `root_agent` but prompt it to do only intake/creation, not underwriting.

Example:

> “Create a Venture Twin for a test USD coffee shop in Austin using these complete founder fields. Do not run underwriting.”

Capture `event.get_function_calls()`.

Pass condition:

`create_venture`

appears in the ADK event stream and the venture is persisted in Neon.

### Step 4: prove one specialist

Run only Finance (or Market) research with one round. Confirm one Gemini response, evidence admission, specialist report persistence, and checkpoint.

### Step 5: then run the full Austin proof

Only after Steps 1–4 are green should `scripts/live_adk_proof.py` run the entire root-agent flow.

This layered sequence will locate failures in minutes instead of waiting 15 minutes per attempt.

---

## 11. Canonical non-Kenyan live proof

The current real-business proof is intentionally US-based to demonstrate country agnosticism.

Prompt facts:

- business: specialty coffee shop
- locality: Austin
- subdivision: Texas
- country: United States
- currency: USD
- founder capital: USD 85,000
- protected reserve: USD 15,000
- debt: USD 0
- target monthly owner income: USD 7,500
- maximum acceptable loss: USD 30,000
- founder experience: first-time café operator
- time commitment: full-time
- launch target: six months

The proof must fail if any of the following occur:

- KES appears in US monetary assumptions
- Kenya/KRA/county-specific regulation appears
- invented Austin rent/demand/licensing is promoted to verified truth
- the agent creates a generic business plan without persisting a Venture Twin
- no five specialist roles are recorded for full underwriting
- deterministic simulation does not run
- a critical unknown is silently filled with fabricated precision

The proof should report:

- exact root ADK tool sequence
- venture ID
- jurisdiction/currency
- specialist roles
- evidence count + source URLs
- rejected/contradictory evidence
- underwriting decision
- simulation count
- model confidence/evidence coverage
- critical unknowns
- biggest risks
- validation tasks
- event history

---

## 12. Long-running execution: important architectural gap

Cogen has durable workflow/checkpoint state, but the production executor is still too coupled to synchronous requests/background process execution.

A serious venture investigation may take minutes/hours or span days. It should **not** be one giant HTTP request.

Recommended production evolution:

```text
ADK/root request
   |
   v
persist workflow
   |
   v
Cloud Tasks or Pub/Sub
   |
   v
Cloud Run worker claims one bounded work unit
   |
   v
research / synthesize / underwrite / validate
   |
   v
checkpoint Neon
   |
   +--> complete
   |
   +--> enqueue next work unit
```

This allows scale-to-zero, crash recovery, retries, model quota backoff, and multi-hour/day workflows without keeping one request alive.

Current `FastAPI BackgroundTasks`-style execution should not be considered the final durable scheduler.

---

## 13. Product acceptance criteria the architecture must preserve

When changing code, challenge it against these domains:

### Purpose / anti-echo-chamber
- Does Cogen challenge the founder?
- Can it reject the venture?
- Can it identify the exact killing variable?
- Does it distinguish evidence from optimism?

### Founder intake / minimum human input
- Ask only founder-specific facts the system cannot research.
- Progressive intake; no giant form.
- Never ask the founder to manually provide public data Cogen can fetch.

### Persistent state
- One canonical Venture Twin.
- Typed first-class objects, not a giant unstructured chat blob.
- Every material mutation is auditable.

### Evidence / truthfulness
- provenance/confidence/source URL
- contradictions retained
- staleness/dependency invalidation
- official sources for legal/regulatory facts
- unknowns allowed

### Tooling / web navigation
- route queries to the best current source type: official authorities, maps/directories, property/supplier sites, professional directories, laws/PDFs/databases, etc.

### Orchestration
- role scope matters; “more agents” is not inherently better
- specialists share canonical state
- no arbitrary voting
- deterministic code owns deterministic business logic

### Flagship underwriting
- reverse-model founder constraints
- capital-at-risk gates
- sensitivity/Monte Carlo without fake precision
- explicit killing variables

### Experiments
- smallest useful real-world validation where online evidence is inadequate
- simulations cannot contaminate real evidence

### Forks
- inherit shared evidence where valid
- isolate fork-specific assumptions
- promote an alternative only intentionally

### Persistence/recovery
- idempotent steps
- checkpoints
- resume without rerunning completed expensive work
- model/context-window limits must not erase venture truth

---

## 14. Known stale / misleading repo docs

At handover time:

- `README.md` on `main` still describes Firestore and the older Ruiru-centric MVP.
- `docs/ARCHITECTURE.md` also still mentions Firestore.
- the active branch has Neon/Postgres and explicit global jurisdiction/currency state.

Do not use those stale Firestore references as architectural requirements.

Once the live stack is proven and the branch is safely merged, update README/ARCHITECTURE to match the final reality.

---

## 15. Merge/deployment strategy from here

Recommended order:

1. Keep working on `flagship/global-agent` until direct Gemini probe + minimal ADK proof + one specialist proof are green.
2. Run the full Austin live proof.
3. Reconcile `main` and `flagship/global-agent` without force-pushing.
4. Run Python 3.11/3.13 test matrix after reconciliation.
5. Merge/promote only the green reconciled commit to `main`.
6. Let `deploy-cloud-run` build/push/deploy that exact commit.
7. Use authenticated Cloud Run probe to prove `/healthz`, `/readyz`, Neon connectivity, then live Gemini path.
8. Separately repair/verify public `run.app` access.
9. Run the same non-Kenyan business proof against the deployed service/ADK runtime.
10. Only then call the flagship stack production/demo-ready.

Do not use a docs-only commit on `main` merely to test deployment; `main` pushes trigger the deployment workflow.

---

## 16. Useful files to read first

Start here:

- `app/agent.py` — real Google ADK root agent and tools
- `app/domain.py` — Venture Twin types including jurisdiction/currency
- `app/service.py` — application operations
- `app/workflow.py` — checkpointed workflow/recovery
- `app/orchestration.py` — specialist roles and iterative research
- `app/research.py` — offline/live Gemini research provider + grounding/evidence behavior
- `app/context.py` — bounded role-specific context pruning
- `app/model_runtime.py` — model retry/fallback health router
- `app/repository.py` — SQLite + Neon/Postgres persistence
- `app/state.py` — durable state records/events/etc.
- `app/engine.py` — deterministic underwriting/decision logic
- `app/validation.py` — real-world validation task design
- `app/sandbox.py` — isolated scenario testing
- `app/main.py` — API + readiness routes
- `scripts/live_adk_proof.py` — current Austin full live proof harness
- `.github/workflows/live-agent.yml` — live ADK proof CI
- `.github/workflows/deploy.yml` — Cloud Run deployment/probing
- `tests/test_globality.py` — US/Australia/China regression tests
- `tests/test_recovery_granularity.py` — specialist recovery behavior
- `tests/test_architecture.py` — architectural safety behavior

---

## 17. What not to claim yet

Until the next engineer produces the missing live proof, do **not** claim that:

- Gemini has successfully answered Cogen in production
- the ADK root agent has successfully selected and completed Cogen tools live
- all five specialists have completed live research
- Neon contains a fully live-underwritten Austin venture
- Cloud Run public access is fixed
- the deployed end-to-end stack is launch-ready

What is fair to claim:

- deterministic architecture/tests are green
- Google ADK agent code exists
- Neon/Postgres persistence exists
- global jurisdiction/currency regression tests exist and pass
- context pruning/recovery/model-router code exists and tests pass
- WIF/Artifact Registry/Cloud Run deployment mechanics have successfully progressed through authentication/build/push/deploy
- the remaining immediate blocker is live Gemini response isolation, followed by ADK/tool proof

---

## 18. Definition of done for this handover's next owner

The immediate milestone is complete only when one artifact/log shows all of the following from a fresh run:

```text
DIRECT_GEMINI_OK
ADK_ROOT_MODEL_RESPONSE_OK
ADK_TOOL_CALL create_venture
ADK_TOOL_CALL run_underwriting
NEON_VENTURE_PERSISTED
country=United States
subdivision=Texas
currency=USD
specialists=finance,market,regulatory,execution,adversary
evidence_count > 0
simulation_runs > 0
decision=<needs_data|reject|conditional|approve>
NO_KENYA_LEAK
LIVE_ADK_NEON_PROOF_PASS
```

Then reconcile and deploy that exact green tree to Cloud Run.

---

## 19. Final orientation

The central idea is simple:

> Cogen should be able to remain useful when the model changes, when a request crashes, when context windows are exhausted, when the founder disappears for a month, when evidence changes, and when the business is in Kenya, the US, Europe, Australia, China, or elsewhere.

That only works if the model is treated as a replaceable reasoning worker around durable truth rather than as the memory/database/business engine itself.

Preserve that principle while finishing the live stack.
