"""Regression coverage for reconciled charts and data-context changes."""
import unittest

from data_engine import generate_synthetic_budget_actuals
from dashboard import dataframe_fingerprint, monthly_summary, reconciled_waterfall, refresh_context


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.raw = generate_synthetic_budget_actuals()

    def test_waterfall_includes_all_movements_when_drivers_are_limited(self):
        trace = reconciled_waterfall(self.raw, self.raw, limit=2).data[0]
        self.assertIn("Other movements", trace.x)
        self.assertAlmostEqual(sum(trace.y[:-1]), self.raw.actual.sum(), places=6)
        self.assertEqual(trace.measure[-1], "total")

    def test_waterfall_reconciles_without_flagged_drivers(self):
        trace = reconciled_waterfall(self.raw, self.raw.iloc[:0]).data[0]
        self.assertAlmostEqual(sum(trace.y[:-1]), self.raw.actual.sum(), places=6)

    def test_waterfall_recalculates_stale_variances(self):
        self.raw["variance"] = 999999
        trace = reconciled_waterfall(self.raw, self.raw).data[0]
        self.assertAlmostEqual(sum(trace.y[1:-1]), (self.raw.actual - self.raw.budget).sum(), places=6)

    def test_monthly_trend_keeps_fiscal_years_separate(self):
        other = self.raw.assign(fiscal_year=2027)
        import pandas as pd
        summary = monthly_summary(pd.concat([self.raw, other]))
        self.assertEqual(len(summary), 24)
        self.assertEqual(summary.iloc[12].label, "FY2027 · P01")
        self.assertAlmostEqual(summary.actual.sum(), self.raw.actual.sum()*2)

    def test_changed_context_clears_generated_outputs(self):
        state = {"view": "before", "chat_messages": ["old"], "executive_markdown": "old",
                 "chat_exports": {"file": b"old"}, "quick_action_result": "old", "preference": "keep"}
        refresh_context(state, "view", "after")
        self.assertEqual(state, {"view": "after", "preference": "keep"})

    def test_unchanged_context_preserves_outputs(self):
        fingerprint = dataframe_fingerprint(self.raw)
        state = {"view": fingerprint, "chat_messages": ["answer"]}
        refresh_context(state, "view", dataframe_fingerprint(self.raw.copy()))
        self.assertEqual(state["chat_messages"], ["answer"])
        changed = self.raw.copy()
        changed.loc[0, "actual"] += 1
        self.assertNotEqual(dataframe_fingerprint(changed), fingerprint)
