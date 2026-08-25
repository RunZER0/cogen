# Cogen takeover handover

**Status timestamp:** 2026-08-25 18:49 EAT (Africa/Nairobi)  
**Repository:** `RunZER0/cogen`  
**Active engineering branch:** `flagship/global-agent`  
**Pre-handover active code head:** `130c11ad0cf5c982d4293de9b170d37dd81e1ca5`  
**Default branch / production-history head at handover:** `main` @ `a8270cbf0667b0bec6f472e1409fc90d13c176ce`

---

## 1. Read this first

Cogen is being built as a **persistent, adversarial venture-building agent**, not a business-plan generator and not a generic startup chatbot.

The intended user promise is:

> Before you put money into a business, Cogen does the homework, models what has to be true, attacks the assumptions, tells you when the business should not be opened, and then keeps the Venture Twin alive through validation, commitment, launch and material changes.

The most important current fact is also the easiest one to accidentally misstate:

> **We have attempted to enter the Gemini/Google ADK model path, but as of this handover we have NOT proven one successful Gemini response in the live proof.**

Do not report that Gemini is working until a minimal direct `generate_content` request returns successfully and is logged, followed by a visible ADK tool-call trace.

The application architecture and offline/tested logic are substantially further along than the live model proof.

---

## 2. What Cogen is supposed to do

Given a founder statement such as:

> I have USD 85,000 and want to open a specialty coffee shop in Austin, Texas. I must keep USD 15,000 untouched, I have no debt available, I need the business eventually to pay me USD 7,500/month, I am a first-time cafe operator, I will not accept losing more than USD 30,000, and I want to launch within six months.

Cogen should:

1. Resolve only genuinely inferable jurisdiction/currency facts and ask the smallest question where ambiguity remains.
2. Create one durable **Venture Twin**.
3. Build first-class assumptions rather than a prose plan.
4. Research current market, cost, regulatory, execution and failure evidence for the actual jurisdiction.
5. Reject unsupported material claims rather than treating model text as evidence.
6. Reverse-model what has to be true for the founder's income/capital constraints to work.
7. Run deterministic financial calculations and Monte Carlo/sensitivity analysis outside the LLM.
8. Produce `NEEDS_DATA`, `REJECT`, `CONDITIONAL`, or `APPROVE` based on current evidence/model state.
9. Name the assumptions capable of killing the configuration.
10. Create the smallest useful real-world validation tasks where online evidence is insufficient.
11. Preserve the venture over time and re-underwrite when material facts change.
12. Allow meaningful forks (location/format/configuration) without corrupting the parent history.
13. Keep irreversible legal/capital actions human-approved.

A valid Cogen answer can be: **DO NOT OPEN THIS BUSINESS**.

---

## 3. Country-agnostic requirement

Cogen must work for a buyer/founder in the US, Europe, Australia, China, Kenya or another jurisdiction without importing Kenyan assumptions into the core.

The active branch now carries explicit venture fields for:

- country
- subdivision / state / province / region
- locality
- currency
- locale

Core financial assumption units are derived from the Venture Twin currency rather than hard-coded `KES`.

Regression coverage currently includes:

- Austin, Texas, United States -> `USD`
- Melbourne, Victoria, Australia -> `AUD`
- Shenzhen, Guangdong, China -> `CNY`

The US regression explicitly fails on Kenya/KES/county-specific core leakage.

Important principle:

> `location` is descriptive. `country`, `subdivision`, `locality`, `currency`, and locale/jurisdiction context are explicit durable state.

Regulatory work must find the actual national/subnational/local/sector authorities for that venture. It must never assume Kenyan counties, KRA, US states, or any other jurisdiction from server context.

---

## 4. Branch state — DO NOT FORCE-MOVE `main`

At the time of this handover, GitHub comparison reports:

- `flagship/global-agent` is **29 commits ahead** of `main`
- `flagship/global-agent` is **3 commits behind** `main`
- branches are **diverged**

