"""Tests for deterministic FP&A calculation guardrails."""

from __future__ import annotations

import unittest

import pandas as pd
from pydantic import ValidationError

from guardrails import (
    FinancialAnalysisOutput,
    FinancialValidationWarning,
    VarianceItem,
    calculate_exact_variances,
    validate_response_against_dataframe,
    verify_llm_output,
)


class FinancialGuardrailTests(unittest.TestCase):
    def test_calculate_exact_variances_recalculates_incoming_values(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "cost_center": "Marketing",
                    "gl_account": "Marketing Campaigns",
                    "budget": 100_000,
                    "actual": 125_000,
                    "variance": 999_999,
                    "synthetic_driver": "volume",
                }
            ]
        )

        result = calculate_exact_variances(raw)

        self.assertEqual(result.loc[0, "Variance_USD"], 25_000)
        self.assertEqual(result.loc[0, "Variance_Pct"], 25.0)
        self.assertEqual(result.loc[0, "Driver_Category"], "Volume")

    def test_financial_output_serialises_hyphenated_alias(self) -> None:
        payload = FinancialAnalysisOutput(
            Summary_Header="Monthly variance review",
            Top_Variance_Drivers=[],
            Executive_Commentary="No material variance detected.",
            Compliance_Flag=False,
            Re_calculation_Check_Passed=True,
        )

        exported = payload.model_dump(by_alias=True)

        self.assertIn("Re-calculation_Check_Passed", exported)
        self.assertNotIn("Re_calculation_Check_Passed", exported)

    def test_verify_llm_output_passes_matching_aggregated_variance(self) -> None:
        raw = pd.DataFrame(
            [
                {"CostCenter": "Marketing", "GL_Account": "Ads", "GL_Description": "Paid Ads", "Budget": 100.0, "Actual": 120.0},
                {"CostCenter": "Marketing", "GL_Account": "Ads", "GL_Description": "Paid Ads", "Budget": 200.0, "Actual": 240.0},
            ]
        )
        payload = FinancialAnalysisOutput(
            Summary_Header="Marketing Ads variance",
            Top_Variance_Drivers=[
                VarianceItem(
                    CostCenter="Marketing",
                    GL_Account="Ads",
                    GL_Description="Paid Ads",
                    Budget=300.0,
                    Actual=360.0,
                    Variance_USD=60.0,
                    Variance_Pct=20.0,
                    Driver_Category="Volume",
                )
            ],
            Executive_Commentary="Marketing Ads is above plan due to higher media volume.",
            Compliance_Flag=False,
            Re_calculation_Check_Passed=False,
        )

        self.assertTrue(verify_llm_output(raw, payload))
        self.assertTrue(payload.Re_calculation_Check_Passed)

    def test_verify_llm_output_flags_bad_llm_arithmetic(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "CostCenter": "IT Ops",
                    "GL_Account": "Cloud Hosting",
                    "GL_Description": "Cloud Hosting",
                    "Budget": 100_000.0,
                    "Actual": 112_000.0,
                }
            ]
        )
        payload = FinancialAnalysisOutput(
            Summary_Header="IT Ops variance",
            Top_Variance_Drivers=[
                VarianceItem(
                    CostCenter="IT Ops",
                    GL_Account="Cloud Hosting",
                    GL_Description="Cloud Hosting",
                    Budget=100_000.0,
                    Actual=112_000.0,
                    Variance_USD=50_000.0,
                    Variance_Pct=50.0,
                    Driver_Category="Price",
                )
            ],
            Executive_Commentary="Cloud Hosting is over budget.",
            Compliance_Flag=False,
            Re_calculation_Check_Passed=True,
        )

        with self.assertWarns(FinancialValidationWarning):
            passed = verify_llm_output(raw, payload)

        self.assertFalse(passed)
        self.assertFalse(payload.Re_calculation_Check_Passed)
        self.assertTrue(payload.Compliance_Flag)

    def test_invalid_driver_category_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            VarianceItem(
                CostCenter="Finance",
                GL_Account="External Consulting",
                GL_Description="External Consulting",
                Budget=100.0,
                Actual=120.0,
                Variance_USD=20.0,
                Variance_Pct=20.0,
                Driver_Category="InventedCategory",
            )

    def test_zero_budget_keeps_amount_and_undefined_percentage(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "CostCenter": "Finance",
                    "GL_Account": "External Consulting",
                    "Budget": 0.0,
                    "Actual": 10_000.0,
                }
            ]
        )

        result = calculate_exact_variances(raw)
        self.assertEqual(result.loc[0, "Variance_USD"], 10_000.0)
        self.assertTrue(pd.isna(result.loc[0, "Variance_Pct"]))

    def test_response_validator_accepts_numbers_from_tool_output(self) -> None:
        source_summary = {
            "total_budget": 100_000.0,
            "total_actual": 125_000.0,
            "variance_usd": 25_000.0,
            "variance_pct": 25.0,
        }

        self.assertTrue(
            validate_response_against_dataframe(
                "Budget was $100,000 and variance was $25,000 or 25.0%.",
                source_summary,
            )
        )

    def test_response_validator_rejects_unsupported_large_numbers(self) -> None:
        source_summary = {
            "total_budget": 100_000.0,
            "total_actual": 125_000.0,
            "variance_usd": 25_000.0,
            "variance_pct": 25.0,
        }

        self.assertFalse(
            validate_response_against_dataframe(
                "Budget was $100,000 and variance was $88,000.",
                source_summary,
            )
        )


if __name__ == "__main__":
    unittest.main()
