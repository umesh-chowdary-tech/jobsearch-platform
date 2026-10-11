"""TypeSafe's Jev: a "System One" model that answers typed questions about a
text - a choice, a yes/no, a score - with a probability and a confidence, and
writes no prose.

Here to be measured against the inbox rules (mail_eval.py) before anything
relies on it; nothing in the cycle calls it yet. Held to data/llm-limits.yaml
like every provider, and counted against the profile it reads for: a request
that would pass a cap is never sent.

API (docs.typesafe.ai/api): POST /v1/systemone, Bearer key, a body of
{"model", "state", "questions"}; billed on input tokens only.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .limits import Meter, estimate_tokens
from .llm import load_dotenv
from .profile import Profile

URL = "https://api.typesafe.ai/v1/systemone"
RETRIES = 3          # 429 and 529 ("overloaded"), with 1, 2, 4 s between


@dataclass
class JevAnswer:
    ok: bool
    answers: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    tokens: int = 0
    error: str = ""
    limited: str = ""


class Jev:
    def __init__(self, prof: Profile, timeout: float = 30.0, purpose: str = "eval") -> None:
        load_dotenv(prof.env)
        self.meter = Meter(prof.root, prof.id)
        self.timeout = timeout
        self.purpose = purpose
        self.model = os.environ.get("JEV_MODEL") or "jev-latest"

    @property
    def key(self) -> str | None:
        return os.environ.get("JEV_API_KEY") or None

    @property
    def available(self) -> bool:
        return bool(self.key)

    def ask(self, state: str, questions: dict[str, Any]) -> JevAnswer:
        if not self.key:
            return JevAnswer(ok=False, error="JEV_API_KEY not set")
        body = {"model": self.model, "state": state, "questions": questions}
        why = self.meter.check("jev", estimate_tokens(state, json.dumps(questions)), self.purpose)
        if why:
            return JevAnswer(ok=False, error=why, limited=f"Jev: {why}")
        r = None
        for attempt in range(RETRIES + 1):
            try:
                r = requests.post(URL, json=body, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.key}",
                                           "Content-Type": "application/json"})
            except requests.RequestException as exc:
                self.meter.record("jev", 0, self.purpose)
                return JevAnswer(ok=False, error=type(exc).__name__)
            if r.status_code not in (429, 529) or attempt == RETRIES:
                break
            self.meter.record("jev", 0, self.purpose)
            time.sleep(2 ** attempt)
        if r.status_code != 200:
            self.meter.record("jev", 0, self.purpose)
            limited = "Jev: rate limited (HTTP 429)" if r.status_code == 429 else ""
            return JevAnswer(ok=False, error=f"HTTP {r.status_code}: {r.text[:160]}", limited=limited)
        try:
            data = r.json()
        except ValueError:
            self.meter.record("jev", estimate_tokens(state), self.purpose)
            return JevAnswer(ok=False, error="reply was not JSON")
        tokens = int((data.get("usage") or {}).get("input_tokens") or 0) or estimate_tokens(state)
        self.meter.record("jev", tokens, self.purpose)
        return JevAnswer(ok=True, answers=data.get("answers") or {}, model=str(data.get("model") or ""),
                         tokens=tokens)