The main-only work is concentrated in Cloud Run deployment diagnostics / Docker deployment history. The active branch also contains modified versions of those files, but the histories are not linearly related.

**Do not `git reset --hard` / force-push `main` to the active branch.**

Before promotion:

1. inspect `git log main --not flagship/global-agent`
2. inspect `git diff flagship/global-agent...main -- .github/workflows/deploy.yml Dockerfile`
3. reconcile the three main-only commits into the active branch or build a merge commit
4. run CI and live proofs on the reconciled tree
5. only then promote/merge to `main`

Current refs at handover:

```text
main
  a8270cbf0667b0bec6f472e1409fc90d13c176ce
  "Diagnose and force Cloud Run public routing"

flagship/global-agent
  130c11ad0cf5c982d4293de9b170d37dd81e1ca5
  "Run live proof directly through Google ADK Runner"
```

---

## 5. Architecture actually present in code

### 5.1 Root agent

`app/agent.py` defines a real Google ADK `Agent` named `venture_underwriter`, using `Gemini(...)` and typed Cogen tools.

Root tools currently include:

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

The model does **not** directly edit the database. Consequential mutation goes through typed application/service boundaries.

### 5.2 Specialists — important distinction

The five specialist roles are currently:

- finance
- market
- regulatory
- execution
- adversary

They are **not five independent Google ADK agents with separate memories**.

`app/orchestration.py` implements them as scoped, independently retryable research units around the one canonical Venture Twin. Each receives bounded working context, can perform a small number of evidence-driven follow-up rounds, and returns **candidate evidence**.

Default/current design limits follow-up research to a small bounded budget (`max_rounds`, normally 2; hard-capped to 3 in the orchestrator).

This is intentional. Do not turn them into five noisy agents merely for a “multi-agent” label unless independent agency materially improves the workflow.

### 5.3 Canonical state

The Venture Twin is the durable truth source. Core concepts are first-class rather than an opaque LLM JSON blob, including assumptions/evidence/decisions/events/workflows/forks/contradictions/experiments/roadmap state.

Runtime repository selection is in `app/runtime.py`:

- Postgres / Neon when `DATABASE_BACKEND=postgres|postgresql|neon`
- SQLite for local/offline development

### 5.4 Deterministic vs model work

LLM/model responsibilities:

- interpretation
- research decomposition
- evidence discovery
- hypothesis generation
- adversarial investigation
- synthesis/explanation

Deterministic application responsibilities:

- evidence admission rules
- financial calculations
- Monte Carlo/sensitivity
- dependency invalidation
- workflow status/checkpoints
- gate/status transitions
- state mutation boundaries

Do not move financial truth or state transition rules into prose prompts.

### 5.5 Bounded working context

`app/context.py` exists to reconstruct role-specific working memory from durable state rather than replaying an indefinitely growing chat transcript.

The design prioritizes material assumptions/evidence by factors such as:

- criticality
- financial/material impact
- uncertainty / low confidence
- staleness
- specialist role relevance

The intended mental model is:

> Neon holds durable memory. Gemini gets a bounded working set.

### 5.6 Model sustainment

`app/model_runtime.py` contains `GeminiModelRouter` and `ModelHealth` for direct Gemini research-provider calls.

Current configured concept:

- primary model from `GEMINI_MODEL`
- optional fallback from `GEMINI_FALLBACK_MODEL`
- bounded attempts per model
- health counters (`total_calls`, failures by model, fallback count, last successful model)

Important: this router applies to direct research-provider calls. The root ADK agent has its own `Gemini(...)` configuration in `app/agent.py` and currently retries via ADK HTTP retry options. Do not assume the root agent automatically uses `GeminiModelRouter`.

### 5.7 Recovery

`app/workflow.py` implements checkpointed analysis phases:

```text
PLAN
RESEARCH
SYNTHESIS
UNDERWRITE
VALIDATION
MONITOR
```

Completed phases are persisted and skipped on retry.

Research is now additionally checkpointed **per specialist**. On resume, reports already persisted for that workflow cause those specialist roles to be skipped.

