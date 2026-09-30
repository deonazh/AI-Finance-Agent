"""Calculation and validation guardrails for FP&A variance analysis.

This module keeps financial arithmetic out of LLM prompts. Budget, actual,
variance dollars, and variance percentage are calculated deterministically in
Python, then packaged into strict Pydantic v2 schemas before any narrative
agent consumes the data.
"""

from __future__ import annotations

import math
import re
import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DriverCategory = Literal["Price", "Volume", "Timing", "Fx_Impact", "Unknown"]

RECALCULATION_ALIAS = "Re-calculation_Check_Passed"
CALCULATION_KEY_COLUMNS: tuple[str, str] = ("CostCenter", "GL_Account")
CALCULATION_COLUMNS: tuple[str, ...] = (
    "CostCenter",
    "GL_Account",
    "GL_Description",
    "Budget",
    "Actual",
    "Variance_USD",
    "Variance_Pct",
    "Driver_Category",
)


class FinancialValidationWarning(UserWarning):
    """Raised as a warning when LLM output fails deterministic recalculation."""


class VarianceItem(BaseModel):
    """Validated variance row handed to or returned by an analysis agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    CostCenter: str = Field(min_length=1)
    GL_Account: str = Field(min_length=1)
    GL_Description: str = Field(min_length=1)
    Budget: float
    Actual: float
    Variance_USD: float
    Variance_Pct: float | None
    Driver_Category: DriverCategory

    @field_validator("Budget", "Actual", "Variance_USD", "Variance_Pct")
    @classmethod
    def numeric_values_must_be_finite(cls, value: float | None) -> float | None:
        """Reject NaN and infinity before values reach the LLM layer."""

        if value is None:
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("Financial values must be finite numbers.")
        return numeric


class FinancialAnalysisOutput(BaseModel):
    """Validated executive analysis payload.

    `Re-calculation_Check_Passed` is exposed as a JSON alias because hyphens
    are invalid in Python attribute names. Use `model_dump(by_alias=True)` when
    sending this object to systems that expect the exact external field name.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    Summary_Header: str = Field(min_length=1)
    Top_Variance_Drivers: list[VarianceItem] = Field(default_factory=list)
    Executive_Commentary: str = Field(min_length=1)
    Compliance_Flag: bool
    Re_calculation_Check_Passed: bool = Field(alias=RECALCULATION_ALIAS)


