# Validation record

Updated on **30 September 2026** for the local source release. This is a record of executed checks and their limits, not a claim of hosted production certification.

## Executed checks

| Check | Result |
| --- | --- |
| Fresh Python 3.11 environment | Created separately from the development environment |
| Full dependency snapshot installation | `requirements.lock.txt` installed successfully |
| Dependency consistency | `pip check`: no broken requirements |
| Offline regression suite | 213 tests passed, including actual Streamlit AppTest interactions |
| Natural-language what-ifs | 25 new calculation/UI regressions cover the reported Marketing question, budget-only application, date resolution, cash lags, illustrative forecast increments, locks, follow-ups, and workbook reconciliation |
| Planning services/storage | Allocation rounding, forecast history boundaries, revenue/workforce/capital models, cash reconciliation, scenario isolation, rolling versions, stale-edit rejection, review locks, and backup restoration covered |
| Planning UI | Creation, grid edits, forecast save, schedules, cash inputs, review transitions, cloning, exports, assistant preview/application, and persistence across a new app session covered |
| What-if browser update (30 September) | Exact Marketing budget question produced October-only preview; typed submission preserved scroll position; normal/expanded tables stayed inside panels at 390px, 1366px, and 1680px; follow-up/export persisted across tabs; applying changed one budget row with actuals/forecast preserved |
| Planning browser | Visible top navigation above KPIs; all six planning pages exposed Copilot; history/export persisted across tabs; normal and expanded tables stayed inside their panels at 390px, 1366px, and 1680px; typed questions preserved page scroll position |
| Sample report command | Generated an Excel pack from the committed 18-row SGD scenario |
| Sample control totals | Budget 226,000; actual 255,300; variance 29,300 SGD |
| Local server health | Streamlit health endpoint returned `ok` |
| Desktop browser | Dashboard and review rendered; typed chat replies preserved page scroll position and showed the response start, with no nested vertical scroller; full-width chat preserved conversation context; wide-table bounds checked at 390px, 1366px, and 1680px |
| Narrow viewport smoke check | 390px viewport rendered without document overflow or application exceptions; sidebar behavior requires interaction |
| Screenshots | Captured from the actual running application using synthetic data |

The 30 September update used the existing pinned Python 3.11 project environment; the separate fresh installation was recorded for the earlier release. Dependency consistency, all 213 tests, and source-only documentation/hygiene checks passed again.

Local documentation links and the basic source hygiene check passed. A later tracked-file recheck could not run because Git was unavailable in the validation environment; tracked-file verification remains required before publication. The documented three-question demo sequence was executed: its March IT Ops export reconciled to two rows, SGD 28,000 budget, and SGD 33,700 actual. The checker validates local Markdown targets, common source credential patterns, and prohibited tracked artifacts when a Git repository is present. It is a basic check, not a complete secret scanner or security audit.

The stateful-tab UI tests include a transport adapter because Streamlit 1.63 AppTest does not serialize tab-container widgets. It supplies the same selected-label state sent by a browser; separate Chrome checks exercise real tab clicks and resizing.

## Regression coverage

- Planning chat scope/date clarification, year/quarter/range queries, saved-revision context, scoped workbook reconciliation, missing actuals, cash timing, cost-only scope labels, read-only model routing, and reviewed proposals.
- Upload schema, empty inputs, duplicate monthly grain, malformed/nonfinite amounts, invalid periods/years, mixed currencies, and stale incoming variance columns.
- Materiality thresholds, zero budgets, direction/sign conventions, reporting currency, missing comparison periods, and reconciled waterfall totals.
- Analyst schema/reference comparisons and executive/chat numeric guardrails, including scaled currency amounts.
- Copilot follow-up context, exact entity matching, ambiguous entity guards, explicit source/period constraints, bounded tool use, role-correct history, and sanitized provider failures.
- Excel source/summary reconciliation, literal formula-like text, review JSON identity checks, and resolved-item validation.
- Streamlit upload/demo switching, reporting cutoff, stale-result invalidation, chat/export behavior, session bounds, review-note save, and pack generation.

## Not exercised

- Live OpenAI or Anthropic requests: provider integration tests use mocks, with no evaluation of current live-model quality, latency, or pricing.
- A hosted GitHub Actions run: the workflow is configured but must run after publication.
- Docker build/runtime: Docker was unavailable in the validation environment.
- Multi-user load, maximum-size uploads, authenticated access control, penetration testing, full database disaster recovery, container-volume persistence, or ERP integration. Local database reopen and individual plan JSON restore are tested.
- Full mobile/accessibility acceptance. The viewport smoke check is narrower than a usability or accessibility audit.

## Reproduce

```bash
python -m pip install -r requirements.lock.txt
python -m pip check
python scripts/check_repository.py
python -m unittest discover -s tests -v
python scripts/demo_report.py
```

Use synthetic data and keep provider credentials unset for offline checks. Existing `.env` files and configured tracing should not be part of a test checkout. The relative-date prompt tests pin the test clock so historical fixtures remain stable.

For confidential business deployment, complete the acceptance work in [DEPLOYMENT.md](DEPLOYMENT.md) and the provider evaluation in [COPILOT.md](COPILOT.md).
