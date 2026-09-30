"""Full-width budgeting, forecast, scenario, and review workspace."""
from copy import deepcopy
import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import style_chart
from epm import (CATEGORIES, METHODS, periods, base_plan, demo_plan, validate_plan, frame, from_frame,
    from_expense_ledger, read_planning_file, fingerprint, actuals_fingerprint, missing_actuals, replace_grid,
    add_account, apply_actuals, forecast, adjust, apply_drivers, monthly_report, variance_report, cash_report,
    compare_plans, backtest, planning_pack, planning_answer, cents, revenue_driver)
from epm_store import PlanStore
from exports import csv_bytes
from planning_chatbot import PlanningChatbot
from uuid import uuid4


SECTIONS = ["Planning", "Budgeting", "Forecasting", "Business drivers", "Cash planning", "Plan review"]


def _open(record):
    st.session_state.epm_record = record
    st.session_state.epm_open = record["id"]
    for key in ["epm_proposal", "epm_answer", "epm_pack"]:
        st.session_state.pop(key, None)
    st.rerun()


def _save(store, record, plan, actor, note):
    updated = store.save(record["id"], record["revision"], plan, actor, note)
    st.session_state.epm_notice = "Saved revision " + str(updated["revision"]) + "."
    _open(updated)


def _error(exc):
    st.error(str(exc) if isinstance(exc, ValueError) else "The planning database is busy or unavailable. Check its location and reload before saving.")


def _amount(value, currency):
    return "Not available" if pd.isna(value) else f"{currency} {value:,.0f}"


def _sum(values):
    return values.sum(skipna=False)


def _table(data, **kwargs):
    columns = {}
    for column in data.columns:
        label = str(column).replace("_", " ").title()
        if pd.api.types.is_numeric_dtype(data[column]) and not pd.api.types.is_bool_dtype(data[column]):
            columns[column] = st.column_config.NumberColumn(label, format="localized")
        else:
            columns[column] = label
    st.dataframe(data, column_config=columns, **kwargs)


def _chart(df, fields, title, currency):
    figure = go.Figure()
    palette = ["#5edbc0", "#8da6ce", "#ffad85", "#b39ddb"]
    for index, (key, label) in enumerate(fields.items()):
        figure.add_trace(go.Scatter(x=df.period, y=df[key], name=label, mode="lines+markers", connectgaps=False,
            line={"color": palette[index % len(palette)], "width": 2}))
    figure = style_chart(figure)
    figure.update_layout(title=dict(text=title, x=0, y=.98), height=350,
        margin=dict(l=15, r=15, t=80, b=20), legend=dict(orientation="h", y=1.14, x=0, yanchor="top"))
    figure.update_yaxes(tickprefix=currency + " ")
    return figure


def _create(store, actor):
    with st.expander("Create or import a plan", expanded=not store.list()):
        source = st.selectbox("Starting point", ["Service business demo", "Blank budget", "Planning CSV / XLSX", "Loaded expense ledger", "Restore planning backup"], key="epm_create_source")
        if source == "Service business demo":
            st.caption("A synthetic 24-month plan with revenue, delivery costs, payroll, overheads, and depreciation. January–June 2026 actuals are closed; 18 future months remain.")
            if st.button("Create demo plan", type="primary", key="epm_create_demo"):
                _open(store.create(demo_plan(), actor))
            return
        if source == "Restore planning backup":
            upload = st.file_uploader("Planning backup JSON", type=["json"], key="epm_restore")
            st.caption("Restoration creates a new draft. It does not overwrite an existing plan or inherit approval.")
            if st.button("Restore as new draft", disabled=upload is None, key="epm_restore_submit"):
                _open(store.restore(upload.getvalue(), actor))
            return
        with st.form("epm_create_form"):
            name = st.text_input("Plan name", "Operating budget")
            a, b, c = st.columns(3)
            year = a.number_input("Start year", 1900, 2197, 2026)
            start_month = b.selectbox("Start month", list(range(1, 13)))
            count = c.selectbox("Planning horizon (months)", [12, 18, 24, 36], index=2)
            start = f"{year}-{start_month:02d}"
            horizon = periods(start, count)
            currency = st.selectbox("Planning currency", ["SGD", "USD", "EUR", "GBP", "AUD", "HKD"])
            closed_count = st.number_input("Closed months from start", 0, count, min(6, count))
            cutoff = horizon[closed_count - 1] if closed_count else str(pd.Period(start, freq="M") - 1)
            upload = None
            if source == "Planning CSV / XLSX":
                upload = st.file_uploader("Planning table", type=["csv", "xlsx"], key="epm_planning_upload")
                st.caption("Required: period (YYYY-MM), department, account, category, budget, actual, forecast. One row per account/month; leave future actuals blank. Use the downloadable example below.")
            if source == "Loaded expense ledger":
                st.caption("Uses the ledger most recently opened in Reporting. Requires 12 months of budget rows; all imported accounts initially use Opex. Start year and closed month apply; horizon is 12 months and currency comes from the source.")
            department = st.text_input("Initial department (blank budget)", "Corporate") if source == "Blank budget" else None
            if st.form_submit_button("Create plan", type="primary"):
                if source == "Planning CSV / XLSX":
                    if upload is None:
                        raise ValueError("Select a planning table first.")
                    plan = from_frame(read_planning_file(upload.getvalue(), upload.name), name, start, count, cutoff, currency, upload.name)
                elif source == "Loaded expense ledger":
                    ledger = st.session_state.get("epm_source_ledger")
                    if ledger is None:
                        raise ValueError("Open a ledger in Reporting first.")
                    if start_month != 1 or closed_count > 12:
                        raise ValueError("Expense-ledger conversion uses January–December and 0–12 closed months.")
                    plan = from_expense_ledger(ledger, year, closed_count, name)
                else:
                    plan = base_plan(name, start, count, cutoff, currency)
                    plan = add_account(plan, department, "Operating expenses", "Opex", 0)
                _open(store.create(plan, actor))
        st.download_button("Download planning example CSV", csv_bytes(frame(demo_plan())), "planning_example.csv", "text/csv", key="epm_template")


