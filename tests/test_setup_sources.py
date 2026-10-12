"""What setup reads, and what it never keeps or sends. Every person and
address here is made up."""
from __future__ import annotations

import io
import zipfile

import pytest

from jobsearch import setup_sources as S

INDIAN = """Priya Sample
Pune, India | priya.sample@example.com | +91 00000 22222 | DOB: 01/01/1998 | Gender: Female
PAN: ABCDE1234F  Aadhaar: 2345 6789 0123
EXPERIENCE
Software Engineer, Acme Freight, Pune    2021 - Present
Built the billing API in Python
PERSONAL DETAILS
Date of Birth : 01/01/1998
Father's Name : Ramesh Sample
Marital Status : Single
Languages Known : English, Hindi, Marathi
Permanent Address : 12 Example Road, Pune 411001
REFERENCES
Mr. A Kumar, Manager, Acme Freight, +91 00000 33333
DECLARATION
I hereby declare that the above information is true to the best of my knowledge.
Place: Pune
"""


def test_personal_details_are_never_kept():
    kept, removed = S.strip_private(S.lines_of(INDIAN))
    text = "\n".join(kept)
    for secret in ("01/01/1998", "Female", "ABCDE1234F", "2345 6789 0123", "Ramesh", "Single",
                   "12 Example Road", "A Kumar", "00000 33333", "hereby declare"):
        assert secret not in text, secret
    # What the work needs stays: the job, the work, the languages, contacts for the pattern finder.
    for kept_line in ("Software Engineer, Acme Freight", "Built the billing API in Python",
                      "Languages Known : English, Hindi, Marathi", "priya.sample@example.com"):
        assert kept_line in text, kept_line
    assert removed["date of birth"] == 2 and removed["a parent's name"] == 1 and removed["a reference"] == 1
    assert removed["PAN"] == 1 and removed["Aadhaar"] == 1 and removed["home address"] == 1


def test_a_long_sentence_ends_a_personal_block_under_an_unknown_heading():
    lines = ["Personal Details", "Date of Birth: 01/01/1990", "Mentoring",
             "Mentored four junior engineers through their first production launches over two years at the company"]
    kept, _ = S.strip_private(lines)
    assert kept == [lines[-1]]


@pytest.mark.parametrize("line, section, listed", [
    ("Go, PostgreSQL, React, Docker", "skills", True),
    ("Skills: Python, Go", None, True),
    ("Python | Go | Kafka | Redis | AWS", None, True),
    ("Built the refund service in Go and PostgreSQL", "experience", False),
    ("Built the refund service in Go and PostgreSQL", None, False),
])
def test_a_skills_list_is_told_apart_from_work_that_used_a_skill(line, section, listed):
    assert S.listed_only(line, section) is listed


def test_sections_follow_the_headings():
    lines = ["Neha", "EXPERIENCE", "Built things", "Technical Skills:", "Go, Python", "Education", "B.Tech"]
    assert S.sections(lines) == [None, "experience", "experience", "skills", "skills", "education", "education"]


# ── Word files ───────────────────────────────────────────────────────────────

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _run(text, props=""):
    return f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _docx(body_paras, header_paras=()):
    def doc(tag, paras):
        return f'<?xml version="1.0"?><w:{tag} {NS}>' + "".join(f"<w:p>{p}</w:p>" for p in paras) + f"</w:{tag}>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc("document", body_paras).replace("<w:p>", "<w:body><w:p>", 1)
                   .replace("</w:document>", "</w:body></w:document>"))
        if header_paras:
            z.writestr("word/header1.xml", doc("hdr", header_paras))
    return buf.getvalue()


def test_word_headers_are_read_and_hidden_text_is_not():
    data = _docx(
        [_run("Built the billing API in Python") + _run(" Kubernetes Rust Terraform", '<w:vanish/>'),
         _run("Led nothing") + _run(" Staff Engineer", '<w:color w:val="FFFFFF"/>'),
         _run("Tiny") + _run(" keyword stuffing here", '<w:sz w:val="2"/>')],
        header_paras=[_run("Priya Sample | priya.sample@example.com")])
    lines, seen = S.docx_lines(data)
    assert lines[0] == "Priya Sample | priya.sample@example.com"
    text = "\n".join(lines)
    assert "Built the billing API in Python" in text
    for hidden in ("Kubernetes", "Staff Engineer", "keyword stuffing"):
        assert hidden not in text, hidden
    assert seen["hidden_words"] == 3 + 2 + 3


def test_a_text_box_is_read_once():
    box = ('<w:r><w:pict><w:txbxContent><w:p>' + _run("Inside the box") + '</w:p></w:txbxContent></w:pict></w:r>')
    lines, _ = S.docx_lines(_docx([_run("Before") + box]))
    assert lines.count("Inside the box") == 1 and "Before" in lines


# ── PDFs ─────────────────────────────────────────────────────────────────────

