"""Setup from a resume: the AI drafts, every item quotes the resume, and only
what the person confirms becomes their profile. The resume is made up."""
from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from jobsearch import onboard as O
from jobsearch.assemble import build, load_store
from jobsearch.grounding import untraced_bullets
from jobsearch.llm import Answer
from jobsearch.profile import Profile
from jobsearch.render import render_text
from jobsearch.skills import load_index
from jobsearch.triage import load_profile, triage_posting
from samples import ROOT

RESUME = """Neha Example
Hyderabad, India | neha.example@example.com | +91 00000 11111 | linkedin.com/in/neha-example-0
EXPERIENCE
Software Engineer, Globex Payments, Bengaluru    Jul 2021 - Present
Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day
Helped move the team's CI from Jenkins to GitHub Actions
Junior Developer, Initech Software, Pune    2019 - 2021
Maintained the billing dashboard in React
EDUCATION
B.Tech, Computer Science, Example University, 2015 - 2019
SKILLS
Go, PostgreSQL, React, Docker
LANGUAGES
English, Hindi
"""

# What a model might answer: right where it quotes, wrong where it embellishes.
ANSWER = {
    "name": {"value": "Neha Example", "source": "Neha Example"},
    "location": {"value": "Hyderabad, India", "source": "Hyderabad, India | [email] | [phone] | [link]"},
    "jobs": [
        {"employer": "Globex Payments", "title": "Software Engineer", "location": "Bengaluru",
         "start": "2021-07", "end": "present",
         "source": "Software Engineer, Globex Payments, Bengaluru Jul 2021 - Present"},
        {"employer": "Initech Software", "title": "Junior Developer", "location": "Pune",
         "start": "2019", "end": "2021", "source": "4| Junior Developer, Initech Software, Pune 2019 - 2021"},
        {"employer": "Hooli", "title": "Tech Lead", "start": "2018", "end": "2019",
         "source": "Tech Lead, Hooli, 2018 - 2019"},                       # invented
    ],
    "entries": [
        {"type": "work", "where": "Globex Payments", "title": "Refund service", "period": "2021-07 to present",
         "headline": {"text": "Built the refund service in Go and PostgreSQL",
                      "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
         "facts": [
             {"text": "handling 40,000 refunds a day",
              "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
             {"text": "handling 50,000 refunds a day",                       # a number changed
              "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
             {"text": "Led the CI move from Jenkins to GitHub Actions",      # strengthened
              "source": "Helped move the team's CI from Jenkins to GitHub Actions"},
         ],
         "tech": [
             {"name": "Go", "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
             {"name": "PostgreSQL",
              "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
             {"name": "Kubernetes",                                          # not on that line
              "source": "Built the refund service in Go and PostgreSQL, handling 40,000 refunds a day"},
         ]},
        {"type": "work", "where": "Initech Software", "title": "", "period": "2019 to 2021",
         "headline": {"text": "Maintained the billing dashboard in React",
                      "source": "Maintained the billing dashboard in React"},
         "facts": [], "tech": [{"name": "React", "source": "Maintained the billing dashboard in React"}]},
        {"type": "education", "where": "Example University", "title": "", "period": "2015 to 2019",
         "headline": {"text": "B.Tech, Computer Science",
                      "source": "B.Tech, Computer Science, Example University, 2015 - 2019"},
         "facts": [], "tech": []},
        {"type": "award", "where": "Globex Payments", "title": "",
         "headline": {"text": "Engineer of the Year 2023", "source": "Engineer of the Year 2023"},  # invented
         "facts": [], "tech": []},
    ],
    "languages": [{"value": "English", "source": "English, Hindi"}, {"value": "French", "source": "English, Hindi"}],
}


class FakeLLM:
    available = True

    def __init__(self, data=ANSWER, ok=True, limited=""):
        self.data, self.ok, self.limited, self.sent = data, ok, limited, []

    def ask_json(self, system, user, **kw):
        self.sent.append(user)
        if not self.ok:
            return Answer(ok=False, limited=self.limited, failures=[{"provider": "claude", "error": "HTTP 429"}])
        return Answer(ok=True, data=self.data, provider="claude", model="claude-opus-5-5")


@pytest.fixture
def new(tmp_path):
    """A root with the shared data, and a profile that doesn't exist yet."""
    import shutil
    shutil.copytree(ROOT / "data", tmp_path / "data")
    return Profile(tmp_path, "neha")


def _docx(text: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{line}</w:t></w:r></w:p>"
                   for line in text.splitlines())
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w='
           '"http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """A one-page PDF with real text, built by hand."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    ops = ["BT", "/F1 10 Tf", "40 800 Td", "13 TL"] + [f"({esc(line)}) Tj T*" for line in text.splitlines()] + ["ET"]
    content = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"]
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


# ── reading ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, data", [("cv.txt", RESUME.encode()), ("cv.docx", _docx(RESUME)),
                                        ("cv.pdf", _pdf(RESUME))])
def test_text_comes_out_of_every_kind_of_file(name, data):
    lines = O.lines_of(O.extract_text(data, name))
    assert "Neha Example" in lines[0] and any("40,000 refunds" in line for line in lines)


@pytest.mark.parametrize("name, data, why", [
    ("cv.png", b"x" * 100, "upload a PDF"),
    ("cv.txt", b"   \n  ", "no text could be read"),
    ("cv.docx", b"not a zip", "could not be read"),
])
def test_a_file_that_cant_be_read_says_why(name, data, why):
    with pytest.raises(O.SetupError, match=why):
        O.extract_text(data, name)


def test_contact_details_are_found_here_and_never_sent(new):
    info = O.upload(new, RESUME.encode(), "cv.txt")
    assert info["contacts"] == ["email", "linkedin", "phone"]
    sent = info["will_send"]
    for secret in ("neha.example@example.com", "00000 11111", "linkedin.com/in/neha-example-0"):
        assert secret not in sent
    assert "[email]" in sent and "[phone]" in sent and "1| Neha Example" in sent


# ── drafting and checking ────────────────────────────────────────────────────

def _drafted(new, data=ANSWER):
    O.upload(new, RESUME.encode(), "cv.txt")
    llm = FakeLLM(data)
    return O.draft(new, llm=llm), llm


def test_only_what_the_resume_says_survives_the_check(new):
    d, llm = _drafted(new)
    ids = {it["id"] for it in d["items"]}
    assert {"id-name", "id-location", "job-1", "job-2", "e1", "e1-f1", "e1-t1", "e1-t2", "e2", "e3",
            "lang-1"} <= ids
    why = {g["what"]: g["why"] for g in d["dropped"]}
    assert why["job: Tech Lead at Hooli"] == "its quote is not in the resume"
    assert "'50,000 refunds'" in why["fact: handling 50,000 refunds a day"]
    assert why["fact: Led the CI move from Jenkins to GitHub Actions"] == "'led' - its line doesn't say that"
    assert why["skill: Kubernetes"] == "its line doesn't name it"
    assert why["award: Engineer of the Year 2023"] == "its quote is not in the resume"
    assert why["language: French"] == "its value is not in its quote"
    assert all(it["state"] == "pending" for it in d["items"])         # nothing confirmed for the person


def test_contact_details_come_from_the_file_not_the_model(new):
    d, llm = _drafted(new)
    found = {it["field"]: it for it in d["items"] if it["kind"] == "identity"}
    assert found["email"]["value"] == "neha.example@example.com" and found["email"]["by"] == "pattern"
    assert found["phone"]["value"] == "+91 00000 11111"
    assert "neha.example@example.com" not in llm.sent[0]


def test_no_answer_changes_nothing_and_says_why(new):
    O.upload(new, RESUME.encode(), "cv.txt")
    with pytest.raises(O.SetupError, match="daily spend limit"):
        O.draft(new, llm=FakeLLM(ok=False, limited="Claude: daily spend limit reached"))
    assert O.load_draft(new)["status"] == "uploaded"


# ── review ───────────────────────────────────────────────────────────────────

def _confirm_all(prof):
    for it in O.load_draft(prof)["items"]:
        O.update_item(prof, it["id"], "confirmed")


def test_an_edit_is_the_persons_own_words(new):
    _drafted(new)
    it = O.update_item(new, "e1-f1", "confirmed", {"text": "handling about 40,000 refunds a day at peak"})
    assert it["edited"] and it["state"] == "confirmed"
    with pytest.raises(O.SetupError, match="can't be edited"):
        O.update_item(new, "e1-f1", fields={"source": "made up"})


def test_a_fact_the_person_writes_is_confirmed_by_writing_it(new):
    _drafted(new)
    it = O.add_fact(new, "e1", "Cut refund failures after the Go rewrite")
    assert it["state"] == "confirmed" and it["by"] == "you"


def test_a_left_out_fact_comes_back_as_the_resume_line_itself(new):
    d, _ = _drafted(new)
    i = next(n for n, g in enumerate(d["dropped"]) if g["what"].startswith("fact: Led"))
    it = O.restore(new, i)
    assert it["text"] == "Helped move the team's CI from Jenkins to GitHub Actions" and it["state"] == "pending"


def test_skills_come_only_from_confirmed_lines_of_confirmed_entries(new):
    _drafted(new)
    O.update_item(new, "e1", "confirmed")
    O.update_item(new, "e1-t1", "confirmed")
    O.update_item(new, "e2-t1", "confirmed")                          # its entry is still pending
    assert [s["name"] for s in O.state(new)["skills"]] == ["Go"]


# ── writing the profile ──────────────────────────────────────────────────────

def test_only_confirmed_items_reach_the_profile(new):
    _drafted(new)
    _confirm_all(new)
    O.update_item(new, "e2", "removed")                               # its facts and skills go with it
    O.set_tiers(new, {"Go": "core", "PostgreSQL": "working"})
    prefs = O.state(new)["prefs"]
    assert prefs["preferred"] == ["Hyderabad"] and prefs["acceptable"] == ["India"] and prefs["years"] >= 5
    res = O.save(new, prefs)
    assert res["saved"] and new.exists()
    store = load_store(new)
    assert store["identity"]["name"] == "Neha Example" and store["identity"]["email"] == "neha.example@example.com"
    assert store["identity"]["title"]["official"] == "Software Engineer"            # the current job
    assert [j["employer"] for j in store["identity"]["employment"]] == ["Globex Payments", "Initech Software"]
    assert "Initech Software" not in {e["where"] for e in store["entries"] if e["type"] == "work"}
    work = next(e for e in store["entries"] if e["where"] == "Globex Payments")
    assert work["facts"] == ["handling 40,000 refunds a day"] and work["tech"] == ["Go", "PostgreSQL"]
    skills = yaml.safe_load(new.file("skills.yaml").read_text(encoding="utf-8"))
    assert skills["tiers"]["core"] == [{"skill": "Go", "backed_by": 1}]
    assert "React" not in yaml.safe_dump(skills)                       # its entry was removed
    assert "written by setup from a resume" in new.file("master-store.yaml").read_text(encoding="utf-8")


def test_setup_opens_again_on_what_was_saved(new):
    """The form sends the city and company boxes as typed text; reopening Setup
    after a save read them back as text and the screen failed on .join()."""
    _drafted(new)
    _confirm_all(new)
    O.save(new, {**O.state(new)["prefs"], "preferred": "Pune, Remote", "acceptable": "", "watch": "Globex"})
    prefs = O.state(new)["prefs"]
    assert prefs["preferred"] == ["Pune", "Remote"] and prefs["acceptable"] == [] and prefs["watch"] == ["Globex"]


def test_pending_items_are_left_out_and_counted(new):
    _drafted(new)
    O.update_item(new, "id-name", "confirmed")
    O.update_item(new, "job-1", "confirmed")
    res = O.save(new, O.state(new)["prefs"])
    assert res["left_out"] > 0 and load_store(new)["entries"] == []


def test_nothing_is_written_without_a_confirmed_name(new):
    _drafted(new)
    O.update_item(new, "job-1", "confirmed")
    with pytest.raises(O.SetupError, match="confirm your name"):
        O.save(new, {})
    assert not new.exists()


def test_an_existing_profile_is_replaced_only_when_asked(new):
    _drafted(new)
    _confirm_all(new)
    O.save(new, O.state(new)["prefs"])
    with pytest.raises(O.SetupError, match="already exists"):
        O.save(new, O.state(new)["prefs"])
    assert O.save(new, O.state(new)["prefs"], replace=True)["saved"]


def test_a_new_profile_works_end_to_end_and_its_resume_invents_nothing(new):
    """The point of Phase 1: from an uploaded resume to a tailored one, where
    every line traces back to something the person confirmed."""
    _drafted(new)
    _confirm_all(new)
    O.add_fact(new, "e1", "On call for the payments platform")
    prefs = dict(O.state(new)["prefs"], skip=["qa"], level="ic")
    O.save(new, prefs)
    index, search = load_index(new), load_profile(new.dir)
    posting = {"id": "p1", "title": "Backend Engineer (Go)", "company": "acme", "location": "Hyderabad, India",
               "description": "Go and PostgreSQL services. 3+ years of experience."}
    assert triage_posting(index, search, posting).verdict == "keep"
    store = load_store(new)
    v, entries, _ = build(new, store, index, posting, use_model=False)
    text = render_text(store["identity"], v, entries, v.surfaces, index)
    assert "handling 40,000 refunds a day" in text and "50,000" not in text and "Kubernetes" not in text
    assert untraced_bullets(text, store) == []
    assert untraced_bullets(text + "    - Increased revenue by 30%\n", store) == ["Increased revenue by 30%"]


def test_the_sample_resumes_invent_nothing_either():
    """The same check, on every fictional sample's best resume for a posting."""
    from samples import SAMPLES, sample
    for pid in SAMPLES:
        prof = sample(pid)
        store, index = load_store(prof), load_index(prof)
        posting = {"id": "x", "title": "Software Engineer", "description": "Python, React, Kotlin, AWS."}
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Profile(Path(tmp), pid)
            v, entries, _ = build(scratch, store, index, posting, use_model=False)
        text = render_text(store["identity"], v, entries, v.surfaces, index)
        assert untraced_bullets(text, store) == [], pid
