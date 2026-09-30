# Data contract

## Required grain

Supply one row per **fiscal year × period × cost center × G/L account**. Budget and actual must be comparable monthly expense balances in one reporting currency. Do not repeat a full account budget on every invoice row.

| Column | Type | Validation |
| --- | --- | --- |
| `fiscal_year` | Integer | 1900–2200 |
| `period` | Integer | 1–12 |
| `cost_center` | Text | Required; surrounding whitespace trimmed |
| `gl_account` | Text | Required; surrounding whitespace trimmed |
| `budget` | Numeric | Finite amount; rounded to 2 decimal places |
| `actual` | Numeric | Finite amount; rounded to 2 decimal places |
| `currency` | Optional text | One three-letter code throughout the file; no conversion |
| `synthetic_driver` | Optional text | Demo/source assertion: price, volume, timing, mix, normal |
| `driver_note` | Optional text | Source explanation; not independently verified |

Without a currency column, select the reporting denomination in the sidebar. For reproducible sharing, include it in the source file.

CSV and XLSX are supported. XLSX reads the first worksheet. Excel formulas must have cached numeric results from a spreadsheet application; the app does not calculate input formulas. CSV uses comma separation and a header row. Use unformatted numeric amounts such as `1250.50`, not `$1,250.50` or accounting parentheses. Negative numbers represent credits and are retained.

Header normalization accepts common aliases such as `fy`, `cost_centre`, `budget_amount`, `actuals`, and `g_l_account`. Duplicate original headers and ambiguous aliases that normalize to the same column are rejected.

## Rejected inputs

- Missing required columns or an empty extract.
- Duplicate monthly keys, including identical duplicated rows.
- Blank department/account dimensions.
- Invalid, missing, or infinite budget/actual values, or values that overflow reporting calculations.
- Fractional/out-of-range fiscal periods or years.
- Multiple currencies or missing currency codes when that column exists.
- More than 100,000 ledger rows or an upload over 20 MB.

The application reports validation errors instead of silently inserting zeroes. Correct the extract and re-upload it. Missing periods are called out; their absence is not evidence of zero spend. A two-period comparison requires data for both periods.

## Derived fields

The app recalculates `variance`, `variance_pct`, absolute variances, `variance_direction`, `quarter`, and `period_label`. Supplied values in those columns are not trusted as calculation inputs.

A zero budget with nonzero actual activity is flagged regardless of thresholds. Its percentage is undefined. It remains usable in charts, chat, review registers, and briefings. For a negative budget, the percentage formula keeps the signed denominator; the favorable/unfavorable label follows the sign of `actual - budget`.

## Reporting boundaries

The upload may contain multiple years. The sidebar selects one reporting year and the last included period before the data is passed to Copilot or the analyst workflow. Dashboard filters then refine that reporting ledger. Copilot can expand from a filtered view to the full reporting ledger, but cannot recover excluded future periods or another year.

Period names assume a January–December calendar. Re-map a non-calendar financial year before using calendar-language questions. Relative periods use the server date; use explicit period numbers for repeatable historical analysis.

## Working-paper semantics

A review JSON backup is bound to a SHA-256 fingerprint of sorted ledger keys, budget, actual, and currency. Reordering rows does not invalidate it; changing those values does. The fingerprint does not certify completeness or authenticity and does not cover optional descriptive notes. Restoring a backup does not constitute an approval.

Budget/actual source columns remain read-only in the review editor. Resolved items require an owner and an explanation. All review changes are session state until you download a backup. No immutable edit history is maintained.

The downloadable CSV neutralizes formula-like strings. Excel exports store formula-like source text as literal strings. Numeric amounts retain their numeric types, including negative values.
