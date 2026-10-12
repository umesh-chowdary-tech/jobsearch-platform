"""The database side of statestore: what it sets up on the table that holds
everyone's var/ (no Postgres needed - the driver is a stand-in)."""
from __future__ import annotations

import sys
import types

from jobsearch import statestore


def test_the_table_is_closed_to_everyone_but_its_owner(monkeypatch):
    """On Supabase the public key acts as "anon" and signed-in users as
    "authenticated"; neither may read var_files, whatever the project's API
    settings say."""
    ran: list[str] = []

    class Conn:
        def execute(self, sql, params=None):
            ran.append(" ".join(str(sql).split()))
            return self

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: Conn()))
    pg = statestore.Postgres("postgresql://u:p@aws-0-ap-south-1.pooler.supabase.com:5432/postgres")
    pg._connect()
    assert ran[0].startswith("CREATE TABLE IF NOT EXISTS var_files")
    assert ran[1] == "ALTER TABLE var_files ENABLE ROW LEVEL SECURITY"
    assert "REVOKE ALL ON var_files FROM anon" in ran[2] and "REVOKE ALL ON var_files FROM authenticated" in ran[2]
    assert not any("CREATE POLICY" in s for s in ran)            # no policy: nobody but the owner
    pg._connect()
    assert len(ran) == 3                                          # set up once per process
