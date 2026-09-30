# FP&A planning workspace

The **Planning**, **Budgeting**, and **Forecasting** tabs cover budget preparation, revenue and cost planning, forecast updates, scenario comparison, cash projections, local review states, and reporting. **Reporting** retains the existing expense-analysis dashboard and copilot.

Planning runs without an API key. Finance Copilot is available beside every planning tab, with shared conversation history, follow-ups, scoped Excel downloads, and a full-width view. Optional OpenAI/Anthropic routing can classify unfamiliar report questions; financial calculations remain in Python.

## Start a plan

1. Select **Planning** in the top navigation.
2. Open **Create or import a plan** and select **Service business demo**.
3. Select **Create demo plan**. The plan is saved locally immediately.
4. Inspect the monthly revenue and operating-profit outlook in **Planning**.
5. Select a reporting year to distinguish annual results from the full planning horizon.

The synthetic example covers January 2026–December 2027, with actuals closed through June 2026. It includes revenue, delivery costs, payroll, overheads, depreciation, opening cash/receivables/payables, workforce schedules, and an equipment purchase. All later actuals are unknown, rather than zero.

Additional starting points:

- **Blank budget:** create a department/account and add the remaining accounts in Budgeting. Closed-period actuals remain missing until supplied; choose zero closed months for a purely prospective budget.
- **Planning CSV / XLSX:** upload the [service-business example](../examples/planning_service_business.csv), or a table following the contract below. This imports monthly amounts; it does not reconstruct workforce/capital schedules or cash assumptions from the numbers.
- **Loaded expense ledger:** first load data in Reporting, then create a plan from that source. Each account must have all 12 budget months. Select a cutoff to exclude future actuals; imported accounts begin as Opex. Revenue is not inferred.
- **Restore planning backup:** restore a downloaded JSON into a new draft. Existing plans remain untouched. A backup does not grant an approved status.

## Budgeting

Filter by department and account, select Budget or Forecast, edit the monthly grid, and save with a change explanation. Forecast editing only exposes open months. Actuals are protected in this editor.

Use **Add a planning account** to define a department, account, category, and monthly baseline. Classify revenue positively and expenses positively. Category choices are Revenue, COGS, Payroll, Opex, and Depreciation. Corrections/credits may be negative.

Use **Allocate an annual budget** to distribute a target over a full calendar year. Twelve nonnegative weights determine monthly phasing. Equal weights spread evenly; zero weights receive zero. The allocation reconciles to the annual amount down to the minor unit, including rounding remainders.

Forecast and scenario versions retain a locked baseline budget. Approved versions are locked completely. Create a new budget from the planning CSV or blank starting point when you need independently revised targets; an approved budget's linked scenarios preserve its original targets.

## Actuals refresh

In Budgeting, expand **Refresh actuals and advance the close**. Upload `period,department,account,actual` and select the cutoff. An optional `currency` must match the plan. Keys must already exist in the plan; duplicate or future-period input is rejected.

The import updates supplied rows. It does not convert omitted rows to zero. Missing closed-period actuals make the affected monthly outlook totals unavailable and prevent submission. Explicitly enter zero where a zero balance is established by the source. Restating actuals creates a saved revision.

After advancing the close, confirm the new cash, receivable, and payable opening balances in Cash planning. Cash projections and review submission reject balances dated to an earlier cutoff.

## Forecasts and scenarios

Supported baseline methods:

| Method | Rule | Required data |
| --- | --- | --- |
| Budget | Copy each future month's budget | Budget for every plan line |
| Last actual | Carry each account's latest closed monthly actual forward | Complete latest closed month |
| Trailing 3 months | Use the mean of the latest three closed monthly actuals | Three complete closed months per account |

A one-time uplift can be applied to the generated baseline. It is not compounded monthly. No future actuals are used. The operation replaces future forecast values across the selected plan; reapply business-driver schedules afterward if those should take precedence.

For alternatives, create a **Scenario** in Plan review. Select department/account/category and a future month range, then preview a percentage change and/or an additional amount per matching line per month. Inspect the resulting changes and explicitly apply or discard them.

