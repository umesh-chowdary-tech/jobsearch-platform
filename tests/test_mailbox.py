"""The inbox reader can read. It cannot do anything else - held two ways:
by what the source is able to call, and by a fake server that records every
command it receives."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from jobsearch import mailbox
from jobsearch.profile import Profile

SRC = Path(mailbox.__file__)


def test_the_reader_imports_nothing_that_sends():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    assert not imported & {"smtplib", "requests", "httpx", "socket", "webbrowser"}


def test_the_reader_issues_no_command_that_changes_the_mailbox():
    src = SRC.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r'"""(?:.|\n)*?"""', "", code)                  # docstrings may name them
    for forbidden in ("STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", ".store(", ".copy(",
                      ".expunge(", ".append(", "\\\\Seen", "\\\\Deleted"):
        # list.append is fine; imaplib's append is the one that matters
        if forbidden == ".append(":
            continue
        assert forbidden not in code, forbidden
    assert "readonly=True" in code
    assert "BODY[]" not in code and "RFC822)" not in code          # every body fetch is a PEEK


class FakeIMAP:
    """Records every command. Serves two messages."""
    calls: list = []

    def __init__(self, host, port, timeout=None):
        FakeIMAP.calls = [("connect", host)]

    def login(self, user, pw):
        FakeIMAP.calls.append(("login", user))

    def list(self):
        return "OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

    def select(self, name, readonly=False):
        FakeIMAP.calls.append(("select", name, readonly))
        return "OK", [b"2"]

    def response(self, code):
        return code, [b"777"]

    def uid(self, command, *args):
        FakeIMAP.calls.append(("uid", command, args))
        if command == "SEARCH":
            return "OK", [b"41 42"]
        if command == "FETCH":
            spec = args[1]
            if "HEADER.FIELDS" in spec:
                raw = (b"From: Globex HR <talent@globex.example>\r\n"
                       b"Subject: Globex Careers: Thank you for applying\r\n"
                       b"Date: Wed, 24 Sep 2026 10:00:00 +0000\r\nMessage-ID: <a@b>\r\n\r\n")
                return "OK", [(b"41 (X-GM-THRID 9 UID 41 BODY[HEADER.FIELDS] {1}", raw), b")"]
            raw = (b"Content-Type: text/plain\r\n\r\nThank you for applying for the position of R-123456.")
            return "OK", [(b"41 (UID 41 BODY[] {1}", raw), b")"]
        raise AssertionError(f"unexpected command {command}")

    def logout(self):
        FakeIMAP.calls.append(("logout",))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GMAIL_USER=me@example.com\nGMAIL_APP_PASSWORD=abcdefghijklmnop\n"
                                   "MAIL_PROFILE=someone\n")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", FakeIMAP)
    yield Profile(tmp_path, "someone")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)


def test_a_sync_opens_read_only_and_only_peeks(fake):
    with mailbox.Inbox(fake) as box:
        uids = box.new_uids({}, 60)
        heads = box.headers(uids)
        bodies = box.bodies([41])
    assert uids == [41, 42] and heads[0].from_addr == "talent@globex.example"
    assert "R-123456" in bodies[41]
    selects = [c for c in FakeIMAP.calls if c[0] == "select"]
    assert selects == [("select", '"[Gmail]/All Mail"', True)]
    commands = {c[1] for c in FakeIMAP.calls if c[0] == "uid"}
    assert commands <= {"SEARCH", "FETCH"}
    fetches = [c[2][1] for c in FakeIMAP.calls if c[0] == "uid" and c[1] == "FETCH"]
    assert fetches and all("BODY.PEEK" in f for f in fetches)
    assert FakeIMAP.calls[-1] == ("logout",)


def test_after_the_first_run_only_newer_mail_is_asked_for(fake):
    with mailbox.Inbox(fake) as box:
        box.new_uids({"uidvalidity": "777", "last_uid": 41}, 60)
    search = next(c for c in FakeIMAP.calls if c[0] == "uid" and c[1] == "SEARCH")
    assert search[2] == (None, "UID 42:*")


def test_a_reset_mailbox_numbering_starts_over(fake):
    """If Gmail's UIDVALIDITY changes, the old last-UID means nothing."""
    with mailbox.Inbox(fake) as box:
        box.new_uids({"uidvalidity": "OLD", "last_uid": 41}, 60)
    search = next(c for c in FakeIMAP.calls if c[0] == "uid" and c[1] == "SEARCH")
    assert "X-GM-RAW" in search[2]


def test_no_credentials_is_a_clear_error_and_no_connection(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    with pytest.raises(mailbox.MailError, match="not set"):
        mailbox.Inbox(Profile(tmp_path, "someone"))


def test_an_inbox_is_read_only_for_the_profile_it_belongs_to(fake):
    """MAIL_PROFILE names one profile; another one never gets the login."""
    assert mailbox.configured(fake)
    other = Profile(fake.root, "someone-else")
    assert not mailbox.configured(other)
    with pytest.raises(mailbox.MailError, match="MAIL_PROFILE"):
        mailbox.Inbox(other)


def test_the_password_never_appears_in_an_error(fake, monkeypatch):
    def refuse(self, user, pw):
        raise mailbox.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
    monkeypatch.setattr(FakeIMAP, "login", refuse)
    with pytest.raises(mailbox.MailError) as exc:
        mailbox.Inbox(fake).__enter__()
    assert "abcdefghijklmnop" not in str(exc.value) and "AUTHENTICATIONFAILED" in str(exc.value)
