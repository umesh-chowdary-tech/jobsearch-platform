"""The fictional sample profiles, for tests.

Tests read people's facts only from profiles/sample-*: never a real profile.
`sample()` reads one in place; `make_root()` copies data/ and a profile into a
temporary root with an empty var/, for tests that write.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml

from jobsearch.profile import Profile

ROOT = Path(__file__).resolve().parents[1]
# The one most tests read: it exercises every part of the schema.
SAMPLE = "sample-backend"
SAMPLES = ("sample-backend", "sample-frontend", "sample-android")


def senders_with(employers: dict[str, str]) -> str:
    """A data folder holding the shared mail-senders.yaml plus these employer
    domains - the shared list names no one's employers."""
    d = Path(tempfile.mkdtemp(prefix="senders-"))
    doc = yaml.safe_load((ROOT / "data" / "mail-senders.yaml").read_text(encoding="utf-8")) or {}
    doc["employers"] = dict(employers)
    (d / "mail-senders.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(d)


def sample(pid: str = SAMPLE) -> Profile:
    """A sample profile, read in place. For tests that only read."""
    return Profile(ROOT, pid)


def make_root(tmp: Path, pid: str = SAMPLE, data: bool = True) -> Profile:
    """A fresh root holding data/ (unless `data=False`) and one sample
    profile, with that profile's var/ and the shared var/ created empty."""
    tmp = Path(tmp)
    if data and not (tmp / "data").exists():
        shutil.copytree(ROOT / "data", tmp / "data")
    shutil.copytree(ROOT / "profiles" / pid, tmp / "profiles" / pid)
    prof = Profile(tmp, pid)
    prof.var.mkdir(parents=True, exist_ok=True)
    prof.shared.mkdir(parents=True, exist_ok=True)
    return prof
