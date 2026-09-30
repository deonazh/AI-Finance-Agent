"""Regression tests for agent-level calculation guardrails."""

from __future__ import annotations

import unittest

import pandas as pd

from agents import _analyst_numbers_match, _build_deterministic_analysis


class AgentGuardrailTests(unittest.TestCase):
    def test_deterministic_analysis_recalculates_stale_variance_columns(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "fiscal_year": 2026,
                    "period_label": "FY2026-P01",
                    "cost_center": "IT Ops",
                    "gl_account": "Cloud Hosting",
                    "budget": 100_000.0,
                    "actual": 125_000.0,
                    "variance": 999_999.0,
                    "variance_pct": 999.0,
                    "synthetic_driver": "price",
                }
            ]
        )

        analysis = _build_deterministic_analysis(raw, threshold_pct=10.0, threshold_dollars=50_000.0)

        self.assertEqual(analysis.total_budget, 100_000.0)
        self.assertEqual(analysis.total_actual, 125_000.0)
        self.assertEqual(analysis.net_variance, 25_000.0)
        self.assertEqual(analysis.net_variance_pct, 25.0)
        self.assertEqual(analysis.top_cost_drivers[0].variance, 25_000.0)

    def test_analyst_numeric_guardrail_rejects_changed_llm_numbers(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "fiscal_year": 2026,
                    "period_label": "FY2026-P01",
                    "cost_center": "Marketing",
                    "gl_account": "Marketing Campaigns",
                    "budget": 100_000.0,
                    "actual": 130_000.0,
                }
            ]
        )
        expected = _build_deterministic_analysis(raw, threshold_pct=10.0, threshold_dollars=50_000.0)
        changed = expected.model_copy(deep=True)
        changed.top_cost_drivers[0].variance = 75_000.0

        self.assertFalse(_analyst_numbers_match(expected, changed))


if __name__ == "__main__":
    unittest.main()
