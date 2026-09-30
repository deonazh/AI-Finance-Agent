"""A portable month-end review register tied to the exact ledger contents."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json

import pandas as pd

from data_engine import filter_significant_variances
from exports import workbook_bytes
from reporting import currency_from_frame

STATUSES = ["Open", "In progress", "Resolved"]
EDITABLE = ["owner", "status", "due_date", "explanation", "next_action"]
KEY_COLUMNS = ["fiscal_year", "period", "cost_center", "gl_account", "budget", "actual"]


def ledger_id(df):
    columns = KEY_COLUMNS + (["currency"] if "currency" in df else [])
    stable = df[columns].sort_values(KEY_COLUMNS[:4]).to_json(orient="records", double_precision=10)
    return sha256(stable.encode()).hexdigest()


def row_id(row):
    return sha256(json.dumps([str(row[col]) for col in KEY_COLUMNS], ensure_ascii=False).encode()).hexdigest()[:24]


def review_register(df, notes=None, threshold_pct=10.0, threshold_dollars=50_000.0):
    notes = notes or {}
    flagged = filter_significant_variances(df, threshold_pct, threshold_dollars)
    rows = []
    for row in flagged.to_dict("records"):
        identifier = row_id(row)
        fields = {"owner": "", "status": "Open", "due_date": "", "explanation": "", "next_action": ""}
        fields.update(notes.get(identifier, {}))
        rows.append({"review_id": identifier, **{c: row[c] for c in KEY_COLUMNS},
                     "variance": row["variance"], "variance_pct": row["variance_pct"],
                     "flag_reason": row["flag_reason"], **fields})
    return pd.DataFrame(rows, columns=["review_id", *KEY_COLUMNS, "variance", "variance_pct", "flag_reason", *EDITABLE])


def validate_notes(notes, valid_ids):
    if not isinstance(notes, dict) or set(notes) - set(valid_ids):
        raise ValueError("Review notes contain records outside this ledger.")
    clean = {}
    for key, values in notes.items():
        if not isinstance(values, dict) or set(values) - set(EDITABLE):
            raise ValueError("Review notes have an unsupported field.")
        fields = {}
        for name in EDITABLE:
            value = values.get(name, "Open" if name == "status" else "")
            if isinstance(value, (dict, list)):
                raise ValueError("Review fields must contain text values.")
            fields[name] = "" if value is None or pd.isna(value) else str(value).strip()
        if fields["status"] not in STATUSES:
            raise ValueError("Choose Open, In progress, or Resolved for the review status.")
        if any(len(value) > 4000 for value in fields.values()):
            raise ValueError("Review fields must be 4,000 characters or fewer.")
        if fields["due_date"]:
            try:
                date.fromisoformat(fields["due_date"])
            except ValueError as exc:
                raise ValueError("Due date must use YYYY-MM-DD.") from exc
        if fields["status"] == "Resolved" and not (fields["owner"] and fields["explanation"]):
            raise ValueError("A resolved item requires an owner and an explanation.")
        clean[key] = fields
    return clean


def save_edits(edited, existing, df):
    valid_ids = {row_id(row) for row in df.to_dict("records")}
    updates = {str(row["review_id"]): {col: row[col] for col in EDITABLE} for row in edited.to_dict("records")}
    return validate_notes({**existing, **updates}, valid_ids)


def review_json(df, notes):
    checked = validate_notes(notes, {row_id(row) for row in df.to_dict("records")})
    payload = {"schema_version": 1, "ledger_id": ledger_id(df),
               "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "notes": checked}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode()


def restore_review(data, df):
    if len(data) > 5_000_000:
        raise ValueError("Review file must be under 5 MB.")
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("This is not a valid review JSON file.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported review file version.")
    if payload.get("ledger_id") != ledger_id(df):
        raise ValueError("This review belongs to a different ledger. Load the same source data before restoring it.")
    return validate_notes(payload.get("notes"), {row_id(row) for row in df.to_dict("records")})


def month_end_pack(df, register, context, briefing=""):
    summary = df.groupby("cost_center", as_index=False).agg(budget=("budget", "sum"), actual=("actual", "sum"))
    summary["variance"] = summary["actual"] - summary["budget"]
    methods = pd.DataFrame([
        ("Variance", "Actual - Budget; positive is unfavorable for expense accounts."),
        ("Variance percentage", "Variance / Budget × 100. Blank for zero budgets."),
        ("Materiality", "Absolute percentage OR amount exceeds the threshold; all nonzero activity with zero budget is flagged."),
        ("Review status", "User-entered review notes; not an approval, accounting adjustment, or audit trail."),
        ("Root causes", "Source-supplied classifications and analyst hypotheses require supporting evidence."),
        ("Coverage", "Only the selected reporting year, closed periods, and dashboard filters are included."),
    ], columns=["Rule", "Explanation"])
    sheets = {"Department Summary": summary, "Ledger Detail": df, "Review Register": register, "Methodology": methods}
    if briefing:
        sheets["Management Brief"] = pd.DataFrame({"Briefing": briefing.splitlines()})
    metadata = {"Currency": currency_from_frame(df), "Ledger fingerprint": ledger_id(df),
                "Rows included": len(df), "Total budget": float(df.budget.sum()),
                "Total actual": float(df.actual.sum()), "Variance": float((df.actual-df.budget).sum()), **context}
    return workbook_bytes(sheets, metadata)
