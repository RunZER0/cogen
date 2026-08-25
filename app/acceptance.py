"""Machine-readable 100-question Cogen flagship acceptance contract."""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    id: int
    category: str
    question: str
    implementation: str
    verification: str

QUESTION_SETS = {
    'Purpose & anti-echo-chamber': [
        'Can Cogen prevent premature capital/time commitment rather than merely generate a plan?',
        'Does Cogen actively try to falsify founder assumptions?',
        'Can it resist an echo chamber and preserve contrary evidence?',
        'Can it return a reject/do-not-proceed outcome despite founder enthusiasm?',
        'Can it distinguish a bad venture from a bad configuration?',
        'Can it identify the exact assumptions that killed a configuration?',
        'Can it preserve valid surviving evidence when changing configuration?',
        'Does insufficient evidence result in NEEDS_DATA/CONDITIONAL instead of invented certainty?',
        'Does the state distinguish evidence, assumptions, founder inputs, model outputs, and unknowns?',
        'Can every consequential recommendation be traced to durable evidence/model state?',
    ],
    'Progressive intake & minimum human input': [
        'Can Cogen begin from a bare idea without a giant questionnaire?',
        'Does it ask only materially decision-relevant questions?',
        'Is intake progressive rather than all-at-once?',
        'Can ascertainable facts be researched instead of asked from the founder?',
        'Does it minimize founder input without manufacturing material facts?',
        'When online evidence is unavailable, can it request the smallest realistic field-validation task?',
        'Can founder assertions remain distinct from verified evidence?',
        'Can intake requirements vary with venture type and current evidence gaps?',
        'Can research outcomes change what Cogen asks next?',
        'Can a founder return later without re-entering durable known facts?',
    ],
    'Persistent state & writes': [
        'Is venture state durable and independent of the chat transcript?',
        'Are consequential state writes explicit and inspectable?',
        'Are founder constraints stored separately from researched evidence?',
        'Are assumptions first-class records with confidence, provenance, materiality and dependencies?',
        'Is evidence immutable/deduplicated by a stable fingerprint rather than silently overwritten?',
        'Can a decision retain the assumptions/reasoning snapshot that produced it?',
        'Can Cogen explain that a recommendation changed because underlying state changed?',
        'Can upstream changes invalidate only affected downstream assumptions?',
        'Can conflicting evidence coexist instead of one item silently deleting another?',
        'Can the venture be reconstructed from durable snapshots plus an append-only event history?',
    ],
    'Evidence & truthfulness': [
        'Does each material claim carry source/confidence or remain unresolved?',
        'Do regulatory/legal claims require authoritative official evidence?',
        'Can copied/unsourced model claims be rejected from canonical evidence?',
        'Can evidence carry freshness/staleness state?',
        'Can evidence retain access/publication timestamps where known?',
        'Can contradictory material evidence be recorded for resolution?',
        'Does Cogen refuse to invent prices, rent, licences, salaries, margins or market size?',
        'Are source types assigned differentiated strength rather than treated equally?',
        'Does uncertainty work prioritize weak high-impact assumptions?',
        'Can research stop once additional evidence is unlikely to change the decision?',
    ],
    'Source routing & tools': [
        'Can Cogen choose source classes appropriate to the question rather than generic search only?',
        'Can it route regulatory work toward official portals and registries?',
        'Can it route market/location work toward maps, listings, reviews and local evidence?',
        'Can it route finance work toward quotes, listings, fees and numeric evidence?',
        'Can it route execution work toward actual suppliers/providers and contactable entities?',
        'Can provider candidates be evaluated for relevance, locality and current reachability?',
        'Can Cogen identify when physical validation is required because online evidence is inadequate?',
        'Can useful discoveries be persisted so they are not repeatedly researched?',
        'Can blocked/unavailable sources leave an explicit unresolved gap rather than trigger fabrication?',
        'Can the final state enumerate important unanswered questions and why they remain unanswered?',
    ],
    'Specialist orchestration': [
        'Are multiple specialists used only for materially distinct roles?',
        'Do all specialists read/write one canonical Venture Twin rather than separate memories?',
        'Is there an adversarial specialist whose mandate is to find reasons the venture fails?',
        'Is financial analysis a distinct specialist responsibility?',
        'Is market/customer/competition analysis a distinct specialist responsibility?',
        'Is regulatory/legal analysis constrained by stricter source policy?',
        'Is execution/vendor/provider research a distinct specialist responsibility?',
        'Can specialists disagree without one agent overwriting another agent evidence?',
        'Does synthesis resolve outputs by admissibility, evidence strength and materiality rather than voting?',
        'Can a specialist failure be retried without replaying already-completed workflow phases?',
    ],
    'Flagship architecture': [
        'Is there one canonical Venture Twin source of truth?',
        'Is the domain model composed of first-class objects rather than one opaque LLM JSON blob?',
        'Are assumptions, evidence, decisions, events, workflows, forks, contradictions and experiments first-class?',
        'Are probabilistic LLM tasks separated from deterministic financial computation?',
        'Can the financial model be reproduced without an LLM call?',
        'Can model-proposed evidence enter state only through deterministic validation/mutation boundaries?',
        'Do state writes support idempotency/preconditions rather than blind duplicate appends?',
        'Are irreversible execution gates explicitly separated from research/model suggestions?',
        'Can material events trigger re-underwriting of the affected venture state?',
        'Can the architecture be explained as uncertainty reduction plus safe execution rather than agent theatre?',
    ],
    'Sandbox & experiments': [
        'Can Cogen run safe computational experiments without mutating canonical state?',
        'Can it execute thousands of deterministic Monte Carlo runs over venture economics?',
        'Can it expose sensitivity/risk concentration around uncertain assumptions?',
        'Can it compare alternative configurations on the same baseline?',
        'Can it model competitor-entry shocks?',
        'Can it model rent/price/payroll/demand/regulatory-cost shocks?',
        'Are sandbox branches disposable without contaminating evidence?',
        'Can experiment outputs retain baseline/scenario assumptions and results for inspection?',
        'Are simulation values structurally prevented from becoming real-world evidence?',
        'Can Cogen design a real-world validation experiment when computation cannot resolve an uncertainty?',
    ],
    'Forking & alternatives': [
        'Can a venture fork at a meaningful decision point?',
        'Does a child fork retain applicable evidence from its parent?',
        'Can supermarket-to-minimart style changes preserve unaffected research?',
        'Does changing location invalidate location/demand/competition-specific evidence in the child only?',
        'Can a fork become the working configuration without destroying parent history?',
        'Are forks created for causal failure variables rather than endless speculative alternatives?',
        'Can Cogen avoid overwriting the parent when evaluating a second location/configuration?',
        'Can it identify what evidence would discriminate between two forks?',
        'Can shared fatal assumptions remain visible across alternative forks?',
        'Can rejected/archived forks remain recoverable if future material conditions change?',
    ],
    'Recovery & seamless interaction': [
        'Can a failed process resume from the last durable workflow checkpoint?',
        'Are workflow starts and phase writes idempotent under retry?',
        'Are workflow/job states explicit (running, waiting, blocked, retryable, failed, complete, superseded)?',
        'Are transient failures distinguished from terminal failures?',
        'Can a returning founder see durable completed work, changed facts, uncertainties and required actions?',
        'Does the product behave as one persistent venture relationship rather than disconnected chats?',
        'Can monitoring focus on material changes instead of noisy notifications?',
        'Can a material change name the assumptions/decision it affected?',
        'Does correctness rely on durable state rather than replaying a long context window?',
        'Can the end-to-end system tell the founder whether the venture deserves money, what remains unknown, what to do next, and recover after failure without manufactured certainty?',
    ],
}