Saved-version comparison requires matching currency and account/category coverage within the common monthly range. Rolling horizons and different close dates are supported: only overlapping months are compared, the period range is shown, and each row identifies its current and prior actual/forecast basis. Positive profit impact means an improvement. A notice identifies different actual datasets or close dates so newly closed periods and actual restatements are not mistaken for scenario effects.

**Rolling forecasts:** use Plan review → Roll the forecast horizon. The new forecast preserves overlapping monthly inputs and can extend across calendar years. Newly introduced budget months use the preceding year's same-month budget when available, otherwise the last known budget. These are provisional seeds, not approved extensions of the source budget. Review them and regenerate forecasts; reapply workforce/capital schedules where needed. Horizons are limited to 36 months.

## Business drivers

**Revenue:** select a Revenue account, starting monthly units/customers, price per unit/customer, and monthly volume growth. The model calculates `units × price × (1 + monthly growth)^month offset` for the selected open-month range. Price is fixed. The saved assumption explains the inputs and scope.

**Workforce:** enter department/account, start/end month, FTEs, monthly salary per FTE, benefits/burden percentage, and annual raise percentage. The formula is `FTE × monthly salary × (1 + burden) × (1 + raise)^anniversaries`. Fractional FTEs are allowed. Raises apply on each start-date anniversary. Map positions to Payroll category accounts.

**Capital:** enter department/account, asset name, in-service month, purchase cost, and useful life in months. Map to Depreciation category accounts. Straight-line depreciation begins in the in-service month, assumes no residual value, and allocates rounding remainders so the full depreciable cost reconciles. The purchase is a cash outflow in that month.

Workforce and capital schedules **replace** future forecast totals for their mapped accounts. Enter the entire planned workforce or asset base for each mapped account, including existing items. Deleting the final schedule entry clears the account's future modeled total. Reapplying a schedule does not add the same expense a second time. Actuals and baseline budget are preserved.

## Cash planning

Supply opening cash, receivables, and payables immediately after the actuals cutoff. Configure collection and supplier-payment lags of 0–3 whole months. Opening receivables/payables settle in the first forecast month; new forecast revenue and COGS/Opex follow their lags. Payroll is immediate, and depreciation is excluded from cash payments.

The projection reports collections, supplier payments, payroll, capital purchases, other outflows, financing, closing cash, and remaining receivables/payables. It identifies the first negative closing-cash month within the horizon.

Other outflows and financing are explicit monthly assumptions. Enter relevant taxes, debt service, or distributions there. The model does not automatically calculate VAT, statutory tax, inventory, loan amortization, or a complete balance sheet. Cash timing uses simplified whole-month lags; it is not invoice-level treasury forecasting.

## Finance Copilot on every planning page

Copilot reads the selected **saved** plan and revision. Page filters and unsaved grid/form changes do not change chat scope; name an exact department/account and calendar period in the question. Replies state the plan, currency, period, and actuals cutoff. The sidebar selects the plan; **Reload latest saved revision** refreshes changes saved in another session.

| Request | Result |
| --- | --- |
| `summarise plan` | Monthly budget and outlook, including operating profit and EBITDA |
| `show budget for Sales in 2026` | Baseline budget for that department and year |
| `what about 2027?` | Same report and department for the new year |
| `export that to Excel` | Complete preceding report with scope and plan fingerprint |
| `show forecast for Q3 2026` | Future forecast months in that quarter; closed actuals excluded |
| `show cash for 2027` | Cash for that year, calculated using the complete horizon first |
| `show workforce` / `show capital plan` | Saved schedules, with amounts in reporting currency |
| `top variances` | Up to 10 largest known unfavorable monthly differences |
| `show assumptions` | Saved notes for the whole plan |
| `forecast accuracy` | Past-only one-step baseline evaluation detail |
| `What if I increase Marketing budget by 8% for next month?` | One-month budget preview, profit/variance impact, and a separate illustrative forecast/cash case |
| `increase Marketing forecast by 8% for October 2026` | One-month forecast proposal with profit and cash timing |
| `what about November 2026?` after a what-if | Same scope, field, and percentage in November |
| `increase Marketing by 8%` | Forecast proposal for all open months; the defaults are stated |
| `decrease Opex by 5%` | Reviewable change across future Opex lines |