def _overview(plan):
    if not any(r["category"] == "Revenue" for r in plan["rows"]):
        st.info("This is an expense-only plan. Revenue is absent; the displayed profit equals negative planned costs and does not represent company profitability.")
    monthly = monthly_report(plan)
    years = sorted({p[:4] for p in monthly.period})
    year = st.selectbox("Reporting year", ["Full horizon", *years], key="epm_report_year")
    if year != "Full horizon":
        monthly = monthly[monthly.period.str.startswith(year)]
    revenue = _sum(monthly.outlook_revenue)
    profit = _sum(monthly.outlook_operating_profit)
    budget_profit = _sum(monthly.budget_operating_profit)
    a, b = st.columns(2)
    c, d = st.columns(2)
    a.metric("Revenue outlook", _amount(revenue, plan["currency"]))
    b.metric("Operating profit", _amount(profit, plan["currency"]))
    c.metric("Profit vs budget", _amount(profit - budget_profit, plan["currency"]))
    d.metric("Operating margin", "Not available" if pd.isna(profit) or not revenue else f"{100 * profit / revenue:.1f}%")
    left, right = st.columns(2)
    with left:
        st.plotly_chart(_chart(monthly, {"budget_revenue": "Budget", "outlook_revenue": "Actual + forecast"}, "Revenue outlook", plan["currency"]), width="stretch")
    with right:
        st.plotly_chart(_chart(monthly, {"budget_operating_profit": "Budget", "outlook_operating_profit": "Actual + forecast"}, "Operating profit", plan["currency"]), width="stretch")
    st.caption("Actuals through " + plan["closed_through"] + "; forecast afterward. Costs are positive inputs. Operating profit excludes interest and tax. Missing actuals remain unknown.")
    _table(monthly, hide_index=True, width="stretch")
    with st.expander("Account-level Budget–Actual–Forecast comparison"):
        detail = variance_report(plan)
        if year != "Full horizon":
            detail = detail[detail.period.str.startswith(year)]
        _table(detail, hide_index=True, width="stretch")
        st.caption("A positive favorable impact means increased revenue or reduced cost.")


