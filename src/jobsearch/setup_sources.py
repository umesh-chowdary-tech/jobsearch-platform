"""What setup reads about a person, and how - before any of it reaches a model.

  files      resumes as PDF, Word (.docx) or plain text. Word's headers and
             footers are read too (contact details often sit there); text a
             reader can't see - hidden, white or tiny, a trick for resume
             filters - is left out and counted. A PDF's page header and footer,
             repeated on every page, are kept once; page numbers are dropped.
  pages      a portfolio page the person names: fetched once, politely
             (robots.txt), from a public address only - never this server's
             own network - and read as text.
  github     the person's public repositories through GitHub's API: names,
             descriptions, languages, topics, READMEs. Never stars or
             followers - numbers nobody should claim as their own.
  notes      anything else, in the person's own words.

  private    what a resume often carries that nothing here needs - date of
             birth, Aadhaar, PAN, passport, father's name, marital status,
             religion, home address, references - is removed when the text is
             read, so it is neither kept nor sent. What was removed is counted
             for the person to see.

LinkedIn is never read: it is kept as a link for resumes. (No LinkedIn or
Naukri scraping, ever - see data/ats-registry.yaml's constraints.)
"""
from __future__ import annotations

import io
import ipaddress
import re
import socket
import urllib.parse
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

MAX_BYTES = 5_000_000          # an uploaded file
MAX_PAGES = 20                 # of a PDF; a resume is a few
MAX_DOCX_XML = 20_000_000      # one part of a .docx, unzipped
MAX_SEND_CHARS = 60_000        # of one source's text sent to the model
MAX_PAGE_BYTES = 1_500_000     # a portfolio page
MAX_NOTE_CHARS = 5_000
KINDS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class SetupError(Exception):
    """Something the person has to fix - a message for a human, never a crash."""


def lines_of(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


# ── sections ────────────────────────────────────────────────────────────────
# A resume's headings, so a skill named only in its "Skills" list is told
# apart from one a piece of work names, and a "Personal Details" block can be
# left out whole.

HEADINGS = {
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "career history", "internships", "internship", "professional history"),
    "projects": ("projects", "personal projects", "academic projects", "key projects", "selected projects"),
    "education": ("education", "academics", "academic qualifications", "educational qualifications",
                  "qualifications", "academic background", "education and training"),
    "skills": ("skills", "technical skills", "key skills", "core skills", "core competencies", "competencies",
               "tools", "technologies", "tech stack", "tools and technologies", "skill set", "skillset",
               "areas of expertise", "expertise", "technical expertise", "it skills", "software skills"),
    "personal": ("personal details", "personal information", "personal profile", "personal data",
                 "personal particulars", "personal info"),
    "references": ("references", "referees", "reference"),
    "declaration": ("declaration",),
    "other": ("certifications", "certificates", "certification", "awards", "achievements", "publications",
              "languages", "languages known", "summary", "professional summary", "profile", "objective",
              "career objective", "about me", "about", "hobbies", "interests", "extra-curricular activities",
              "extracurricular activities", "activities", "volunteering", "volunteer experience", "honours",
              "honors", "trainings", "training", "courses", "strengths", "accomplishments", "patents",
              "positions of responsibility", "leadership"),
}
_HEADING = {name: key for key, names in HEADINGS.items() for name in names}


def heading_of(line: str) -> str | None:
    """The section a heading line starts, or None. "Skills: Python, Go" is a
    skills line, not a heading - the caller sees it as both."""
    t = re.sub(r"[\s:|\-–—_*#•]+$", "", line.strip().lower())
    t = re.sub(r"^[\s:|\-–—_*#•]+", "", t)
    if len(t) > 40:
        return None
    return _HEADING.get(" ".join(t.split()))


def sections(lines: list[str]) -> list[str | None]:
    """The section each line is in (None before the first heading)."""
    out, now = [], None
    for line in lines:
        h = heading_of(line)
        if h:
            now = h
        out.append(now)
    return out