Use a year, a month with its year, a quarter with its year, or `2026-07 to 2026-12`. Read-only report queries require explicit dates; what-if queries support relative dates as explained below. A new standalone question starts with the full plan; follow-ups beginning with “what about”, “how about”, “and”, or “same” inherit the previous validated scope. “Export that” needs a previous successful report. Unknown explicit department/account scopes and ambiguous periods return clarification instead of a broader result. Cash requires the full company scope because opening balances are company-level.

**Reading and downloads:** Select **Expand** for full-width chat and **Back to plan** to return. The composer stays above the latest reply; earlier messages are collapsed. Tables stay inside the panel and scroll sideways. Planning replies preview up to 12 rows; Excel includes the full selected report. The session retains 40 messages and 5 workbooks. Download conversations and workbooks before clearing chat. Switching planning tabs keeps history; switching plans or saving/reloading a different revision clears it. Reporting has a separate conversation and ledger scope.

**Optional providers:** Local mode handles supported reports and natural-language percentage what-ifs without an API key. Choose OpenAI or Anthropic under Assistant settings to classify otherwise unrecognized questions. The router receives the current question only, not the stored plan or table amounts; questions can themselves contain confidential details. A validated report enum is its only output. Scope parsing, calculations, exports, and all mutations remain local. Missing credentials, refusals, invalid output, or provider failures fall back to local guidance. Live-provider quality has not been evaluated for this release.

### Budget and forecast what-ifs

Ask **“What if I increase Marketing budget by 8% for next month?”** The answer shows the current and proposed amount, the selected month, budget operating profit, forecast operating profit, favorable profit variance, and cash impact. Calculations use saved plan values. The question creates a preview; nothing is saved yet.

A **budget** is an allowance or target. Raising it does not mean the business will spend more. A budget-only preview leaves forecast amounts, actuals, and projected cash unchanged. It can improve the comparison against budget without improving the business's performance.

The answer also calculates a separate **illustrative spending case**: add the budget increment to the same forecast lines. It does not overwrite a different existing forecast with the new budget. Extra expense reduces operating profit; cash changes when the saved payment lag says it is paid. A Revenue change uses collection timing, Payroll is paid in-month, and Depreciation is non-cash. No extra sales are assumed from higher Marketing spend. Effects past the plan horizon remain in closing payables/receivables instead of being reported as cash paid/collected. Stale cash assumptions produce a warning rather than a cash estimate.

For the unmodified service-business demo, `increase Marketing budget by 8% for October 2026` raises SGD 15,600 to SGD 16,848. Budget operating profit falls by SGD 1,248; the forecast and cash stay unchanged. If the additional SGD 1,248 is also spent, forecast operating profit falls by SGD 1,248 and cash falls in November because the demo uses a one-month supplier payment lag. This is a model assumption, not a prediction of Marketing's return.

**Dates and scope:** “next month” means the next calendar month using the server's date; the answer displays that date and the resolved month. “First forecast month” or “next open month” means the month after the saved actuals cutoff. You can also specify `October 2026`, `Q4 2026`, `2027`, or `2026-10 to 2026-12`. Chat what-ifs protect all closed months. Requests outside the horizon are rejected without clipping or broadening. The demo horizon is fixed at 2026–2027, so use an explicit demo month if today's relative date is outside it.

Name an exact account, department, or category. For a shared account name, use `Sales / Payroll` or `Payroll in Sales`. Unknown or ambiguous scopes require clarification. Natural wrappers such as “what would happen if we reduced…”, and `%` or `percent`, are supported for percentage changes. More complex assumptions belong in the planning forms. If you omit the field, it defaults to **forecast**; if you omit the period, it covers **all open months**. Both defaults appear in the preview. A fresh what-if does not inherit prior report filters.

**Review and apply:** Inspect **Affected monthly inputs**, then choose **Apply reviewed proposal** or **Discard proposal**. A budget proposal applies budget values only; the illustrated spending case is never applied with it. To change expected spending, ask for a **forecast** what-if. Application requires Draft status, an editable target, a matching plan fingerprint, and the expected database revision. Locked-budget and submitted/approved versions can be explored but cannot apply a prohibited edit. Forecast/Scenario versions retain their locked baseline budgets.

