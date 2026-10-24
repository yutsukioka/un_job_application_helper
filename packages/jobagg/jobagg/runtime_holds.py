"""Exact indexed integrity holds in the existing protected review ledger.

These gates do not exempt any unresolved historical record from reconciliation.
Integrity holds have no clock-based retry or probe grant. Original attempts,
charges, accepted evidence and dirty versions remain intact.
"""

import hashlib
import json
import re

from jobagg import runtime_authority
from jobagg.runtime_source_head import owner

PREFIX = "runtime-hold/"
SCOPES = {"task", "source", "host", "unit"}


def exists(conn):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobagg_legacy_review_holds'"
        ).fetchone()
    )


def key(scope, identity):
    if scope not in SCOPES or not isinstance(identity, str) or not identity:
        raise ValueError("exact_runtime_hold_scope_required")
    return PREFIX + scope + "/" + identity


def put(conn, *, scope, identity, reason, evidence_sha256, next_condition, owner_fd):
    """Explicit owner-held idle review, immutable and idempotent at exact scope."""
    state = runtime_authority.validate(conn)
    owner(owner_fd, state["owner_path"])
    if state["phase"] != "idle":
        raise ValueError("runtime_hold_requires_idle_review")
    if (
        not isinstance(reason, str)
        or not reason
        or not isinstance(next_condition, str)
        or not next_condition
        or not re.fullmatch("[a-f0-9]{64}", evidence_sha256)
    ):
        raise ValueError("runtime_hold_evidence_and_release_condition_required")
    value = {
        "schema": "native-scoped-integrity-hold-1",
        "epoch": state["epoch"],
        "scope": scope,
        "identity": identity,
        "reason": reason,
        "evidence_sha256": evidence_sha256,
        "next_condition": next_condition,
        "automatic_retry": False,
        "probe_ceiling": 0,
        "on_exhaustion": "operator_review",
        "certifies_acceptance_or_publication": False,
    }
    prior = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (key(scope, identity),)
    ).fetchone()
    if prior:
        old = json.loads(prior[0])
        if (
            old.get("epoch") != state["epoch"]
            or old.get("scope") != scope
            or old.get("identity") != identity
        ):
            raise ValueError("runtime_hold_existing_review_differs")
        causes = old.get("causes", [old])
        for cause in causes:
            if cause["evidence_sha256"] == evidence_sha256 and cause["reason"] == reason:
                if cause != value:
                    raise ValueError("runtime_hold_existing_review_differs")
                return cause
        if len(causes) >= 64:
            raise ValueError("runtime_hold_active_review_bound")
        causes = [*causes, value]
        first = next(c for c in causes if c.get("released") is not True)
        payload = json.dumps({**first, "causes": causes}, sort_keys=True)
        conn.execute(
            "UPDATE jobagg_legacy_review_holds SET review_json=? WHERE run_id=?",
            (payload, key(scope, identity)),
        )
    else:
        conn.execute(
            "INSERT INTO jobagg_legacy_review_holds VALUES(?,?)",
            (key(scope, identity), json.dumps(value, sort_keys=True)),
        )
    return value


def find(conn, *, source=None, task=None, host=None, unit=None):
    if not exists(conn):
        return None
    for scope, identity in (("source", source), ("task", task), ("host", host), ("unit", unit)):
        if identity is None:
            continue
        row = conn.execute(
            "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?",
            (key(scope, identity),),
        ).fetchone()
        if row:
            value = json.loads(row[0])
            state = runtime_authority.state(conn)
            if (
                value.get("schema") != "native-scoped-integrity-hold-1"
                or state is None
                or value.get("epoch") != state["epoch"]
                or value.get("scope") != scope
                or value.get("identity") != identity
            ):
                raise ValueError("runtime_hold_binding_changed")
            if value.get("released") is not True:
                return value
    return None


def task_predicate(conn, *, source, task, host):
    """Filter inside the indexed frontier query, before LIMIT 1 (no starvation)."""
    if not exists(conn):
        return "1"
    epoch = (
        "(SELECT epoch FROM jobagg_runtime_state WHERE id=1)"
        if runtime_authority.state(conn)
        else "NULL"
    )
    return " AND ".join(
        "NOT EXISTS(SELECT 1 FROM jobagg_legacy_review_holds rh WHERE rh.run_id='"
        + PREFIX
        + scope
        + "/' || "
        + column
        + " AND NOT (coalesce(json_extract(rh.review_json,'$.released'),0)=1 AND json_extract(rh.review_json,'$.epoch')="
        + epoch
        + "))"
        for scope, column in (("source", source), ("task", task), ("host", host))
    )


def require_selection(conn, selection):
    for item in selection["units"]:
        hold = find(conn, source=item["source_id"], unit=item["unit"])
        if hold:
            raise ValueError("publication_scope_integrity_hold: " + hold["reason"])


def release(
    conn, *, scope, identity, expected_evidence_sha256, review_sha256, correction, owner_fd
):
    """Explicit matching-hold release after reviewed correction; retain both reviews."""
    state = runtime_authority.validate(conn)
    owner(owner_fd, state["owner_path"])
    if (
        state["phase"] != "idle"
        or not correction
        or not re.fullmatch("[a-f0-9]{64}", review_sha256)
    ):
        raise ValueError("runtime_hold_release_requires_idle_exact_review")
    record = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (key(scope, identity),)
    ).fetchone()
    if record is None:
        raise ValueError("runtime_hold_release_exact_scope_missing")
    previous = json.loads(record[0])
    if (
        previous.get("schema") != "native-scoped-integrity-hold-1"
        or previous.get("epoch") != state["epoch"]
        or previous.get("scope") != scope
        or previous.get("identity") != identity
    ):
        raise ValueError("runtime_hold_release_original_review_changed")
    causes = previous.get("causes", [previous])
    selected = [
        i
        for i, c in enumerate(causes)
        if c.get("evidence_sha256") == expected_evidence_sha256 and c.get("released") is not True
    ]
    if len(selected) != 1:
        raise ValueError("runtime_hold_release_original_review_changed")
    index = selected[0]
    original = causes[index]
    value = {
        **original,
        "released": True,
        "release_review_sha256": review_sha256,
        "correction": correction,
        "previous_review": original,
    }
    causes = [*causes[:index], value, *causes[index + 1 :]]
    active = [cause for cause in causes if cause.get("released") is not True]
    current = {**(active[0] if active else value), "causes": causes} if len(causes) > 1 else value
    audit = {
        "schema": "native-scoped-hold-release-review-1",
        "previous": previous,
        "current": current,
    }
    payload = json.dumps(audit, sort_keys=True)
    conn.execute(
        "INSERT INTO jobagg_legacy_review_holds VALUES(?,?)",
        ("runtime-hold-release/" + hashlib.sha256(payload.encode()).hexdigest(), payload),
    )
    from jobagg.runtime_containment import invalidate

    invalidate(conn, "scoped hold release requires updated shared-state review")
    conn.execute(
        "UPDATE jobagg_legacy_review_holds SET review_json=? WHERE run_id=?",
        (json.dumps(current, sort_keys=True), key(scope, identity)),
    )
    return value
