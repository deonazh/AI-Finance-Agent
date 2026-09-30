"""Generate a reporting pack from the committed synthetic SGD scenario."""
from argparse import ArgumentParser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents import run_variance_analysis
from data_engine import load_budget_actuals_file
from review import review_register, month_end_pack


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs'/'demo_review.xlsx')
    args = parser.parse_args()
    df = load_budget_actuals_file(ROOT/'examples'/'monthly_budget_actuals.csv')
    brief = run_variance_analysis(df, use_llm=False)
    pack = month_end_pack(df, review_register(df), {'Source': 'Committed synthetic SGD scenario', 'Reporting year': 2026,
        'Through period': 3, 'Percentage threshold': 10, 'Amount threshold': 50_000}, brief.executive_markdown)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pack)
    print(f"Rows: {len(df)} | Budget: SGD {df.budget.sum():,.2f} | Actual: SGD {df.actual.sum():,.2f} | Variance: SGD {df.variance.sum():,.2f}")
    print(f"Saved: {args.output}")


if __name__ == '__main__':
    main()