EVIDENCE = {
    'Purpose & anti-echo-chamber': ('app/engine.py; app/orchestration.py; app/domain.py', 'tests/test_engine.py; tests/test_architecture.py'),
    'Progressive intake & minimum human input': ('app/intake.py; app/validation.py; app/state.py', 'tests/test_architecture.py'),
    'Persistent state & writes': ('app/state.py; app/repository.py; app/domain.py; app/evidence.py', 'tests/test_repository.py; tests/test_architecture.py'),
    'Evidence & truthfulness': ('app/evidence.py; app/source_router.py; app/research.py', 'tests/test_architecture.py'),
    'Source routing & tools': ('app/source_router.py; app/research.py; app/validation.py', 'tests/test_architecture.py; live-e2e'),
    'Specialist orchestration': ('app/orchestration.py; app/workflow.py; app/evidence.py', 'tests/test_architecture.py; live-e2e'),
    'Flagship architecture': ('app/domain.py; app/service.py; app/simulation.py; app/state.py', 'tests/test_engine.py; tests/test_architecture.py'),
    'Sandbox & experiments': ('app/sandbox.py; app/simulation.py; app/validation.py', 'tests/test_engine.py; tests/test_architecture.py'),
    'Forking & alternatives': ('app/forks.py; app/evidence.py; app/state.py', 'tests/test_architecture.py'),
    'Recovery & seamless interaction': ('app/workflow.py; app/service.py; app/state.py; app/main.py', 'tests/test_api.py; tests/test_architecture.py; live-e2e'),
}

CRITERIA = []
for category, questions in QUESTION_SETS.items():
    implementation, verification = EVIDENCE[category]
    for question in questions:
        CRITERIA.append(AcceptanceCriterion(len(CRITERIA) + 1, category, question, implementation, verification))

if [item.id for item in CRITERIA] != list(range(1, 101)):
    raise RuntimeError("Acceptance contract must contain criteria 1..100 exactly once")