Follow with `what about November 2026?` to retain the field, percentage, and account with a new period. A bare month inherits a year only when the preceding what-if covers one year. `export that to Excel` downloads the proposed line changes, primary and illustrative profit/cash reports, and assumptions. The export is still a preview. A new saved revision invalidates the conversation and proposal. The assistant cannot approve plans, execute code, or silently save changes.

## Review, storage, and exports

Plans progress through **Draft → Submitted → Approved**. A submitted plan may be returned to Draft. Approved plans stay locked; create a separate version for further changes. Every successful save and transition records a revision snapshot, timestamp, review label, explanation, and content fingerprint. Concurrent stale edits fail without overwriting newer work.

This is a **local review workflow**. Review names are user-entered labels, not authenticated identities. The app does not enforce independent approvers or department-level authorization. The database is accessible to all sessions of the same deployed app; do not expose this workspace publicly with private plans. Database administrators can alter local history.

Storage defaults to `data/planning.sqlite3`. Configure `FPA_DATABASE_PATH` in the server environment for another protected, persistent path. Plans survive browser sessions and app restarts when the same database remains available. Ephemeral hosting requires a persistent volume or a different storage backend. The database and backups are excluded from the source package.

Use **Document planning assumptions** to edit the saved assumption notes before review.

Download a JSON backup to restore a plan as a new draft. Its checksum detects accidental modification, not authenticated provenance. The included review history is informational; restored plans must be reviewed again. JSON preserves the selected plan and review-event metadata, not every earlier revision's full payload. For full workspace recovery, use a consistent SQLite backup of the database and test restoration.

The FP&A Excel workbook includes monthly P&L, Budget–Actual–Forecast detail, planning inputs, cash forecast, workforce/capital schedules, cash assumptions, assumptions, evaluation results, methodology, and version context. Schedule fields ending `_cents` use minor units; the main financial reports use reporting currency. Spreadsheet text is written literally.

## Planning input contract

| Field | Meaning |
| --- | --- |
| `period` | Calendar month as `YYYY-MM` |
| `department` | Nonblank department identifier |
| `account` | Nonblank planning account identifier |
| `category` | Revenue, COGS, Payroll, Opex, or Depreciation |
| `budget` | Finite monthly budget amount |
| `actual` | Finite posted amount; blank for future or missing actuals |
| `forecast` | Finite monthly forecast amount; closed-period values are retained but not used in the outlook |
| `currency` | Optional; every value must match the selected plan currency |

Each department/account requires exactly one row for every month in the selected horizon, with one consistent category. Duplicate keys/headers are rejected. The upload limit is 20 MB and 50,000 lines; this is a resource guard, not a performance benchmark. Currency selection does not perform FX translation.

Planning inputs round to two decimal places using half-up rounding, then store as integer minor units. This two-decimal currency contract excludes currencies requiring another minor-unit convention. Reports convert values for presentation; the existing expense-analysis engine has its own floating-point reporting implementation.

## Engineering and evaluation

`epm.py` contains validation and pure planning operations. `epm_store.py` owns SQLite transactions, version locks, optimistic revision checks, snapshots, and backup handling. `epm_ui.py` connects these services to the full-width Streamlit workspace. Existing reporting modules remain separate.

The historical evaluation runs expanding, past-only, one-step tests of last-actual and trailing-three-month methods. MAE is in reporting currency; WAPE divides absolute errors by absolute actuals and is undefined when its denominator is zero. The tests need at least four complete actual months. They compare simple baselines; they do not establish seasonality support, future forecast accuracy, or the quality of user-entered scenarios.

This release is an FP&A planning and analysis application. Group consolidation, eliminations, full statutory financial statements, non-calendar fiscal mappings, multi-currency translation, authenticated approval roles, ERP writeback, and production tenant isolation remain outside the implemented scope.

Generate a sample planning pack from the terminal:

```bash
python scripts/demo_plan.py
```
