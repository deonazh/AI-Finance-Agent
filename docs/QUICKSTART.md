# Start here

Use the tabs at the top of the page to move between **Reporting**, **Planning**, **Budgeting**, **Forecasting**, **Business drivers**, **Cash planning**, **Plan review**, and **Guide**. On smaller screens, scroll the tab bar sideways. Reporting has its own tabs above the summary cards.

## Explore a complete planning cycle

1. Open **Planning**, then **Create or import a plan → Service business demo → Create demo plan**. This saves a synthetic 24-month plan locally.
2. Open **Budgeting** to edit monthly targets or allocate an annual budget. Explain changes before saving.
3. Open **Forecasting** to generate a baseline from budget, the last actual month, or a trailing three-month average. Scenario changes have a preview before you apply them.
4. Open **Business drivers** to model revenue from price and volume, workforce from staffing and salaries, and capital purchases with depreciation.
5. Open **Cash planning** to set opening balances and payment timing, then inspect future cash.
6. Open **Plan review** to create separate scenarios, compare their results in Forecasting, record review decisions, inspect history, or download a workbook and backup.

The sidebar selects your saved plan and provides **Reload latest saved revision**. All planning tabs work on that same version. Approved plans remain locked; create a new draft to continue planning.

## Ask Finance Copilot

Copilot is available beside Reporting and every planning tab. Select **Expand** for a full-width answer, then **Back to plan/dashboard** to return. The input stays above the latest response; older messages are collapsed. Wide tables scroll horizontally inside their bounds. Planning previews show up to 12 rows; Excel includes the complete result.

Try `show budget for 2026`, then `what about 2027?`, then `export that to Excel`. Other examples: `show forecast for Q3 2026`, `show cash`, `show workforce`, `show assumptions`, `top variances`, or `What if I increase Marketing budget by 8% for next month?`.

For a budget what-if, read the budget-only effect first, then the separate “if the same change also happens in the forecast” illustration. A bigger allowance does not automatically mean higher spending. Review **Affected monthly inputs** and apply only if you want to save the proposed budget. Ask for a **forecast** change to revise expected spending. “Next month” uses the server calendar and is displayed explicitly; `October 2026` is a stable month to use with the demo. Follow with `what about November 2026?` or `export that to Excel`.

Planning chat reads the **saved plan**, independently of page filters and unsaved form edits. Name the department/account and period in the question. Every answer states its scope. Switching planning tabs preserves chat; saving or selecting another version starts a fresh conversation. Reporting chat uses the ledger and its own conversation.

Local mode needs no API key. Optional OpenAI/Anthropic settings are under **Assistant settings**. Plan reports and calculations run in Python; optional routing sends your question to the selected provider. Proposed changes still require explicit review and application.

## Use business data

Reporting accepts monthly expense-ledger exports. Planning accepts a separate monthly table with period, department, account, category, budget, actual, and forecast. The detailed guides explain templates, validation, formulas, and limitations. Use the sidebar data-source controls in Reporting and the creation/import controls on the planning pages.

This is a local FP&A application, not a transaction-processing ERP. Review names are labels, not authenticated approvals. Plan data is stored on the server; keep hosting private and back up the database. Read the deployment guide before shared use.