def _budget(store, record, actor):
    plan, editable = record["plan"], record["state"] == "Draft"
    budget_editable = editable and not record["budget_locked"]
    st.markdown("### Build the operating plan")
    st.caption("Maintain monthly inputs and allocate changes across accounts. Budget versions preserve approved targets; forecast/scenario versions keep their baseline budget locked.")
    department = st.selectbox("Department", ["All", *sorted({r["department"] for r in plan["rows"]})], key="epm_grid_department")
    account = st.selectbox("Account", ["All", *sorted({r["account"] for r in plan["rows"] if department == "All" or r["department"] == department})], key="epm_grid_account")
    whole = frame(plan)
    mask = ((whole.department == department) if department != "All" else pd.Series(True, index=whole.index)) & ((whole.account == account) if account != "All" else pd.Series(True, index=whole.index))
    target = st.radio("Edit values", ["Budget", "Forecast"], horizontal=True, key="epm_grid_target").lower()
    can_edit = budget_editable if target == "budget" else editable
    visible = whole[mask].copy()
    if target == "forecast":
        visible = visible[visible.period > plan["closed_through"]]
    with st.form("epm_grid_form"):
        edited = st.data_editor(visible, disabled=[c for c in visible.columns if c != target] if can_edit else True,
            hide_index=True, width="stretch", height=min(650, max(180, (len(visible) + 1) * 35)),
            key=f"epm_grid_{record['id']}_{record['revision']}_{target}_{department}_{account}")
        note = st.text_input("Reason for input changes", key="epm_grid_note")
        if st.form_submit_button("Save monthly inputs", disabled=not can_edit or visible.empty, type="primary"):
            whole.loc[edited.index, target] = edited[target]
            _save(store, record, replace_grid(plan, whole, target), actor, note)
    with st.expander("Add a planning account"):
        with st.form("epm_add_account"):
            a, b = st.columns(2)
            dept = a.text_input("Department name", "Corporate")
            account_name = b.text_input("Account name")
            category = st.selectbox("Account category", CATEGORIES)
            amount = st.number_input("Monthly baseline budget", value=0.0, step=100.0)
            st.caption("Closed-period actuals for a new account remain blank until explicitly supplied.")
            if st.form_submit_button("Add account", disabled=not budget_editable):
                _save(store, record, add_account(plan, dept, account_name, category, amount), actor, "Added planning account")
    with st.expander("Allocate an annual budget"):
        with st.form("epm_allocate"):
            a, b = st.columns(2)
            pair_options = sorted({(r["department"], r["account"]) for r in plan["rows"]})
            pair = a.selectbox("Allocation account", pair_options, format_func=lambda p: " / ".join(p))
            year = b.selectbox("Budget year", sorted({r["period"][:4] for r in plan["rows"]}))
            total = st.number_input("Annual target", value=120000.0, step=1000.0)
            weights = st.text_input("Monthly weights (January to December)", "1,1,1,1,1,1,1,1,1,1,1,1")
            if st.form_submit_button("Allocate and save", disabled=not budget_editable):
                from epm import allocate_budget
                _save(store, record, allocate_budget(plan, pair[0], pair[1], year, total, weights), actor, "Allocated annual budget across months")
    with st.expander("Refresh actuals and advance the close"):
        with st.form("epm_actuals_form"):
            horizon = periods(plan["start"], plan["months"])
            cutoff = st.selectbox("Actuals closed through", [p for p in horizon if p >= plan["closed_through"]])
            upload = st.file_uploader("Actuals CSV / XLSX", type=["csv", "xlsx"], key="epm_actuals_upload")
            st.caption("Columns: period, department, account, actual; optional currency must match. Known rows are updated; missing rows remain unknown. Cash opening balances must be refreshed separately when the cutoff advances.")
            reason = st.text_input("Actuals source / reason", key="epm_actuals_reason")
            if st.form_submit_button("Import actuals", disabled=not editable or upload is None):
                updated = apply_actuals(plan, read_planning_file(upload.getvalue(), upload.name), cutoff)
                _save(store, record, updated, actor, reason)
        template = frame(plan).loc[:, [*KEYS_UI, "actual"]]
        st.download_button("Download actuals input template", csv_bytes(template), "actuals_template.csv", "text/csv")


KEYS_UI = ["period", "department", "account"]


def _forecasts(store, record, actor, records):
    plan, editable = record["plan"], record["state"] == "Draft"
    st.markdown("### Forecast and explore alternatives")
    with st.form("epm_forecast_form"):
        a, b = st.columns(2)
        method = a.selectbox("Forecast method", METHODS, key="epm_forecast_method")
        growth = b.number_input("One-time uplift (%)", -100.0, 1000.0, 0.0, step=1.0)
        st.caption("Replaces future forecast lines across the plan. Actuals and budget are preserved. Apply workforce/capital schedules afterward if they should override the baseline method.")
        if st.form_submit_button("Generate forecast", disabled=not editable, type="primary"):
            _save(store, record, forecast(plan, method, growth), actor, f"Generated {method} forecast with {growth:g}% uplift")
    st.markdown("#### Scenario adjustment")
    with st.form("epm_adjust_form"):
        a, b, c = st.columns(3)
        department = a.selectbox("Adjust department", ["All", *sorted({r["department"] for r in plan["rows"]})])
        account = b.selectbox("Adjust account", ["All", *sorted({r["account"] for r in plan["rows"]})])
        category = c.selectbox("Adjust category", ["All", *CATEGORIES])
        a, b, c, d = st.columns(4)
        future = [p for p in periods(plan["start"], plan["months"]) if p > plan["closed_through"]]
        start = a.selectbox("Adjustment start", future or [plan["closed_through"]])
        end = b.selectbox("Adjustment end", future or [plan["closed_through"]], index=max(0, len(future)-1))
        rate = c.number_input("Change (%)", -100.0, 1000.0, 0.0)
        delta = d.number_input("Additional amount per line/month", value=0.0, step=100.0)
        if st.form_submit_button("Preview scenario change", disabled=not editable or not future):
            proposed, changes = adjust(plan, "forecast", department, account, category, start, end, rate, delta)
            st.session_state.epm_proposal = (fingerprint(plan), proposed, changes)
    _proposal(store, record, actor)
    st.markdown("#### Compare saved versions")
    choices = [r for r in records if r["id"] != record["id"]]
    if choices:
        other = st.selectbox("Comparison version", choices, format_func=lambda r: f"{r['name']} · {r['state']} · r{r['revision']}")
        baseline = store.get(other["id"])["plan"]
        try:
            if actuals_fingerprint(plan) != actuals_fingerprint(baseline):
                st.info("These versions contain different actuals or close dates. The comparison includes newly closed periods and source-data changes as well as planning changes.")
            result = compare_plans(plan, baseline)
            st.caption(f"Comparing common months only: {result.period.min()} to {result.period.max()}. Current actuals through {plan['closed_through']}; comparison actuals through {baseline['closed_through']}. Each line identifies its actual/forecast basis.")
            a, b = st.columns(2)
            a.metric("Operating profit change", _amount(result.profit_impact.sum(skipna=False), plan["currency"]))
            b.metric("Compared with", other["name"])
            _table(result, hide_index=True, width="stretch")
        except ValueError as exc:
            st.info(str(exc))
    else:
        st.info("Create a scenario in Plan review to compare it with this plan.")
    with st.expander("Historical forecast evaluation"):
        scores, detail = backtest(plan)
        st.caption("Past-only, one-step evaluation of last-actual and trailing-three-month baselines. MAE is in reporting currency; WAPE uses absolute actuals. At least four complete actual months are required. These results do not establish future accuracy.")
        if scores.empty:
            st.info("Not enough complete actual history to evaluate these methods.")
        else:
            _table(scores, hide_index=True, width="stretch")
            _table(detail, hide_index=True, width="stretch")