def listed_only(line: str, section: str | None) -> bool:
    """A line that only lists skills - under a Skills heading, "Skills: ..."
    inline, or a bare run of comma-separated names. It says a person named a
    skill, not that a piece of work used it."""
    if section == "skills":
        return True
    low = line.lower()
    if re.match(r"^\s*(technical\s+)?(skills|tools|technologies|tech stack)\s*[:\-–]", low):
        return True
    parts = [p for p in re.split(r"[,|•·;/]", line) if p.strip()]
    return len(parts) >= 4 and all(len(p.split()) <= 3 for p in parts)


# ── private details ─────────────────────────────────────────────────────────

_LABELS = (r"date\s+of\s+birth|d\.?\s?o\.?\s?b\.?|birth\s*date|born\s+on|father'?s?\s+name|mother'?s?\s+name|"
           r"husband'?s?\s+name|spouse'?s?\s+name|guardian'?s?\s+name|marital\s+status|gender|sex|religion|"
           r"caste|nationality|passport(?:\s+(?:no\.?|number))?|pan(?:\s+(?:no\.?|number|card))?|"
           r"aadh?aa?r(?:\s+(?:no\.?|number|card))?|blood\s+group|height|weight|visa\s+status")
_LABEL_LINE = re.compile(rf"^\s*(?:{_LABELS}|(?:permanent|current|residential|home|postal|correspondence)?\s*address)"
                         r"\s*[:\-–]", re.I)
# A value runs to the next separator, or to the next label on the same line.
_LABEL_INLINE = re.compile(rf"\b(?:{_LABELS})\s*[:\-–]\s*(?:(?!\b(?:{_LABELS})\s*[:\-–])[^|•;,])+", re.I)
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"(?<!\d)[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}(?!\d)")
_PASSPORT = re.compile(r"\b[A-Z][0-9]{7}\b")
_KIND = {"date of birth": "date of birth", "dob": "date of birth", "d.o.b": "date of birth", "birth": "date of birth",
         "born": "date of birth", "father": "a parent's name", "mother": "a parent's name", "husband": "a spouse's name",
         "spouse": "a spouse's name", "guardian": "a guardian's name", "marital": "marital status",
         "gender": "gender", "sex": "gender", "religion": "religion", "caste": "caste", "nationality": "nationality",
         "passport": "passport number", "pan": "PAN", "aadh": "Aadhaar", "blood": "blood group", "height": "height",
         "weight": "weight", "visa": "visa status", "address": "home address"}


def _kind_of(label: str) -> str:
    low = label.lower()
    return next((v for k, v in _KIND.items() if low.lstrip().startswith(k) or k in low.split(":")[0]),
                "a personal detail")


def strip_private(lines: list[str]) -> tuple[list[str], Counter]:
    """(lines kept, what was removed - by kind). Whole "Personal Details" and
    "References" blocks go, and labelled details anywhere; an ID number that
    looks like Aadhaar, PAN or a passport is cut out of its line."""
    kept: list[str] = []
    removed: Counter = Counter()
    block: str | None = None
    for line in lines:
        h = heading_of(line)
        if h:
            block = h if h in ("personal", "references", "declaration") else None
            if block:
                continue
        if block:
            # Languages often sit among personal details, and are worth keeping.
            if block == "personal" and re.match(r"^\s*languages?(\s+known)?\s*[:\-–]", line, re.I):
                kept.append(line)
                continue
            # A personal block is short "label: value" lines. A long sentence
            # means the block ended under a heading we didn't recognise.
            if len(line) < 90 or ":" in line:
                removed["a reference" if block == "references" else
                        "a declaration line" if block == "declaration" else _kind_of(line)] += 1
                continue
            block = None
        if _LABEL_LINE.match(line):
            for kind in [_kind_of(m.group(0)) for m in _LABEL_INLINE.finditer(line)] or [_kind_of(line)]:
                removed[kind] += 1
            continue
        new = line
        for m in list(_LABEL_INLINE.finditer(new)):
            removed[_kind_of(m.group(0))] += 1
        new = _LABEL_INLINE.sub("[personal detail removed]", new)
        for pat, kind in ((_PAN, "PAN"), (_AADHAAR, "Aadhaar"), (_PASSPORT, "passport number")):
            n = len(pat.findall(new))
            if n:
                removed[kind] += n
                new = pat.sub("[id removed]", new)
        kept.append(new)
    return kept, removed


