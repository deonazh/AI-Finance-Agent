"""Transactional local plan versions with optimistic concurrency and snapshots.

Actor names are local review labels, not authenticated identities. This store
is a single-workspace deployment, not a tenant authorization boundary.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json
import os
import sqlite3

from epm import validate_plan, fingerprint, missing_actuals, text_field, roll_horizon


class ConflictError(ValueError):
    pass


def storage_path():
    return Path(os.environ.get("FPA_DATABASE_PATH", str(Path(__file__).parent / "data" / "planning.sqlite3"))).expanduser()


class PlanStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else storage_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
                    revision INTEGER NOT NULL, parent_id TEXT, budget_locked INTEGER NOT NULL,
                    payload TEXT NOT NULL, updated_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    plan_id TEXT NOT NULL REFERENCES plans(id), revision INTEGER NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL,
                    at_utc TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, PRIMARY KEY(plan_id, revision));
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            raise ValueError("The selected plan no longer exists.")
        result = dict(row)
        result["plan"] = validate_plan(json.loads(result.pop("payload")))
        return result

    def list(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,name,state,revision,parent_id,budget_locked,updated_utc FROM plans ORDER BY updated_utc DESC,id")]

    def get(self, identifier):
        with self.connection() as db:
            return self._decode(db.execute("SELECT * FROM plans WHERE id=?", (identifier,)).fetchone())

    def create(self, plan, actor, parent_id=None):
        validate_plan(plan)
        actor = text_field(actor, "Review name")
        identifier, now = uuid4().hex, datetime.now(timezone.utc).isoformat()
        payload = json.dumps(plan, allow_nan=False, sort_keys=True)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                locked = plan["kind"] != "Budget"
                if parent_id:
                    parent = self._decode(db.execute("SELECT * FROM plans WHERE id=?", (parent_id,)).fetchone())
                    locked = locked or parent["budget_locked"] or parent["state"] == "Approved"
                    if locked:
                        previous = {r[:4]: r[4] for r in self._budgets(parent["plan"])}
                        shared = [r for r in self._budgets(plan) if r[:4] in previous]
                        if any(r[4] != previous[r[:4]] for r in shared):
                            raise ValueError("A linked scenario must retain its source budget for overlapping periods.")
                db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?)", (identifier, plan["name"], "Draft", 1, parent_id, int(locked), payload, now))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (identifier, 1, "Created", actor, "New draft", now, "Draft", payload, fingerprint(plan)))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.get(identifier)

    @staticmethod
    def _budgets(plan):
        return sorted((r["period"], r["department"], r["account"], r["category"], r["budget_cents"]) for r in plan["rows"])

    def save(self, identifier, expected_revision, plan, actor, note):
        return self._change(identifier, expected_revision, actor, note, plan=plan)

    def transition(self, identifier, expected_revision, state, actor, note):
        return self._change(identifier, expected_revision, actor, note, state=state)

    def _change(self, identifier, expected_revision, actor, note, plan=None, state=None):
        actor = text_field(actor, "Review name")
        note = text_field(note, "Change/review note", 2000)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = self._decode(db.execute("SELECT * FROM plans WHERE id=?", (identifier,)).fetchone())
                if current["revision"] != expected_revision:
                    raise ConflictError("This plan changed in another session. Reload it before saving; no changes were written.")
                if plan is not None:
                    if current["state"] != "Draft":
                        raise ValueError("Only drafts are editable. Return a submitted plan to draft or create a new version of an approved plan.")
                    validate_plan(plan)
                    if plan["closed_through"] < current["plan"]["closed_through"]:
                        raise ValueError("Actuals cutoff cannot move backwards within a version.")
                    if any(plan[key] != current["plan"][key] for key in ["currency", "start", "months", "kind"]):
                        raise ValueError("Currency, horizon, and plan type cannot change within a version.")
                    if current["budget_locked"] and self._budgets(plan) != self._budgets(current["plan"]):
                        raise ValueError("The baseline budget is locked. Create a separate budget version to revise it.")
                    state, action = current["state"], "Saved"
                else:
                    if (current["state"], state) not in {("Draft", "Submitted"), ("Submitted", "Draft"), ("Submitted", "Approved")}:
                        raise ValueError("Unsupported review transition. Approved versions remain locked.")
                    plan, action = current["plan"], state
                    if state in {"Submitted", "Approved"} and missing_actuals(plan):
                        raise ValueError("Complete all closed-period actuals before submitting or approving.")
                    if state in {"Submitted", "Approved"} and plan["cash"]["as_of"] != plan["closed_through"]:
                        raise ValueError("Confirm cash opening balances for the current actuals cutoff before review.")
                payload, now = json.dumps(plan, sort_keys=True, allow_nan=False), datetime.now(timezone.utc).isoformat()
                revision = expected_revision + 1
                locked = current["budget_locked"] or state == "Approved"
                db.execute("UPDATE plans SET name=?,state=?,revision=?,budget_locked=?,payload=?,updated_utc=? WHERE id=? AND revision=?",
                    (plan["name"], state, revision, int(locked), payload, now, identifier, expected_revision))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (identifier, revision, action, actor, note, now, state, payload, fingerprint(plan)))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.get(identifier)

    def clone(self, identifier, name, kind, actor):
        record = self.get(identifier)
        plan = deepcopy(record["plan"])
        plan["name"], plan["kind"] = text_field(name, "Plan name"), kind
        plan["assumptions"].append(f"Copied from {record['name']} revision {record['revision']} ({record['state']}).")
        return self.create(plan, actor, parent_id=identifier)

    def history(self, identifier):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT revision,action,actor,note,at_utc,state,fingerprint FROM events WHERE plan_id=? ORDER BY revision DESC", (identifier,))]

    def roll(self, identifier, name, start, count, actor):
        current = self.get(identifier)
        plan = roll_horizon(current["plan"], name, start, count)
        return self.create(plan, actor, parent_id=identifier)

    def snapshot(self, identifier, revision):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM events WHERE plan_id=? AND revision=?", (identifier, revision)).fetchone()
            if row is None:
                raise ValueError("Revision not found.")
            return validate_plan(json.loads(row["payload"]))

    def backup(self, identifier):
        record = self.get(identifier)
        return json.dumps({"format": "fpa-plan-backup", "version": 1, "plan": record["plan"],
            "source_state": record["state"], "source_revision": record["revision"], "fingerprint": fingerprint(record["plan"]),
            "review_history": self.history(identifier)}, indent=2, allow_nan=False).encode()

    def restore(self, data, actor):
        if len(data) > 20_000_000:
            raise ValueError("Backup must be under 20 MB.")
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid planning backup JSON.") from exc
        if not isinstance(payload, dict) or payload.get("format") != "fpa-plan-backup" or payload.get("version") != 1:
            raise ValueError("Unsupported planning backup.")
        plan = validate_plan(payload.get("plan"))
        if fingerprint(plan) != payload.get("fingerprint"):
            raise ValueError("Backup checksum does not match its contents.")
        plan = deepcopy(plan)
        plan["name"] = (plan["name"][:105] + " · restored")
        plan["assumptions"].append("Restored as a new draft. Backup review history is unverified and does not grant approval.")
        return self.create(plan, actor)
