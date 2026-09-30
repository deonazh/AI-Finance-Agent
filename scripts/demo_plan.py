"""Generate a reproducible synthetic FP&A planning pack without the UI."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epm import demo_plan, monthly_report, planning_pack


def main():
    plan = demo_plan()
    destination = ROOT / "outputs" / "synthetic_fpa_plan.xlsx"
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(planning_pack(plan))
    result = monthly_report(plan)
    current_year = result[result.period.str.startswith("2026")]
    print("Synthetic FY2026 revenue outlook:", current_year.outlook_revenue.sum())
    print("Synthetic FY2026 operating profit:", current_year.outlook_operating_profit.sum())
    print("FP&A workbook:", destination)


if __name__ == "__main__":
    main()
