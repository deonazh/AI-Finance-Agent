"""FP&A planning services. Stored monetary amounts are integer minor units.

No model-generated code or formulas are evaluated. Mutations return a new plan.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from io import BytesIO
import json
import re

import pandas as pd

from exports import workbook_bytes

CATEGORIES = ("Revenue", "COGS", "Payroll", "Opex", "Depreciation")
METHODS = ("Budget", "Last actual", "Trailing 3 months")
KEYS = ["period", "department", "account"]
MAX_CENTS = 100_000_000_000_000


def cents(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Amounts must be finite numbers; use an explicit zero when appropriate.")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal(MAX_CENTS) / 100:
            raise ValueError("Amount is nonfinite or exceeds the planning limit.")
        return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Amounts must be finite numbers.") from exc


def rounded(value):
    number = Decimal(str(value))
    if not number.is_finite() or abs(number) > MAX_CENTS:
        raise ValueError("Calculated amount exceeds the planning limit. Review the model inputs.")
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Periods must use YYYY-MM.")
    if not 1900 <= int(value[:4]) <= 2199:
        raise ValueError("Planning years must be between 1900 and 2199.")
    return value


def periods(start, count):
    month(start)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 36:
        raise ValueError("Choose a horizon of 1–36 months.")
    result = pd.period_range(start, periods=count, freq="M").astype(str).tolist()
    for value in result:
        month(value)
    return result


def text_field(value, name, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1–{maximum} characters.")
    return value.strip()


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("Unsupported planning document version.")
    allowed = {"schema_version", "name", "currency", "start", "months", "closed_through", "rows", "workforce", "capex", "cash", "assumptions", "source", "kind"}
    if set(plan) != allowed:
        raise ValueError("Planning document contains missing or unsupported fields.")
    text_field(plan["name"], "Plan name")
    text_field(plan["source"], "Source", 500)
    if plan["kind"] not in {"Budget", "Forecast", "Scenario"}:
        raise ValueError("Choose Budget, Forecast, or Scenario.")
    if not isinstance(plan["currency"], str) or not re.fullmatch(r"[A-Z]{3}", plan["currency"]):
        raise ValueError("Use a three-letter reporting currency.")
    horizon = periods(plan["start"], plan["months"])
    if month(plan["closed_through"]) > horizon[-1] or plan["closed_through"] < str(pd.Period(horizon[0], freq="M") - 1):
        raise ValueError("Closed-through month must be within the horizon or immediately before it.")
    rows = plan["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50_000:
        raise ValueError("A plan requires 1–50,000 monthly lines.")
    seen, categories, members = set(), {}, {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {*KEYS, "category", "budget_cents", "actual_cents", "forecast_cents"}:
            raise ValueError("Invalid planning line fields.")
        if row["period"] not in horizon or row["category"] not in CATEGORIES:
            raise ValueError("A line has an invalid period or account category.")
        for field in ["department", "account"]:
            if text_field(row[field], field) != row[field]:
                raise ValueError("Remove surrounding whitespace from account and department names.")
        key = tuple(row[k] for k in KEYS)
        if key in seen:
            raise ValueError("Duplicate month/department/account planning line.")
        seen.add(key)
        pair = (row["department"], row["account"])
        if pair in categories and categories[pair] != row["category"]:
            raise ValueError("An account must keep the same category across periods.")
        categories[pair] = row["category"]
        members.setdefault(pair, set()).add(row["period"])
        for field in ["budget_cents", "actual_cents", "forecast_cents"]:
            value = row[field]
            if field == "actual_cents" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_CENTS:
                raise ValueError("Stored monetary values must be bounded integer minor units.")
        if row["period"] > plan["closed_through"] and row["actual_cents"] is not None:
            raise ValueError("Future periods cannot contain posted actuals. Advance the actuals cutoff explicitly.")
    if any(values != set(horizon) for values in members.values()):
        raise ValueError("Each department/account must have one line for every planning month.")
    if not isinstance(plan["assumptions"], list) or len(plan["assumptions"]) > 200:
        raise ValueError("A plan supports up to 200 assumption notes.")
    for note in plan["assumptions"]:
        text_field(note, "Assumption", 2000)
    cash = plan["cash"]
    if not isinstance(cash, dict) or set(cash) != {"as_of", "opening_cash_cents", "opening_ar_cents", "opening_ap_cents", "collection_lag", "payment_lag", "monthly_other_outflow_cents", "monthly_financing_cents"}:
        raise ValueError("Invalid cash assumptions.")
    for field, value in cash.items():
        if field == "as_of":
            month(value)
            continue
        maximum = 3 if field.endswith("lag") else MAX_CENTS
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > maximum or (field.endswith("lag") and value < 0):
            raise ValueError("Invalid cash amount or payment/collection lag (0–3 months).")
    if cash["opening_ar_cents"] < 0 or cash["opening_ap_cents"] < 0:
        raise ValueError("Opening receivables and payables must be nonnegative.")
    _validate_schedules(plan)
    return plan


def _validate_schedules(plan):
    for collection in ["workforce", "capex"]:
        rows = plan[collection]
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("Driver schedules must have at most 500 entries.")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid driver schedule.")
            required = {"department", "account", "start", "end", "heads", "monthly_salary_cents", "burden_pct", "annual_raise_pct"} if collection == "workforce" else {"department", "account", "start", "asset", "cost_cents", "life_months"}
            if set(row) != required:
                raise ValueError("Invalid driver schedule fields.")
            text_field(row["department"], "Department")
            text_field(row["account"], "Account")
            month(row["start"])
            if collection == "workforce":
                if month(row["end"]) < row["start"]:
                    raise ValueError("A position end month must follow its start month.")
                for key, limit in [("heads", 100000), ("burden_pct", 200), ("annual_raise_pct", 100)]:
                    value = row[key]
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or not Decimal(str(value)).is_finite() or not 0 <= value <= limit:
                        raise ValueError("Headcount and workforce rates must be finite and nonnegative.")
                value = row["monthly_salary_cents"]
            else:
                text_field(row["asset"], "Asset")
                life = row["life_months"]
                if isinstance(life, bool) or not isinstance(life, int) or not 1 <= life <= 600:
                    raise ValueError("Asset life must be 1–600 months.")
                value = row["cost_cents"]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CENTS:
                raise ValueError("Driver cost must be a nonnegative amount.")


def base_plan(name, start="2026-01", count=24, cutoff="2026-06", currency="SGD", kind="Budget"):
    return {"schema_version": 1, "name": name, "currency": currency, "start": start, "months": count,
            "closed_through": cutoff, "kind": kind, "rows": [], "workforce": [], "capex": [], "assumptions": [],
            "source": "Manual planning workspace", "cash": {"as_of": cutoff, "opening_cash_cents": 0, "opening_ar_cents": 0,
            "opening_ap_cents": 0, "collection_lag": 1, "payment_lag": 1, "monthly_other_outflow_cents": 0, "monthly_financing_cents": 0}}


def demo_plan():
    plan = base_plan("Service business · operating plan")
    plan["source"] = "Synthetic service business; no company data"
    accounts = [("Sales", "Service contracts", "Revenue", 180000), ("Delivery", "Project revenue", "Revenue", 60000),
                ("Delivery", "Delivery costs", "COGS", 65000), ("Delivery", "Payroll", "Payroll", 65000),
                ("Sales", "Payroll", "Payroll", 30000), ("Corporate", "Facilities and admin", "Opex", 20000),
                ("Sales", "Marketing", "Opex", 15000), ("Corporate", "Depreciation", "Depreciation", 3000)]
    for i, period in enumerate(periods(plan["start"], plan["months"])):
        for department, account, category, amount in accounts:
            budget = cents(Decimal(amount) * (Decimal("1.04") if period.endswith(("10", "11", "12")) else 1))
            actual = rounded(Decimal(budget) * Decimal(str(1 + ((i % 3) - 1) * .02 + (.03 if category == "Revenue" else .01)))) if period <= plan["closed_through"] else None
            plan["rows"].append(dict(period=period, department=department, account=account, category=category,
                                     budget_cents=budget, actual_cents=actual, forecast_cents=budget))
    plan["cash"].update(opening_cash_cents=cents(300000), opening_ar_cents=cents(240000), opening_ap_cents=cents(100000))
    plan["assumptions"] = ["Synthetic operating plan. Opening cash, AR and AP are balances immediately after June 2026.",
        "Revenue is recorded positively; costs are recorded positively. Actuals through June; future actuals are unknown."]
    workers = [dict(department="Delivery", account="Payroll", start="2026-01", end="2027-12", heads=10, monthly_salary_cents=cents(5000), burden_pct=30, annual_raise_pct=3),
               dict(department="Sales", account="Payroll", start="2026-01", end="2027-12", heads=4, monthly_salary_cents=cents(6000), burden_pct=25, annual_raise_pct=3)]
    assets = [dict(department="Corporate", account="Depreciation", start="2025-01", asset="Existing equipment", cost_cents=cents(108000), life_months=36),
              dict(department="Corporate", account="Depreciation", start="2026-09", asset="Planned equipment", cost_cents=cents(24000), life_months=24)]
    return apply_drivers(plan, workers, assets)


def frame(plan):
    df = pd.DataFrame(plan["rows"])
    for name in ["budget", "actual", "forecast"]:
        df[name] = df[name + "_cents"].map(lambda v: None if pd.isna(v) else int(v) / 100)
    return df[[*KEYS, "category", "budget", "actual", "forecast"]]


def from_frame(df, name, start, count, cutoff, currency, source="CSV / XLSX planning import"):
    required = {*KEYS, "category", "budget", "actual", "forecast"}
    if not required <= set(df.columns) or len(df) > 50_000:
        raise ValueError("Planning input requires period, department, account, category, budget, actual, forecast (maximum 50,000 lines).")
    if "currency" in df and set(df.currency.astype(str).str.strip()) != {currency}:
        raise ValueError("Planning input currency must match the selected reporting currency.")
    plan = base_plan(name, start, count, cutoff, currency)
    plan["source"] = source
    for row in df.to_dict("records"):
        values = {key: str(row[key]).strip() if not pd.isna(row[key]) else "" for key in [*KEYS, "category"]}
        values.update({key + "_cents": None if key == "actual" and pd.isna(row[key]) else cents(row[key]) for key in ["budget", "actual", "forecast"]})
        plan["rows"].append(values)
    return validate_plan(plan)


def read_planning_file(data, name):
    if len(data) > 20_000_000:
        raise ValueError("Planning uploads must be under 20 MB.")
    try:
        raw = pd.read_excel(BytesIO(data), header=None) if name.lower().endswith(".xlsx") else pd.read_csv(BytesIO(data), header=None)
        headers = [str(v).strip().lower() for v in raw.iloc[0]]
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate input headers.")
        df = raw.iloc[1:].copy()
        df.columns = headers
        return df.reset_index(drop=True)
    except (IndexError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError("Unable to read a populated planning table.") from exc


def from_expense_ledger(df, year, cutoff, name):
    if isinstance(cutoff, bool) or not isinstance(cutoff, int) or not 0 <= cutoff <= 12:
        raise ValueError("Choose 0–12 closed months for the expense ledger.")
    selected = df[df.fiscal_year == year].copy()
    if selected.empty:
        raise ValueError("The source has no data for that year.")
    close = f"{year}-{cutoff:02d}" if cutoff else str(pd.Period(f"{year}-01", freq="M") - 1)
    plan = base_plan(name, f"{year}-01", 12, close, str(selected.currency.iloc[0]))
    plan["source"] = "Imported expense ledger; full-year budget coverage must be reviewed"
    for (department, account), group in selected.groupby(["cost_center", "gl_account"]):
        indexed = group.set_index("period")
        for index, period in enumerate(periods(plan["start"], 12), 1):
            if index not in indexed.index:
                raise ValueError("The expense source must include all 12 budget months for each account. Use the planning template for incomplete extracts.")
            source = indexed.loc[index]
            plan["rows"].append(dict(period=period, department=str(department), account=str(account), category="Opex",
                budget_cents=cents(source.budget), actual_cents=cents(source.actual) if index <= cutoff else None,
                forecast_cents=cents(source.budget)))
    plan["assumptions"] = ["Imported accounts are classified as Opex. Map payroll and depreciation explicitly if needed. No revenue inferred."]
    return validate_plan(plan)


def fingerprint(plan):
    return sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def roll_horizon(plan, name, start, count):
    horizon = periods(start, count)
    if start > str(pd.Period(plan["closed_through"], freq="M") + 1) or horizon[-1] <= plan["closed_through"]:
        raise ValueError("A rolling horizon must start no later than the first open month and include future periods.")
    if start < plan["start"]:
        raise ValueError("A rolling horizon cannot invent earlier actual history. Import that history as a separate plan.")
    updated = deepcopy(plan)
    updated.update(name=text_field(name, "Plan name"), start=start, months=count, kind="Forecast", rows=[])
    groups = {}
    for row in plan["rows"]:
        groups.setdefault((row["department"], row["account"]), {})[row["period"]] = row
    for pair, available in groups.items():
        for period in horizon:
            if period in available:
                row = deepcopy(available[period])
            else:
                previous_year = str(pd.Period(period, freq="M") - 12)
                reference = available.get(previous_year, available[max(available)])
                row = {**reference, "period": period, "actual_cents": None, "forecast_cents": reference["budget_cents"]}
                available[period] = row
            updated["rows"].append(row)
    updated["assumptions"].append(f"Rolled horizon to {start} for {count} months. New months use the same month of the preceding year's budget where available, otherwise the last known budget. Review these seeded targets; they are not an approved extension of the source budget.")
    return validate_plan(updated)


def actuals_fingerprint(plan):
    rows = sorted([(r["period"], r["department"], r["account"], r["category"], r["actual_cents"]) for r in plan["rows"] if r["period"] <= plan["closed_through"]])
    return sha256(json.dumps([plan["currency"], plan["closed_through"], rows], separators=(",", ":")).encode()).hexdigest()


def missing_actuals(plan):
    return sum(r["actual_cents"] is None for r in plan["rows"] if r["period"] <= plan["closed_through"])


def replace_grid(plan, edited, target="forecast"):
    if target not in {"forecast", "budget"}:
        raise ValueError("Only budget or forecast can be edited here.")
    checked = from_frame(edited, plan["name"], plan["start"], plan["months"], plan["closed_through"], plan["currency"])
    original = {tuple(row[k] for k in KEYS): row for row in plan["rows"]}
    if set(original) != {tuple(row[k] for k in KEYS) for row in checked["rows"]}:
        raise ValueError("Grid edits cannot add, remove, or rename accounts. Use account setup instead.")
    updated = deepcopy(plan)
    updates = {tuple(row[k] for k in KEYS): row for row in checked["rows"]}
    for row in updated["rows"]:
        other = updates[tuple(row[k] for k in KEYS)]
        for key in ["category", "actual_cents"]:
            if row[key] != other[key]:
                raise ValueError("Actuals and account categories are protected in the planning grid.")
        if target == "forecast" and row["period"] <= plan["closed_through"]:
            if row["forecast_cents"] != other["forecast_cents"]:
                raise ValueError("Closed-month forecasts are protected; the outlook uses actuals.")
        else:
            row[target + "_cents"] = other[target + "_cents"]
    return validate_plan(updated)


def add_account(plan, department, account, category, monthly_budget):
    department = text_field(department, "Department")
    account = text_field(account, "Account")
    if any(r["department"] == department and r["account"] == account for r in plan["rows"]):
        raise ValueError("That department/account already exists.")
    updated = deepcopy(plan)
    for period in periods(plan["start"], plan["months"]):
        updated["rows"].append(dict(period=period, department=department, account=account, category=category,
            budget_cents=cents(monthly_budget), actual_cents=None, forecast_cents=cents(monthly_budget)))
    updated["assumptions"].append(f"Added {department} / {account}; closed-period actuals must be supplied explicitly.")
    return validate_plan(updated)


def allocate_budget(plan, department, account, year, total, weights):
    try:
        values = [Decimal(value.strip()) for value in weights.split(",")]
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Provide 12 comma-separated nonnegative monthly weights.") from exc
    if len(values) != 12 or any(not v.is_finite() or not 0 <= v <= 1_000_000_000 for v in values) or sum(values) <= 0:
        raise ValueError("Provide 12 finite, nonnegative weights with a positive total.")
    updated = deepcopy(plan)
    selected = sorted([r for r in updated["rows"] if r["department"] == department and r["account"] == account and r["period"].startswith(str(year) + "-")], key=lambda r: r["period"])
    if len(selected) != 12:
        raise ValueError("Annual allocation requires all 12 months for the selected account and year.")
    amount = cents(total)
    # Cumulative rounding allocates every minor unit and leaves zero-weight months at zero.
    cumulative, previous = Decimal(0), 0
    for row, weight in zip(selected, values):
        cumulative += weight
        current = rounded(Decimal(amount) * cumulative / sum(values))
        row["budget_cents"] = current - previous
        previous = current
    updated["assumptions"].append(f"Allocated {total:g} across {year}, {department} / {account}, weights {weights}.")
    return validate_plan(updated)


def apply_actuals(plan, df, cutoff):
    month(cutoff)
    if cutoff < plan["closed_through"]:
        raise ValueError("Actuals cutoff cannot move backwards.")
    if not {*KEYS, "actual"} <= set(df.columns) or len(df) > 50_000:
        raise ValueError("Actuals upload requires period, department, account, actual.")
    if "currency" in df and set(df.currency.astype(str)) != {plan["currency"]}:
        raise ValueError("Actuals currency must match the selected plan.")
    keys = {tuple(row[k] for k in KEYS) for row in plan["rows"]}
    updates = {}
    for row in df.to_dict("records"):
        key = tuple(str(row[k]).strip() for k in KEYS)
        if key not in keys or key in updates or key[0] > cutoff:
            raise ValueError("Actuals contain an unknown/duplicate key or a period beyond the cutoff.")
        updates[key] = cents(row["actual"])
    updated = deepcopy(plan)
    updated["closed_through"] = cutoff
    for row in updated["rows"]:
        key = tuple(row[k] for k in KEYS)
        if key in updates:
            row["actual_cents"] = updates[key]
    updated["assumptions"].append(f"Actuals refreshed through {cutoff}; {len(updates)} source rows supplied. Missing values remain unknown.")
    return validate_plan(updated)


def forecast(plan, method, growth_pct=0):
    if method not in METHODS:
        raise ValueError("Unsupported forecast method.")
    if not any(r["period"] > plan["closed_through"] for r in plan["rows"]):
        raise ValueError("No future periods remain. Roll the horizon before generating a forecast.")
    growth = Decimal(str(growth_pct))
    if not growth.is_finite() or not -100 <= growth <= 1000:
        raise ValueError("Forecast uplift must be between -100% and 1,000%.")
    updated = deepcopy(plan)
    groups = {}
    for row in plan["rows"]:
        groups.setdefault((row["department"], row["account"]), []).append(row)
    baselines = {}
    for pair, rows in groups.items():
        history = sorted((r for r in rows if r["period"] <= plan["closed_through"]), key=lambda r: r["period"])
        count = 1 if method == "Last actual" else 3
        if method != "Budget":
            if len(history) < count or any(r["actual_cents"] is None for r in history[-count:]):
                raise ValueError(f"{pair[0]} / {pair[1]} needs {count} complete closed months for {method}.")
            baselines[pair] = Decimal(sum(r["actual_cents"] for r in history[-count:])) / count
    for row in updated["rows"]:
        if row["period"] > plan["closed_through"]:
            base = Decimal(row["budget_cents"]) if method == "Budget" else baselines[(row["department"], row["account"])]
            row["forecast_cents"] = rounded(base * (1 + growth / 100))
    updated["assumptions"].append(f"Forecast: {method}; one-time uplift {growth_pct:g}% applied to open months. No future actuals used.")
    return validate_plan(updated)


def adjust(plan, field, department="All", account="All", category="All", start=None, end=None, percent=0, monthly_delta=0):
    if field not in {"budget", "forecast"}:
        raise ValueError("Choose budget or forecast.")
    horizon = periods(plan["start"], plan["months"])
    start, end = start or horizon[0], end or horizon[-1]
    if start not in horizon or end not in horizon or end < start:
        raise ValueError("Select a valid start/end range within the planning horizon.")
    rate = Decimal(str(percent))
    if not rate.is_finite() or not -100 <= rate <= 1000:
        raise ValueError("Adjustment must be between -100% and 1,000%.")
    delta = cents(monthly_delta)
    updated, changes = deepcopy(plan), []
    for row in updated["rows"]:
        if not start <= row["period"] <= end or (field == "forecast" and row["period"] <= plan["closed_through"]):
            continue
        if any(choice != "All" and row[key] != choice for key, choice in [("department", department), ("account", account), ("category", category)]):
            continue
        before = row[field + "_cents"]
        after = rounded(Decimal(before) * (1 + rate / 100)) + delta
        row[field + "_cents"] = after
        changes.append({**{k: row[k] for k in KEYS}, "before": before / 100, "after": after / 100, "change": (after - before) / 100})
    if not changes:
        raise ValueError("No editable planning lines match that scope.")
    updated["assumptions"].append(f"{field.title()} adjustment: {department} / {account} / {category}; {start}–{end}; {percent:g}% and {monthly_delta:g} per matching line/month.")
    return validate_plan(updated), pd.DataFrame(changes)


def apply_drivers(plan, workforce, capex):
    updated = deepcopy(plan)
    updated["workforce"], updated["capex"] = workforce, capex
    _validate_schedules(updated)
    for collection, category in [(workforce, "Payroll"), (capex, "Depreciation")]:
        for item in collection:
            matches = [r for r in updated["rows"] if r["department"] == item["department"] and r["account"] == item["account"]]
            if not matches or any(r["category"] != category for r in matches):
                raise ValueError(f"Create the {category} account {item['department']} / {item['account']} before applying its schedule.")
    # Include old schedule accounts so deleting the final position/asset clears its future model values.
    targets = {(r["department"], r["account"]) for r in workforce + capex + plan["workforce"] + plan["capex"]}
    for row in updated["rows"]:
        if row["period"] <= plan["closed_through"] or (row["department"], row["account"]) not in targets:
            continue
        amount = 0
        for position in workforce:
            if (row["department"], row["account"]) == (position["department"], position["account"]) and position["start"] <= row["period"] <= position["end"]:
                anniversaries = (pd.Period(row["period"], freq="M").ordinal - pd.Period(position["start"], freq="M").ordinal) // 12
                amount += rounded(Decimal(position["monthly_salary_cents"]) * Decimal(str(position["heads"])) * (1 + Decimal(str(position["burden_pct"])) / 100) * (1 + Decimal(str(position["annual_raise_pct"])) / 100) ** anniversaries)
        for asset in capex:
            offset = pd.Period(row["period"], freq="M").ordinal - pd.Period(asset["start"], freq="M").ordinal
            if (row["department"], row["account"]) == (asset["department"], asset["account"]) and 0 <= offset < asset["life_months"]:
                base, remainder = divmod(asset["cost_cents"], asset["life_months"])
                amount += base + (1 if offset < remainder else 0)
        row["forecast_cents"] = amount
    updated["assumptions"].append("Driver schedules replace future forecast totals for their mapped payroll/depreciation accounts; budget and actuals are preserved. Assets start depreciating in their in-service month.")
    return validate_plan(updated)


def revenue_driver(plan, department, account, start, end, units, unit_price, volume_growth_pct=0):
    horizon = periods(plan["start"], plan["months"])
    if start not in horizon or end not in horizon or end < start or start <= plan["closed_through"]:
        raise ValueError("Revenue driver dates must select future periods within the plan.")
    volume, growth = Decimal(str(units)), Decimal(str(volume_growth_pct))
    price = cents(unit_price)
    if not volume.is_finite() or not growth.is_finite() or not 0 <= volume <= 1_000_000_000 or not -100 <= growth <= 100 or price < 0:
        raise ValueError("Use a nonnegative volume/price and monthly growth between -100% and 100%.")
    updated = deepcopy(plan)
    count = 0
    for row in updated["rows"]:
        if row["department"] == department and row["account"] == account and start <= row["period"] <= end:
            if row["category"] != "Revenue":
                raise ValueError("Price × volume planning requires a Revenue category account.")
            offset = pd.Period(row["period"], freq="M").ordinal - pd.Period(start, freq="M").ordinal
            row["forecast_cents"] = rounded(volume * Decimal(price) * (1 + growth / 100) ** offset)
            count += 1
    if not count:
        raise ValueError("No revenue account matches this scope.")
    updated["assumptions"].append(f"Revenue model {department} / {account}, {start}–{end}: {units:g} units × {unit_price:g} per unit; monthly volume growth {volume_growth_pct:g}%, fixed price. Replaces future forecast in that range.")
    return validate_plan(updated)


def monthly_report(plan):
    results = []
    for period in periods(plan["start"], plan["months"]):
        rows = [r for r in plan["rows"] if r["period"] == period]
        closed = period <= plan["closed_through"]
        complete = all(r["actual_cents"] is not None for r in rows) if closed else False
        result = {"period": period, "basis": "Actual" if closed else "Forecast", "actuals_complete": complete}
        for label, field in [("budget", "budget_cents"), ("outlook", "actual_cents" if closed else "forecast_cents")]:
            for category in CATEGORIES:
                value = None if field == "actual_cents" and not complete else sum(r[field] for r in rows if r["category"] == category)
                result[f"{label}_{category.lower()}"] = None if value is None else value / 100
            revenue = result[label + "_revenue"]
            profit = None if revenue is None else (sum(r[field] if r["category"] == "Revenue" else -r[field] for r in rows)) / 100
            result[label + "_operating_profit"] = profit
            result[label + "_ebitda"] = None if profit is None else (sum(r[field] if r["category"] == "Revenue" else -r[field] for r in rows if r["category"] != "Depreciation")) / 100
        result["profit_vs_budget"] = None if result["outlook_operating_profit"] is None else result["outlook_operating_profit"] - result["budget_operating_profit"]
        results.append(result)
    return pd.DataFrame(results)


def variance_report(plan):
    results = []
    for row in plan["rows"]:
        closed = row["period"] <= plan["closed_through"]
        amount = row["actual_cents"] if closed else row["forecast_cents"]
        delta = None if amount is None else amount - row["budget_cents"]
        results.append({**{k: row[k] for k in [*KEYS, "category"]}, "basis": "Actual" if closed else "Forecast",
            "budget": row["budget_cents"] / 100, "outlook": None if amount is None else amount / 100,
            "variance": None if delta is None else delta / 100,
            "favorable_impact": None if delta is None else delta / 100 * (1 if row["category"] == "Revenue" else -1)})
    return pd.DataFrame(results)


def cash_report(plan):
    future = [p for p in periods(plan["start"], plan["months"]) if p > plan["closed_through"]]
    cfg, results = plan["cash"], []
    if future and cfg["as_of"] != plan["closed_through"]:
        raise ValueError("Cash opening balances are from an earlier close. Update and confirm cash assumptions for the new actuals cutoff.")
    revenue, vendor, payroll = {}, {}, {}
    for period in future:
        rows = [r for r in plan["rows"] if r["period"] == period]
        revenue[period] = sum(r["forecast_cents"] for r in rows if r["category"] == "Revenue")
        vendor[period] = sum(r["forecast_cents"] for r in rows if r["category"] in {"COGS", "Opex"})
        payroll[period] = sum(r["forecast_cents"] for r in rows if r["category"] == "Payroll")
    cash, ar, ap = cfg["opening_cash_cents"], cfg["opening_ar_cents"], cfg["opening_ap_cents"]
    for i, period in enumerate(future):
        receipts = (cfg["opening_ar_cents"] if i == 0 else 0) + (revenue[future[i - cfg["collection_lag"]]] if i >= cfg["collection_lag"] else 0)
        payments = (cfg["opening_ap_cents"] if i == 0 else 0) + (vendor[future[i - cfg["payment_lag"]]] if i >= cfg["payment_lag"] else 0)
        capex = sum(a["cost_cents"] for a in plan["capex"] if a["start"] == period)
        net = receipts - payments - payroll[period] - capex - cfg["monthly_other_outflow_cents"] + cfg["monthly_financing_cents"]
        ar += revenue[period] - receipts
        ap += vendor[period] - payments
        results.append({"period": period, **{k: v / 100 for k, v in {"opening_cash": cash, "collections": receipts,
            "supplier_payments": payments, "payroll": payroll[period], "capex": capex,
            "other_outflows": cfg["monthly_other_outflow_cents"], "financing": cfg["monthly_financing_cents"],
            "net_cash_flow": net, "closing_cash": cash + net, "closing_receivables": ar, "closing_payables": ap}.items()}})
        cash += net
    return pd.DataFrame(results)


def compare_plans(plan, baseline):
    if plan["currency"] != baseline["currency"]:
        raise ValueError("Comparison requires the same reporting currency.")
    common = set(periods(plan["start"], plan["months"])) & set(periods(baseline["start"], baseline["months"]))
    if not common:
        raise ValueError("The planning horizons have no common months to compare.")
    current = variance_report(plan)[KEYS + ["category", "basis", "outlook"]]
    previous = variance_report(baseline)[KEYS + ["category", "basis", "outlook"]]
    current = current[current.period.isin(common)]
    previous = previous[previous.period.isin(common)]
    if set(map(tuple, current[KEYS + ["category"]].values)) != set(map(tuple, previous[KEYS + ["category"]].values)):
        raise ValueError("Comparison requires matching account/department/category coverage.")
    result = current.merge(previous, on=KEYS + ["category"], suffixes=("", "_baseline"), validate="one_to_one")
    result["change"] = result.outlook - result.outlook_baseline
    result["profit_impact"] = result["change"] * result.category.map(lambda v: 1 if v == "Revenue" else -1)
    return result


def backtest(plan):
    """Expanding, past-only one-step tests. Never train using future observations."""
    records = []
    df = frame(plan)
    for (department, account), group in df[df.period <= plan["closed_through"]].groupby(["department", "account"]):
        history = group.sort_values("period").to_dict("records")
        for index in range(3, len(history)):
            row, prior = history[index], history[index - 3:index]
            if pd.isna(row["actual"]) or any(pd.isna(r["actual"]) for r in prior):
                continue
            for method, predicted in [("Last actual", prior[-1]["actual"]), ("Trailing 3 months", sum(cents(r["actual"]) for r in prior) / 300)]:
                records.append(dict(department=department, account=account, period=row["period"], method=method,
                    actual=row["actual"], prediction=round(predicted, 2), error=abs(cents(predicted) - cents(row["actual"])) / 100))
    detail = pd.DataFrame(records)
    if detail.empty:
        return pd.DataFrame(), detail
    summaries = []
    for method, group in detail.groupby("method"):
        denominator = group.actual.abs().sum()
        summaries.append({"method": method, "observations": len(group), "mae": group.error.mean(),
            "wape_pct": group.error.sum() / denominator * 100 if denominator else None})
    return pd.DataFrame(summaries), detail


def planning_pack(plan, metadata=None):
    scores, detail = backtest(plan)
    sheets = {"Monthly P&L": monthly_report(plan), "Budget Actual Forecast": variance_report(plan), "Planning detail": frame(plan),
        "Cash forecast": cash_report(plan), "Workforce": pd.DataFrame(plan["workforce"]), "Capital plan": pd.DataFrame(plan["capex"]),
        "Cash assumptions": pd.DataFrame(list(plan["cash"].items()), columns=["Assumption", "Value"]),
        "Assumptions": pd.DataFrame({"Assumption": plan["assumptions"]}), "Forecast evaluation": scores, "Backtest detail": detail,
        "Methodology": pd.DataFrame({"Rule": ["Positive revenue and positive expenses. Positive favorable impact is beneficial.",
            "Outlook = actuals through cutoff + forecast afterward; incomplete actual months remain unknown.",
            "Cash: opening AR/AP settle in first forecast month; new revenue/vendor costs follow whole-month lags; payroll immediate.",
            "Capital purchases paid in full in start month; depreciation excluded from cash.",
            "Other cash outflows and financing are monthly assumptions. No automatic tax, VAT, inventory, or debt calculation.",
            "Schedule fields ending _cents are integer minor units. Headcount can include fractional FTEs.",
            "Backtests are historical one-step baselines; they do not establish future forecast accuracy."]})}
    return workbook_bytes(sheets, {"Plan": plan["name"], "Type": plan["kind"], "Currency": plan["currency"],
        "Start": plan["start"], "Months": plan["months"], "Actuals through": plan["closed_through"],
        "Plan fingerprint": fingerprint(plan), "Missing actual values": missing_actuals(plan), **(metadata or {})})


def planning_answer(plan, question):
    """Bounded local planning assistant; proposed changes never mutate the plan."""
    question = text_field(question, "Question", 4000)
    lowered = question.casefold().strip().rstrip("?.")
    match = re.fullmatch(r"(increase|decrease) (.+?) by (\d+(?:\.\d+)?)\s*%", lowered)
    if match:
        direction, scope, rate = match.groups()
        scopes = [(key, value) for key in ["department", "account", "category"] for value in sorted({r[key] for r in plan["rows"]}) if value.casefold() == scope]
        if len(scopes) != 1:
            return "Use one exact, unambiguous department, account, or category name. No changes were proposed.", None, None
        proposed, changes = adjust(plan, "forecast", **{scopes[0][0]: scopes[0][1]}, percent=float(rate) * (1 if direction == "increase" else -1))
        return f"Proposed change to {len(changes)} future lines for {scopes[0][1]}. Review the preview, then apply explicitly. Budget and actuals stay fixed.", changes, proposed
    if lowered in {"summarise plan", "summarize plan", "summarise", "summary", "summarize", "show forecast", "show p&l"}:
        report = monthly_report(plan)
        message = f"{plan['name']} · {plan['currency']} · actuals through {plan['closed_through']}. The outlook combines closed actuals with future forecasts."
        if missing_actuals(plan):
            message += f" {missing_actuals(plan)} actual values are missing; affected monthly totals are unknown."
        return message, report[["period", "basis", "budget_revenue", "outlook_revenue", "budget_operating_profit", "outlook_operating_profit"]], None
    if lowered in {"show cash", "cash forecast", "cash runway", "show cash forecast"}:
        cash = cash_report(plan)
        negative = cash[cash.closing_cash < 0] if not cash.empty else cash
        message = "No future months remain." if cash.empty else (f"First projected negative closing cash: {negative.iloc[0].period}." if not negative.empty else "No negative closing cash within the modeled horizon.")
        return message + " This follows the saved opening balances, payment lags, and other cash assumptions.", cash, None
    if lowered in {"show assumptions", "assumptions"}:
        return "Saved assumptions for this version.", pd.DataFrame({"Assumption": plan["assumptions"]}), None
    if lowered in {"top variances", "top unfavorable variances", "what changed"}:
        detail = variance_report(plan).dropna(subset=["favorable_impact"])
        return "Largest unfavorable monthly differences against budget; positive favorable impact is beneficial.", detail.sort_values("favorable_impact").head(10), None
    if lowered in {"forecast accuracy", "backtest", "evaluate forecast"}:
        scores, _ = backtest(plan)
        return "Historical one-step baseline evaluation using only earlier actuals. At least four complete months are needed; this does not validate future accuracy.", scores, None
    return "Try: summarise plan; show cash; top variances; show assumptions; forecast accuracy; or increase Marketing by 8%. Changes require an exact account, department, or category and explicit review. Use the scenario controls for more detailed changes.", None, None
