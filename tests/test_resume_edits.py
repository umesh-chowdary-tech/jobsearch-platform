"""Edit mode on the overall resume: hide, always include, order, reword, move.
The profile is fictional."""
from __future__ import annotations

import pytest

from jobsearch import latex
from jobsearch import resume_edits as RE
from jobsearch.assemble import load_store, select_entries
from jobsearch.base_resume import build_base, own_skills
from jobsearch.render import render_text
from jobsearch.resume_layout import group_work
from jobsearch.skills import load_index
from samples import make_root


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setattr(latex, "find_engine", lambda: None)      # the text is enough here
    return make_root(tmp_path)


def _ids(entries):
    return [e["id"] for e in entries]


def _build(prof):
    return build_base(prof, use_model=False, say=lambda _: None)


def _page(prof):
    return (prof.var / "resumes" / "base.txt").read_text(encoding="utf-8")


# ── hiding and keeping in ────────────────────────────────────────────────────

def test_hiding_a_project_takes_its_pieces_off_the_page_and_unhide_brings_them_back(prof):
    store = load_store(prof)
    RE.set_flag(prof, store, "acme-agent-platform", "hidden", True)
    rec = _build(prof)
    assert "acme-agent-platform" not in rec["emits"] and "acme-support-agent" not in rec["emits"]
    assert [e["id"] for e in rec["outline"]["hidden"]] == ["acme-agent-platform"]
    assert load_store(prof)["entries"] == store["entries"]           # nothing deleted from the profile
    RE.set_flag(prof, store, "acme-agent-platform", "hidden", False)
    assert "acme-agent-platform" in _build(prof)["emits"]


def test_always_include_beats_the_match_and_unhides(prof):
    store, index = load_store(prof), load_index(prof)
    kw = own_skills(index)
    one = dict(per_job=1, extras=False)                     # room for one project: the newest
    assert "acme-track" not in _ids(select_entries(store, index, kw, **one))
    RE.set_flag(prof, store, "acme-track", "hidden", True)
    d = RE.set_flag(prof, store, "acme-track", "pinned", True)
    assert "acme-track" not in d["hidden"]
    assert "acme-track" in _ids(select_entries(RE.apply(store, d), index, kw, **one))


def test_an_always_included_piece_survives_a_low_pieces_setting(prof):
    store, index = load_store(prof), load_index(prof)
    kw = own_skills(index)
    d = RE.set_flag(prof, store, "acme-synthetic-checks", "pinned", True)
    chosen = _ids(select_entries(RE.apply(store, d), index, kw, pieces=1))
    assert "acme-synthetic-checks" in chosen and "acme-support-agent" not in chosen


# ── order ────────────────────────────────────────────────────────────────────

def test_the_persons_order_wins_over_newest_first(prof):
    store, index = load_store(prof), load_index(prof)
    entries = select_entries(store, index, own_skills(index))
    assert [g["entry"]["id"] for g in group_work(entries)[0]["projects"]][:2] == ["acme-agent-platform", "acme-track"]
    d = RE.set_order(prof, store, ["acme-track", "acme-agent-platform"])
    entries = select_entries(RE.apply(store, d), index, own_skills(index))
    assert [g["entry"]["id"] for g in group_work(entries)[0]["projects"]][:2] == ["acme-track", "acme-agent-platform"]


# ── wording ──────────────────────────────────────────────────────────────────

def _a_fact(store, eid):
    return next(e for e in store["entries"] if e["id"] == eid)["facts"][0]


def test_a_reword_changes_the_page_not_the_profile(prof):
    store = load_store(prof)
    old = _a_fact(store, "acme-track")
    RE.reword(prof, store, "acme-track", old, "Owned the tracking API end to end")
    _build(prof)
    assert "Owned the tracking API end to end" in _page(prof) and old not in _page(prof)
    assert _a_fact(load_store(prof), "acme-track") == old
    RE.reword(prof, store, "acme-track", old, "")                    # put back
    _build(prof)
    assert old in _page(prof)


