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


@pytest.fixture
def prof(tmp_path):
    """The main sample profile in a fresh temporary root."""
    return make_root(tmp_path)
