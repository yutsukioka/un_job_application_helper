"""Temporary databases and captured fixtures only; no provider or production use."""

from dataclasses import asdict
from datetime import UTC, datetime
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest
import yaml

from jobagg import source_incident_repair as repair
from jobagg.captured_transient_repair import IOM_LISTING_URL, IFAD_LISTING_URL, UNV_SUPPORT_URL
from jobagg.models import JobRecord
from jobagg.pipelines.inventory_checks import verify_listing
from jobagg.pipelines.http_checkpoint import HostIneligible
from jobagg.pipelines.sync_source import load_sources
from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.remediation_worker import Worker, dump, implementation_hash, sha, task_key


WHO_BODY = b"""<form id="ftlform" action="unavailablerequisition.ftl"><input name="ftlpageid" value="unavaibleRequisitionPage"><div id="requisitionUnavailableInterface"></div></form><script>api.fillInterface('requisitionUnavailableInterface', ['The job description you are trying to view is no longer available.']);</script>"""
SEARCH = "https://app.unv.org/api/doa/doa/SearchDoaAsyncByAzureCognitive"


def stamp(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(value))


class Fixture:
    def __init__(self, root):
        self.root = root
        self.workspace = root / "worker"
        self.workspace.mkdir()
        self.lock = root / "owner.lock"
        self.lock.touch()
        self.registry = root / "sources.yaml"
        self.robots = root / "robots.yaml"
        self.now = int(time.time()) - 1000
        self.sources = [
            {
                "id": "iom_oracle_hcm",
                "ats_family": "oracle_hcm",
                "base_url": "https://fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com",
                "extra": {"site_number": "CX_1001"},
            },
            {
                "id": "ifad_peoplesoft",
                "ats_family": "peoplesoft",
                "base_url": IFAD_LISTING_URL.split("?")[0],
                "extra": {"listing_url": IFAD_LISTING_URL},
            },
            {
                "id": "unv_uvp",
                "ats_family": "unv",
                "base_url": "https://app.unv.org",
                "extra": {
                    "api_url": SEARCH,
                    "page_size": 50,
                    "max_pages": 10,
                    "search_payload": {},
                    "detail_api_url_template": "https://app.unv.org/api/doa/doa/{job_id}",
                    "public_category_sections_url": UNV_SUPPORT_URL,
                },
            },
            {
                "id": "who_taleo",
                "ats_family": "taleo",
                "base_url": "https://careers.who.int/careersection/ex/jobsearch.ftl",
            },
        ]
        self.registry.write_text(yaml.safe_dump({"sources": self.sources}))
        self.robots.write_text("default: {}\n")
        self.maintenance = root / "maintenance"
        self.maintenance.write_text("owned test maintenance\n")
        self.output = root / "output"
        self.output.mkdir()
        write(
            self.output / repair.GATE_NAME,
            {"state": "complete", "status": "published", "database_transactions_complete": True},
        )
        self.config = root / "dispatcher.json"
        write(
            self.config,
            {
                "shared_lock_path": str(self.lock),
                "worker_cwd": str(Path(repair.__file__).resolve().parent.parent),
                "publication_cwd": str(Path(repair.__file__).resolve().parent.parent),
                "maintenance_file": str(self.maintenance),
                "publication_worker_database": str(self.workspace / "jobs.sqlite3"),
                "worker_argv": [
                    "python",
                    "--workspace",
                    str(self.workspace),
                    "--shared-lock",
                    str(self.lock),
                    "--registry",
                    str(self.registry),
                    "--robots",
                    str(self.robots),
                ],
                "publication_argv": [
                    "python",
                    "--worker-database",
                    str(self.workspace / "jobs.sqlite3"),
                    "--shared-lock",
                    str(self.lock),
                    "--registry",
                    str(self.registry),
                    "--output-dir",
                    str(self.output),
                ],
            },
        )
        self.seal()
        bootstrap = root / "bootstrap.json"
        write(
            bootstrap,
            {
                "schema_version": 1,
                "shared_lock": str(self.lock),
                "reviewed_at": stamp(time.time()),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "scope_source_ids": [x["id"] for x in self.sources],
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
            },
        )
        self.policy = SharedPolicy(self.lock, [x["id"] for x in self.sources], bootstrap)
        self.policy.initialize()
        with self.db() as c:
            c.executescript("""CREATE TABLE remediation_tasks(task_id TEXT PRIMARY KEY,source_id TEXT,kind TEXT,external_id TEXT,payload TEXT,status TEXT,eligible_at REAL,discovered_at REAL,attempts INTEGER,claim TEXT,last_error TEXT,receipt TEXT);
CREATE TABLE remediation_attempts(attempt_id TEXT PRIMARY KEY,task_id TEXT,source_id TEXT,kind TEXT,started_at REAL,finished_at REAL,status TEXT,evidence TEXT);
CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,next_list_at REAL,last_list_at REAL,listing_ids TEXT,listing_proof TEXT,last_service REAL,host TEXT);
CREATE TABLE source_circuit_breakers(source_id TEXT PRIMARY KEY,state TEXT);
CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,external_id TEXT,title TEXT,description TEXT,raw_json TEXT,first_seen_at TEXT,closes_at TEXT);
CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY,source_id TEXT,checked_at REAL,proof TEXT);""")
        self.selected = []

    def seal(self, value=None):
        write(
            self.workspace / "worker_workspace.json",
            {
                "version": "deterministic-fetch-v1",
                "shared_lock": str(self.lock),
                "implementation_sha256": value or implementation_hash(),
                "registry_sha256": sha(self.registry),
                "robots_sha256": sha(self.robots),
            },
        )

    def db(self):
        c = sqlite3.connect(self.workspace / "jobs.sqlite3")
        c.row_factory = sqlite3.Row
        return c

    def capture(
        self,
        claim,
        source,
        kind,
        identity,
        body,
        *,
        url,
        number=1,
        code=200,
        method="GET",
        legacy=False,
        when=None,
        request=None,
    ):
        family = next(x["ats_family"] for x in self.sources if x["id"] == source)
        when = self.now + 101 if when is None else when
        base = self.workspace / "captures" / claim / "http"
        base.mkdir(parents=True, exist_ok=True)
        blob = base / f"{number:05}.body.gz"
        blob.write_bytes(gzip.compress(body))
        meta = {
            "number": number,
            "source_binding": {"source_id": source, "ats_family": family, "cxs_base_url": None},
            "external_id": identity or None,
            "phase": {"kind": kind, "job_id": identity or None},
            "method": method,
            "url": url,
            "response_url": url,
            "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
            "response_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
            "state": "response_captured" if code == 200 else "failed",
            "status_code": code,
            "body_captured": True,
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "artifact": str(blob),
            "started_at": stamp(when),
            "finished_at": stamp(when + 1),
            "response_headers": {
                "Content-Type": "application/json" if body.startswith(b"{") else "text/html"
            },
        }
        if legacy:
            meta.pop("source_binding")
        if code != 200:
            meta.update(
                error_type="HTTPError",
                failure_category="transient_transport",
                error=f"HTTPError: HTTP Error {code}: Captured HTTP denial/error",
            )
        if request is not None:
            meta["request_body_sha256"] = hashlib.sha256(
                json.dumps(request, separators=(",", ":")).encode()
            ).hexdigest()
        path = base / f"{number:05}.json"
        write(path, meta)
        return path

    def incident(self, source, identity="", *, legacy=False, action=None):
        kind = "detail" if identity else "listing"
        key = task_key(source, kind, identity)
        claim = source + "-" + (identity or "listing")
        payload = {}
        family = next(x["ats_family"] for x in self.sources if x["id"] == source)
        if kind == "detail":
            url = (
                "https://app.unv.org/opportunities/" + identity
                if source == "unv_uvp"
                else "https://careers.who.int/careersection/ex/jobdetail.ftl?job=" + identity
            )
            job = JobRecord(
                source,
                source,
                family,
                "Retained role",
                url,
                external_id=identity,
                source_url=url,
                raw={"id": identity} if source == "unv_uvp" else {"contestNo": identity},
            )
            original = self.workspace / "captures" / (claim + "-listing") / "listing.json"
            write(
                original,
                {"source_id": source, "observed_at": stamp(self.now), "jobs": [asdict(job)]},
            )
            payload = {
                "listing": json.loads(dump(asdict(job))),
                "frame_path": str(original),
                "frame_sha256": sha(original),
            }
            evidence = {
                "frame_path": str(original),
                "frame_sha256": sha(original),
                "enumeration": {"complete": False},
            }
            with self.db() as c:
                c.execute(
                    "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        claim + "-listing",
                        task_key(source, "listing"),
                        source,
                        "listing",
                        self.now - 2,
                        self.now + 1,
                        "done",
                        dump(evidence),
                    ),
                )
                c.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
                    (
                        source + ":" + identity,
                        source,
                        identity,
                        "Retained role",
                        "Accepted full job text",
                        '{"accepted":true}',
                        stamp(self.now - 100),
                        None,
                    ),
                )
                c.execute(
                    "INSERT INTO remediation_observations VALUES(?,?,?,?)",
                    (source + ":" + identity, source, self.now, dump({"preserve": True})),
                )
        if source == "who_taleo":
            self.capture(claim, source, kind, identity, WHO_BODY, url=url)
            error = "IncompleteDetailResponse: Taleo returned no native requisition identity"
            action = action or "who_unavailable"
        elif source == "unv_uvp" and action != "retry_captured_transient":
            self.capture(
                claim,
                source,
                kind,
                identity,
                b'{"traceRegistries":[],"isSuccess":true,"value":null}',
                url="https://app.unv.org/api/doa/doa/" + identity,
                legacy=legacy,
            )
            error = "ValueError: UNV detail response is not a single public assignment"
            action = "unv_absent"
        elif source == "unv_uvp":
            native = {
                "isSuccess": True,
                "value": {
                    "id": identity,
                    "volunteersCategoryDetails": {
                        "volunteersCategory": {"value": {"code": "NATIONAL"}}
                    },
                    "status": {"value": {"code": "DOA_SOURCING"}},
                },
            }
            self.capture(
                claim,
                source,
                kind,
                identity,
                json.dumps(native).encode(),
                url="https://app.unv.org/api/doa/doa/" + identity,
            )
            self.capture(
                claim,
                source,
                kind,
                identity,
                b"<html>502 Bad Gateway Azure Front Door OriginConnectionAborted</html>",
                url=UNV_SUPPORT_URL,
                number=2,
                method="POST",
                code=502,
                request={"volunteerCategoryCode": "NATIONAL", "entityName": "doa,doaCandidate"},
            )
            error = "HTTPError: HTTP Error 502: Captured HTTP denial/error"
        else:
            url = IOM_LISTING_URL if source == "iom_oracle_hcm" else IFAD_LISTING_URL
            body = (
                b"<html>planned outage scheduled maintenance</html>"
                if source == "iom_oracle_hcm"
                else b"<html>IFAD eRecruitment system is currently offline for maintenance</html>"
            )
            self.capture(claim, source, kind, identity, body, url=url, code=503)
            error = "HTTPError: HTTP Error 503: Captured HTTP denial/error"
            action = "retry_captured_transient"
        receipt = {
            "capture_directory": str(self.workspace / "captures" / claim),
            "error": error,
            "incomplete_response_count": 3,
            "retry_decision": None,
        }
        with self.db() as c:
            c.execute(
                "INSERT INTO remediation_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    source,
                    kind,
                    identity,
                    dump(payload),
                    "blocked",
                    0,
                    self.now - 100,
                    7,
                    claim,
                    error,
                    dump(receipt),
                ),
            )
            c.execute(
                "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                (
                    claim,
                    key,
                    source,
                    kind,
                    self.now + 100,
                    self.now + 110,
                    "blocked",
                    dump(receipt),
                ),
            )
            c.execute(
                "INSERT OR IGNORE INTO remediation_sources VALUES(?,?,?,?,?,?,?)",
                (source, self.now + 10000, None, "[]", "{}", self.now, None),
            )
        self.selected.append({"task_id": key, "action": action})
        return key

    def census(self, identities=(), *, total=None, old=False):
        source = next(s for s in load_sources(self.registry) if s.id == "unv_uvp")
        when = self.now + 50 if old else self.now + 200
        claim = "census"
        jobs = [
            JobRecord(
                source.id,
                source.id,
                "unv",
                "Role",
                "https://app.unv.org/opportunities/" + x,
                external_id=x,
                raw={"id": x},
            )
            for x in identities
        ]
        body = json.dumps(
            {
                "value": {
                    "total": len(jobs) if total is None else total,
                    "result": [{"id": x} for x in identities],
                }
            }
        ).encode()
        meta = self.capture(
            claim,
            source.id,
            "listing",
            "",
            body,
            url=SEARCH,
            method="POST",
            when=when,
            request={"take": 50, "skip": 0},
        )
        proof = verify_listing(source, jobs, [meta])
        frame = self.workspace / "captures" / claim / "listing.json"
        write(
            frame,
            {
                "source_id": source.id,
                "observed_at": stamp(when + 2),
                "jobs": [asdict(x) for x in jobs],
            },
        )
        with self.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                (
                    claim,
                    task_key(source.id, "listing"),
                    source.id,
                    "listing",
                    when - 1,
                    when + 3,
                    "done",
                    dump(
                        {"frame_path": str(frame), "frame_sha256": sha(frame), "enumeration": proof}
                    ),
                ),
            )
            c.execute(
                "UPDATE remediation_sources SET last_list_at=?,listing_ids=?,listing_proof=? WHERE source_id=?",
                (when + 2, dump([x.identity_key() for x in jobs]), dump(proof), source.id),
            )
        return frame

    def plan(self, **kwargs):
        return repair.prepare(self.workspace, self.lock, self.config, self.selected, **kwargs)

    def protected(self):
        with self.db() as c:
            data = {
                t: [dict(x) for x in c.execute("SELECT * FROM " + t)]
                for t in [
                    "jobs",
                    "remediation_observations",
                    "remediation_attempts",
                    "remediation_sources",
                    "source_circuit_breakers",
                ]
            }
        data["policy"] = {
            str(p): p.read_bytes() for p in self.policy.root.rglob("*") if p.is_file()
        }
        return data


