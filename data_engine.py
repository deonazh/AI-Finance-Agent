"""Financial data engine for the AI Finance Agent.

This module creates and processes enterprise-style Budget vs. Actual data.
It is intentionally ERP-friendly: each record is keyed by fiscal year,
period, cost center, and G/L account, which mirrors the grain commonly
exported from SAP, Oracle, Workday Adaptive, or public sector ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import numpy as np
import pandas as pd


FISCAL_YEAR = 2026

COST_CENTERS: tuple[str, ...] = (
    "IT Ops",
    "HR",
    "Finance",
    "Supply Chain",
    "Marketing",
)

GL_ACCOUNTS: tuple[str, ...] = (
    "Cloud Hosting",
    "Software Licenses",
    "External Consulting",
    "Travel",
    "Training",
    "Office Supplies",
    "Facilities",
    "Payroll",
    "Recruitment",
    "Marketing Campaigns",
)

BASE_BUDGETS: dict[str, float] = {
    "Cloud Hosting": 185_000,
    "Software Licenses": 145_000,
    "External Consulting": 120_000,
    "Travel": 58_000,
    "Training": 32_000,
    "Office Supplies": 18_000,
    "Facilities": 82_000,
    "Payroll": 410_000,
    "Recruitment": 44_000,
    "Marketing Campaigns": 165_000,
}

COST_CENTER_MULTIPLIERS: dict[str, float] = {
    "IT Ops": 1.25,
    "HR": 0.62,
    "Finance": 0.78,
    "Supply Chain": 1.05,
    "Marketing": 1.18,
}


@dataclass(frozen=True)
class VarianceConfig:
    """Configurable thresholds for isolating material finance variances."""

    fiscal_year: int = FISCAL_YEAR
    variance_pct_threshold: float = 10.0
    dollar_threshold: float = 50_000.0
    seed: int = 42


REQUIRED_COLUMNS: tuple[str, ...] = (
    "fiscal_year",
    "period",
    "cost_center",
    "gl_account",
    "budget",
    "actual",
)


def generate_synthetic_budget_actuals(
    fiscal_year: int = FISCAL_YEAR,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic ERP Budget vs. Actual data at monthly ledger grain.

    Enterprise accounting logic:
    - Budget is set at period, cost center, and G/L account level.
    - Actuals include normal operating noise plus deliberate enterprise events.
    - Synthetic driver labels help the AI agent distinguish price, volume,
      and timing issues when producing variance commentary.
    """

    rng = np.random.default_rng(seed)
    periods = range(1, 13)
    rows: list[dict[str, Any]] = []

    for period in periods:
        # Typical enterprise seasonality: Q4 campaigns and renewals run hotter,
        # while mid-year travel and hiring may fluctuate.
        quarter = ((period - 1) // 3) + 1
        seasonal_factor = {
            1: 0.96,
            2: 1.00,
            3: 1.04,
            4: 1.12,
        }[quarter]

        for cost_center in COST_CENTERS:
            cc_factor = COST_CENTER_MULTIPLIERS[cost_center]
            for gl_account in GL_ACCOUNTS:
                base_budget = BASE_BUDGETS[gl_account] * cc_factor * seasonal_factor
                budget = max(5_000.0, base_budget * rng.normal(1.0, 0.04))

                normal_noise = rng.normal(0.0, 0.045)
                event_pct = 0.0
                driver = "normal"
                driver_note = "Within normal monthly operating tolerance."

                event_pct, driver, driver_note = _synthetic_variance_event(
                    period=period,
                    cost_center=cost_center,
                    gl_account=gl_account,
                    rng=rng,
                )

                actual = max(0.0, budget * (1.0 + normal_noise + event_pct))

                rows.append(
                    {
                        "fiscal_year": fiscal_year,
                        "period": period,
                        "period_label": f"FY{fiscal_year}-P{period:02d}",
                        "quarter": f"Q{quarter}",
                        "cost_center": cost_center,
                        "gl_account": gl_account,
                        "budget": round(budget, 2),
                        "actual": round(actual, 2),
                        "synthetic_driver": driver,
                        "driver_note": driver_note,
                    }
                )

    return calculate_variances(pd.DataFrame(rows))


def _synthetic_variance_event(
    period: int,
    cost_center: str,
    gl_account: str,
    rng: np.random.Generator,
) -> tuple[float, str, str]:
    """Inject realistic enterprise events that produce explainable variances."""

    # Price/rate issue: cloud consumption and vendor repricing often create
    # IT operating expense pressure even when service volumes are unchanged.
    if cost_center == "IT Ops" and gl_account == "Cloud Hosting" and period in {5, 6, 7}:
        return 0.24, "price", "Cloud unit rates and reserved capacity mix drove run-rate pressure."

    # Timing issue: annual software renewals can hit before the budget phasing.
    if gl_account == "Software Licenses" and period in {1, 12}:
        return 0.18, "timing", "Annual license renewals landed ahead of budget phasing."

    # Volume issue: project demand tends to pull consulting spend above plan.
    if cost_center in {"Finance", "Supply Chain"} and gl_account == "External Consulting" and period in {3, 4, 9}:
        return 0.28, "volume", "Transformation workstreams used more consulting days than planned."

    # Volume issue: brand campaigns are deliberately bursty.
    if cost_center == "Marketing" and gl_account == "Marketing Campaigns" and period in {9, 10, 11}:
        return 0.31, "volume", "Campaign volume and paid media intensity exceeded the monthly plan."

    # Timing/favorable: travel may be delayed rather than permanently saved.
    if gl_account == "Travel" and period in {2, 8}:
        return -0.19, "timing", "Travel activity slipped into later periods."

    # Headcount issue: payroll underspend often reflects vacancies.
    if cost_center == "HR" and gl_account == "Payroll" and period in {4, 5, 6}:
        return -0.13, "volume", "Vacancy lag created temporary payroll underspend."

    # Smaller random events create a more realistic long tail.
    if rng.random() < 0.055:
        driver = rng.choice(["price", "volume", "timing"])
        event = float(rng.choice([-1, 1]) * rng.uniform(0.11, 0.24))
        note = {
            "price": "Unit cost or vendor rate moved away from budget assumptions.",
            "volume": "Activity levels differed from the demand plan.",
            "timing": "Invoice or accrual timing differed from budget phasing.",
        }[str(driver)]
        return event, str(driver), note

    return 0.0, "normal", "Within normal monthly operating tolerance."


def calculate_variances(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate absolute and percentage Budget vs. Actual variances.

    Variance is defined as Actual - Budget:
    - Positive variance means actual spend exceeded budget.
    - Negative variance means underspend or delayed spend versus budget.
    """

    clean = _normalise_columns(df)
    for column in ("budget", "actual"):
        numeric = pd.to_numeric(clean[column], errors="coerce")
        if not np.isfinite(numeric.to_numpy(dtype=float)).all():
            raise ValueError(f"{column} must contain finite numeric amounts; missing or invalid values cannot be treated as zero.")
        clean[column] = numeric.astype(float).round(2)

    if any(not np.isfinite(clean[column].abs().sum()) for column in ("budget", "actual")):
        raise ValueError("Amounts exceed the supported numeric reporting range.")
    clean["variance"] = (clean["actual"] - clean["budget"]).round(2)
    clean["variance_pct"] = np.where(
        clean["budget"].abs() > 0,
        (clean["variance"] / clean["budget"].replace(0, np.nan)) * 100,
        np.nan,
    )
    if not np.isfinite(clean["variance"]).all() or not np.isfinite(clean.loc[clean["budget"] != 0, "variance_pct"]).all():
        raise ValueError("Calculated variances exceed the supported numeric reporting range.")
    clean["abs_variance"] = clean["variance"].abs()
    clean["abs_variance_pct"] = clean["variance_pct"].abs()
    clean["variance_direction"] = np.where(
        clean["variance"] > 0,
        "Unfavorable",
        np.where(clean["variance"] < 0, "Favorable", "On budget"),
    )
    if "period" in clean:
        clean["quarter"] = clean["period"].apply(lambda period: f"Q{((int(period) - 1) // 3) + 1}")
    if {"fiscal_year", "period"}.issubset(clean.columns):
        clean["period_label"] = [f"FY{int(year)}-P{int(period):02d}" for year, period in zip(clean["fiscal_year"], clean["period"])]

    return _order_columns(clean)


def filter_significant_variances(
    df: pd.DataFrame,
    variance_pct_threshold: float = 10.0,
    dollar_threshold: float = 50_000.0,
) -> pd.DataFrame:
    """Return rows where either percentage or dollar impact is material.

    Finance teams usually review both:
    - percentage variance catches small accounts moving sharply;
    - dollar variance catches large accounts with modest percentage movement.
    Both favorable and unfavorable variances are reviewed by absolute magnitude.
    """

    if not np.isfinite([variance_pct_threshold, dollar_threshold]).all() or min(variance_pct_threshold, dollar_threshold) < 0:
        raise ValueError("Materiality thresholds must be finite and non-negative.")
    with_variances = calculate_variances(df)
    mask = (
        (with_variances["abs_variance_pct"] > float(variance_pct_threshold))
        | (with_variances["abs_variance"] > float(dollar_threshold))
        | ((with_variances["budget"] == 0) & (with_variances["actual"] != 0))
    )
    significant = with_variances.loc[mask].copy()
    significant["flag_reason"] = np.select(
        [
            (significant["budget"] == 0) & (significant["actual"] != 0),
            (significant["abs_variance_pct"] > float(variance_pct_threshold))
            & (significant["abs_variance"] > float(dollar_threshold)),
            significant["abs_variance_pct"] > float(variance_pct_threshold),
            significant["abs_variance"] > float(dollar_threshold),
        ],
        ["Unbudgeted activity", "Percent and dollar threshold", "Percent threshold", "Dollar threshold"],
        default="Not flagged",
    )
    return significant.sort_values(["abs_variance", "abs_variance_pct"], ascending=False).reset_index(drop=True)


def build_variance_dataset(
    config: VarianceConfig | None = None,
    export_path: str | Path | None = "outputs/significant_variances.csv",
) -> pd.DataFrame:
    """Generate, calculate, filter, and optionally export significant variances."""

    config = config or VarianceConfig()
    df = generate_synthetic_budget_actuals(fiscal_year=config.fiscal_year, seed=config.seed)
    significant = filter_significant_variances(
        df,
        variance_pct_threshold=config.variance_pct_threshold,
        dollar_threshold=config.dollar_threshold,
    )
    if export_path is not None:
        export_to_csv(significant, export_path)
    return significant


def load_budget_actuals_csv(file: str | Path | IO[str] | IO[bytes]) -> pd.DataFrame:
    """Load a user-provided Budget vs. Actual CSV and calculate variances."""

    return _read_ledger(file, "CSV")


def load_budget_actuals_file(file: str | Path | IO[str] | IO[bytes]) -> pd.DataFrame:
    """Load a user-provided Budget vs. Actual CSV or XLSX file."""

    file_name = str(getattr(file, "name", file))
    suffix = Path(file_name).suffix.lower()
    if suffix == ".xlsx":
        return _read_ledger(file, "XLSX")
    if suffix == ".csv" or suffix == "":
        return _read_ledger(file, "CSV")
    raise ValueError("Unsupported upload type. Please upload a .csv or .xlsx file.")


def _read_ledger(file, file_label):
    # Preserve the original header: pandas' default header mangling hides duplicates.
    reader = pd.read_excel if file_label == "XLSX" else pd.read_csv
    raw = reader(file, header=None, dtype=object, nrows=100_002)
    if raw.empty:
        raise ValueError("The file has no ledger rows.")
    df = raw.iloc[1:].copy().reset_index(drop=True)
    df.columns = raw.iloc[0].tolist()
    return _validate_budget_actuals_frame(df, file_label)


def _validate_budget_actuals_frame(df: pd.DataFrame, file_label: str) -> pd.DataFrame:
    """Reject ambiguous ledger inputs before any business totals are calculated."""
    clean = _normalise_columns(df)
    if clean.columns.duplicated().any():
        raise ValueError("Duplicate column names or aliases: use one column for each required field.")
    missing = set(REQUIRED_COLUMNS) - set(clean.columns)
    if missing:
        raise ValueError(f"{file_label} is missing required column(s): {', '.join(sorted(missing))}")
    if clean.empty:
        raise ValueError("The file has no ledger rows.")
    if len(clean) > 100_000:
        raise ValueError("Upload at most 100,000 monthly ledger rows; aggregate transaction extracts first.")
    for column, low, high in [("fiscal_year", 1900, 2200), ("period", 1, 12)]:
        numeric = pd.to_numeric(clean[column], errors="coerce")
        invalid = numeric.isna() | ~numeric.between(low, high) | (numeric % 1 != 0)
        if invalid.any():
            rows = ", ".join(str(i + 2) for i in np.flatnonzero(invalid.to_numpy())[:5])
            raise ValueError(f"{column} must be a whole number from {low} to {high}. File row(s): {rows}.")
        clean[column] = numeric.astype(int)
    for column in ("cost_center", "gl_account"):
        invalid = clean[column].isna() | clean[column].astype(str).str.strip().eq("")
        if invalid.any():
            raise ValueError(f"{column} cannot be blank. Correct the source extract before uploading.")
        clean[column] = clean[column].astype(str).str.strip()
    for column in ("budget", "actual"):
        numeric = pd.to_numeric(clean[column], errors="coerce")
        invalid = ~np.isfinite(numeric.to_numpy(dtype=float))
        if invalid.any():
            rows = ", ".join(str(i + 2) for i in np.flatnonzero(invalid)[:5])
            raise ValueError(f"{column} must contain numeric amounts, without currency symbols or thousands separators. File row(s): {rows}.")
        clean[column] = numeric.astype(float).round(2)
    grain = ["fiscal_year", "period", "cost_center", "gl_account"]
    if clean.duplicated(grain).any():
        raise ValueError("Duplicate monthly ledger keys found. Supply one row per fiscal_year, period, cost_center, gl_account; aggregate transactions and avoid repeating budgets.")
    if "currency" in clean:
        codes = clean["currency"].astype(str).str.strip().str.upper()
        if not codes.str.fullmatch(r"[A-Z]{3}").all() or clean["currency"].isna().any():
            raise ValueError("currency must contain a three-letter reporting currency on every row.")
        if codes.nunique() != 1:
            raise ValueError("Mixed currencies cannot be summed. Convert to one reporting currency before uploading.")
        clean["currency"] = codes
    return calculate_variances(clean)


def export_to_csv(df: pd.DataFrame, export_path: str | Path) -> Path:
    """Write a clean DataFrame export to CSV, creating parent folders as needed."""

    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from exports import csv_bytes
    path.write_bytes(csv_bytes(df))
    return path


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise common ERP export column names to snake_case names."""

    normalised = df.copy()
    normalised.columns = [
        str(col).strip().lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        for col in normalised.columns
    ]

    aliases = {
        "cost_centre": "cost_center",
        "costcenter": "cost_center",
        "gl": "gl_account",
        "g_l_account": "gl_account",
        "account": "gl_account",
        "actuals": "actual",
        "budget_amount": "budget",
        "actual_amount": "actual",
        "fy": "fiscal_year",
    }
    return normalised.rename(columns=aliases)


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the export stable and easy to consume by agents and BI tools."""

    preferred = [
        "fiscal_year",
        "period",
        "period_label",
        "quarter",
        "cost_center",
        "gl_account",
        "budget",
        "actual",
        "variance",
        "variance_pct",
        "abs_variance",
        "abs_variance_pct",
        "variance_direction",
        "flag_reason",
        "synthetic_driver",
        "driver_note",
    ]
    columns = [col for col in preferred if col in df.columns]
    columns.extend([col for col in df.columns if col not in columns])
    return df.loc[:, columns]


if __name__ == "__main__":
    dataset = build_variance_dataset()
    print(f"Generated {len(dataset):,} significant variance rows")
    print(dataset.head(10).to_string(index=False))