def test_a_new_number_asks_whether_it_was_measured_and_an_estimate_is_refused(prof):
    store = load_store(prof)
    old = _a_fact(store, "acme-track")
    longer = old + ", cutting lookups by 40%"
    with pytest.raises(RE.EditError, match="measured, or is it your estimate"):
        RE.reword(prof, store, "acme-track", old, longer)
    with pytest.raises(RE.EditError, match="only measured results"):
        RE.reword(prof, store, "acme-track", old, longer, measured=False)
    assert RE.load(prof)["wording"] == {}
    RE.reword(prof, store, "acme-track", old, longer, measured=True)
    assert RE.load(prof)["wording"]["acme-track"][old] == longer


def test_a_reword_of_a_fact_that_changed_on_the_profile_is_refused(prof):
    with pytest.raises(RE.EditError, match="changed on your Profile"):
        RE.reword(prof, load_store(prof), "acme-track", "a sentence no fact says", "anything")


def test_the_outline_marks_a_reworded_bullet_with_its_original(prof):
    store = load_store(prof)
    old = _a_fact(store, "acme-track")
    RE.reword(prof, store, "acme-track", old, "Owned the tracking API end to end")
    rec = _build(prof)
    track = next(p for p in rec["outline"]["jobs"][0]["projects"] if p["id"] == "acme-track")
    b = next(b for b in track["bullets"] if b["reworded"])
    assert b == {"text": "Owned the tracking API end to end", "original": old, "reworded": True}


# ── the API ──────────────────────────────────────────────────────────────────

def test_the_hosted_app_takes_edits_and_refuses_what_it_cannot_do(prof):
    from jobsearch.web import routes
    from jobsearch.web.runner import Runner

    def post(body):
        return routes.post(prof, "/api/resumes/edit", body, Runner(prof), scope=routes.HOSTED)

    status, out = post({"op": "hide", "id": "acme-track"})
    assert status == 200 and out["hidden"] == ["acme-track"]
    with pytest.raises(routes.ApiError, match="no such piece of work"):
        post({"op": "pin", "id": "nope"})
    with pytest.raises(routes.ApiError, match="op is"):
        post({"op": "delete", "id": "acme-track"})
    view = routes.get(prof, "/api/resumes", {}, Runner(prof), scope=routes.HOSTED)
    assert view["edits"]["hidden"] == ["acme-track"]


def test_text_render_of_an_unedited_store_is_unchanged(prof):
    """apply() with no edits is the store itself, as far as the page can tell."""
    store, index = load_store(prof), load_index(prof)
    kw = own_skills(index)
    a = select_entries(store, index, kw)
    b = select_entries(RE.apply(store, RE.load(prof)), index, kw)
    assert _ids(a) == _ids(b)
    from jobsearch.assemble import Variant
    v = Variant(id="t", angle="t")
    assert render_text(store["identity"], v, a, [], index) == render_text(store["identity"], v, b, [], index)


# ── moving a piece: the same "Part of" as the Profile page ───────────────────

from test_onboard import _confirm_all, _drafted, new  # noqa: E402,F401  (a profile made by Setup)


def test_moving_a_piece_on_the_resume_changes_part_of_on_the_profile(new):  # noqa: F811 (the fixture)
    from jobsearch import onboard as O
    from jobsearch.web import routes
    from jobsearch.web.runner import Runner
    _drafted(new)
    _confirm_all(new)
    piece = O.add_item(new, "entry", {"type": "work", "title": "Refund retries", "where": "Globex Payments",
                                      "period": "2023 to present", "headline": "Added retries to refunds"})
    O.update_item(new, piece["id"], "confirmed")
    O.save(new, O.state(new)["prefs"])
    out = routes.post(new, "/api/resumes/edit", {"op": "move", "id": piece["id"], "part_of": "e1"}, Runner(new),
                      scope=routes.HOSTED)
    assert out[0] == 200
    assert {e["id"]: e for e in load_store(new)["entries"]}[piece["id"]]["part_of"] == "e1"
    with pytest.raises(O.SetupError, match="same job"):          # a 400 at the server (routes.error)
        routes.post(new, "/api/resumes/edit", {"op": "move", "id": "e2", "part_of": "e1"}, Runner(new),
                    scope=routes.HOSTED)