@pytest.fixture
def case(tmp_path):
    return Fixture(tmp_path)


def test_explicit_mixed_actions_preserve_history_schedule_and_idempotency(case):
    case.incident("iom_oracle_hcm")
    case.incident("ifad_peoplesoft")
    case.incident("unv_uvp", "101", legacy=True)
    case.incident("unv_uvp", "102")
    case.incident("who_taleo", "2604311")
    case.incident("unv_uvp", "103", action="retry_captured_transient")
    case.census(["103"])
    protected = case.protected()
    plan = case.plan()
    assert plan["database_writes"] == 0 and case.protected() == protected
    result = repair.apply(plan)
    assert result["tasks_changed"] == 6 and case.protected() == protected
    with case.db() as c:
        for item in plan["items"]:
            actual = dict(
                c.execute(
                    "SELECT * FROM remediation_tasks WHERE task_id=?",
                    (item["task_before"]["task_id"],),
                ).fetchone()
            )
            assert actual == item["task_after"]
            for key in ["attempts", "claim", "payload", "discovered_at", "last_error"]:
                assert actual[key] == item["task_before"][key]
        assert c.execute("SELECT count(*) FROM source_incident_repairs").fetchone()[0] == 6
    assert repair.apply(plan)["status"] == "already_applied"
    assert case.protected() == protected


