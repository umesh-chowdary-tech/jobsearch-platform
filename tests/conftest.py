from __future__ import annotations

import pytest

from samples import make_root


@pytest.fixture(autouse=True)
def _no_profile_env(monkeypatch):
    """A developer's own settings must not leak in: a JOBSEARCH_PROFILES
    pointing at a real profile folder would put a real person under test."""
    for name in ("JOBSEARCH_PROFILES", "JOBSEARCH_VAR", "JOBSEARCH_PROFILE", "MAIL_PROFILE", "JOBSEARCH_CONTACT",
                 "JOBSEARCH_APP_NAME", "JOBSEARCH_OPERATOR", "JOBSEARCH_CITY", "JOBSEARCH_PRIVACY_EMAIL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fixed_window(monkeypatch):
    """The pool's 60-day window counts from today. Tests carry fixed dates, so
    "today" is fixed too - otherwise they would start failing two months on."""
    import datetime as dt
    from jobsearch import pool
    monkeypatch.setattr(pool, "_today", lambda: dt.date(2026, 10, 16))


@pytest.fixture(autouse=True)
def _no_live_check(monkeypatch):
    """Opening a posting asks its board whether it is still open. Not in tests:
    a test that wants it replaces pool._ask_board."""
    from jobsearch import pool
    monkeypatch.setattr(pool, "_ask_board", lambda fetcher, board, pid, company: None)
    pool._checked.clear()


@pytest.fixture
def prof(tmp_path):
    """The main sample profile in a fresh temporary root."""
    return make_root(tmp_path)
