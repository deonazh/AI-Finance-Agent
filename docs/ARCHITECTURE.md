# Architecture and design decisions

## Planning architecture

`epm.py` validates a calendar-month planning contract and provides pure budget allocation, price × volume revenue, workforce/capital models, forecasting, scenarios, P&L, cash, comparison, and backtest functions. Stored monetary amounts are integer minor units after half-up input rounding. Actuals, budget, and forecast remain separate fields.

`epm_store.py` owns SQLite transactions, optimistic revision checks, draft/submitted/approved states, immutable-by-application approved versions, snapshot history, and checksummed backup/restore. A transaction writes the version and event together. Each operation opens and closes its own connection. The database is one local shared workspace, not a tenant isolation boundary; actor names are unverified labels.

`app.py` renders stateful top navigation above all summary cards and loads the selected page. `epm_ui.py` provides planning workbenches and the shared, expandable Copilot panel. `planning_chatbot.py` parses explicit plan scopes, keeps validated follow-up context, and runs deterministic reporting/export services. `planning_whatif.py` resolves percentage-change requests and dates, then calls the same planning/cash services for a primary preview and a separate illustrative budget-plus-forecast case. Budget deltas are calculated in integer minor units; matching forecast increments preserve existing forecast differences. Exports carry both cases and their assumptions. Optional structured model output selects a report enum only; it never supplies scope, amounts, or writes. Proposals carry the source-plan fingerprint, require explicit review, and save only against the expected database revision. The planning engine does not expose code execution or arbitrary formulas.

Planning conversations are bound to plan ID, saved revision, and payload fingerprint. A new saved revision resets messages and downloads; moving between planning tabs preserves them. Reporting chat is isolated from planning chat. The UI limits history to 40 messages, retains five Excel downloads, and previews 12 table rows without a nested vertical scroller.

The [planning guide](PLANNING.md) defines the financial model, supported inputs, calculations, and limits.

## Actuals data flow

`data_engine.py` accepts an uploaded monthly ledger, validates the input contract, and recalculates derived fields. `app.py` applies the reporting year/cutoff and dashboard filters. The resulting frames feed four consumers: charts, Copilot, the review register, and executive analysis.

`dashboard.py` computes charts from the same frames. The waterfall includes an “Other movements” residual when the largest displayed accounts do not explain the entire change. A content fingerprint invalidates generated outputs when their data context changes.

`reporting.py` uses a Python `ContextVar` for currency formatting. Provider and UI work run within a scoped context rather than sharing a mutable global currency across sessions. This is denomination display, not currency translation.

## Agent boundaries

The executive workflow has two actual graph nodes:

1. **Data analyst:** aggregate budget, actuals, variance, materiality counts, departments, and leading drivers in Python. Optional structured model output must preserve calculated values and source classifications.
2. **Executive storyteller:** turn validated analysis into a management briefing. The resulting prose passes a numeric-reference check; a failure retains the deterministic briefing.

LangGraph orchestrates the sequence. A sequential implementation maintains the same interface if LangGraph is unavailable. There is no need for autonomous planning to calculate a variance.

Copilot uses one tool-calling model loop when configured. Labels such as `Excel_Report_Agent` and `Variance_Investigation_Agent` identify routing outcomes; they are not independent background agents. Its bounded tools profile, filter, aggregate, compare, and export data. The model receives no shell, unrestricted Python, or file-editing tool.

Tool arguments preserve recognized requested entities, periods, directions, and source scope. Unknown tools and unrequested FX lookups are rejected. Request size, history, call counts, and provider time are bounded; see [Copilot reliability](COPILOT.md) for exact limits and caveats.

After the model finishes tool use, the answer is validated immediately. A tool-free answer or failed numeric check is replaced by local routing. Financial reference values come from tool results, not model-supplied arguments.

## Review state and exports

`review.py` creates an exception register from material rows. Stable row identifiers include the ledger grain and amounts. Editable fields are owner, status, due date, explanation, and next action. A JSON snapshot allows a reviewer to resume against the same ledger.

`exports.py` creates in-memory Excel files, freezes headers, adds filters, formats amounts, and keeps potentially executable text literal. The month-end pack includes selected ledger rows, department totals, review notes, calculation conventions, generation time, source, filters, thresholds, currency, and a dataset fingerprint. A previously generated management brief is included if available for that view.

Copilot's detailed investigation workbook deliberately includes a full-reporting-year detail sheet for the selected driver. That broader sheet is labeled separately from the filtered investigation. A generic extract's Profile sheet reconciles to the extracted detail, not the whole source ledger.

## Controls and their limits

| Control | What it checks | What it cannot prove |
| --- | --- | --- |
| Upload validation | Schema, grain, amounts, period/year bounds, denomination consistency | ERP completeness or whether an amount was posted correctly |
| Deterministic arithmetic | Report values are derived from uploaded budget/actual | Accounting treatment or source-system truth |
| Pydantic schemas | Expected fields and finite structured values | Semantic validity of narrative statements |
| Analyst reference comparison | Amounts/counts/classifications preserve the calculated reference | Business rationale for a classification supplied in the source |
| Narrative number check | Recognizable financial values appear in trusted results | Correct attribution, sign interpretation in every prose form, or causality |
| Review fingerprint | Backup matches the same keys, amounts, and currency | Authorship, authorization, or an immutable history |
| Formula-safe export | Strings are not written as Excel formulas | Safety of arbitrary external spreadsheet files outside this workflow |

These controls reduce particular errors. They do not make LLM output inherently authoritative. The person reviewing the pack remains responsible for validating explanations and deciding actions.

## Important tradeoffs

- **Reporting horizons:** the actuals dashboard selects one year; planning supports calendar-month horizons of up to 36 months and separate year views. Neither implements a custom fiscal calendar.
- **Two state models:** planning versions/review snapshots persist locally in SQLite; actuals exception notes remain session-based. Authenticated collaborative approval remains future work.
- **Deterministic fallback:** supports offline demos and predictable business routes; it cannot understand every natural-language question.
- **Two-decimal reporting:** appropriate for the sample management-reporting use case; not a ledger-posting or exact-decimal accounting engine.
- **Bounded tool calling:** narrows available operations, but provider answers still require evaluation and human review.
- **Modular monolith:** straightforward local deployment, but the chatbot module is over 3,600 lines and mixes parsing, data tools, exports, provider execution, and formatting. Separating these responsibilities is a concrete maintainability priority.

## Extension path

A production deployment should add authenticated roles, department/dataset authorization, protected shared persistence, tamper-resistant audit controls, approved retention/provider policies, complete recovery testing, observability, workload testing, and an explicit fiscal-calendar dimension. ERP connectors should first produce the validated monthly contract rather than write directly into an LLM prompt.

For a module-by-module implementation walkthrough and worked example, see the [system design guide](SYSTEM_DESIGN.md). The [readiness assessment](READINESS.md) separates implemented controls from remaining production work.