# ── files ───────────────────────────────────────────────────────────────────

def _xml(z: zipfile.ZipFile, name: str) -> ElementTree.Element:
    info = z.getinfo(name)
    if info.file_size > MAX_DOCX_XML:
        raise SetupError("that Word file is too large once unzipped")
    with z.open(info) as f:
        xml = f.read(MAX_DOCX_XML + 1)     # the declared size can lie; this can't
    if len(xml) > MAX_DOCX_XML:
        raise SetupError("that Word file is too large once unzipped")
    # Word never writes a DTD. One here could declare entities that expand
    # without end, so the file is refused, not parsed.
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise SetupError("that Word file holds something a resume doesn't - save it again from Word")
    return ElementTree.fromstring(xml)  # noqa: S314  # nosec B314 - bounded, and no DTD reaches it


def _hidden(run: ElementTree.Element) -> bool:
    """A run a reader can't see: hidden, white, or under 4 points."""
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    vanish = rpr.find(f"{W}vanish")
    if vanish is not None and vanish.get(f"{W}val", "true").lower() not in ("0", "false", "off"):
        return True
    color = rpr.find(f"{W}color")
    if color is not None and color.get(f"{W}val", "").upper() in ("FFFFFF", "FFFFFE", "FEFEFE"):
        return True
    size = rpr.find(f"{W}sz")
    try:
        return size is not None and int(size.get(f"{W}val", "24")) < 8      # half-points
    except ValueError:
        return False


_RUN_HOLDERS = {f"{W}{t}" for t in ("hyperlink", "ins", "smartTag", "fldSimple", "sdt", "sdtContent",
                                     "customXml", "moveTo")}


def _runs(para: ElementTree.Element) -> Any:
    """A paragraph's own runs - not those of a text box drawn inside it,
    which is a paragraph of its own (and not deleted text)."""
    for child in para:
        if child.tag == f"{W}r":
            yield child
        elif child.tag in _RUN_HOLDERS:
            yield from _runs(child)


def _paragraphs(root: ElementTree.Element) -> tuple[list[str], int]:
    lines, hidden = [], 0
    for para in root.iter(f"{W}p"):
        bits = []
        for run in _runs(para):
            text = "".join((n.text or "") if n.tag == f"{W}t" else " " for n in run
                           if n.tag in (f"{W}t", f"{W}tab", f"{W}br"))
            if _hidden(run):
                hidden += len(text.split())
                continue
            bits.append(text)
        lines.append("".join(bits))
    return lines, hidden