@pytest.mark.parametrize("source", ["iom_oracle_hcm", "ifad_peoplesoft"])
def test_listing_repair_preserves_future_source_eligibility(case, source, monkeypatch):
    case.incident(source)
    due = time.time() + 7200
    with case.db() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=? WHERE source_id=?", (due, source))
    protected = case.protected()
    plan = case.plan()
    assert plan["items"][0]["task_after"]["eligible_at"] == due
    repair.apply(plan)
    assert case.protected() == protected
    # Exercise actual worker selection/claim gates against the repaired fixture DB.
    worker = object.__new__(Worker)
    worker._database_local = SimpleNamespace(database=SimpleNamespace(connect=case.db, connection_scope=case.db))
    worker.by_id = {s.id: s for s in load_sources(case.registry)}
    worker.shared_policy = case.policy
    worker._eligible_peaks = (0, 0)
    monkeypatch.setattr(time, "time", lambda: due - 1)
    assert worker.choose() is None
    with pytest.raises(HostIneligible, match="eligibility"):
        worker.claim(plan["items"][0]["task_after"])
    monkeypatch.setattr(time, "time", lambda: due)
    assert worker.choose()["task_id"] == plan["items"][0]["task_before"]["task_id"]
    assert case.protected() == protected


@pytest.mark.parametrize("offset", [-60, 0])
def test_elapsed_listing_schedule_does_not_delay_repair(case, monkeypatch, offset):
    case.incident("iom_oracle_hcm")
    now = time.time()
    with case.db() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=?", (now + offset,))
    monkeypatch.setattr(time, "time", lambda: now)
    plan = case.plan()
    assert plan["items"][0]["task_after"]["eligible_at"] == now


