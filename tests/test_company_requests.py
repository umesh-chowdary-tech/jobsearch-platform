"""Companies people ask to be watched (company_requests.py), and the admin
page's side of it: discovery looks for the board, a found one is watched."""
from __future__ import annotations

import json

import pytest

from jobsearch import company_requests as cr
from jobsearch.discovery import DiscoveryRun
from jobsearch.models import NOT_FOUND, OK, BoardProbe, Escalation
from jobsearch.web import routes
from jobsearch.web.routes import HOSTED
from jobsearch.web.runner import Runner
from samples import make_root


@pytest.fixture
def prof(tmp_path):
    return make_root(tmp_path)


def _other(prof, pid="sample-android"):
    from samples import make_root as mk
    return mk(prof.root, pid)


def found(company="Globex", slug="globex", doubt=False):
    p = BoardProbe(company=company, provider="greenhouse", slug=slug, status=OK, url="https://example.com")
    esc = [Escalation(kind="identity", company=company, provider="greenhouse", slug=slug,
                      reason="the board calls itself Globex Holdings")] if doubt else []
    return DiscoveryRun(probes=[p], escalations=esc)


def test_asking_twice_or_together_is_one_request(prof):
    a = cr.ask(prof, "Globex Pvt Ltd", "https://globex.example/careers")
    assert a["status"] == "asked" and a["id"] == "globex"
    cr.ask(prof, "globex")                                     # the same person again: nothing new
    cr.ask(_other(prof), "GLOBEX")                             # someone else: one more asking
    req = cr.load(prof.shared)["globex"]
    assert req["asked"] == 2 and req["url"] == "https://globex.example/careers"


def test_the_shared_list_names_no_one(prof):
    cr.ask(prof, "Globex")
    text = (prof.shared / cr.FILE).read_text(encoding="utf-8")
    assert prof.id not in text
    assert json.loads((prof.var / cr.MINE).read_text()) == ["globex"]


def test_a_company_already_watched_says_so(prof):
    (prof.shared / "boards.json").write_text(json.dumps(
        {"lever:initech": {"provider": "lever", "slug": "initech", "company": "Initech"}}))
    r = cr.ask(prof, "Initech Technologies")
    assert r["status"] == "watching" and r["board"] == "lever:initech"


@pytest.mark.parametrize("company, url, match", [("", "", "name the company"),
                                                 ("Globex", "javascript:alert(1)", "web address"),
                                                 ("Globex", "https://x.example/" + "a" * 300, "web address")])
def test_nonsense_is_refused(prof, company, url, match):
    with pytest.raises(cr.RequestError, match=match):
        cr.ask(prof, company, url)


def test_a_person_has_a_few_waiting_at_once(prof):
    for n in range(cr.MAX_OPEN):
        cr.ask(prof, f"Company {n}")
    with pytest.raises(cr.RequestError, match="waiting already"):
        cr.ask(prof, "One More")


def test_withdrawing_the_last_ask_removes_a_waiting_request(prof):
    cr.ask(prof, "Globex")
    cr.withdraw(prof, "globex")
    assert cr.load(prof.shared) == {} and cr.mine(prof) == []
    with pytest.raises(cr.RequestError):
        cr.withdraw(prof, "globex")


def test_what_discovery_found_is_recorded(prof):
    for name in ("Globex", "Initech", "Hooli"):
        cr.ask(prof, name)
    cr.record_search(prof.shared, "globex", found(doubt=True))
    cr.record_search(prof.shared, "initech", DiscoveryRun(probes=[BoardProbe(
        company="Initech", provider="lever", slug="initech", status=NOT_FOUND, url="")]))
    cr.record_search(prof.shared, "hooli", DiscoveryRun(probes=[BoardProbe(
        company="Hooli", provider="lever", slug="hooli", status="error", url="")]))
    mine = {r["id"]: r for r in cr.mine(prof)}
    assert mine["globex"]["status"] == "watching" and mine["globex"]["board"] == "greenhouse:globex"
    assert "check it's theirs" in mine["globex"]["note"]
    assert mine["initech"]["status"] == "not_found"
    assert mine["hooli"]["status"] == "asked" and "try again" in mine["hooli"]["note"]   # an error isn't absence


def test_stop_watching_takes_the_board_out(prof):
    cr.ask(prof, "Globex")
    (prof.shared / "boards.json").write_text(json.dumps({"greenhouse:globex": {"company": "Globex"}}))
    cr.record_search(prof.shared, "globex", found())
    cr.stop_watching(prof.shared, "globex", "a different Globex")
    assert json.loads((prof.shared / "boards.json").read_text()) == {}
    assert cr.mine(prof)[0]["status"] == "declined" and cr.mine(prof)[0]["note"] == "a different Globex"


def test_a_person_asks_on_the_hosted_service(prof):
    run = Runner(prof)
    _, r = routes.post(prof, "/api/companies/ask", {"company": "Globex"}, run, scope=HOSTED)
    assert routes.get(prof, "/api/companies", {}, run, scope=HOSTED)[0]["company"] == "Globex"
    routes.post(prof, f"/api/companies/{r['id']}/withdraw", {}, run, scope=HOSTED)
    assert routes.get(prof, "/api/companies", {}, run, scope=HOSTED) == []


def test_the_admin_finds_boards_for_whats_waiting(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("jwt")
    from jobsearch import discovery
    from jobsearch.profile import shared_var
    from test_admin import BOSS, do
    from test_hosted import joined, make_env
    client, root = make_env(tmp_path, monkeypatch)
    alice = joined(client, root)
    cr.ask(alice, "Globex")
    asked = []

    def fake_search(fetcher, company, **kw):
        asked.append(company)
        return found(company)
    monkeypatch.setattr(discovery, "search_company", fake_search)
    monkeypatch.setattr("jobsearch.http.default_fetcher", lambda var: None)
    assert do(client, action="company_search").json()["started"] is True
    client.app.state.service.cycle.join(5)
    assert asked == ["Globex"]
    assert "greenhouse:globex" in json.loads((shared_var(root) / "boards.json").read_text())
    o = client.get("/api/admin/overview", headers=BOSS).json()
    assert o["requests"][0]["status"] == "watching"
    assert do(client, action="company_stop", id="globex", note="wrong one").status_code == 200
    assert cr.mine(alice)[0]["status"] == "declined"