There are tests for simulated process death after a specialist and recovery without rerunning completed specialist roles.

Caveat: this is durable workflow recovery logic, but production execution is still too coupled to long synchronous work. A real multi-hour/day production agent should run bounded work units via a durable queue/waker (Cloud Tasks/Pub/Sub/another reliable dispatcher) instead of one giant HTTP request.

---

## 6. 100-question acceptance contract

`app/acceptance.py` contains exactly 100 numbered architecture acceptance questions across 10 categories:

1. purpose / anti-echo-chamber
2. progressive intake / minimum human input
3. persistent state / writes
4. evidence / truthfulness
5. source routing / tools
6. specialist orchestration
7. flagship architecture
8. sandbox / experiments
9. forking / alternatives
10. recovery / seamless interaction

The module has a guard that IDs are exactly `1..100`.

**Do not confuse the presence of this contract with proof that all 100 criteria have been production-verified.**

There is currently no `docs/ACCEPTANCE_100.md` file in the active branch despite an earlier conversational claim that such a document existed. The canonical acceptance list in the repo is currently `app/acceptance.py`.

---

## 7. What is proven

### 7.1 Clean CI

Latest clean branch verification before this handover:

- commit: `130c11ad0cf5c982d4293de9b170d37dd81e1ca5`
- GitHub workflow run: `32853920652`
- Python 3.11: success
- Python 3.13: success
- observed test count in the live-proof setup: **45 passed**

The suite includes architecture/globality/recovery tests in addition to the original MVP tests.

### 7.2 Google Cloud plumbing already established

The following Google Cloud setup has been created and had successful platform-level steps during deployment work:

```text
GCP project:           cogen-506607
project number:        257066508186
region:                us-central1
Artifact Registry:     cogen
registry URI:          us-central1-docker.pkg.dev/cogen-506607/cogen
Cloud Run service:     cogen
GitHub deploy SA:      cogen-github-deployer@cogen-506607.iam.gserviceaccount.com
Cloud Run runtime SA:  cogen-runtime@cogen-506607.iam.gserviceaccount.com
WIF pool:              github
WIF provider:          cogen-github
provider resource:     projects/257066508186/locations/global/workloadIdentityPools/github/providers/cogen-github
repo restriction:      RunZER0/cogen
```

GitHub -> GCP Workload Identity Federation authentication was proven to work during deployment attempts.

Artifact Registry authentication, Docker push, and Cloud Run revision deployment have also succeeded in prior `main` runs.

### 7.3 Docker packaging bug fixed

An earlier Dockerfile installed the Python project **before** copying `app/`, producing a wheel that could omit Cogen and allowing `uvicorn` to resolve another top-level `app` package.

The corrected Dockerfile copies the app before install and contains build-time assertions that:

- `app.main` resolves from `/app/app/...`
- `/healthz` exists
- `/readyz` exists

Preserve these assertions.

### 7.4 Country leakage regression

`tests/test_globality.py` verifies currency-specific core units for US/Australia/China and rejects Kenyan core leakage in the US fixture.

---

## 8. What is NOT proven — do not claim these yet

As of this handover:

- **No successful Gemini response is proven in the live agent proof.**
- No root ADK `create_venture` tool call has been proven from a live Gemini response.
- No root ADK `run_underwriting` tool call has been proven from a live Gemini response.
- The Austin live proof has not produced a completed underwriting decision.
- The full ADK -> Cogen tools -> specialists -> Neon -> deterministic underwriting path is not yet green.
- The public Cloud Run `run.app` health path has had unresolved 404/front-door behavior in prior deployments.
- Durable long-running execution via queue/work units is not yet implemented.
- Specialist roles are scoped research workers, not independent ADK subagents.

Do not phrase any of the above as complete merely because the code exists.

---

## 9. Exact live Gemini / ADK failure history

### Attempt A — CLI surface mistake

Workflow run: `32853244932`

The initial proof setup called a nonexistent command:

```text
google-agents-cli cmd-info --json
```

Installed CLI version in the run was `google-agents-cli 1.4.1`; it reported:

```text
Error: No such command 'cmd-info'.
```

This was a test-harness error, not a Cogen/Gemini failure. It was fixed.

### Attempt B — `agents-cli run` wrapper timeout

Workflow run: `32853441820`  
Job ID: `97819586738`

`agents-cli` successfully:

- started its local ADK server on port 18080
- accepted the Austin prompt
- entered the ADK execution path

After roughly 120 seconds, the CLI-side SSE HTTP reader timed out:

```text
requests.exceptions.ConnectionError:
HTTPConnectionPool(host='127.0.0.1', port=18080): Read timed out.
```

This did **not** prove a Gemini response or a Cogen tool call. It demonstrated that the CLI wrapper is unsuitable as the definitive proof for a potentially long underwriting turn.

### Attempt C — direct Google ADK `Runner.run_async()`

Workflow run: `32853920964`  
Job ID: `97821151927`

Harness: `scripts/live_adk_proof.py`

The run reached Google ADK's model request setup and logged:

```text
Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.
```

Then `runner.run_async()` produced no captured function-call/final-response event before the harness's explicit 900-second timeout.

After 15 minutes:

```text
Root node venture_underwriter was cancelled.
...
asyncio.wait_for(run_agent(), timeout=900)
...
TimeoutError
```

The exact conclusion is:

> **A Gemini/ADK model execution was attempted, but no successful Gemini response has been proven.**

Do not infer from “ADK started” that Gemini successfully answered.

Also note: because the proof timed out before `prove_neon_state()` ran, it is currently unknown whether a partial side effect (for example a created Austin Venture Twin) was persisted before cancellation. Check Neon before creating repeated live fixtures.

---

## 10. The next operator's first 60 minutes

Do these in order. Do not start by redesigning the architecture.

### Step 1 — get onto the correct branch and verify baseline

```bash
git fetch origin
git checkout flagship/global-agent
git pull --ff-only origin flagship/global-agent
uv sync --extra dev --python 3.13
uv run pytest
```

Expected current baseline: 45 passing tests on a clean GitHub runner.

### Step 2 — prove Gemini with the smallest possible request

Before ADK, Neon, search grounding, or underwriting, issue **one direct Gemini request** with no tools:

```python
import os
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
response = client.models.generate_content(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.7-flash"),
    contents="Reply exactly: COGEN_GEMINI_OK",
)
print(response.text)
```

Success criterion:

```text
COGEN_GEMINI_OK
```

Log elapsed time and the selected model. Do not log the API key.

If this hangs or errors, **stop debugging Cogen**. Fix Gemini/API/model/key connectivity first.

If the configured model ID is rejected, verify the currently supported Gemini model IDs against current official Google documentation/API rather than guessing.

### Step 3 — eliminate the dual-key ambiguity

The live workflow currently sets both:

```text
GEMINI_API_KEY
GOOGLE_API_KEY
```

to the same GitHub secret because ADK/client surfaces differed during testing. ADK explicitly logged that it chose `GOOGLE_API_KEY`.

Once the direct call works, standardize the environment intentionally. Do not keep two aliases unless required by the installed ADK/GenAI versions.

### Step 4 — verify whether the failed Austin run left partial Neon state

Before rerunning the same live fixture, query the Cogen repository/Neon for ventures where:

```text
country = United States
currency = USD
location contains Austin
```

Inspect events/workflow/specialist records. Do not blindly create duplicates.

### Step 5 — stage the ADK proof instead of jumping to full underwriting

Prove these progressively:

1. ADK root agent responds to a tool-free trivial prompt.
2. Root agent executes one cheap/read-only Cogen tool.
3. Root agent calls only `create_venture` for a complete intake and returns.
4. Root agent can inspect that persisted Venture Twin.
5. Direct research provider can make one Gemini grounded research call independently.
6. One specialist role completes and checkpoints.
7. Full five-specialist workflow runs.
8. Root agent calls `run_underwriting` and receives completion.

