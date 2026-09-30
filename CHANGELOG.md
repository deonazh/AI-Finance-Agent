# Changelog

## 2026-09-30

- Fixed natural-language budget/forecast what-ifs, including “increase Marketing budget by 8% for next month”, with explicit date resolution and protected closed periods.
- Added before/after amounts, profit and variance impacts, cash timing, and a separate illustrative forecast case for budget changes. No revenue uplift is assumed from higher spend.
- Added what-if period follow-ups and Excel exports containing proposed inputs, both impact cases, and calculation assumptions.
- Kept proposals reviewable without saving, enforced target-specific locks in the UI, and added calculation, scope, cash, export, and application regressions.
- Updated the in-app guides and public documentation with a worked example and clear budget-versus-forecast explanations.

## 2026-09-23

- Added visible top navigation for Reporting, Planning, Budgeting, Forecasting, Business drivers, Cash planning, Plan review, and Guide; moved reporting tabs above the cards.
- Added planning Copilot to every planning page with validated report scopes, follow-ups, history, Excel exports, bounded optional intent routing, and full-width reading. Saved changes invalidate old chat context; proposals still require explicit application.
- Added unified in-app user guides and refreshed navigation documentation/screenshots.


## Unreleased — FP&A planning workspace

- Added full-width budgeting, forecast/scenario, business-driver, cash, review/version, and planning-assistant screens.
- Added monthly budget inputs, annual weighted allocation, price × volume revenue, workforce timing/benefits/raises, and capital depreciation models.
- Added forecast baselines, actual/forecast splicing, rolling cross-year horizons, version comparisons, and past-only historical baseline evaluation.
- Added SQLite persistence, atomic revision snapshots, stale-edit protection, approved-version locks, and new-draft backup restoration.
- Added reviewable planning-assistant proposals and FP&A workbooks with financial/model detail.
- Added synthetic planning inputs, a user guide, application screenshots, and planning calculation/storage/UI regressions.

## Earlier — actuals workflow and repository preparation

- Contained wide Copilot tables and long text inside replies; tightened chat spacing, header sizing, and message styling.

- Improved Copilot reading with a naturally sized latest reply, collapsible history, a composer above replies, and full-width chat that preserves session context.

- Added reporting-year/cutoff and currency controls, reconciled dashboard views, and clearer empty-data handling.
- Added a material-variance review register, ownership and explanation validation, ledger-bound JSON backup/restore, and a styled month-end reporting pack.
- Strengthened CSV/XLSX validation, zero-budget treatment, duplicate-header checks, numeric bounds, and formula-safe exports.
- Hardened Copilot source/entity/period scope, follow-up history, tool schemas, call budgets, provider failures, and external-rate validation.
- Added offline regression and Streamlit interaction coverage, a pinned environment snapshot, CI configuration, and a non-root container definition.
- Rebuilt the README with actual screenshots, a synthetic SGD scenario, architecture, deployment boundaries, validation evidence, and user and technical documentation.

Live-provider evaluation, hosted CI, and production deployment acceptance remain separate verification steps.