@pytest.mark.parametrize("later_gate", ["task", "host"])
def test_listing_schedule_does_not_lower_later_task_or_host_gate(case, later_gate):
    case.incident("iom_oracle_hcm")
    now = time.time()
    with case.db() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=?", (now + 300,))
        if later_gate == "task":
            conn.execute("UPDATE remediation_tasks SET eligible_at=?", (now + 900,))
    if later_gate == "host":
        host = "fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com"
        path = case.policy.root / "hosts" / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
        write(path, {"stopped": False, "eligible_at": now + 900})
    plan = case.plan()
    assert plan["items"][0]["task_after"]["eligible_at"] == now + 900


@pytest.mark.parametrize("invalid", [None, -1, float("inf"), "missing_row"])
def test_listing_repair_requires_valid_current_source_schedule(case, invalid):
    case.incident("ifad_peoplesoft")
    with case.db() as conn:
        if invalid == "missing_row":
            conn.execute("DELETE FROM remediation_sources")
        else:
            conn.execute("UPDATE remediation_sources SET next_list_at=?", (invalid,))
    before = case.protected()
    with pytest.raises(ValueError):
        case.plan()
    assert case.protected() == before


def test_source_schedule_change_after_preview_refuses_apply(case):
    case.incident("ifad_peoplesoft")
    plan = case.plan()
    with case.db() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=next_list_at+3600")
    before = case.protected()
    with pytest.raises(ValueError, match="state changed since preview"):
        repair.apply(plan)
    assert case.protected() == before
    with case.db() as conn:
        assert conn.execute("SELECT status FROM remediation_tasks").fetchone()[0] == "blocked"