def _proposal(store, record, actor):
    proposal = st.session_state.get("epm_proposal")
    if proposal is None:
        return
    before, proposed, changes = proposal
    if before != fingerprint(record["plan"]):
        st.session_state.pop("epm_proposal", None)
        st.info("The plan changed; generate a fresh proposal.")
        return
    original_budget = {(r["period"],r["department"],r["account"]):r["budget_cents"] for r in record["plan"]["rows"]}
    changes_budget = ("field" in changes and changes.field.eq("budget").any()) or any(original_budget.get((r["period"],r["department"],r["account"])) != r["budget_cents"] for r in proposed["rows"])
    target = "budget" if changes_budget else "forecast"
    can_apply = record["state"] == "Draft" and not (changes_budget and record["budget_locked"])
    st.markdown("#### Review proposed change")
    st.caption(f"{len(changes):,} monthly line(s). Applying changes {target} values; an illustrated spending alternative is not applied.")
    with st.expander("Affected monthly inputs", expanded=len(changes) <= 3):
        _table(changes, hide_index=True, width="stretch", height=min(600, max(180, (len(changes)+1)*35)))
    if not can_apply:
        st.info("Preview only: this version's budget is locked. Use a separate editable Budget plan to change targets; a Forecast or Scenario keeps its baseline budget." if changes_budget and record["budget_locked"] else "Preview only: this version is locked. Create an editable draft in Plan review to apply changes.")
    a, b = st.columns(2)
    if a.button("Apply reviewed proposal", type="primary", disabled=not can_apply, key="epm_apply_proposal"):
        _save(store, record, proposed, actor, f"Applied reviewed {target} proposal")
    if b.button("Discard proposal", key="epm_discard_proposal"):
        st.session_state.pop("epm_proposal", None)
        st.rerun()