def docx_lines(data: bytes) -> tuple[list[str], dict[str, int]]:
    """Headers, then the body, then footers - each line once."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            body, hidden = _paragraphs(_xml(z, "word/document.xml"))
            heads, feet = [], []
            for name in sorted(n for n in names if re.fullmatch(r"word/(header|footer)\d*\.xml", n)):
                got, h = _paragraphs(_xml(z, name))
                hidden += h
                (heads if "header" in name else feet).extend(got)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise SetupError(f"that Word file could not be read ({type(exc).__name__})") from exc
    seen, out = set(), []
    for line in lines_of("\n".join(heads + body + feet)):
        key = line.lower()
        if key in seen and len(line) < 80:          # a header repeated per section
            continue
        seen.add(key)
        out.append(line)
    return out, {"hidden_words": hidden}


_PAGE_NO = re.compile(r"^\s*(page\s*)?\d{1,3}\s*(/|of)\s*\d{1,3}\s*$|^\s*page\s+\d{1,3}\s*$|^\s*-\s*\d{1,3}\s*-\s*$",
                      re.I)


def pdf_lines(data: bytes) -> tuple[list[str], dict[str, int]]:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SetupError("that PDF is password-protected - save a copy without a password and upload that")
        pages = len(reader.pages)
    except SetupError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many kinds; all mean "unreadable"
        raise SetupError(f"that PDF could not be read ({type(exc).__name__})") from exc
    if pages > MAX_PAGES:
        raise SetupError(f"that PDF has {pages} pages - upload a resume of up to {MAX_PAGES}")
    try:
        texts = [lines_of(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise SetupError(f"that PDF could not be read ({type(exc).__name__})") from exc
    # A line at the top or bottom of two or more pages is the page's header or
    # footer: kept the first time only.
    edges = Counter(line.lower() for t in texts for line in set(t[:2] + t[-2:]))
    repeated = {k for k, n in edges.items() if n >= 2} if len(texts) > 1 else set()
    out, seen, dropped = [], set(), 0
    for t in texts:
        for line in t:
            low = line.lower()
            if _PAGE_NO.match(line):
                dropped += 1
                continue
            if low in repeated:
                if low in seen:
                    dropped += 1
                    continue
                seen.add(low)
            out.append(line)
    return out, {"repeated_lines": dropped}


def read_file(data: bytes, filename: str) -> tuple[list[str], dict[str, int]]:
    """The file's lines, and what reading it noticed."""
    kind = KINDS.get(Path(filename).suffix.lower())
    if not kind:
        raise SetupError("upload a PDF, a Word file (.docx) or a plain text file")
    if len(data) > MAX_BYTES:
        raise SetupError("that file is larger than 5 MB")
    if kind == "pdf":
        lines, seen = pdf_lines(data)
    elif kind == "docx":
        lines, seen = docx_lines(data)
    else:
        lines, seen = lines_of(data.decode("utf-8", "replace")), {}
    if len(re.sub(r"\s", "", "".join(lines))) < 40:
        raise SetupError("no text could be read from that file - if it is a scan, upload a text PDF or "
                         "a Word file instead")
    return lines, seen


# ── pages ───────────────────────────────────────────────────────────────────

class _Text(HTMLParser):
    """The words a visitor reads: no scripts, styles or navigation chrome."""
    SKIP = {"script", "style", "noscript", "svg", "template", "head", "iframe", "canvas"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "td", "th",
             "header", "footer", "dd", "dt", "blockquote", "pre", "title", "ul", "ol", "table", "main"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def html_lines(html: str) -> list[str]:
    p = _Text()
    p.feed(html)
    return lines_of("".join(p.parts))


def _public(host: str) -> None:
    """Refuse a host that resolves into a private network - this server's
    own, or anything not on the public internet."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise SetupError(f"couldn't find {host} - check the address") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise SetupError("that address isn't on the public internet")


def check_url(url: str) -> urllib.parse.SplitResult:
    url = url.strip()
    if len(url) > 300:
        raise SetupError("that address is too long")
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise SetupError("enter a web address, like https://example.com")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise SetupError("enter a web address, like https://example.com")
    if parts.port not in (None, 80, 443) or parts.username or parts.password:
        raise SetupError("enter a plain web address, like https://example.com")
    return parts


def fetch_page(url: str, robots_dir: Path, agent: str) -> tuple[str, list[str]]:
    """(final address, lines) of one public page - robots.txt asked first, at
    most three redirects, each checked again, and 1.5 MB read at most."""
    from .robots import RobotsCache
    robots = RobotsCache(robots_dir, agent)
    for _ in range(4):
        parts = check_url(url)
        url = urllib.parse.urlunsplit(parts)
        _public(parts.hostname or "")
        if not robots.check(url).allowed:
            raise SetupError("that site asks programs not to read it (its robots.txt) - paste the text as a note")
        try:
            r = requests.get(url, timeout=10, allow_redirects=False, stream=True,
                             headers={"User-Agent": agent, "Accept": "text/html,text/plain;q=0.9"})
        except requests.RequestException as exc:
            raise SetupError(f"couldn't open that page ({type(exc).__name__})") from None
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = urllib.parse.urljoin(url, r.headers["location"])
            r.close()
            continue
        if r.status_code != 200:
            r.close()
            raise SetupError(f"that page answered {r.status_code}")
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("text/html", "text/plain", "application/xhtml+xml"):
            r.close()
            raise SetupError("that address isn't a web page")
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                break
        r.close()
        text = body[:MAX_PAGE_BYTES].decode(r.encoding or "utf-8", "replace")
        lines = html_lines(text) if ctype != "text/plain" else lines_of(text)
        if len(re.sub(r"\s", "", "".join(lines))) < 40:
            raise SetupError("that page shows its content with JavaScript, which can't be read here - "
                             "paste the text into the note instead")
        return url, lines
    raise SetupError("that page redirects too many times")


# ── GitHub ──────────────────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
MAX_REPOS = 10
README_CHARS = 4_000


def github_user(url: str) -> str:
    parts = check_url(url)
    host = (parts.hostname or "").lower()
    user = parts.path.strip("/").split("/")[0] if parts.path.strip("/") else ""
    if host not in ("github.com", "www.github.com") or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", user):
        raise SetupError("enter your GitHub profile address, like https://github.com/your-name")
    return user


def markdown_lines(md: str) -> list[str]:
    """A README's prose: no code blocks, images, badges or HTML."""
    out, fenced = [], False
    for line in md.splitlines():
        if line.strip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)                 # images and badges
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)             # links keep their text
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"[*_`]{1,3}", "", line)
        out.append(line)
    return lines_of("\n".join(out))