Instrument **before and after** every model call and every tool execution with monotonic elapsed time. The current 15-minute black box is unacceptable for production debugging.

### Step 6 — then rerun `scripts/live_adk_proof.py`

The full proof requires:

- visible ADK `create_venture`
- visible ADK `run_underwriting`
- persisted Austin/US/USD Venture Twin
- no KES/Kenya/KRA leakage
- all five specialist reports
- deterministic underwriting result
- evidence + source URLs
- validation tasks / critical unknowns

Only after this passes should the branch be promoted.

### Step 7 — reconcile with `main`

Do not overwrite the three main-only commits. Reconcile `deploy.yml` and `Dockerfile`, rerun CI, then perform the Cloud Run live E2E.

---

## 11. Live flagship fixture

The canonical non-Kenyan test fixture is in `scripts/live_adk_proof.py`:

```text
Business: specialty coffee shop
Location: Austin, Texas, United States
Currency: USD
Available capital: 85,000
Protected reserve: 15,000
Debt available: 0
Target monthly owner income: 7,500
Max acceptable loss: 30,000
Founder experience: first-time cafe operator
Founder time: full-time
Launch target: 6 months
```

Why this fixture exists:

- prevents Kenya-specific code from passing accidentally
- forces cost/rent/payroll/margin modelling
- forces current local market evidence
- forces US/Texas/Austin regulatory sourcing
- forces first-time-operator execution risk
- gives deterministic constraints with which to falsify the business

A passing system should be willing to reject the business or require validation. It should not produce a cheerful generic cafe plan.

---

## 12. Current Google / GitHub secret assumptions

Repository Actions secrets are expected to contain:

```text
GEMINI_API_KEY
DATABASE_URL
```

Never print their values.

The GCP WIF/deployment identifiers are non-secret infrastructure identifiers and are currently present in deployment configuration / prior repo setup.

The Neon URL is expected to be a Postgres URL accepted by `PostgresRepository` with:

```text
DATABASE_BACKEND=postgres
DATABASE_URL=<GitHub secret>
```

Live research expects:

```text
RESEARCH_MODE=live
GEMINI_API_KEY=<GitHub secret>
```

---

## 13. Cloud Run state and unresolved public URL issue

Platform-level deployment work already proved:

- GitHub WIF authentication
- Artifact Registry authentication
- Docker build/push
- Cloud Run revision deployment

Prior public `run.app` health probes returned 404 even though the Docker image's build-time assertions proved Cogen contains `/healthz` and `/readyz`.

`main` contains explicit Cloud Run ingress/default URL/public invoker diagnostics. The active branch contains a deployment workflow that also attempts an authenticated Cloud Run proxy path so public routing can be distinguished from container/runtime health.

Do not spend time changing FastAPI routes until the service is inspected through an authenticated/proxied path. The image itself was explicitly verified to contain the Cogen routes.

Deployment cost containment target has been:

```text
region: us-central1
min instances: 0
max instances: 3
CPU: 1
memory: 512MiB
request timeout: 300s
```

Long-term autonomous work should not rely on a 300-second HTTP request anyway.

---

## 14. Long-running execution direction

Current workflow recovery is durable, but execution needs to be decomposed for production.

Target shape:

```text
user / event
   -> enqueue bounded work unit
   -> Cloud Run worker claims workflow checkpoint
   -> one specialist / one research round / one deterministic phase
   -> persist checkpoint to Neon
   -> enqueue next unit if more work remains
   -> scale to zero safely between units
```

Google Cloud-native candidates include Cloud Tasks or Pub/Sub depending on final semantics.

The Venture Twin may live for months/years. A single model/request invocation should not.

---

## 15. Important files map