def _drivers(store, record, actor):
    plan = record["plan"]
    editable = record["state"] == "Draft"
    st.markdown("### Revenue, workforce and capital plans")
    with st.expander("Revenue: price × volume"):
        pairs = sorted({(r["department"], r["account"]) for r in plan["rows"] if r["category"] == "Revenue"})
        future = [p for p in periods(plan["start"], plan["months"]) if p > plan["closed_through"]]
        if pairs and future:
            with st.form("epm_revenue_driver"):
                pair = st.selectbox("Revenue account", pairs, format_func=lambda p: " / ".join(p))
                a, b, c = st.columns(3)
                units = a.number_input("Starting monthly units / customers", min_value=0.0, value=100.0)
                price = b.number_input("Revenue per unit / customer", min_value=0.0, value=1000.0)
                growth = c.number_input("Monthly volume growth (%)", -100.0, 100.0, 0.0)
                a, b = st.columns(2)
                start = a.selectbox("Revenue model start", future)
                end = b.selectbox("Revenue model end", future, index=len(future)-1)
                st.caption("Replaces revenue forecast for this account and period range. Price is fixed; volume growth compounds monthly. Assumptions are saved with the revision.")
                if st.form_submit_button("Apply revenue model", disabled=not editable):
                    updated = revenue_driver(plan, pair[0], pair[1], start, end, units, price, growth)
                    _save(store, record, updated, actor, "Applied price × volume revenue assumptions")
        else:
            st.info("Add a Revenue account and open future periods to use price × volume planning.")
    st.caption("Schedules replace future forecast totals for their mapped accounts. Include the full planned workforce or asset base for those accounts. Actuals and baseline budgets remain intact.")
    workforce = pd.DataFrame(plan["workforce"], columns=["department", "account", "start", "end", "heads", "monthly_salary_cents", "burden_pct", "annual_raise_pct"])
    workforce["monthly_salary"] = workforce.pop("monthly_salary_cents") / 100
    capex = pd.DataFrame(plan["capex"], columns=["department", "account", "start", "asset", "cost_cents", "life_months"])
    capex["cost"] = capex.pop("cost_cents") / 100
    with st.form("epm_drivers_form"):
        st.markdown("#### Workforce schedule")
        st.caption("Map to a Payroll category account. Headcount supports fractional FTEs; salary is monthly per FTE. Raises compound on each start-date anniversary.")
        workforce = st.data_editor(workforce, num_rows="dynamic", hide_index=True, width="stretch", disabled=not editable,
            key=f"epm_workforce_{record['id']}_{record['revision']}", column_config={
                "department": st.column_config.TextColumn(required=True), "account": st.column_config.TextColumn(required=True),
                "start": st.column_config.TextColumn("Start (YYYY-MM)", required=True), "end": st.column_config.TextColumn("End (YYYY-MM)", required=True),
                "heads": st.column_config.NumberColumn("FTEs", min_value=0, required=True),
                "monthly_salary": st.column_config.NumberColumn("Monthly salary", min_value=0, required=True),
                "burden_pct": st.column_config.NumberColumn("Benefits / burden %", min_value=0, required=True),
                "annual_raise_pct": st.column_config.NumberColumn("Annual raise %", min_value=0, required=True)})
        st.markdown("#### Capital schedule")
        st.caption("Map to a Depreciation category account. Assets are paid in their start month and depreciated straight-line over their useful life; no residual value is assumed.")
        capex = st.data_editor(capex, num_rows="dynamic", hide_index=True, width="stretch", disabled=not editable,
            key=f"epm_capex_{record['id']}_{record['revision']}", column_config={
                "department": st.column_config.TextColumn(required=True), "account": st.column_config.TextColumn(required=True),
                "start": st.column_config.TextColumn("In service (YYYY-MM)", required=True),
                "asset": st.column_config.TextColumn(required=True), "cost": st.column_config.NumberColumn("Asset cost", min_value=0, required=True),
                "life_months": st.column_config.NumberColumn("Useful life (months)", min_value=1, max_value=600, step=1, required=True)})
        reason = st.text_input("Driver change explanation", key="epm_drivers_reason")
        if st.form_submit_button("Apply schedules and save", disabled=not editable, type="primary"):
            workers, assets = workforce.to_dict("records"), capex.to_dict("records")
            for row in workers:
                row["monthly_salary_cents"] = cents(row.pop("monthly_salary"))
            for row in assets:
                row["cost_cents"] = cents(row.pop("cost"))
                if pd.isna(row["life_months"]) or int(row["life_months"]) != row["life_months"]:
                    raise ValueError("Useful life must be a whole number of months.")
                row["life_months"] = int(row["life_months"])
            _save(store, record, apply_drivers(plan, workers, assets), actor, reason)


def _cash(store, record, actor):
    plan, editable = record["plan"], record["state"] == "Draft"
    st.markdown("### Cash outlook and working capital")
    st.caption("Saved opening balances are immediately after " + plan["cash"]["as_of"] + ". Opening receivables/payables settle in the first forecast month; new activity follows the selected lag. Payroll is paid in the same month.")
    if plan["cash"]["as_of"] != plan["closed_through"]:
        st.warning("The actuals cutoff advanced. Confirm updated opening balances as of " + plan["closed_through"] + " before using the cash projection.")
    with st.expander("Cash assumptions", expanded=True):
        with st.form("epm_cash_form"):
            cfg = deepcopy(plan["cash"])
            a, b, c = st.columns(3)
            cfg["opening_cash_cents"] = cents(a.number_input("Opening cash", value=cfg["opening_cash_cents"]/100, step=1000.0))
            cfg["opening_ar_cents"] = cents(b.number_input("Opening receivables", min_value=0.0, value=cfg["opening_ar_cents"]/100, step=1000.0))
            cfg["opening_ap_cents"] = cents(c.number_input("Opening payables", min_value=0.0, value=cfg["opening_ap_cents"]/100, step=1000.0))
            a, b, c, d = st.columns(4)
            cfg["collection_lag"] = a.selectbox("Collection lag (months)", [0,1,2,3], index=cfg["collection_lag"])
            cfg["payment_lag"] = b.selectbox("Supplier payment lag", [0,1,2,3], index=cfg["payment_lag"])
            cfg["monthly_other_outflow_cents"] = cents(c.number_input("Other cash outflows / month", value=cfg["monthly_other_outflow_cents"]/100, step=100.0))
            cfg["monthly_financing_cents"] = cents(d.number_input("Financing inflow / month", value=cfg["monthly_financing_cents"]/100, step=100.0))
            st.caption("Enter tax, debt service, and other cash items explicitly. This projection does not calculate VAT, statutory tax, inventory movements, or a complete balance sheet.")
            if st.form_submit_button("Save cash assumptions", disabled=not editable, type="primary"):
                cfg["as_of"] = plan["closed_through"]
                updated = deepcopy(plan); updated["cash"] = cfg
                _save(store, record, validate_plan(updated), actor, "Updated cash opening balances and timing assumptions")
    cash = cash_report(plan)
    if cash.empty:
        st.info("All planning periods are closed. Create a new horizon to forecast cash.")
        return
    a, b, c = st.columns(3)
    a.metric("Ending cash", _amount(cash.closing_cash.iloc[-1], plan["currency"]))
    b.metric("Lowest closing cash", _amount(cash.closing_cash.min(), plan["currency"]))
    negative = cash[cash.closing_cash < 0]
    c.metric("First negative cash month", "None in horizon" if negative.empty else negative.iloc[0].period)
    st.plotly_chart(_chart(cash, {"closing_cash": "Closing cash", "net_cash_flow": "Net movement"}, "Projected liquidity", plan["currency"]), width="stretch")
    _table(cash, hide_index=True, width="stretch")


