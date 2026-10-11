"""Every number in generated text must come from a fact, unit and all.

One check for everything a model writes that an employer or a referrer will
read: the resume summary (assembly) and every outreach draft. The store's rule
is the same for both - never add, round or interpolate a number.
"""
from __future__ import annotations

import re

URL = re.compile(r"https?://\S+")
_NUM = re.compile(r"\d+(?:[.,]\d+)*%?")
_WORD_NUMBERS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|twelve|twenty|fifty|"
    r"dozens?|hundreds?|thousands?|millions?)\b", re.I)


def _number_pairs(text: str) -> set[tuple[str, str]]:
    """(number, the word it counts) for every number in the text.

    Digits alone let "3 years" through, because "under 3 months" is in the
    facts. Pairing each number with its unit catches exactly that swap. A
    number that ends its clause pairs with "" and only has to exist somewhere.
    The percent sign stays on the number: "40 of the checks" does not ground
    "40% of the checks".
    """
    out: set[tuple[str, str]] = set()
    for m in _NUM.finditer(text):
        nxt = re.match(r"[^A-Za-z.;:!?\n]*?([A-Za-z]+)", text[m.end():m.end() + 30])
        out.add((m.group(0), nxt.group(1).lower() if nxt else ""))
    return out


def _plain(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def untraced_bullets(resume_text: str, store: dict) -> list[str]:
    """The bullet lines of a rendered resume that no confirmed fact says, word
    for word - the no-invention check. Facts print verbatim, so on a resume
    built from the store this is always empty; a line that isn't is a claim
    the person never made."""
    known: set[str] = set()
    for e in store.get("entries") or []:
        known.add(_plain(e.get("headline")))
        known.update(_plain(f) for f in e.get("facts") or [])
        known.update(_plain(m.get("claim")) for m in e.get("metrics") or [] if m.get("measured"))
    out = []
    for line in resume_text.splitlines():
        m = re.match(r"\s*[-•]\s+(.+)$", line)
        if m and _plain(m.group(1)) not in known:
            out.append(m.group(1).strip())
    return out


def numbers_grounded(text: str, allowed: list[str]) -> tuple[bool, str]:
    text = URL.sub(" ", text)
    blob = " . ".join(URL.sub(" ", a) for a in allowed if a)
    have = _number_pairs(blob)
    have_nums = {n for n, _ in have}
    for num, word in sorted(_number_pairs(text)):
        if word and (num, word) not in have:
            return False, f"'{num} {word}' is not in any fact"
        if not word and num not in have_nums:
            return False, f"'{num}' is not in any fact"
    # As words, not substrings: "ten" is inside "content", which let
    # "ten years of experience" through on the real store.
    for w in _WORD_NUMBERS.findall(text):
        if not re.search(rf"\b{w}\b", blob, re.I):
            return False, f"'{w}' is a number no fact states"
    return True, "every number is in a fact"
