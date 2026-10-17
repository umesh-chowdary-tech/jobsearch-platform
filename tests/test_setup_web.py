"""Setup through the local app: the upload is guarded like every other write,
and the whole flow - upload, draft, confirm, save - works over HTTP."""
from __future__ import annotations

import http.client
import json
import shutil
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from jobsearch import onboard
from jobsearch.profile import Profile
from jobsearch.web import server as srv
from samples import ROOT
from test_onboard import ANSWER, RESUME, FakeLLM


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    monkeypatch.setattr(onboard, "LLM", lambda prof, **kw: FakeLLM(ANSWER))
    port = _free_port()
    app = srv.App(Profile(tmp_path, "neha"), port)          # no profile yet: setup mode
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app
    httpd.shutdown()
    httpd.server_close()


def call(app, method, path, body=None, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": f"127.0.0.1:{app.port}", "Origin": f"http://127.0.0.1:{app.port}", **(headers or {})}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, json.loads(r.read() or b"{}")
    c.close()
    return out


def upload(app, data=None, ctype="text/plain", origin=None):
    data = RESUME.encode() if data is None else data
    h = {"Content-Type": ctype}
    if origin is not None:
        h["Origin"] = origin
    return call(app, "POST", "/api/setup/upload", headers=h, raw=data)


def test_an_upload_from_another_site_is_refused(live):
    assert upload(live, origin="https://attacker.test")[0] == 403
    assert onboard.load_draft(live.prof) == {}


def _headers_only(app, origin, length):
    """An upload's request line and headers, its body not sent yet."""
    s = socket.create_connection(("127.0.0.1", app.port), timeout=10)
    s.sendall((f"POST /api/setup/upload HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nOrigin: {origin}\r\n"
               f"Content-Type: text/plain\r\nContent-Length: {length}\r\n\r\n").encode())
    return s


def _read_to_end(s) -> bytes:
    out = b""
    while chunk := s.recv(65536):
        out += chunk
    return out


def test_a_refusal_reads_a_small_body_first_so_the_reply_is_not_reset(live):
    # Closing a socket with unread bytes resets the connection, and the reset
    # can beat the reply to the client (seen on Windows CI). So the server
    # reads the refused body before replying, then closes cleanly.
    body = RESUME.encode()
    with _headers_only(live, "https://attacker.test", len(body)) as s:
        s.settimeout(0.3)
        with pytest.raises(TimeoutError):
            s.recv(1)                       # no reply while the body is unread
        s.settimeout(10)
        s.sendall(body)
        reply = _read_to_end(s)             # a clean end, not a reset
    assert reply.split()[1] == b"403" and b"\r\nConnection: close\r\n" in reply
    assert onboard.load_draft(live.prof) == {}


def test_a_refused_body_over_the_drain_limit_is_not_waited_for(live):
    with _headers_only(live, "https://attacker.test", srv.DRAIN_LIMIT + 1) as s:
        s.settimeout(2)                     # well under DRAIN_SECONDS
        reply = _read_to_end(s)
    assert reply.split()[1] == b"403" and b"\r\nConnection: close\r\n" in reply


def test_an_upload_with_no_origin_is_refused(live):
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.request("POST", "/api/setup/upload", body=RESUME.encode(),
              headers={"Host": f"127.0.0.1:{live.port}", "Content-Type": "text/plain"})
    assert c.getresponse().status == 403


@pytest.mark.parametrize("ctype, status", [("image/png", 415), ("application/x-www-form-urlencoded", 415),
                                           ("multipart/form-data; boundary=x", 415)])
def test_only_resume_file_types_are_taken(live, ctype, status):
    assert upload(live, ctype=ctype)[0] == status


def test_a_huge_upload_is_refused_before_it_is_read(live):
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.putrequest("POST", "/api/setup/upload", skip_host=True)
    for k, v in {"Host": f"127.0.0.1:{live.port}", "Origin": f"http://127.0.0.1:{live.port}",
                 "Content-Type": "application/pdf", "Content-Length": str(onboard.MAX_BYTES + 1)}.items():
        c.putheader(k, v)
    c.endheaders()
    assert c.getresponse().status == 413


def test_the_whole_flow_over_http(live):
    status, s = call(live, "GET", "/api/setup")
    assert status == 200 and s["exists"] is False and s["status"] == "new"

    status, s = upload(live)
    assert status == 200 and s["status"] == "uploaded"
    assert "neha.example@example.com" not in s["will_send"] and "[email]" in s["will_send"]

    status, s = call(live, "POST", "/api/setup/draft", {})
    assert status == 200 and s["status"] == "drafted" and s["dropped"]

    for it in s["items"]:
        assert call(live, "POST", "/api/setup/item", {"id": it["id"], "state": "confirmed"})[0] == 200
    status, s = call(live, "POST", "/api/setup/fact", {"entry": "e1", "text": "On call for payments"})
    assert any(i["text"] == "On call for payments" for i in s["items"] if i["kind"] == "fact")
    status, s = call(live, "POST", "/api/setup/skills", {"tiers": {"Go": "core"}})
    assert next(k for k in s["skills"] if k["name"] == "Go")["tier"] == "core"

    status, s = call(live, "POST", "/api/setup/save", {"prefs": s["prefs"]})
    assert status == 200 and s["exists"] and s["result"]["entries"] >= 2
    assert live.prof.exists()
    # Saving again needs the explicit replace.
    assert call(live, "POST", "/api/setup/save", {"prefs": s["prefs"]})[0] == 400
    assert call(live, "POST", "/api/setup/save", {"prefs": s["prefs"], "replace": True})[0] == 200


def test_a_bad_request_says_why(live):
    upload(live)
    call(live, "POST", "/api/setup/draft", {})
    status, body = call(live, "POST", "/api/setup/item", {"id": "nope", "state": "confirmed"})
    assert status == 400 and "no item" in body["error"]
    status, body = call(live, "POST", "/api/setup/item", {"id": "e1-f1", "state": "approved"})
    assert status == 400 and "state must be one of" in body["error"]
    assert call(live, "POST", "/api/setup/restore", {"index": True})[0] == 400
    assert call(live, "POST", "/api/setup/nonsense", {})[0] == 404


def test_a_cross_site_setup_change_is_refused(live):
    upload(live)
    status, _ = call(live, "POST", "/api/setup/draft", {}, headers={"Origin": "https://attacker.test"})
    assert status == 403
    assert onboard.load_draft(live.prof)["status"] == "uploaded"