def test_listing_schedule_floor_does_not_apply_to_unv_detail(case):
    case.incident("unv_uvp", "103", action="retry_captured_transient")
    case.census(["103"])
    with case.db() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=?", (time.time() + 10000,))
    plan = case.plan()
    assert plan["items"][0]["task_after"]["eligible_at"] == plan["as_of"]


def test_preview_old_seal_is_read_only_and_never_executable(case):
    case.incident("who_taleo", "2604311")
    deployed = case.root / "old-release"
    (deployed / "jobagg").mkdir(parents=True)
    (deployed / "jobagg" / "old.py").write_text("# prior implementation\n")
    old_seal = repair.package_implementation_hash(deployed)
    config = json.loads(case.config.read_text())
    config.update(worker_cwd=str(deployed), publication_cwd=str(deployed))
    write(case.config, config)
    case.seal(old_seal)
    case.maintenance.unlink()
    before = (case.workspace / "jobs.sqlite3").read_bytes()
    files = {str(x) for x in case.root.rglob("*")}
    plan = case.plan(expected_old_implementation=old_seal)
    assert (
        not plan["runtime"]["executable"]
        and plan["runtime"]["candidate_implementation_sha256"] == implementation_hash()
    )
    assert (case.workspace / "jobs.sqlite3").read_bytes() == before and {
        str(x) for x in case.root.rglob("*")
    } == files
    with pytest.raises(ValueError, match="Old-seal"):
        repair.apply(plan)
    with repair.connection(case.workspace) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE remediation_tasks SET attempts=0")


@pytest.mark.parametrize("role", ["worker", "publication"])
def test_configured_code_must_match_current_seal(case, role):
    case.incident("who_taleo", "2604311")
    wrong = case.root / "wrong-release"
    (wrong / "jobagg").mkdir(parents=True)
    (wrong / "jobagg" / "other.py").write_text("# different implementation\n")
    config = json.loads(case.config.read_text())
    config[role + "_cwd"] = str(wrong)
    write(case.config, config)
    with pytest.raises(ValueError, match="implementation differs from workspace seal"):
        case.plan()


