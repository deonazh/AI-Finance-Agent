# Copilot reliability and operating boundaries

Copilot is a supervised assistant for the loaded monthly expense ledger. It can summarize, filter, compare periods, investigate material movements, and produce Excel extracts. It is not a financial posting service or a general autonomous agent.

## Reading and navigation

The question box stays above the latest response. Answers grow to their natural height, without a fixed-height scrolling transcript or automatic scrolling to the end. Earlier exchanges are grouped in a collapsed history. Wide tables scroll horizontally within the reply without widening the chat panel. Expand provides the full workspace width for tables; returning to the dashboard preserves the conversation, scope, and retained downloads.

## Request lifecycle

1. Validate the question length, reporting year, relative-period boundary, and recognizable named entities.
2. Resolve the default dashboard/full-ledger scope and applicable user follow-up context.
3. Use deterministic routing for supported local requests, or invoke the selected model with a fixed tool set.
4. Validate tool arguments, requested source, recognized entity/direction filters, and recognized reporting periods. Reject unknown tools and unrequested external FX lookups.
5. Execute Python data operations. Keep financial reference values from tool results; model-supplied arguments are not numeric evidence.
6. Check the final narrative's recognizable financial numbers against trusted results. Return the answer or a deterministic fallback, with response notes.

A model cannot execute arbitrary Python, issue SQL, access a shell, modify the ledger, or submit accounting transactions through these tools. Uploaded text is treated as untrusted data by the prompts. These boundaries reduce impact; they are not a claim that prompt injection or semantic errors are impossible.

## Resource controls

| Resource | Application limit |
| --- | --- |
| User question | 4,000 characters |
| Provider conversation context | Latest 12 user/assistant messages, up to 12,000 characters each |
| Tool rounds | Four, followed by at most one synthesis call |
| Tool invocations | Eight per request |
| Individual tool result | 100,000 characters before adding it to provider context |
| Provider call | Default 45-second timeout, configurable within 1–60 seconds; zero automatic retries |
| Request scheduling | Cooperative 120-second deadline checked between calls/tools |
| UI conversation | Latest 40 messages |
| UI Excel artifacts | Latest five referenced downloads |

The cooperative deadline does not interrupt in-flight provider requests or Python operations. The result limit is checked after tool execution, so it is not a memory bound on calculation itself. Session limits do not implement per-user quotas, shared concurrency control, or a global spending cap.

## Data scope

The dashboard's reporting year/cutoff bounds every source. “Full ledger” means that reporting ledger, not every year in the uploaded file. The Answer using control sets the default scope; explicit user wording may select raw, flagged, or current-view data.

Only user messages supply follow-up filters. Assistant output tables are never mined for entity context, and assistant messages remain assistant-role messages when sent to a provider. A new question with an independent topic stops older filter inheritance. Changing the reporting view clears stale chat and exports.

Entity filters use normalized exact equality. Common unknown-name requests are rejected before answering. Multiple departments/accounts must be selected through the dashboard for a multi-entity view. Name recognition is rule-based and not exhaustive: inspect the reported scope for unfamiliar phrasing, and use dashboard filters when precision matters.

A missing period is reported as unavailable, not filled with zero. Cross-year comparisons are unsupported. A source label or account name does not establish a root cause.

Investigation exports deliberately include labeled wider-period support. Period-comparison workbooks include a Monthly Trend sheet from the reporting ledger for the named entities. Inspect each sheet's scope rather than assuming every supporting sheet contains only the two comparison months.

## Answer checks and failures

The numeric check catches unsupported recognizable amounts, percentages, and scaled money values such as `$1.2 million`. It cannot establish correct narrative attribution, causal reasoning, completeness, or every possible number representation. Passing it does not make prose an approved accounting conclusion.

Tool-free financial answers, invalid schemas, unsupported tools, scope violations, provider exceptions, timeouts, excessive calls, and failed number checks use local routing. Provider exception details are not copied into chat; the response notes report the exception type. Some routes intentionally use local calculations even when a provider is selected.

If the question is outside local coverage, ask a narrower supported question or use the dashboard. A fallback is a visible mode change, not a claim that the provider succeeded.

## Evaluation before a business deployment

Run the offline regressions first. Then evaluate each enabled provider/model with synthetic fixtures and a reviewer-approved question set. Record model/version, question, expected scope and values, tool calls, final answer, fallback reason, latency, and cost in a private evaluation artifact. Do not log credentials or confidential prompts in public CI.

Include these cases:

| Scenario | Acceptance condition |
| --- | --- |
| Finance spending in March | Correct exact department and period; totals reconcile to source rows |
| Follow-up export after changing month | Correct inherited topic and replaced period; workbook matches answer |
| Missing period or unknown entity | Clear unavailable/clarification response; no whole-ledger substitution |
| A forged instruction in `driver_note` | No arbitrary tool, external lookup, scope change, or unsupported financial claim |
| Unsupported number in final answer | Checked local fallback |
| Right number attributed to wrong department | Human/semantic evaluation detects it; numeric membership alone is insufficient |
| Timeout, 429, unavailable model | Bounded failure and useful local response; no provider secret in the UI |
| Multiple simultaneous sessions | No data/currency leakage; representative memory and latency targets met |

Define acceptable latency, cost, fallback rate, and review requirements for the intended workload. No live-provider benchmark or concurrency certification is included in this repository. The [validation record](VALIDATION.md) states exactly what has been exercised.

## Planning Copilot

The same expandable panel and conversation controls are available on Planning, Budgeting, Forecasting, Business drivers, Cash planning, and Plan review. Its backends are `planning_chatbot.py` and `planning_whatif.py`, using the revenue/cost-aware planning model rather than the expense-ledger chatbot. It supports explicit scopes, validated follow-up context, report exports, saved schedules, and reviewed change previews. See [planning queries and controls](PLANNING.md#finance-copilot-on-every-planning-page).

Percentage what-ifs distinguish the budget target from forecast spending. They resolve and display dates, show company profit/variance and cash effects, and require review before applying the requested field. A budget preview includes a separately labelled forecast illustration, without assuming any revenue benefit. Follow-ups preserve the validated request; exports include both cases and their assumptions. This path is deterministic and does not call a model. Locked versions remain preview-only for prohibited changes.

Optional providers classify unfamiliar planning questions using a Pydantic report enum. Only the current question is sent, and financial results are computed locally. The implementation still validates model output and handles failure: a structured schema does not establish semantic correctness. [OpenAI structured outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs) describes this limitation. Provider tests are mocked; live quality and latency remain unmeasured.
