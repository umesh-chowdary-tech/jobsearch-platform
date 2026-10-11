"""The hosted connector: var/ mirrored to a database, and the tools a chat calls.

No network and no Postgres: `DictRemote` stands in for the database with the
same four operations."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from jobsearch import pipeline, rank, statestore
from jobsearch.connector import JUDGED_BY, Judgement, Tools
from samples import make_root


class DictRemote:
    """Strict like Postgres: every read and write must be inside lock()."""
    def __init__(self) -> None:
        self.rows: dict[str, tuple[bytes, str]] = {}
        self.inside = False

    @contextlib.contextmanager
    def lock(self):
        self.inside = True
        try:
            yield
        finally:
            self.inside = False

    def _check(self):
        if not self.inside:
            raise RuntimeError("used outside lock()")

    def hashes(self):
        self._check()
        return {p: h for p, (_, h) in self.rows.items()}

    def get(self, paths):
        self._check()
        return {p: self.rows[p][0] for p in paths if p in self.rows}

    def put(self, rows):
        self._check()
        for p, data, h in rows:
            self.rows[p] = (data, h)

    @contextlib.contextmanager
    def cycle_lock(self):
        yield True


# ── statestore ───────────────────────────────────────────────────────────────

def _files(var: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (var / rel).parent.mkdir(parents=True, exist_ok=True)
        (var / rel).write_text(text, encoding="utf-8")


def test_push_then_pull_restores_a_wiped_disk(tmp_path):
    remote = DictRemote()
    pc = tmp_path / "pc" / "var"
    _files(pc, {"pipeline.json": "{}", "seen/greenhouse__x.json": "[1]",
                "discovery/run.json": "big", "dashboard.html": "<html>"})
    rep = statestore.push(pc, remote)
    # History and generated files stay home.
    assert sorted(rep.pushed) == ["pipeline.json", "seen/greenhouse__x.json"]

    server = tmp_path / "server" / "var"
    rep = statestore.pull(server, remote)
    assert sorted(rep.pulled) == ["pipeline.json", "seen/greenhouse__x.json"]
    assert (server / "seen" / "greenhouse__x.json").read_text() == "[1]"


def test_history_and_generated_files_stay_home_at_any_depth(tmp_path):
    """Discovery runs live under var/_shared/, dashboards under each profile."""
    remote, var = DictRemote(), tmp_path / "var"
    _files(var, {"_shared/discovery/run.json": "big", "_shared/boards.json": "{}",
                 "sample-backend/dashboard.html": "<html>", "sample-backend/pipeline.json": "{}"})
    assert sorted(statestore.push(var, remote).pushed) == ["_shared/boards.json", "sample-backend/pipeline.json"]


def test_only_changed_files_are_sent(tmp_path):
    remote, var = DictRemote(), tmp_path / "var"
    _files(var, {"a.json": "1", "b.json": "2"})
    statestore.push(var, remote)
    (var / "b.json").write_text("3")
    assert statestore.push(var, remote).pushed == ["b.json"]
    assert statestore.push(var, remote).pushed == []


def test_a_change_made_elsewhere_is_pulled_before_pushing(tmp_path):
    remote = DictRemote()
    pc, server = tmp_path / "pc", tmp_path / "server"
    _files(pc, {"pipeline.json": "v1"})
    statestore.push(pc, remote)
    statestore.pull(server, remote)
    (server / "pipeline.json").write_text("v2-server")
    statestore.push(server, remote)

    rep = statestore.push(pc, remote)          # pc never touched it: takes v2, sends nothing
    assert rep.pulled == ["pipeline.json"] and rep.pushed == []
    assert (pc / "pipeline.json").read_text() == "v2-server"


def test_both_sides_changed_keeps_the_local_copy_aside(tmp_path):
    remote = DictRemote()
    pc, server = tmp_path / "pc", tmp_path / "server"
    _files(pc, {"pipeline.json": "v1"})
    statestore.push(pc, remote)
    statestore.pull(server, remote)
    (server / "pipeline.json").write_text("server")
    statestore.push(server, remote)
    (pc / "pipeline.json").write_text("pc")

    rep = statestore.pull(pc, remote)
    assert rep.conflicts == ["pipeline.json"]
    assert (pc / "pipeline.json").read_text() == "server"
    kept = list((pc / statestore.CONFLICTS).glob("pipeline.json.*"))
    assert [k.read_text() for k in kept] == ["pc"]


def test_a_file_missing_locally_is_not_deleted_remotely(tmp_path):
    remote, var = DictRemote(), tmp_path / "var"
    _files(var, {"a.json": "1"})
    statestore.push(var, remote)
    (var / "a.json").unlink()
    statestore.push(var, remote)
    assert "a.json" in remote.rows


def test_status_reports_without_syncing(tmp_path):
    remote, var = DictRemote(), tmp_path / "var"
    _files(var, {"a.json": "1"})
    assert statestore.status(var, remote) == {"to_pull": [], "to_push": ["a.json"]}
    assert remote.rows == {}


# ── tools ────────────────────────────────────────────────────────────────────

def _posting(pid: str, **kw):
    p = {"id": pid, "title": f"Backend Engineer {pid}", "company": "acme", "board": "greenhouse:acme",
         "verdict": "keep", "url": f"https://example.invalid/{pid}", "location": "Bangalore",
         "matched": {"Python": "core"}, "coverage": "1/2", "score": 10, "description": "Build APIs."}
    p.update(kw)
    return p


@pytest.fixture
def root(tmp_path):
    prof = make_root(tmp_path)
    postings = {
        "1": _posting("1", ai={"fit": "strong", "why": "fits", "by": "x via groq",
                               "prompt": rank.PROMPT_VERSION}),
        "2": _posting("2"),
        "3": _posting("3", verdict="drop"),
        "4": _posting("4", closed_at="2026-10-01T00:00:00+00:00"),
        "5": _posting("5", title="Staff Engineer", years_required=15),
    }
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": postings}), encoding="utf-8")
    return prof


def test_find_jobs_lists_only_open_kept_undecided(root):
    t = Tools(root)
    ids = [j["id"] for j in t.find_jobs()["jobs"]]
    assert ids[0] == "1"                       # judged strong sorts first
    assert set(ids) == {"1", "2", "5"}         # dropped and closed are not offered
    assert {j["id"]: j["fit"] for j in t.find_jobs()["jobs"]}["2"] == "unranked"


def test_decisions_take_postings_off_the_list(root):
    t = Tools(root)
    t.set_status("1", "applied", "via referral")
    t.save_for_later("2", 7)
    assert [j["id"] for j in t.find_jobs()["jobs"]] == ["5"]
    assert [j["id"] for j in t.find_jobs(status="applied")["jobs"]] == ["1"]
    assert pipeline.load(root)["1"]["history"][-1]["note"] == "via referral"


def test_find_jobs_filters_by_fit_and_search(root):
    t = Tools(root)
    assert [j["id"] for j in t.find_jobs(fit=["unranked"])["jobs"]] == ["2", "5"] or \
        {j["id"] for j in t.find_jobs(fit=["unranked"])["jobs"]} == {"2", "5"}
    assert [j["id"] for j in t.find_jobs(search="staff")["jobs"]] == ["5"]


def test_rank_round_trip_stores_claude_as_the_judge(root):
    t = Tools(root)
    todo = t.jobs_to_rank()
    # Groq's "strong" is read again by the more trusted judge, and first -
    # those are the ones acted on (rank.unread).
    assert [p["id"] for p in todo["postings"]][0] == "1"
    assert {p["id"] for p in todo["postings"]} == {"1", "2", "5"}
    assert "never" in todo["rules"].lower() or "must not" in todo["rules"]
    assert todo["profile"]

    res = t.save_rankings([Judgement(id="2", fit="strong", why="Builds APIs as at Acme"),
                           Judgement(id="5", fit="strong", why="Same platform work"),
                           Judgement(id="nope", fit="weak", why="x")])
    assert res["saved"] == 2 and res["unknown_ids"] == ["nope"]
    t.save_rankings([Judgement(id="1", fit="possible", why="APIs, but no Go")])
    # The years cap is enforced here, not trusted to the chat.
    assert res["capped_to_weak_for_years"] == ["5"]

    blob = json.loads((root.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]
    assert blob["2"]["ai"]["by"] == JUDGED_BY and rank.judged_by(blob["2"]["ai"]) == "claude"
    assert blob["5"]["ai"]["fit"] == "weak" and blob["5"]["ai"]["why"].startswith("Asks for 15+")
    assert t.jobs_to_rank()["postings"] == []


def test_get_job_accepts_a_prefix_and_trims_text(root):
    t = Tools(root)
    blob = json.loads((root.var / "triage-results.json").read_text(encoding="utf-8"))
    blob["postings"]["2"]["description"] = "x" * 10000
    (root.var / "triage-results.json").write_text(json.dumps(blob), encoding="utf-8")
    job = t.get_job("2")
    assert job["id"] == "2" and len(job["description"]) < 6100


def test_writes_reach_the_remote_and_a_fresh_server_sees_them(root, tmp_path):
    remote = DictRemote()
    statestore.push(root.root / "var", remote)
    Tools(root, remote).set_status("1", "skipped")

    fresh = make_root(tmp_path / "fresh")
    t2 = Tools(fresh, remote)                  # empty disk, as after Render sleeps
    assert [j["id"] for j in t2.find_jobs(status="skipped")["jobs"]] == ["1"]


# ── the gate ─────────────────────────────────────────────────────────────────

def test_only_the_secret_path_reaches_the_server(root):
    pytest.importorskip("mcp", reason="the connector's packages: pip install -e .[server]")
    from starlette.testclient import TestClient
    from jobsearch.connector import build_app

    secret = "s" * 32
    app = build_app(root, secret, None)
    with TestClient(app) as c:
        assert c.get("/healthz").text == "ok"
        assert c.post("/mcp", json={}).status_code == 404
        assert c.post("/" + "t" * 32 + "/mcp", json={}).status_code == 404
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}}}
        hdr = {"Accept": "application/json, text/event-stream"}
        r = c.post(f"/{secret}/mcp", json=init, headers=hdr)
        assert r.status_code == 200, r.text
        r = c.post(f"/{secret}/mcp", headers=hdr,
                   json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert {"find_jobs", "jobs_to_rank", "save_rankings", "set_status", "refresh_jobs"} <= names
        r = c.post(f"/{secret}/mcp", headers=hdr,
                   json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "get_job", "arguments": {"posting_id": "zzz"}}})
        res = r.json()["result"]
        assert res["isError"] and "no triaged posting" in res["content"][0]["text"]


def test_neons_pooled_url_is_refused():
    with pytest.raises(ValueError, match="pooled"):
        statestore.Postgres("postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/db")


def test_a_short_secret_is_refused(root):
    from jobsearch.connector import build_app
    with pytest.raises(SystemExit):
        build_app(root, "short", None)


def test_refresh_runs_the_cycle_in_the_background_and_saves_it(root, monkeypatch):
    import jobsearch.cycle as C
    remote = DictRemote()

    def fake_cycle(r, quiet=False, profiles=None):
        assert [p.id for p in profiles] == [root.id]       # this person's cycle, not everyone's
        seen = Path(r) / "var" / "_shared" / "seen"
        seen.mkdir(parents=True, exist_ok=True)
        (seen / "greenhouse__acme.json").write_text("[]")
        return 0
    monkeypatch.setattr(C, "run_cycle", fake_cycle)
    t = Tools(root, remote)
    assert t.refresh_jobs()["started"] is True
    for th in __import__("threading").enumerate():
        if th.name == "refresh":
            th.join(timeout=10)
    st = t.refresh_status()["refresh"]
    assert st["running"] is False and st["ok"] is True
    assert "_shared/seen/greenhouse__acme.json" in remote.rows


def test_refresh_steps_aside_when_another_machine_is_cycling(root, monkeypatch):
    import jobsearch.cycle as C
    remote = DictRemote()

    @contextlib.contextmanager
    def taken():
        yield False
    remote.cycle_lock = taken
    monkeypatch.setattr(C, "run_cycle", lambda *a, **k: pytest.fail("must not poll twice"))
    t = Tools(root, remote)
    t.refresh_jobs()
    for th in __import__("threading").enumerate():
        if th.name == "refresh":
            th.join(timeout=10)
    assert "PC" in t.refresh_status()["refresh"]["error"]