@pytest.mark.parametrize("table", ["jobs", "remediation_observations"])
def test_listing_repair_binds_all_existing_source_jobs_and_observations(case, table):
    case.incident("iom_oracle_hcm")
    with case.db() as conn:
        conn.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
            ("retained", "iom_oracle_hcm", "200", "Title", "Accepted text", "{}", "old", None),
        )
        conn.execute(
            "INSERT INTO remediation_observations VALUES(?,?,?,?)",
            ("retained", "iom_oracle_hcm", case.now, "accepted observation"),
        )
    plan = case.plan()
    with case.db() as conn:
        if table == "jobs":
            conn.execute("UPDATE jobs SET description='changed'")
        else:
            conn.execute("UPDATE remediation_observations SET proof='changed'")
    with pytest.raises(ValueError, match="changed"):
        repair.apply(plan)
    with case.db() as conn:
        assert conn.execute("SELECT status FROM remediation_tasks").fetchone()[0] == "blocked"


@pytest.mark.parametrize(
    "tamper",
    [
        "task",
        "claim",
        "attempt",
        "job",
        "observation",
        "source_state",
        "circuit",
        "host",
        "quota",
        "maintenance",
        "gate",
        "capture",
        "frame",
        "seal",
        "selection",
        "stale",
    ],
)
def test_stale_or_tampered_plan_refuses_without_mutation(case, tamper):
    key = case.incident("unv_uvp", "101", legacy=True)
    frame = case.census()
    plan = case.plan()
    if tamper in {"task", "claim", "attempt", "job", "observation", "source_state", "circuit"}:
        with case.db() as c:
            sql = {
                "task": "UPDATE remediation_tasks SET attempts=8",
                "claim": "UPDATE remediation_tasks SET claim='foreign'",
                "attempt": "UPDATE remediation_attempts SET status='changed' WHERE kind='detail'",
                "job": "UPDATE jobs SET description='changed'",
                "observation": "UPDATE remediation_observations SET proof='{}'",
                "source_state": "UPDATE remediation_sources SET listing_ids='[\"unv_uvp:101\"]'",
                "circuit": "INSERT INTO source_circuit_breakers VALUES('unv_uvp','open')",
            }[tamper]
            c.execute(sql)
    elif tamper == "host":
        write(
            case.policy.root
            / "hosts"
            / ("host-" + hashlib.sha256(b"app.unv.org").hexdigest()[:24] + ".json"),
            {"stopped": True, "eligible_at": 0},
        )
    elif tamper == "quota":
        p = case.policy.root / "attempts" / ("a" * 64 + ".json")
        write(
            p,
            {"kind": "detail", "source_id": "unv_uvp", "started_at": case.now, "attempt_id": "new"},
        )
    elif tamper == "maintenance":
        case.maintenance.write_text("foreign owner")
    elif tamper == "gate":
        write(case.output / repair.GATE_NAME, {"state": "exporting"})
    elif tamper == "capture":
        Path(plan["items"][0]["outcome"]["captures"][0]["path"]).write_text("{}")
    elif tamper == "frame":
        frame.write_text("{}")
    elif tamper == "seal":
        case.seal("different")
    elif tamper == "selection":
        plan["selections"][0]["action"] = "who_unavailable"
    else:
        plan["as_of"] -= 1000
    before = case.protected()
    with case.db() as c:
        task_before = dict(
            c.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
    with pytest.raises((ValueError, KeyError)):
        repair.apply(plan)
    assert case.protected() == before
    with case.db() as c:
        assert (
            dict(c.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone())
            == task_before
        )
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_incident_repairs'"
        ).fetchone()


@pytest.mark.parametrize(
    "mode",
    [
        "partial",
        "old",
        "present",
        "newer_reappearance",
        "wrong_source",
        "wrong_body",
        "wrong_attempt",
    ],
)
def test_absence_requires_current_reverified_complete_newer_census(case, mode):
    case.incident("unv_uvp", "101", legacy=True)
    frame = case.census(
        ["101"] if mode in {"present", "newer_reappearance"} else [],
        total=1 if mode == "partial" else None,
        old=mode == "old",
    )
    if mode == "newer_reappearance":
        case.selected[0]["census_frame"] = str(
            case.workspace / "captures" / "older" / "listing.json"
        )
    if mode == "wrong_source":
        data = json.loads(frame.read_text())
        data["source_id"] = "who_taleo"
        write(frame, data)
    if mode == "wrong_body":
        (frame.parent / "http/00001.body.gz").write_bytes(gzip.compress(b"{}"))
    if mode == "wrong_attempt":
        with case.db() as c:
            c.execute("UPDATE remediation_attempts SET status='blocked' WHERE attempt_id='census'")
    with pytest.raises(ValueError):
        case.plan()


