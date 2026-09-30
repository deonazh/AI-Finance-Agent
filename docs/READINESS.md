# Readiness assessment

Assessment of the current source on **30 September 2026**. This is an engineering assessment of this repository, not a certification.

## Verdict

| Intended use | Assessment |
| --- | --- |
| Public source release with synthetic examples | Ready to publish with the documented scope and limitations |
| Local supervised demonstration | Working and tested for covered workflows |
| Controlled business pilot | Requires organizational data approval, reconciled extracts, review/retention arrangements, and tests on representative data |
| Shared confidential production deployment | Not ready: identity, authorization, shared-hosting/recovery controls, and live-model acceptance remain outstanding |

The source release supports the documented local workflows. Shared production use requires the additional controls and acceptance evidence below.

## Evidence

- The current offline suite was rerun: **213 tests passed**.
- Dependency consistency was rechecked with `pip check`.
- The recorded fresh environment installation, sample reconciliation, app interactions, and browser checks are detailed in [VALIDATION.md](VALIDATION.md).
- Source documentation checks can run independently; checking tracked Git contents also requires a working Git installation.
- Live model calls, hosted CI, the container runtime, security testing, and production concurrency have not been validated.

## Priorities before shared business use

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| 1 | Authentication and server-side dataset authorization | An unauthorized user cannot access another user's data, review notes, or generated artifacts |
| 1 | Production storage and review controls | Local planning persistence, revision snapshots, and stale-write rejection exist; add authenticated authorship, protected shared storage, retention, full database recovery, and independent approval enforcement |
| 1 | Approved data/provider handling | Hosting, provider exposure, tracing, retention, and deletion choices match the intended organization's requirements |
| 1 | Live-provider and semantic evaluations | Reviewed fixtures measure scope, numbers, attribution, unsupported causes, fallback behavior, latency, and cost for each enabled model |
| 2 | Operational protection | Per-user quotas, global spend limits, concurrency control, cancellation, redacted telemetry, and actionable alerts are exercised |
| 2 | Representative workload testing | Actual expected ledger sizes, multiple sessions, export sizes, and failure conditions meet defined resource/latency targets |
| 2 | Financial service/refactor work | Query/context handling, calculations, exports, and provider code have clearer boundaries and a consistent precision/currency contract |
| 3 | Broader enterprise capabilities | Revenue planning and cross-year horizons exist; add custom fiscal calendars, group consolidation, full balance sheets, ingestion connectors, and currency translation only with explicit requirements and tests |

The order depends on the intended deployment. Adding more features alone does not establish production readiness.

## Implemented planning scope

The FP&A workspace includes budget inputs/allocation, revenue/workforce/capital drivers, forecast baselines, rolling versions, scenario comparisons, cash projections, local review states, persistent revision snapshots, a planning Copilot with local reports and optional intent routing, and export/restore. See [PLANNING.md](PLANNING.md). This does not establish feature parity with a commercial enterprise suite. There is no authenticated approval workflow, group consolidation, full balance sheet, statutory tax engine, or tenant isolation.

## Specific technical debt

The chatbot module currently combines many responsibilities in over 3,600 lines. Calculation and guardrail helpers use multiple representations and tolerances; a legacy `Variance_USD` name is misleading for a multi-denomination reporting app. UI tests do not replace browser layout or accessibility checks. The optional analyst model call should be evaluated for value because Python already computes its numerical reference.

The strongest next design improvement is a typed query/result contract that carries metric, value, entity, period, currency, and dataset identity. Coupled with deterministic financial services, this would support stronger semantic attribution checks and a cleaner provider boundary. It is proposed work, not already implemented.

## Source release and deployment

The source distribution contains application code, dependency manifests, tests, synthetic examples, screenshots, and product documentation. Publish or clone the extracted source as a normal repository so its README, code, and CI workflow are available directly.

Keep credentials, actual business ledgers, review backups, and generated working papers out of the public repository. Run the [repository checks](VALIDATION.md#reproduce) before publishing and validate the hosted CI workflow after publication. Deployment requirements are documented in [DEPLOYMENT.md](DEPLOYMENT.md).