def _pdf(pages):
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "\n".join(["BT", "/F1 10 Tf", "40 800 Td", "13 TL"] + [f"({esc(x)}) Tj T*" for x in lines]
                            + ["ET"]).encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % len(objs))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_a_pdf_page_header_is_kept_once_and_page_numbers_go():
    data = _pdf([["Priya Sample - Resume", "Built the billing API in Python", "Page 1 of 2"],
                 ["Priya Sample - Resume", "Moved the team to GitHub Actions", "Page 2 of 2"]])
    lines, seen = S.pdf_lines(data)
    assert lines.count("Priya Sample - Resume") == 1
    assert not any(line.startswith("Page ") for line in lines)
    assert "Moved the team to GitHub Actions" in lines and seen["repeated_lines"] == 3


# ── pages and links ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["ftp://example.com/x", "https://example.com:8443/", "https://user:pw@example.com/"])
def test_only_plain_web_addresses_are_taken(url):
    with pytest.raises(S.SetupError):
        S.check_url(url)


def test_an_address_inside_a_private_network_is_refused(monkeypatch):
    """A page fetched on the person's behalf must never reach this server's own
    network - not even through a redirect from a public page."""
    addrs = {"example.com": "93.184.216.34", "internal.example": "10.0.0.5", "localhost": "127.0.0.1"}
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda host, *_: [(0, 0, 0, "", (addrs[host], 0))])
    monkeypatch.setattr("jobsearch.robots.RobotsCache.check", lambda self, url: type("D", (), {"allowed": True})())
    for url in ("http://localhost/", "https://internal.example/me"):
        with pytest.raises(S.SetupError, match="public internet"):
            S.fetch_page(url, __import__("pathlib").Path("."), "test-agent")

    class Redirect:
        status_code, headers = 302, {"location": "https://internal.example/admin"}

        def close(self):
            pass
    monkeypatch.setattr(S.requests, "get", lambda *a, **k: Redirect())
    with pytest.raises(S.SetupError, match="public internet"):
        S.fetch_page("https://example.com/portfolio", __import__("pathlib").Path("."), "test-agent")


def test_a_page_is_read_as_its_words(monkeypatch, tmp_path):
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda host, *_: [(0, 0, 0, "", ("93.184.216.34", 0))])
    monkeypatch.setattr("jobsearch.robots.RobotsCache.check", lambda self, url: type("D", (), {"allowed": True})())
    html = ("<html><head><title>Priya</title><style>p{color:red}</style><script>var a=1</script></head>"
            "<body><h1>Projects</h1><p>Built a library app for my neighbourhood</p><p>Uses React Native</p></body></html>")

    class Page:
        status_code, headers, encoding = 200, {"content-type": "text/html; charset=utf-8"}, "utf-8"

        def iter_content(self, n):
            yield html.encode()

        def close(self):
            pass
    monkeypatch.setattr(S.requests, "get", lambda *a, **k: Page())
    url, lines = S.fetch_page("example.com/projects", tmp_path, "test-agent")
    assert url == "https://example.com/projects"
    assert lines == ["Projects", "Built a library app for my neighbourhood", "Uses React Native"]


def test_linkedin_and_naukri_are_never_read():
    assert S.never_read("https://www.linkedin.com/in/someone") and S.never_read("naukri.com/x")
    assert not S.never_read("https://example.com")


# ── GitHub ───────────────────────────────────────────────────────────────────

class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


def test_github_is_read_through_its_api_own_repositories_only():
    repos = [{"name": "tariff-helper", "description": "RAG over tariff schedules", "language": "Python",
              "topics": ["rag", "llm"], "pushed_at": "2025-06-01T00:00:00Z", "fork": False, "stargazers_count": 999},
             {"name": "someone-elses", "fork": True}]
    readme = "# Tariff Helper\n![badge](https://img.shields.io/x)\nAnswers questions with [Gemma](https://x).\n```py\nimport x\n```"
    calls = []

    def get(url, accept):
        calls.append(url)
        return Resp(200, repos) if url.endswith("sort=pushed") else Resp(200, text=readme)
    lines = S.github_lines("priya-sample", "test-agent", get=get)
    assert "Repository: tariff-helper" in lines and "Main language: Python" in lines
    assert "README: Answers questions with Gemma." in lines and "Link: https://github.com/priya-sample/tariff-helper" in lines
    assert not any("someone-elses" in line or "999" in line or "import x" in line for line in lines)
    assert all(u.startswith("https://api.github.com/") for u in calls)


@pytest.mark.parametrize("status, why", [(404, "no user called"), (403, "limiting requests")])
def test_github_says_what_went_wrong(status, why):
    with pytest.raises(S.SetupError, match=why):
        S.github_lines("nobody", "test-agent", get=lambda url, accept: Resp(status, {}))


@pytest.mark.parametrize("url", ["https://gitlab.com/x", "https://github.com/", "https://github.com/bad name"])
def test_only_a_github_profile_address_is_taken(url):
    with pytest.raises(S.SetupError):
        S.github_user(url)