def test_actual_owner_lock_and_rollback(case):
    case.incident("iom_oracle_hcm")
    case.incident("who_taleo", "2604311")
    plan = case.plan()
    before = case.protected()
    with case.lock.open("r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            repair.apply(plan)

    def fail_after_first(index, conn):
        if index == 0:
            raise RuntimeError("injected interrupted transaction")

    with pytest.raises(RuntimeError):
        repair.apply(plan, fault=fail_after_first)
    assert case.protected() == before
    with case.db() as c:
        assert all(x[0] == "blocked" for x in c.execute("SELECT status FROM remediation_tasks"))
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_incident_repairs'"
        ).fetchone()
    repair.apply(plan)
    with case.db() as c:
        c.execute("UPDATE remediation_tasks SET attempts=attempts+1")
    with pytest.raises(ValueError, match="Journal or later"):
        repair.apply(plan)


def test_recovery_lease_and_probe_history_floor_is_preserved(case):
    case.incident("iom_oracle_hcm")
    now = time.time()
    host = "fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com"
    path = (
        case.policy.root
        / "hosts"
        / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
    )
    state = {
        "stopped": False,
        "eligible_at": 0,
        "recovery": {
            "schema_version": 1,
            "phase": "half_open",
            "failure_kind": "transient_transport",
            "failures": 1,
            "eligible_at": now - 1,
            "probe_attempts": [now - 3, now - 2, now - 1],
            "probe_owner": "other",
            "probe_until": now + 900,
        },
    }
    write(path, state)
    plan = case.plan()
    assert plan["items"][0]["task_after"]["eligible_at"] >= now - 3 + 86400
    repair.apply(plan)
    assert json.loads(path.read_text()) == state


def test_explicit_selection_only_and_invalid_scope(case):
    selected = case.incident("who_taleo", "2604311")
    unselected = case.incident("who_taleo", "2604312")
    case.selected = case.selected[:1]
    plan = case.plan()
    repair.apply(plan)
    with case.db() as c:
        assert (
            c.execute(
                "SELECT status FROM remediation_tasks WHERE task_id=?", (selected,)
            ).fetchone()[0]
            == "unavailable_pending_inventory"
        )
        assert (
            c.execute(
                "SELECT status FROM remediation_tasks WHERE task_id=?", (unselected,)
            ).fetchone()[0]
            == "blocked"
        )
    with pytest.raises(ValueError):
        repair.normalize_selections([])
    with pytest.raises(ValueError):
        repair.normalize_selections(case.selected * 2)
    with pytest.raises(ValueError):
        repair.normalize_selections([{"task_id": "any", "action": "reset_all"}])


@pytest.mark.parametrize("marker_only", [True, False])
def test_transient_retry_retains_reviewed_deadline_hold(case, marker_only):
    from jobagg.deadline_review import record_hold

    key = case.incident("unv_uvp", "103", action="retry_captured_transient")
    case.census(["103"])
    review = {
        "authority": "user_requested_deadline_classification",
        "source_id": "unv_uvp",
        "external_id": "103",
        "review_id": "review-1",
        "reviewed_at": stamp(case.now - 100),
        "active": True,
        "deadline_utc": stamp(case.now - 50),
    }
    with case.db() as c:
        c.execute(
            "UPDATE jobs SET raw_json=?,closes_at=?",
            (dump({"_past_deadline_review": review}), stamp(case.now - 50)),
        )
        if not marker_only:
            current = c.execute("SELECT * FROM jobs WHERE external_id='103'").fetchone()
            record_hold(c, key, "unv_uvp", "103", current)
    before = case.protected()
    with pytest.raises(ValueError, match="deadline hold"):
        case.plan()
    assert case.protected() == before


def test_expired_date_without_review_does_not_invent_new_hold(case):
    case.incident("unv_uvp", "103", action="retry_captured_transient")
    case.census(["103"])
    with case.db() as c:
        c.execute("UPDATE jobs SET closes_at=?", (stamp(case.now - 50),))
    plan = case.plan()
    assert plan["items"][0]["task_after"]["status"] == "pending"
