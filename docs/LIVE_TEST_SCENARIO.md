# Live flagship test scenario

## Business
A first-time founder has KES 1,800,000 available to open a 60–80 m² neighbourhood minimart in Ruiru, Kiambu County, Kenya. The founder wants to preserve KES 150,000 as an untouchable reserve, has no debt facility, will work full-time in the business, and needs the venture to support KES 120,000 monthly owner income once stabilised. Maximum acceptable loss is KES 600,000 and the target launch window is four months.

## Why this scenario is used
This is deliberately a real operating business rather than an AI-demo toy. It forces Cogen to research and reason across premises rent, local competition, customer demand, average basket size, expected transactions per day, supplier economics, gross margin, payroll, utilities, shrinkage/spoilage, registration, tax, county and sector licensing, working-capital adequacy and launch dependencies.

## Required autonomous behaviour
1. Start from founder constraints and build the canonical Venture Twin.
2. Delegate scoped research to finance, market, regulatory, execution and adversarial roles.
3. Refuse unsupported material facts and preserve unresolved assumptions as unknowns.
4. Run deterministic underwriting and Monte Carlo stress tests from the evidence-backed state.
5. Identify the assumptions most capable of killing the venture.
6. Produce minimum real-world validation tasks for evidence that cannot reliably be obtained online, such as actual premises footfall.
7. Block irreversible capital gates while critical evidence remains weak.
8. Persist evidence, specialist reports, workflow checkpoints, decisions and events in Postgres.
9. Resume a partially completed analysis from its durable workflow checkpoint rather than restarting the venture.
10. Support an isolated alternative-location/configuration fork and sandbox shocks without contaminating canonical evidence.
11. Re-underwrite when a material fact changes.

## Live deployment acceptance
The production workflow must deploy Cogen to Google Cloud Run, connect to Neon Postgres, use Gemini in live research mode, run this scenario end-to-end and return a persisted venture with an underwriting result. The test is a failure if it fabricates a material source, treats a sandbox value as evidence, loses state across persistence boundaries, or proceeds through a money-at-risk gate while a critical assumption remains unresolved.
