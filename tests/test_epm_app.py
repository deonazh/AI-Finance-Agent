"""Exercise saved planning workflows through actual Streamlit controls."""
from pathlib import Path
from copy import deepcopy
from datetime import date
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tests.streamlit_helpers import stateful_tabs

from epm import fingerprint
from epm_store import PlanStore
from epm_ui import SECTIONS


class PlanningAppTests(unittest.TestCase):
    def setUp(self):
        transport = stateful_tabs(); transport.start(); self.addCleanup(transport.stop)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "planning.sqlite3")
        env = patch.dict("os.environ", {"FPA_DATABASE_PATH": self.database})
        env.start(); self.addCleanup(env.stop)
        self.app = self.new_app()
        self.app.button(key="epm_create_demo").click().run()
        self.assert_clean()

    def new_app(self):
        app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
        app.session_state["main_navigation"] = "Planning"
        return app.run()

    def assert_clean(self):
        self.assertEqual([e.message for e in self.app.exception], [])
        self.assertEqual([e.value for e in self.app.error], [])

    def section(self, name):
        self.app.session_state["main_navigation"] = name
        self.app.run()
        self.assert_clean()

    def click(self, label):
        next(button for button in self.app.button if button.label == label).click().run()
        self.assert_clean()

    def test_each_workspace_section_renders(self):
        for name in SECTIONS:
            with self.subTest(section=name):
                self.section(name)
                self.assertEqual(len(self.app.chat_input), 1)

    def test_forecast_saves_and_survives_new_browser_session(self):
        self.section("Forecasting")
        self.app.selectbox(key="epm_forecast_method").set_value("Trailing 3 months")
        self.click("Generate forecast")
        record = self.app.session_state.epm_record
        self.assertEqual(record["revision"], 2)
        second = self.new_app()
        self.assertEqual(second.session_state.epm_record["plan"], record["plan"])

    def test_chat_change_is_previewed_before_save(self):
        self.section("Planning")
        original = fingerprint(self.app.session_state.epm_record["plan"])
        self.app.chat_input(key="epm_chat_input").set_value("increase Marketing by 8%").run()
        self.assert_clean()
        self.assertEqual(fingerprint(self.app.session_state.epm_record["plan"]), original)
        self.assertIn("epm_proposal", self.app.session_state)
        self.app.button(key="epm_apply_proposal").click().run()
        self.assert_clean()
        self.assertNotEqual(fingerprint(self.app.session_state.epm_record["plan"]), original)
        self.assertEqual(self.app.session_state.epm_record["revision"], 2)

    @patch("planning_chatbot.date")
    def test_budget_whatif_preview_export_and_apply_touch_budget_only(self, clock):
        clock.today.return_value = date(2026, 9, 30)
        self.section("Budgeting")
        original = deepcopy(self.app.session_state.epm_record)
        self.app.chat_input(key="epm_chat_input").set_value("What if I increase Marketing budget by 8% for next month?").run()
        self.assert_clean()
        self.assertEqual(PlanStore(self.database).get(original["id"])["revision"], 1)
        latest = self.app.session_state.epm_chat_messages[-1]
        self.assertIn("2026-10", latest["content"])
        self.assertIn("metric", latest["table"].columns)
        self.assertTrue(any("illustrative" in exp.label for exp in self.app.expander))
        self.section("Cash planning")
        self.app.chat_input(key="epm_chat_input").set_value("export that to Excel").run()
        self.assert_clean()
        self.assertEqual(len(self.app.session_state.epm_chat_exports), 1)
        self.assertEqual(PlanStore(self.database).get(original["id"])["revision"], 1)
        self.app.button(key="epm_apply_proposal").click().run()
        self.assert_clean()
        saved = PlanStore(self.database).get(original["id"])
        self.assertEqual(saved["revision"], 2)
        changed = []
        for before, after in zip(original["plan"]["rows"], saved["plan"]["rows"]):
            self.assertEqual(before["forecast_cents"], after["forecast_cents"])
            self.assertEqual(before["actual_cents"], after["actual_cents"])
            if before["budget_cents"] != after["budget_cents"]:
                changed.append(after)
        self.assertEqual(len(changed), 1)
        self.assertEqual((changed[0]["period"], changed[0]["account"], changed[0]["budget_cents"]), ("2026-10", "Marketing", 1684800))

    def test_locked_baseline_allows_preview_but_disables_budget_application(self):
        self.section("Plan review")
        self.click("Create separate version")
        record = deepcopy(self.app.session_state.epm_record)
        self.assertTrue(record["budget_locked"])
        self.section("Budgeting")
        for rate in [8, 0]:
            self.app.chat_input(key="epm_chat_input").set_value(f"increase Marketing budget by {rate}% for October 2026").run()
            self.assert_clean()
            self.assertIn("epm_proposal", self.app.session_state)
            self.assertTrue(self.app.button(key="epm_apply_proposal").disabled)
        self.assertEqual(PlanStore(self.database).get(record["id"])["revision"], 1)
        self.app.chat_input(key="epm_chat_input").set_value("increase Marketing forecast by 8% for October 2026").run()
        self.assert_clean()
        self.assertFalse(self.app.button(key="epm_apply_proposal").disabled)

    def test_submitted_plan_whatif_stays_read_only(self):
        self.section("Plan review")
        self.app.text_area(key="epm_review_note").set_value("Review before approval")
        self.click("Record review decision")
        self.section("Budgeting")
        self.app.chat_input(key="epm_chat_input").set_value("increase Marketing forecast by 8% for October 2026").run()
        self.assert_clean()
        self.assertIn("epm_proposal", self.app.session_state)
        self.assertTrue(self.app.button(key="epm_apply_proposal").disabled)
        self.assertEqual(self.app.session_state.epm_record["revision"], 2)

    def test_review_locks_version_and_clone_keeps_original(self):
        self.section("Plan review")
        original = self.app.session_state.epm_record["id"]
        self.app.text_area(key="epm_review_note").set_value("Ready for review")
        self.click("Record review decision")
        self.assertEqual(self.app.session_state.epm_record["state"], "Submitted")
        self.app.text_area(key="epm_review_note").set_value("Reviewed assumptions")
        self.click("Record review decision")
        self.assertEqual(self.app.session_state.epm_record["state"], "Approved")
        self.section("Forecasting")
        self.assertTrue(next(b for b in self.app.button if b.label == "Generate forecast").disabled)
        self.section("Plan review")
        self.click("Create separate version")
        current = self.app.session_state.epm_record
        self.assertEqual(current["parent_id"], original)
        self.assertEqual(current["state"], "Draft")
        self.assertTrue(current["budget_locked"])
        self.assertEqual(PlanStore(self.database).get(original)["state"], "Approved")

    def test_workforce_and_capex_schedules_save_through_editor(self):
        self.section("Business drivers")
        self.app.text_input(key="epm_drivers_reason").set_value("Confirmed staffing and asset schedule")
        self.click("Apply schedules and save")
        self.assertEqual(self.app.session_state.epm_record["revision"], 2)
        self.assertEqual(len(self.app.session_state.epm_record["plan"]["workforce"]), 2)

    def test_budget_grid_persists_changed_amount(self):
        self.section("Budgeting")
        record = self.app.session_state.epm_record
        key = f"epm_grid_{record['id']}_{record['revision']}_budget_All_All"
        self.app.session_state[key] = {"edited_rows": {0: {"budget": 123456.78}}, "added_rows": [], "deleted_rows": []}
        self.app.text_input(key="epm_grid_note").set_value("Updated contract target")
        self.click("Save monthly inputs")
        self.assertEqual(self.app.session_state.epm_record["plan"]["rows"][0]["budget_cents"], 12345678)

    def test_cash_assumptions_and_workbook_are_available(self):
        self.section("Cash planning")
        next(n for n in self.app.number_input if n.label == "Opening cash").set_value(400000.0)
        self.click("Save cash assumptions")
        self.assertEqual(self.app.session_state.epm_record["plan"]["cash"]["opening_cash_cents"], 40000000)
        self.section("Plan review")
        self.click("Prepare FP&A workbook")
        self.assertGreater(len(self.app.session_state.epm_pack[1]), 10000)

    def test_chat_history_follows_tabs_and_exports_same_scope(self):
        self.section("Budgeting")
        self.app.chat_input(key="epm_chat_input").set_value("show budget for 2026").run()
        self.assert_clean()
        self.section("Forecasting")
        self.assertEqual(len(self.app.session_state.epm_chat_messages), 2)
        self.app.chat_input(key="epm_chat_input").set_value("what about 2027?").run()
        self.assert_clean()
        self.assertEqual(self.app.session_state.epm_chat_context["start"], "2027-01")
        self.section("Cash planning")
        self.app.button(key="epm_chat_suggestion_3").click().run()
        self.assert_clean()
        self.assertEqual(len(self.app.session_state.epm_chat_exports), 1)
        self.app.button(key="epm_chat_toggle").click().run()
        self.assert_clean()
        self.assertTrue(self.app.session_state.epm_chat_expanded)
        self.assertEqual(len(self.app.session_state.epm_chat_messages), 6)
        self.app.button(key="epm_chat_toggle").click().run()
        self.assert_clean()
        self.click("Save cash assumptions")
        self.assertEqual(self.app.session_state.epm_chat_messages, [])

    def test_global_guide_loads_without_a_ledger(self):
        self.section("Guide")
        for topic in ["Getting started", "Planning workflows", "Reporting workflows"]:
            self.app.selectbox(key="guide_topic").set_value(topic).run()
            self.assert_clean()

    def test_switching_workspace_preserves_saved_planning_work(self):
        original = self.app.session_state.epm_record["id"]
        PlanStore(self.database).create(self.app.session_state.epm_record["plan"], "Other analyst")
        self.app.session_state["main_navigation"] = "Reporting"
        self.app.run()
        self.assert_clean()
        self.assertEqual(len(self.app.tabs), 12)
        self.app.session_state["main_navigation"] = "Planning"
        self.app.run()
        self.assert_clean()
        self.assertEqual(self.app.session_state.epm_record["id"], original)


if __name__ == "__main__":
    unittest.main()