```text
app/agent.py
  Google ADK root agent + typed tools

app/domain.py
  Venture/state domain objects, including jurisdiction/currency fields

app/runtime.py
  repository + research provider selection

app/repository.py
  SQLite/Postgres durable repositories

app/orchestration.py
  finance/market/regulatory/execution/adversary scoped research workers

app/research.py
  offline/live Gemini research providers

app/context.py
  bounded specialist working-context construction

app/model_runtime.py
  direct Gemini research retry/fallback/health router

app/evidence.py
  evidence admission/dedupe/contradiction policy

app/workflow.py
  durable phase + specialist checkpoints/recovery

app/engine.py
  research ingestion + deterministic underwriting integration

app/simulation.py
  deterministic simulations/Monte Carlo

app/state.py
  events/workflows/research batches/reports/contradictions etc.

app/intake.py
  progressive intake logic

app/validation.py
  real-world validation task design

app/acceptance.py
  100-question machine-readable architecture acceptance contract

scripts/live_adk_proof.py
  current real Austin ADK/Neon proof harness

tests/test_globality.py
  US/Australia/China jurisdiction/currency regression

tests/test_recovery_granularity.py
  specialist-level crash/resume behavior

.github/workflows/live-agent.yml
  real ADK proof workflow on active branch

.github/workflows/deploy.yml
  Cloud Run verification/deployment workflow

Dockerfile
  verified-package build + health-route assertions
```

---

## 16. Product/architecture rules that should survive takeover

1. **No echo chamber.** Attack the founder's thesis.
2. **No generic business-plan output as the core product.**
3. **No fabricated material facts.** Unknown is a valid state.
4. **Do not ask the founder for facts the system can reasonably research.**
5. **Model output is not automatically evidence.**
6. **Official sources for legal/regulatory duties.**
7. **One canonical Venture Twin.** No competing agent memories.
8. **Deterministic financial/state logic stays outside the LLM.**
9. **Do not create subagents for theatre.** Scope them only where independent work/retry/context materially helps.
10. **Country-agnostic core.** Jurisdiction is explicit state.
11. **Keep simulations separate from real-world evidence.**
12. **Fork meaningful causal alternatives, not endless speculative branches.**
13. **Checkpoint before expensive/retryable work.**
14. **Irreversible money/legal actions remain user-approved.**
15. **Never claim a live integration is working because code exists. Prove it in logs/state.**

---

## 17. What “done” means for the next takeover milestone

Do not call the flagship live stack done until all of the following are true in one reconciled commit:

- [ ] direct minimal Gemini request returns successfully
- [ ] root Google ADK agent produces a visible model response
- [ ] root ADK tool-call event shows `create_venture`
- [ ] root ADK tool-call event shows `run_underwriting`
- [ ] Austin Venture Twin is persisted in Neon as US/Texas/USD
- [ ] all five specialist roles complete with persisted reports
- [ ] evidence admission rejects unsupported material claims
- [ ] deterministic underwriting runs (`simulation_runs > 0`)
- [ ] final decision/critical unknowns/biggest risks are returned
- [ ] no Kenya/KES/KRA leakage occurs in the US venture
- [ ] source URLs are inspectable
- [ ] CI is green on supported Python versions
- [ ] `flagship/global-agent` is reconciled with the three main-only commits
- [ ] reconciled `main` deploys a verified Cogen image to Cloud Run
- [ ] authenticated Cloud Run health/readiness reaches the Cogen app and Neon
- [ ] live Cloud Run E2E reaches Gemini successfully
- [ ] long-running workflow plan is decomposed into resumable durable work units before production claims of multi-hour autonomy

---

## 18. Suggested immediate next commit

The next useful commit should **not** be another architecture expansion.

It should be something like:

```text
Prove direct Gemini connectivity before ADK orchestration
```

Add a minimal probe script/workflow that:

1. loads only `GEMINI_API_KEY`
2. performs one ungrounded no-tool `generate_content` request
3. logs start/end/elapsed/model
4. requires exact response `COGEN_GEMINI_OK`
5. fails quickly with the actual API/model exception

Once that is green, progressively reintroduce ADK and Cogen tools as described above.

That is the correct state of affairs at takeover.
