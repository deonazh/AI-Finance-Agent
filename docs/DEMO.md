# Five-minute business demo

Use the committed [synthetic SGD ledger](../examples/monthly_budget_actuals.csv). The scenario represents a small company reviewing January–March 2026 operating expenses. Its numbers are invented and do not represent an employer or client.

## 1. Load a reproducible reporting period

Start the app using the [quick start](../README.md#quick-start). Open **Reporting** in the top navigation. Choose **Upload your data** and select the example CSV. Confirm FY2026, include through March (P03), and SGD. Leave the ledger scope at Full ledger and the default thresholds at 10% and 50,000.

The 18 source rows cover Finance, IT Ops, and Sales expenses across six department/account pairs. Check these control totals:

| Measure | Expected amount |
| --- | ---: |
| Budget | SGD 226,000.00 |
| Actual | SGD 255,300.00 |
| Net expense variance | SGD 29,300.00 unfavorable |
| Variance percentage | 12.9646% (13.0% at one decimal) |

The department totals reconcile as follows:

| Department | Budget | Actual | Variance |
| --- | ---: | ---: | ---: |
| Finance | 100,000 | 107,500 | 7,500 |
| IT Ops | 84,000 | 96,600 | 12,600 |
| Sales | 42,000 | 51,200 | 9,200 |

## 2. Move from overview to evidence

Inspect **Monthly performance** and switch **Explore spend** to the budget-to-actual bridge. Each displayed movement plus the residual reconciles to the overall change. Open **Refine your view**, select Finance, and observe the KPIs, ledger, and default Copilot scope change together.

Use explicit periods for this historical dataset. “Last month” uses the server calendar, so it may refer to a period absent from the sample.

## 3. Ask a useful sequence

With no department filter selected and **Answer using → Full ledger**, ask:

```text
compare budget vs actual for IT Ops by account
what about March?
export that to Excel
```

The last workbook should contain the two March IT Ops source rows: Cloud Hosting and Software Licenses. Their combined budget is SGD 28,000 and actual is SGD 33,700. Its Profile and Summary sheets should reconcile to Detail.

Then ask:

```text
what changed between January and March?
total actual for cost center Atlantis
```

The first request compares actual spend of SGD 76,700 in January with SGD 92,400 in March: an increase of SGD 15,700. The second should ask you to select a valid entity; it must not substitute company-wide totals.

## 4. Review an exception

Open **Review & export**. January Finance / External Consulting has zero budget and SGD 2,500 actual. Its percentage is undefined, and it is flagged as unbudgeted activity.

Enter a synthetic owner, set In progress, and record a next action such as “Confirm whether a budget transfer was approved.” Save the notes. A label such as External Consulting is not evidence of a price or volume cause.

Download the review backup. Clear or restart the session, reload the same ledger, and restore the JSON to recover the notes. Changing the ledger amounts should cause restoration to fail. Resolution requires an owner and explanation; it is a working-paper status, not approval of a payment or journal.

## 5. Produce a management handoff

Clear department filters if you want the full sample pack. Generate an **Executive brief** in local mode. Return to **Review & export**, select **Prepare reporting pack**, and download it.

Check Department Summary, Ledger Detail, Review Register, Methodology, Management Brief, and Report Context. The context should identify FY2026, P03, SGD, thresholds, source, generation time, and a ledger fingerprint. Export the PDF briefing if needed.

An equivalent pack can be generated from the terminal:

```bash
python scripts/demo_report.py
```

## Review the results

Confirm which operating expenses require investigation, assign follow-up owners, and check the evidence behind the management report. Reconcile the reporting pack to the selected ledger and review any unsupported explanations before sharing it. See the [system design guide](SYSTEM_DESIGN.md) for the calculation and AI workflow, and the [deployment guide](DEPLOYMENT.md) for operating requirements.
