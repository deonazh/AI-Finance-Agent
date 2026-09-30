"""Deterministic planning what-ifs with explicit dates and separate budget/spend effects."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import calendar
import math
import re

import pandas as pd

from epm import adjust, cash_report, fingerprint, monthly_report, periods, validate_plan


CHANGE_WORDS = r"increas(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|raise(?:d|s)?|raising|reduce(?:d|s)?|reducing|cut(?:ting)?"
UNSUPPORTED_CHANGE = "I can preview a percentage change such as ‘What if I increase Marketing budget by 8% for next month?’ Name the budget or forecast, an exact account/department, and the period. No changes have been saved."


@dataclass
class WhatIfResult:
    message: str
    changes: pd.DataFrame
    proposal: dict
    context: dict
    reports: dict[str, pd.DataFrame]
    warnings: list[str]


def _scope(plan, phrase):
    phrase = phrase.strip().strip('\"\'').casefold()
    phrase = re.sub(r"^(?:the|our|my)\s+", "", phrase)
    # Explicit department/account combinations avoid accidentally selecting a same-named account elsewhere.
    if " / " in phrase or " in " in phrase:
        if " / " in phrase:
            department, account = phrase.split(" / ", 1)
        else:
            account, department = phrase.split(" in ", 1)
        def exact(key, value):
            value = re.sub(r"^(?:the )?(?:department|account)\s+|\s+(?:department|account)$", "", value).strip()
            return next((r[key] for r in plan['rows'] if r[key].casefold() == value), None)
        department, account = exact('department', department), exact('account', account)
        if department and account and any(r['department'] == department and r['account'] == account for r in plan['rows']):
            return {'department': department, 'account': account}
        raise ValueError("That department/account combination is not in this plan. Use the names shown in Budgeting.")
    dimension = None
    qualified = re.fullmatch(r"(department|account|category)\s+(.+)|(.+?)\s+(department|account|category)", phrase)
    if qualified:
        dimension = qualified.group(1) or qualified.group(4)
        phrase = (qualified.group(2) or qualified.group(3)).strip().strip('\"\'')
    candidates = []
    for key in [dimension] if dimension else ['department', 'account', 'category']:
        for value in sorted({r[key] for r in plan['rows']}):
            if value.casefold() == phrase:
                keys = frozenset((r['department'], r['account']) for r in plan['rows'] if r[key] == value)
                candidates.append((key, value, keys))
    if not candidates:
        raise ValueError("I could not match that scope to the saved plan. Use an exact department/account name from Budgeting; no change was proposed.")
    if len({item[2] for item in candidates}) != 1:
        raise ValueError("That name matches different scopes. Say ‘department NAME’ or ‘account NAME’, or use ‘DEPARTMENT / ACCOUNT’.")
    key, value, _ = candidates[0]
    return {key: value}


def resolve_period(plan, phrase, today, inherited_year=None):
    horizon = periods(plan['start'], plan['months'])
    first_open = str(pd.Period(plan['closed_through'], freq='M') + 1)
    label = ' '.join((phrase or '').casefold().split())
    label = re.sub(r'^(?:for|in|during|over|from)\s+', '', label)
    label = re.sub(r'^the\s+', '', label)
    note = ''
    if not label or label in {'all future months', 'all open months', 'remaining months'}:
        start, end = first_open, horizon[-1]
        note = 'No specific month was given; this preview covers all open months.'
    elif label in {'first forecast month', 'next forecast month', 'next open month'}:
        start = end = first_open
        note = f'Using the first month after the saved close ({plan["closed_through"]}).'
    elif re.fullmatch(r'(next|this|last) month', label):
        offset = {'next': 1, 'this': 0, 'last': -1}[label.split()[0]]
        start = end = str(pd.Period(today, freq='M') + offset)
        note = f'“{label}” means {calendar.month_name[int(start[5:])]} {start[:4]}, based on the server date {today.isoformat()}. The saved actuals close is {plan["closed_through"]}.'
    elif re.fullmatch(r'(next|this|last) year', label):
        year = today.year + {'next': 1, 'this': 0, 'last': -1}[label.split()[0]]
        start, end = f'{year}-01', f'{year}-12'
        note = f'“{label}” uses the server date {today.isoformat()}.'
    elif re.fullmatch(r'\d{4}-\d{2}(?:\s+(?:to|through)\s+\d{4}-\d{2})?', label):
        matches = re.findall(r'\d{4}-\d{2}', label)
        start, end = matches[0], matches[-1]
    elif re.fullmatch(r'\d{4}', label):
        start, end = label + '-01', label + '-12'
    elif re.fullmatch(r'q[1-4]\s+\d{4}', label):
        quarter, year = label.split(); first = (int(quarter[1])-1)*3+1
        start, end = f'{year}-{first:02d}', f'{year}-{first+2:02d}'
    else:
        names = {name.casefold(): month for month in range(1,13) for name in [calendar.month_name[month],calendar.month_abbr[month]]}
        match = re.fullmatch(r'([a-z]+)(?:\s+(\d{4}))?', label)
        if not match or match[1] not in names:
            raise ValueError('Please use next month, first forecast month, October 2026, Q4 2026, a year, or a YYYY-MM to YYYY-MM range. I did not narrow or broaden the requested period.')
        year = match[2] or inherited_year
        if year is None:
            raise ValueError('Please include the year with that month, for example October 2026.')
        start = end = f'{year}-{names[match[1]]:02d}'
    if start not in horizon or end not in horizon or start > end:
        raise ValueError(f'The requested period {start} to {end} is outside this plan ({horizon[0]} to {horizon[-1]}), or reversed. Choose a period inside the horizon.')
    if start <= plan['closed_through']:
        raise ValueError(f'What-if chat protects closed months through {plan["closed_through"]}. Choose an open period starting {first_open} or later.')
    return start, end, note


def parse_change(plan, question, today):
    text = ' '.join(question.casefold().strip().rstrip('?.!').split())
    text = re.sub(r'\s+please$', '', text)
    prefix = r'^(?:(?:what (?:would|will|could) happen if|what happens if|what if|can you|could you|please|show me what happens if)\s+)?(?:(?:i|we)\s+)?(?:were to\s+)?'
    text = re.sub(prefix, '', text)
    match = re.fullmatch(rf'({CHANGE_WORDS})\s+(.+?)\s+by\s+(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)(.*)', text)
    if not match:
        raise ValueError(UNSUPPORTED_CHANGE)
    verb, target, rate, tail = match.groups()
    tail = tail.strip()
    # Permit the date before 'by' too, but never silently accept a second constraint.
    month_names = '|'.join(m.casefold() for m in list(calendar.month_name)[1:] + list(calendar.month_abbr)[1:])
    before_date = re.search(r'\s+(for|during|over|from)\s+((?:the\s+)?(?:next\b|this\b|last\b|first\b|all\b|q[1-4]\b|\d{4}\b|(?:' + month_names + r')\b).*)$', target)
    if before_date:
        if tail:
            raise ValueError('Give one period or range for the change; no change was proposed.')
        tail = before_date.group(0).strip(); target = target[:before_date.start()]
    field = 'forecast'
    fields = re.findall(r'\b(budget|forecast)\b', target)
    if len(fields) > 1:
        raise ValueError('Preview the budget or forecast separately. A budget preview also shows the possible spending impact.')
    if fields:
        field = fields[0]
        # Only remove a field when used as a prefix/suffix, not inside an unknown account name.
        target = re.sub(r'^(?:(?:the|our|my)\s+)?(?:budget|forecast)\s+(?:for\s+)?', '', target)
        target = re.sub(r'\s+(?:budget|forecast)$', '', target)
    elif re.search(r'\s+(?:spending|spend)$', target):
        target = re.sub(r'\s+(?:spending|spend)$', '', target)
    scope = _scope(plan, target)
    start, end, note = resolve_period(plan, tail, today)
    percent = float(rate) * (-1 if verb.startswith(('decreas','reduc','cut')) else 1)
    if not -100 <= percent <= 1000:
        raise ValueError('Use a decrease of at most 100% or an increase of at most 1,000%.')
    return {'field': field, **scope, 'start': start, 'end': end, 'percent': percent, 'date_note': note}


def _impacts(before, after, request):
    a, b = monthly_report(before), monthly_report(after)
    merged = a.merge(b, on='period', suffixes=('_before','_after'))
    monthly = pd.DataFrame({'period': merged.period})
    for measure in ['budget_operating_profit', 'outlook_operating_profit', 'profit_vs_budget']:
        monthly[measure + '_before'] = merged[measure + '_before']
        monthly[measure + '_after'] = merged[measure + '_after']
        monthly[measure + '_change'] = merged[measure + '_after'] - merged[measure + '_before']
    selected = monthly[monthly.period.between(request['start'], request['end'])]
    def matching(plan):
        return [r for r in plan['rows'] if request['start'] <= r['period'] <= request['end'] and all(r[k] == request[k] for k in ['department','account','category'] if k in request)]
    rows_before, rows_after = matching(before), matching(after)
    results = []
    for category in sorted({r['category'] for r in rows_before}):
        for field, label in [('budget_cents', 'Budget'), ('forecast_cents', 'Forecast')]:
            old = sum(r[field] for r in rows_before if r['category'] == category)/100
            new = sum(r[field] for r in rows_after if r['category'] == category)/100
            results.append({'metric': f'{label}: selected {category} lines', 'before': old, 'after': new, 'change': new-old})
    for measure, label in [('budget_operating_profit','Company budget operating profit'), ('outlook_operating_profit','Company forecast operating profit'), ('profit_vs_budget','Company favorable profit variance')]:
        old, new = selected[measure+'_before'].sum(skipna=False), selected[measure+'_after'].sum(skipna=False)
        results.append({'metric': label, 'before': old, 'after': new, 'change': new-old})
    warnings, cash = [], pd.DataFrame()
    try:
        ca, cb = cash_report(before), cash_report(after)
        cash = ca[['period','net_cash_flow','closing_cash','closing_payables','closing_receivables']].merge(cb[['period','net_cash_flow','closing_cash','closing_payables','closing_receivables']], on='period', suffixes=('_before','_after'))
        for column in ['net_cash_flow','closing_cash','closing_payables','closing_receivables']:
            cash[column+'_change'] = cash[column+'_after']-cash[column+'_before']
        old, new = cash.iloc[-1].closing_cash_before, cash.iloc[-1].closing_cash_after
        results.append({'metric': f'Company closing cash at {cash.iloc[-1].period}', 'before': old, 'after': new, 'change': new-old})
    except ValueError as exc:
        warnings.append('Cash impact unavailable: ' + str(exc))
    return pd.DataFrame(results), monthly, cash, warnings


def evaluate_change(plan, request):
    """Revalidate retained requests before deterministic evaluation. Never writes storage."""
    request = deepcopy(request)
    if not isinstance(request, dict):
        raise ValueError('Ask a new what-if question with a budget or forecast, scope, and period.')
    required = {'field','start','end','percent'}
    allowed = required | {'department','account','category','date_note'}
    if not required <= set(request) or set(request)-allowed or request['field'] not in {'budget','forecast'}:
        raise ValueError('Ask a new what-if question with a budget or forecast, scope, and period.')
    if not any(k in request for k in ['department','account','category']):
        raise ValueError('Name a department, account, or category for this what-if.')
    for key in ['department','account','category']:
        if request.get(key) == 'All':
            raise ValueError('“All” is reserved by the adjustment service. Choose a specific named scope without that label.')
        if key in request and request[key] not in {r[key] for r in plan['rows']}:
            raise ValueError('That saved what-if scope is unavailable in this plan.')
    if any(not isinstance(request[k],str) for k in ['start','end']) or isinstance(request['percent'],bool) or not isinstance(request['percent'],(int,float)) or not math.isfinite(request['percent']):
        raise ValueError('Ask a new what-if question with valid dates and a finite percentage.')
    resolve_period(plan, request['start'] + ' to ' + request['end'], date.today())
    args = {k:v for k,v in request.items() if k not in {'field','date_note'}}
    proposed, changes = adjust(plan, request['field'], **args)
    field = request['field']
    changes.insert(3,'field',field)
    impact, monthly, cash, warnings = _impacts(plan, proposed, request)
    label = ' / '.join(request[k] for k in ['department','account','category'] if k in request)
    currency = plan['currency']
    def money(value):return f'{currency} {value:,.2f}'
    total_before, total_after = changes.before.sum(), changes.after.sum()
    delta = total_after-total_before
    reports = {'Impact summary': impact, 'Monthly profit impact': monthly, 'Monthly cash impact': cash}
    message = (f"**{label} · {field.title()} what-if · {request['start']} to {request['end']}**\n\n"
               f"{request.get('date_note', '')}\n\n"
               f"Selected {field} amounts: **{money(total_before)} → {money(total_after)}** "
               f"({request['percent']:+g}%; change {money(delta)}) across {len(changes)} monthly line(s).\n\n")
    def effect(table, label):return float(table.loc[table.metric == label,'change'].iloc[0])
    budget_profit = effect(impact,'Company budget operating profit')
    forecast_profit = effect(impact,'Company forecast operating profit')
    message += f"- Budget operating profit changes by **{money(budget_profit)}** over the selected months.\n"
    message += f"- Forecast operating profit changes by **{money(forecast_profit)}** over the selected months.\n"
    if not cash.empty:
        message += f"- Closing cash at {cash.iloc[-1].period} changes by **{money(cash.iloc[-1].closing_cash_change)}**.\n"
    if field == 'budget':
        message += "\nThis changes the allowance/target only. Forecast amounts, actuals, and the saved cash forecast stay unchanged. A better budget variance does not mean operating performance improved.\n"
        # Scenario assumption: add each budget increment to the matching forecast, not overwrite it with the budget.
        alternative = deepcopy(proposed)
        old_budgets = {(r['period'],r['department'],r['account']):r['budget_cents'] for r in plan['rows']}
        deltas = {(r['period'],r['department'],r['account']):r['budget_cents']-old_budgets[(r['period'],r['department'],r['account'])] for r in proposed['rows']}
        for row in alternative['rows']:
            row['forecast_cents'] += deltas.get((row['period'],row['department'],row['account']),0)
        try:
            validate_plan(alternative)
            spend, spend_monthly, spend_cash, spend_warnings = _impacts(plan, alternative, request)
            reports.update({'Budget and spend impact':spend, 'Budget and spend profit':spend_monthly, 'Budget and spend cash':spend_cash})
            warnings.extend(w for w in spend_warnings if w not in warnings)
            mixed_sign = any(r['forecast_cents'] < 0 <= old['forecast_cents'] for r,old in zip(alternative['rows'],plan['rows']))
            message += (f"\n**If the same change also happens in the forecast:** add {money(delta)} to the matching forecast lines. For an expense account, this assumes the extra allowance is spent (or a reduction is achieved). "
                        f"Forecast operating profit would change by **{money(effect(spend,'Company forecast operating profit'))}**.")
            if not spend_cash.empty:
                changed = spend_cash[spend_cash.net_cash_flow_change.abs() > .005]
                message += f" Closing cash at {spend_cash.iloc[-1].period} would change by **{money(spend_cash.iloc[-1].closing_cash_change)}**."
                if not changed.empty:
                    message += f" The first cash-flow effect is in **{changed.iloc[0].period}**, using the saved payment/collection timing."
                elif abs(effect(spend,'Company forecast operating profit')) > .005:
                    message += " No cash-flow effect falls inside the horizon; payment/collection timing or non-cash expense may explain the difference."
            if mixed_sign:
                warnings.append('The matching forecast alternative crosses below zero on at least one line. This implies a credit/negative amount; review whether that assumption makes sense.')
            message += "\n\nThis second case is an illustration only. To change expected spending instead, ask for a forecast what-if."
        except ValueError:
            warnings.append('The matching-forecast alternative exceeds the model limits and could not be calculated. The budget preview remains available.')
    else:
        message += '\nThe baseline budget and actuals remain fixed. Cash effects use the saved collection/payment lags; they may occur later than the profit effect.'
        if not cash.empty:
            affected = cash[cash.net_cash_flow_change.abs() > .005]
            if not affected.empty:
                message += f' The first cash-flow effect is in **{affected.iloc[0].period}**.'
            elif abs(forecast_profit) > .005:
                message += ' No cash-flow effect falls inside the horizon; inspect timing and the closing payable/receivable changes. Depreciation is non-cash.'
    selected_categories = {r['category'] for r in plan['rows'] if request['start'] <= r['period'] <= request['end'] and all(r[k] == request[k] for k in ['department','account','category'] if k in request)}
    if 'Revenue' not in selected_categories:
        message += '\n\nNo extra sales are assumed from changing costs.'
    if not any(r['category']=='Revenue' for r in plan['rows']):
        warnings.append('This plan has no revenue. Its profit represents negative costs, not company profitability.')
    message += f'\n\n**Nothing has been saved.** Apply the reviewed proposal to change {field} only, or discard it.'
    context = {'report':'what_if','fingerprint':fingerprint(plan),'change':request}
    return WhatIfResult(message,changes,proposed,context,reports,warnings)