def _versions(store, record, actor):
    plan = record["plan"]
    st.markdown("### Review, version and export")
    st.caption("Review names are local labels. These states organize a local workflow; they do not authenticate reviewers or enforce segregation of duties.")
    with st.expander("Document planning assumptions"):
        with st.form("epm_assumptions_form"):
            notes = st.text_area("Assumption notes (one per line)", "\n".join(plan["assumptions"]), height=180, max_chars=400000)
            if st.form_submit_button("Save assumption notes", disabled=record["state"] != "Draft"):
                updated = deepcopy(plan)
                updated["assumptions"] = [line.strip() for line in notes.splitlines() if line.strip()]
                _save(store, record, validate_plan(updated), actor, "Updated planning assumption notes")
    with st.form("epm_clone_form"):
        a, b = st.columns([3,1])
        name = a.text_input("New version name", (plan["name"][:95] + " · scenario"))
        kind = b.selectbox("New version type", ["Scenario", "Forecast", "Budget"])
        if st.form_submit_button("Create separate version", type="primary"):
            _open(store.clone(record["id"], name, kind, actor))
    with st.expander("Roll the forecast horizon"):
        with st.form("epm_roll_form"):
            name = st.text_input("Rolling forecast name", plan["name"][:90] + " · rolling forecast")
            start = st.selectbox("New horizon start", [p for p in periods(plan["start"], plan["months"]) if p <= str(pd.Period(plan["closed_through"], freq="M") + 1)])
            count = st.selectbox("New horizon length", [12,18,24,36], index=2)
            st.caption("Creates a separate forecast. Overlapping budget/actual values stay fixed; new budget months are seeded from prior-year seasonality or the last known budget and require review. Future workforce/capital schedules can be reapplied afterward.")
            if st.form_submit_button("Create rolling forecast"):
                _open(store.roll(record["id"], name, start, count, actor))
    with st.form("epm_review_form"):
        options = {"Draft": ["Submitted"], "Submitted": ["Approved", "Draft"], "Approved": []}[record["state"]]
        state = st.selectbox("Move to", options or ["Approved (locked)"], disabled=not options)
        note = st.text_area("Review decision / explanation", key="epm_review_note")
        if st.form_submit_button("Record review decision", disabled=not options):
            _open(store.transition(record["id"], record["revision"], state, actor, note))
    history = pd.DataFrame(store.history(record["id"]))
    _table(history.drop(columns=["fingerprint"]), hide_index=True, width="stretch")
    with st.expander("Inspect an earlier revision"):
        revision = st.selectbox("Saved revision", history.revision.tolist())
        saved = store.snapshot(record["id"], int(revision))
        _table(monthly_report(saved), hide_index=True, width="stretch")
        st.caption("Stored snapshots are preserved by application operations. A database administrator can modify local storage.")
    left, right = st.columns(2)
    left.download_button("Download plan backup", store.backup(record["id"]), "fpa_plan_backup.json", "application/json", width="stretch")
    if right.button("Prepare FP&A workbook", width="stretch"):
        st.session_state.epm_pack = (fingerprint(plan), planning_pack(plan, {"State": record["state"], "Revision": record["revision"]}))
    pack = st.session_state.get("epm_pack")
    if pack and pack[0] == fingerprint(plan):
        st.download_button("Download FP&A workbook", pack[1], "fpa_planning_pack.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    st.download_button("Download planning inputs CSV", csv_bytes(frame(plan)), "planning_inputs.csv", "text/csv")


def _toggle_assistant():
    st.session_state.epm_chat_expanded = not st.session_state.get("epm_chat_expanded", False)


def _assistant(store, record, actor):
    plan = record["plan"]
    token = (record["id"], record["revision"], fingerprint(plan))
    if st.session_state.get("epm_chat_token") != token:
        st.session_state.epm_chat_token = token
        st.session_state.epm_chat_messages = []
        st.session_state.epm_chat_context = {}
        st.session_state.epm_chat_exports = {}
    messages = st.session_state.epm_chat_messages
    exports = st.session_state.epm_chat_exports
    with st.container(border=True, key="copilot_panel", autoscroll=False):
        title, toggle = st.columns([.65,.35], vertical_alignment="center")
        title.markdown("### Finance Copilot")
        expanded = st.session_state.get("epm_chat_expanded", False)
        toggle.button("Back to plan" if expanded else "Expand", key="epm_chat_toggle",
                      on_click=_toggle_assistant, width="stretch")
        st.caption(f"Saved plan: {plan['name']} · revision {record['revision']} · {plan['currency']}")
        st.caption("Uses saved values. Ask a question or preview a budget/forecast change before saving it.")
        question = ""
        cols = st.columns(4 if expanded else 2)
        for i, (label, prompt) in enumerate([("Plan summary", "summarise plan"), ("Cash outlook", "show cash"),
                                           ("Top variances", "top variances"), ("Export to Excel", "export that to Excel")]):
            if cols[i % len(cols)].button(label, key=f"epm_chat_suggestion_{i}", width="stretch"):
                question = prompt
        typed = st.chat_input("Ask about this plan…", key="epm_chat_input", max_chars=4000)
        with st.expander("Assistant settings"):
            provider = st.selectbox("Provider", ["local fallback", "openai", "anthropic"], key="epm_chat_provider")
            st.caption("Local reports work without an API key. An optional provider can classify unfamiliar questions; only the question is sent. Calculations and plan changes stay in Python.")
            st.caption("Keeps 40 messages and 5 Excel downloads. Switching planning tabs keeps the conversation. A different plan or saved revision starts a fresh conversation.")
        if typed or question:
            question = typed or question
            with st.spinner("Checking the saved plan…"):
                response = PlanningChatbot(plan).answer(question, context=st.session_state.epm_chat_context, provider=provider)
            st.session_state.pop("epm_proposal", None)
            if response.proposal is not None:
                st.session_state.epm_proposal = (fingerprint(plan), response.proposal, response.table)
            # An unresolved question must not leave a previous scope available for an accidental export.
            st.session_state.epm_chat_context = response.context
            impact = response.reports.get("Impact summary")
            cash_detail = response.reports.get("Budget and spend cash")
            if cash_detail is None:
                cash_detail = response.reports.get("Monthly cash impact")
            answer = {"role": "assistant", "content": response.message, "table": impact if impact is not None else response.table,
                      "warnings": response.warnings, "used_llm": response.used_llm,
                      "cash_detail": cash_detail, "cash_illustration": "Budget and spend cash" in response.reports}
            if response.export_bytes:
                export_id = uuid4().hex
                exports[export_id] = response.export_bytes
                answer["export_id"] = export_id
            messages.extend([{"role": "user", "content": question}, answer])
            del messages[:-40]
            retained = {m.get("export_id") for m in messages}
            st.session_state.epm_chat_exports = exports = {k: v for k,v in list(exports.items())[-5:] if k in retained}
            st.rerun()

        def render_message(index, message):
            with st.chat_message(message["role"], avatar=":material/person:" if message["role"] == "user" else ":material/query_stats:"):
                st.markdown(message["content"])
                if message["role"] == "assistant":
                    st.caption("Model-assisted routing · Python calculations" if message.get("used_llm") else "Local planning analysis")
                if message.get("table") is not None:
                    table = message["table"]
                    if table.empty:
                        st.caption("No matching rows.")
                    else:
                        # A compact preview avoids nested vertical scrolling. The complete result is downloadable.
                        preview = table.head(12).rename(columns=lambda c: str(c).replace("_", " ").title())
                        st.markdown(preview.to_html(index=False, escape=True, border=0, na_rep="Not available",
                            float_format=lambda value: f"{value:,.2f}"), unsafe_allow_html=True)
                        if len(table) > 12:
                            st.caption(f"Showing 12 of {len(table):,} rows. Ask ‘export that to Excel’ for the complete result.")
                cash_detail = message.get("cash_detail")
                if cash_detail is not None and not cash_detail.empty:
                    columns = ["period", "net_cash_flow_change", "closing_cash_change", "closing_payables_change", "closing_receivables_change"]
                    affected = cash_detail[cash_detail[columns[1:]].abs().gt(.005).any(axis=1)][columns]
                    if not affected.empty:
                        with st.expander("Cash timing: illustrative spending case" if message.get("cash_illustration") else "Cash timing"):
                            st.caption("Changes versus the saved plan. Positive cash change means more cash; positive payable change means more owed to suppliers. Export the answer for all months.")
                            st.markdown(affected.head(12).rename(columns=lambda c:c.replace("_", " ").title()).to_html(
                                index=False, escape=True, border=0, float_format=lambda value:f"{value:,.2f}"), unsafe_allow_html=True)
                for warning in message.get("warnings", []):
                    st.caption(warning)
                if message.get("export_id") in exports:
                    st.download_button("Download Excel", exports[message["export_id"]], "planning_answer.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"epm_chat_download_{index}")
        if not messages:
            st.markdown("**Explore this plan**")
            st.markdown("Try ‘what if I increase Marketing budget by 8% for next month?’ or ‘show forecast for 2027’. Expand for more room to read the impact.")
        else:
            with st.container(key="copilot_latest", autoscroll=False):
                st.markdown("#### Latest response")
                for i in range(max(0,len(messages)-2),len(messages)):
                    render_message(i, messages[i])
            if len(messages) > 2:
                with st.expander(f"Earlier messages ({len(messages)-2})"):
                    for i, message in enumerate(messages[:-2]):
                        render_message(i, message)
            left, right = st.columns(2)
            if left.button("Clear chat", key="epm_chat_clear", width="stretch"):
                st.session_state.pop("epm_chat_token", None)
                st.session_state.pop("epm_proposal", None)
                st.rerun()
            transcript = "\n\n".join(m["role"].title() + ": " + m["content"] for m in messages)
            right.download_button("Save conversation", transcript, "planning_conversation.md", "text/markdown", width="stretch")
        # Forecasting renders the same shared proposal in its workbench, with one set of Apply controls.
        if st.session_state.get("main_navigation") != "Forecasting" or expanded:
            _proposal(store, record, actor)


def render_planning_workspace(section="Planning"):
    st.markdown('<div class="eyebrow">FP&A / PLANNING WORKSPACE</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-title">Plan with confidence.</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-subtitle">Build your budget. Test the assumptions. See what comes next.</div>', unsafe_allow_html=True)
    try:
        store = PlanStore()
    except (OSError, sqlite3.Error):
        st.error("Planning storage is unavailable. Configure FPA_DATABASE_PATH to a writable, persistent local location.")
        return
    actor = st.sidebar.text_input("Local review name", "Local analyst", key="epm_actor")
    st.sidebar.caption("Local workspace · saved on this server. Reviewer names are labels, not sign-in identities. Use private hosting for business data.")
    notice = st.session_state.pop("epm_notice", None)
    if notice:
        st.success(notice)
    try:
        _create(store, actor)
        records = store.list()
        if not records:
            st.info("Start with the demo, build a blank budget, or import your own planning table.")
            return
        ids = [r["id"] for r in records]
        labels = {r["id"]: f"{r['name']} · {r['state']}" for r in records}
        pending = st.session_state.pop("epm_open", None)
        if pending in ids:
            st.session_state.epm_plan_id = pending
        elif "epm_plan_id" not in st.session_state and st.session_state.get("epm_record", {}).get("id") in ids:
            st.session_state.epm_plan_id = st.session_state.epm_record["id"]
        selected = st.sidebar.selectbox("Saved plan", ids, format_func=labels.get, key="epm_plan_id")
        if st.session_state.get("epm_record", {}).get("id") != selected:
            st.session_state.epm_record = store.get(selected)
            st.session_state.pop("epm_proposal", None)
            st.session_state.pop("epm_answer", None)
            st.session_state.pop("epm_pack", None)
        record = st.session_state.epm_record
        if st.sidebar.button("Reload latest saved revision", key="epm_reload"):
            _open(store.get(selected))
        plan = record["plan"]
        st.markdown("**" + plan["name"] + "**")
        st.caption(f"{plan['kind']} · {record['state']} · Revision {record['revision']} · {plan['currency']} · {plan['start']} to {periods(plan['start'], plan['months'])[-1]} · Actuals through {plan['closed_through']}")
        missing = missing_actuals(plan)
        if missing:
            st.warning(f"{missing} closed-period actual values are missing. Affected totals remain unknown; complete actuals before submitting.")
        if record["state"] != "Draft":
            st.info("This version is locked for editing. Create a separate draft in Plan review to make changes.")
        if st.session_state.get("epm_chat_expanded", False):
            _assistant(store, record, actor)
            return
        workbench, copilot = st.columns([.65, .35], gap="large")
        with workbench:
            if section == "Planning":
                _overview(plan)
            elif section == "Budgeting":
                _budget(store, record, actor)
            elif section == "Forecasting":
                _forecasts(store, record, actor, records)
            elif section == "Business drivers":
                _drivers(store, record, actor)
            elif section == "Cash planning":
                _cash(store, record, actor)
            elif section == "Plan review":
                _versions(store, record, actor)
        with copilot:
            _assistant(store, record, actor)
    except (ValueError, sqlite3.Error) as exc:
        _error(exc)