def github_lines(user: str, agent: str, get: Any = None) -> list[str]:
    """The person's own public repositories, newest first - through GitHub's
    REST API, the documented way to read them (not the website)."""
    get = get or (lambda url, accept: requests.get(url, timeout=15, headers={"User-Agent": agent, "Accept": accept}))
    try:
        r = get(f"{GITHUB_API}/users/{user}/repos?per_page=100&type=owner&sort=pushed",
                "application/vnd.github+json")
    except requests.RequestException as exc:
        raise SetupError(f"couldn't reach GitHub ({type(exc).__name__})") from None
    if r.status_code == 404:
        raise SetupError(f"GitHub has no user called {user}")
    if r.status_code in (403, 429):
        raise SetupError("GitHub is limiting requests right now - try again in an hour")
    if r.status_code != 200 or not isinstance(r.json(), list):
        raise SetupError(f"GitHub answered {r.status_code}")
    repos = [x for x in r.json() if isinstance(x, dict) and not x.get("fork")][:MAX_REPOS]
    if not repos:
        raise SetupError("no public repositories of your own were found there")
    lines: list[str] = []
    for repo in repos:
        name = str(repo.get("name") or "")
        lines.append(f"Repository: {name}")
        if repo.get("description"):
            lines.append(f"Description: {repo['description']}")
        if repo.get("language"):
            lines.append(f"Main language: {repo['language']}")
        if repo.get("topics"):
            lines.append("Topics: " + ", ".join(str(t) for t in repo["topics"]))
        if repo.get("pushed_at"):
            lines.append(f"Last updated: {str(repo['pushed_at'])[:7]}")
        lines.append(f"Link: https://github.com/{user}/{name}")
        try:
            rr = get(f"{GITHUB_API}/repos/{user}/{name}/readme", "application/vnd.github.raw+json")
        except requests.RequestException:
            continue
        if rr.status_code == 200:
            lines += [f"README: {line}" for line in markdown_lines(rr.text[:README_CHARS])[:60]]
    return lines


# ── where each kind of link is allowed ───────────────────────────────────────

NEVER_READ = ("linkedin.com", "naukri.com")


def never_read(url: str) -> bool:
    host = (check_url(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in NEVER_READ)
