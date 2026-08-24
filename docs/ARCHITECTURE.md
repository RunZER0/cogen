# Architecture notes

## Core design decision
The LLM never owns the source of truth. It reasons over and mutates a typed venture state. This makes changes inspectable and lets deterministic tests validate business logic independently from model variability.

## Components

### ADK orchestration
`app/agent.py` defines the Google ADK agent and the only allowed state-mutating tools. The agent can create/inspect ventures, run underwriting, add evidence, apply changes and complete roadmap steps.

### Domain + engine
`app/domain.py` contains the typed venture state. `app/engine.py` performs confidence scoring, uncertainty propagation, simulation, decisioning and gate transitions.

### Research
`app/research.py` defines a provider boundary. `OfflineResearchProvider` is deterministic and explicitly non-factual. `GeminiGroundedResearchProvider` uses Gemini Google Search grounding and a strict anti-fabrication prompt.

### Persistence
`app/repository.py` offers SQLite for local/test mode and Firestore for Cloud Run.

### API/UI
`app/main.py` exposes the HTTP API and `web/` is a no-build static frontend, keeping deployment small and demo-safe.

## Production extensions
- Pub/Sub or Cloud Tasks for durable long-running analysis jobs.
- Cloud Scheduler for periodic watch sweeps.
- Secret Manager for model/API credentials.
- Cloud Logging/Trace plus explicit tool/action audit logs.
- Authenticated per-user venture ownership.
- Provider-specific quote requests and application/form preparation with explicit approval boundaries.