def calculate_exact_variances(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate Budget vs Actual variances deterministically.

    Enterprise accounting logic:
    - `Variance_USD` is always `Actual - Budget`.
    - `Variance_Pct` is always `(Variance_USD / Budget) * 100`.
    - Any incoming variance columns are ignored and recalculated.
    - Zero budget rows retain dollar variance; percentage variance is undefined (NaN).

    Args:
        df: Raw Budget vs Actual data. Common ERP column spellings such as
            `cost_center`, `gl_account`, `budget`, and `actual` are accepted.

    Returns:
        A clean DataFrame with canonical FP&A guardrail columns.

    Raises:
        ValueError: If required columns are missing, numeric values are invalid,
            or contain non-finite amounts.
    """

    try:
        if not isinstance(df, pd.DataFrame):
            raise TypeError("Input must be a pandas DataFrame.")

        clean = _normalise_columns(df)
        missing = set(("CostCenter", "GL_Account", "Budget", "Actual")) - set(clean.columns)
        if missing:
            raise ValueError(f"Missing required column(s): {', '.join(sorted(missing))}")

        if clean.empty:
            return pd.DataFrame(columns=CALCULATION_COLUMNS)

        clean["CostCenter"] = clean["CostCenter"].astype(str).str.strip()
        clean["GL_Account"] = clean["GL_Account"].astype(str).str.strip()
        clean["GL_Description"] = _description_series(clean)
        clean["Budget"] = _coerce_float_series(clean["Budget"], "Budget")
        clean["Actual"] = _coerce_float_series(clean["Actual"], "Actual")

        clean["Variance_USD"] = (clean["Actual"] - clean["Budget"]).round(2).astype("float64")
        clean["Variance_Pct"] = ((clean["Variance_USD"] / clean["Budget"].replace(0, np.nan)) * 100.0).astype("float64")
        clean["Driver_Category"] = clean.apply(_driver_category_for_row, axis=1)

        ordered = [column for column in CALCULATION_COLUMNS if column in clean.columns]
        passthrough = [column for column in clean.columns if column not in ordered]
        return clean.loc[:, ordered + passthrough].copy()
    except (TypeError, ValueError, KeyError, ValidationError) as exc:
        raise ValueError(f"Unable to calculate exact variances: {exc}") from exc


def verify_llm_output(raw_df: pd.DataFrame, llm_payload: FinancialAnalysisOutput) -> bool:
    """Verify LLM variance output against Python recalculation.

    The payload is considered valid only when every `Variance_USD` in
    `Top_Variance_Drivers` matches the recalculated raw data at the same
    CostCenter + GL_Account grain within 0.01%.

    If validation fails, this function mutates `llm_payload` by setting
    `Re_calculation_Check_Passed` to `False`, raises a
    `FinancialValidationWarning`, and returns `False`.
    """

    try:
        payload = FinancialAnalysisOutput.model_validate(llm_payload)
        calculated = calculate_exact_variances(raw_df)
        expected_by_driver = _aggregate_expected_variances(calculated)

        mismatches: list[str] = []
        for item in payload.Top_Variance_Drivers:
            key = _driver_key(item.CostCenter, item.GL_Account)
            expected = expected_by_driver.get(key)
            if expected is None:
                mismatches.append(f"{item.CostCenter} / {item.GL_Account}: no matching raw data")
                continue
            if not _within_relative_tolerance(expected, item.Variance_USD):
                mismatches.append(
                    f"{item.CostCenter} / {item.GL_Account}: expected {expected:.2f}, "
                    f"LLM returned {item.Variance_USD:.2f}"
                )

        if mismatches:
            _mark_payload_status(payload, passed=False)
            _warn_validation_failure("LLM variance output failed recalculation: " + "; ".join(mismatches))
            return False

        _mark_payload_status(payload, passed=True)
        return True
    except Exception as exc:
        if isinstance(llm_payload, FinancialAnalysisOutput):
            _mark_payload_status(llm_payload, passed=False)
        _warn_validation_failure(f"Unable to verify LLM output: {exc}")
        return False


def validate_response_against_dataframe(llm_response_text: str, source_df_summary: dict[str, Any]) -> bool:
    """Check that final response numbers came from deterministic tool output.

    This is a final safety check before Streamlit displays LLM prose. It scans
    the response for financial-looking numbers and confirms each meaningful
    value appears in the source dictionary returned by Python tools. Small
    whole numbers, such as list markers and period numbers, are ignored.

    Args:
        llm_response_text: Final answer generated by the LLM.
        source_df_summary: Nested dict/list/string payloads returned by trusted
            Python tools.

    Returns:
        True when no unsupported large/decimal numbers are detected.
    """

    try:
        if not llm_response_text.strip():
            return False

        # Expand finance abbreviations before matching values, e.g. $1.2m -> $1200000.
        scales = {"k": 1000, "thousand": 1000, "m": 1000000, "million": 1000000,
                  "b": 1000000000, "billion": 1000000000}
        llm_response_text = re.sub(
            r"(?<![A-Za-z])([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(thousand|million|billion|[kmb])\b",
            lambda match: str(float(match.group(1).replace(",", "")) * scales[match.group(2).lower()]),
            llm_response_text, flags=re.IGNORECASE,
        )
        response_numbers = _numbers_in_text(llm_response_text)
        if not response_numbers:
            return True

        valid_numbers = _reference_number_strings(source_df_summary)
        if not valid_numbers:
            return False

        for match in re.finditer(r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?", llm_response_text):
            raw_number = match.group()
            cleaned = raw_number.replace(",", "")
            before = llm_response_text[max(0, match.start()-8):match.start()]
            after = llm_response_text[match.end():match.end()+1]
            financial = bool(re.search(r"(?:[$€£¥]|USD\s*|SGD\s*|EUR\s*|GBP\s*)$", before)) or after == "%"
            try:
                value = float(cleaned)
            except ValueError:
                continue
            if not financial and _is_non_financial_small_integer(cleaned, value):
                continue
            if _number_variants(value).isdisjoint(valid_numbers):
                return False
        return True
    except Exception:
        return False


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map common ERP and BI extract headers into canonical guardrail names."""

    aliases = {
        "cost_center": "CostCenter",
        "cost_centre": "CostCenter",
        "costcenter": "CostCenter",
        "costcentre": "CostCenter",
        "cost_center_name": "CostCenter",
        "gl": "GL_Account",
        "gl_account": "GL_Account",
        "g_l_account": "GL_Account",
        "account": "GL_Account",
        "account_code": "GL_Account",
        "gl_description": "GL_Description",
        "g_l_description": "GL_Description",
        "gl_desc": "GL_Description",
        "account_description": "GL_Description",
        "description": "GL_Description",
        "budget": "Budget",
        "budget_amount": "Budget",
        "actual": "Actual",
        "actuals": "Actual",
        "actual_amount": "Actual",
        "variance": "Variance_USD",
        "variance_usd": "Variance_USD",
        "variance_amount": "Variance_USD",
        "variance_pct": "Variance_Pct",
        "variance_percent": "Variance_Pct",
        "driver": "Driver_Category",
        "driver_type": "Driver_Category",
        "driver_category": "Driver_Category",
        "synthetic_driver": "Driver_Category",
    }

    clean = df.copy()
    clean.columns = [aliases.get(_column_token(column), str(column).strip()) for column in clean.columns]
    return clean.loc[:, ~pd.Index(clean.columns).duplicated(keep="last")]


def _reference_number_strings(payload: Any) -> set[str]:
    valid: set[str] = set()
    for value in _iter_numeric_values(payload):
        valid.update(_number_variants(value))
        valid.update(_number_variants(abs(value)))
    return valid


def _iter_numeric_values(payload: Any) -> list[float]:
    values: list[float] = []
    if isinstance(payload, dict):
        for value in payload.values():
            values.extend(_iter_numeric_values(value))
        return values
    if isinstance(payload, list | tuple | set):
        for value in payload:
            values.extend(_iter_numeric_values(value))
        return values
    if isinstance(payload, str):
        parsed = _try_parse_json(payload)
        if parsed is not None:
            return _iter_numeric_values(parsed)
        return []
    if isinstance(payload, bool) or payload is None:
        return []
    if isinstance(payload, (int, float, np.integer, np.floating)):
        numeric = float(payload)
        return [numeric] if math.isfinite(numeric) else []
    return []


def _try_parse_json(value: str) -> Any | None:
    import json

    try:
        return json.loads(value)
    except Exception:
        return None


def _numbers_in_text(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?", text)


def _number_variants(value: float) -> set[str]:
    if not math.isfinite(value):
        return set()
    variants = {
        f"{value:.2f}",
        f"{value:.1f}",
        f"{value:.0f}",
        str(round(value, 2)),
        str(round(value, 1)),
    }
    if math.isclose(value, round(value), abs_tol=1e-9):
        variants.add(str(int(round(value))))
    return {variant.replace(",", "") for variant in variants}


def _is_non_financial_small_integer(cleaned: str, value: float) -> bool:
    return cleaned.lstrip("+-").isdigit() and abs(value) <= 12


def _column_token(column: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")


def _description_series(df: pd.DataFrame) -> pd.Series:
    if "GL_Description" not in df.columns:
        return df["GL_Account"].astype(str).str.strip()
    return df["GL_Description"].where(df["GL_Description"].notna(), df["GL_Account"]).astype(str).str.strip()


def _coerce_float_series(series: pd.Series, column_name: str) -> pd.Series:
    try:
        numeric = pd.to_numeric(series, errors="raise").astype("float64")
    except Exception as exc:
        raise ValueError(f"{column_name} contains non-numeric values.") from exc

    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError(f"{column_name} contains NaN or infinite values.")
    return numeric


def _ensure_non_zero_budget(budget: pd.Series) -> None:
    zero_rows = budget.index[budget == 0].tolist()
    if zero_rows:
        preview = ", ".join(str(index) for index in zero_rows[:5])
        raise ValueError(f"Budget cannot be zero when calculating Variance_Pct. Row index(es): {preview}.")


def _driver_category_for_row(row: pd.Series) -> DriverCategory:
    existing = _normalise_driver_category(row.get("Driver_Category"))
    if existing is not None:
        return existing
    return "Unknown"


def _normalise_driver_category(value: Any) -> DriverCategory | None:
    if value is None or pd.isna(value):
        return None

    token = _column_token(value)
    mapping: dict[str, DriverCategory | None] = {
        "price": "Price",
        "pricing": "Price",
        "rate": "Price",
        "volume": "Volume",
        "usage": "Volume",
        "demand": "Volume",
        "timing": "Timing",
        "phasing": "Timing",
        "accrual": "Timing",
        "fx": "Fx_Impact",
        "fx_impact": "Fx_Impact",
        "foreign_exchange": "Fx_Impact",
        "currency": "Fx_Impact",
        "normal": None,
        "unknown": None,
    }
    return mapping.get(token)


def _infer_driver_category(row: pd.Series) -> DriverCategory:
    account = f"{row.get('GL_Account', '')} {row.get('GL_Description', '')}".lower()
    if any(token in account for token in ["fx", "foreign exchange", "currency"]):
        return "Fx_Impact"
    if any(token in account for token in ["cloud", "license", "licence", "software", "facilities", "vendor"]):
        return "Price"
    if any(token in account for token in ["payroll", "travel", "consulting", "campaign", "recruit", "hosting"]):
        return "Volume"
    return "Timing"


def _aggregate_expected_variances(df: pd.DataFrame) -> dict[tuple[str, str], float]:
    grouped = (
        df.groupby(list(CALCULATION_KEY_COLUMNS), dropna=False)
        .agg(Budget=("Budget", "sum"), Actual=("Actual", "sum"))
        .reset_index()
    )
    grouped["Variance_USD"] = grouped["Actual"] - grouped["Budget"]
    return {
        _driver_key(row["CostCenter"], row["GL_Account"]): float(row["Variance_USD"])
        for row in grouped.to_dict("records")
    }


def _driver_key(cost_center: str, gl_account: str) -> tuple[str, str]:
    return (str(cost_center).strip().casefold(), str(gl_account).strip().casefold())


def _within_relative_tolerance(expected: float, observed: float, tolerance_pct: float = 0.01) -> bool:
    difference = abs(float(observed) - float(expected))
    if math.isclose(expected, 0.0, abs_tol=1e-12):
        return difference <= 0.01
    return difference <= abs(float(expected)) * (tolerance_pct / 100.0)


def _mark_payload_status(payload: FinancialAnalysisOutput, passed: bool) -> None:
    payload.Re_calculation_Check_Passed = passed
    if not passed:
        payload.Compliance_Flag = True


def _warn_validation_failure(message: str) -> None:
    warnings.warn(message, FinancialValidationWarning, stacklevel=2)
