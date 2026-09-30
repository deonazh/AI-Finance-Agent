# Reporting and investigation

Open **Reporting** in the top navigation. Its **Overview**, **Ledger explorer**, **Review & export**, and **Executive brief** tabs appear above the summary cards.

1. Choose the synthetic demo or upload a CSV/XLSX in the sidebar. Required columns are `fiscal_year,period,cost_center,gl_account,budget,actual`; `currency` and explanatory source fields are optional. Select the reporting year and last closed period. Future periods should not be interpreted as posted actuals.
2. Set materiality as a percentage or absolute amount. Variance is actual minus budget; for this expense ledger, positive variance means overspend. Zero-budget percentages are undefined.
3. Use department, account, period, quarter, direction, and source filters. All displayed KPIs and reports follow this selection. The full ledger option in Copilot still respects the reporting year and close cutoff.
4. Inspect charts in Overview and individual rows in Ledger explorer. Download the filtered view as CSV or prepare focused investigation workbooks.
5. In Review & export, enter an owner, status, and explanation for material items. Save review notes and prepare a reporting pack. Export your review state before closing the session; reporting notes are session-based.
6. In Executive brief, generate a management summary. Numerical inputs come from Python; optional language models provide commentary. Commentary is not evidence of a root cause unless supported by source data.

## Finance Copilot

Choose **Current dashboard view** or **Full ledger** under Answer using. Ask `summarise finances`, `top 5 unfavorable variances`, `show monthly trend`, or a specific period/department question. Use `export to Excel` for a working paper. The copilot guide documents the full reporting query set and its boundaries.

Select **Expand** to read a large answer at full width. The composer appears before the latest response, so the response can be read from its beginning. Older messages are collapsed, and tables remain inside the panel. Save your conversation or Excel downloads before clearing chat. The session keeps 40 messages and 5 workbook downloads.

Changing the ledger or dashboard scope clears the reporting conversation to prevent answers from referring to obsolete filters. Planning uses a separate conversation and data model, available on each planning tab.

## From reporting to planning

A reporting expense ledger does not automatically include revenue, workforce assumptions, opening cash, or future budget rows. Open Planning and choose **Loaded expense ledger** only when the source has all 12 budget months. The imported accounts begin as Opex; confirm their categories in a complete planning input file. Alternatively, create a blank budget or import the planning CSV/XLSX template.

The Planning workflows guide covers budgeting, forecasting, business drivers, cash projections, saved versions, and local review controls.
