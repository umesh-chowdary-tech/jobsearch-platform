"""Reading the curated registry, and writing machine state.

`data/ats-registry.yaml` is hand-written and full of comments explaining why
each exclusion exists. The agent READS it and never writes it — rewriting YAML
strips comments, and those comments are the reasoning.

Machine state goes to `var/_shared/`, which is disposable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .models import NOT_FOUND, BoardProbe, Escalation


def load_registry(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def known_boards(reg: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(provider, slug, company) for every verified board.

    Entries are either a bare slug or a mapping with a `slug` key plus a note —
    `purestorage` carries one because its board renders as "Everpure".
    """
    out: list[tuple[str, str, str]] = []
    for provider, slugs in (reg.get("verified") or {}).items():
        for item in slugs or []:
            if isinstance(item, dict):
                slug, company = item.get("slug"), item.get("company")
            else:
                slug, company = item, None
            if slug:
                out.append((provider, str(slug), str(company or slug)))
    return out


def do_not_reprobe(reg: dict[str, Any]) -> set[str]:
    """Company names whose guessed slugs already 404'd. Normalised by caller."""
    return {str(c) for c in (reg.get("slug_404_do_not_reprobe") or [])}


def excluded_hosts(reg: dict[str, Any]) -> dict[str, str]:
    return {
        str(e.get("host")): str(e.get("reason", ""))
        for e in (reg.get("excluded") or [])
        if isinstance(e, dict) and e.get("host")
    }


# ── machine state ────────────────────────────────────────────────────────────

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_run(
    var_dir: Path,
    probes: Iterable[BoardProbe],
    escalations: Iterable[Escalation],
    summary: dict[str, int],
) -> dict[str, Path]:
    var = Path(var_dir)
    (var / "discovery").mkdir(parents=True, exist_ok=True)
    stamp = _stamp()

    probes = list(probes)
    run_path = var / "discovery" / f"{stamp}.json"
    run_path.write_text(
        json.dumps(
            {
                "ran_at": stamp,
                "summary": summary,
                "probes": [p.to_dict() for p in probes],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # boards.json is the live view the watcher will read: one row per board that
    # currently answers. MERGED, not replaced - `verify --provider lever` probes
    # 5 of 24 boards, and replacing would delete the other 19 from the watcher's
    # world without anything looking wrong.
    boards_path = var / "boards.json"
    boards: dict[str, Any] = {}
    if boards_path.exists():
        try:
            boards = json.loads(boards_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            boards = {}

    for p in probes:
        key = f"{p.provider}:{p.slug}"
        if p.found:
            boards[key] = {
                "provider": p.provider,
                "slug": p.slug,
                "company": p.company,
                "board_name": p.board_name,
                "status": p.status,
                "posting_count": len(p.postings),
                "url": p.url,
                "last_ok": p.checked_at,
            }
        elif p.status == NOT_FOUND:
            # It answered, and the answer was "no such board". Drop it.
            # A transient error or a robots block leaves the row alone: absence
            # of evidence is not evidence the board is gone.
            boards.pop(key, None)

    boards_path.write_text(json.dumps(boards, indent=2, sort_keys=True), encoding="utf-8")

    # A queue, not a snapshot. Nothing here is ever auto-resolved; a human
    # closes these by hand, and the close is itself appended.
    esc_path = var / "escalations.jsonl"
    _append_escalations(esc_path, escalations)

    return {"run": run_path, "boards": boards_path, "escalations": esc_path}


def _escalation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row.get("kind", ""), row.get("provider", ""), row.get("slug", ""))


def read_escalations(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def open_escalations(path: Path) -> list[dict[str, Any]]:
    """Raised, minus anything a human has since resolved."""
    resolved = {
        _escalation_key(r) for r in read_escalations(path) if r.get("kind") == "resolved"
    }
    seen: set[tuple[str, str, str]] = set()
    out = []
    for r in read_escalations(path):
        if r.get("kind") == "resolved":
            continue
        key = _escalation_key(r)
        if key in seen:
            continue
        seen.add(key)
        if ("resolved", r.get("provider", ""), r.get("slug", "")) not in resolved:
            out.append(r)
    return out


def _append_escalations(path: Path, escalations: Iterable[Escalation]) -> int:
    """Append only escalations that are not already open.

    This runs on every poll. Without the dedupe, one unresolved identity doubt
    would append a fresh row every fifteen minutes until the queue was unreadable
    and the human stopped looking - which defeats the whole point of having one.
    """
    existing = {_escalation_key(r) for r in read_escalations(path)}
    resolved = {
        (r.get("provider", ""), r.get("slug", ""))
        for r in read_escalations(path)
        if r.get("kind") == "resolved"
    }
    written = 0
    with Path(path).open("a", encoding="utf-8") as fh:
        for e in escalations:
            d = e.to_dict()
            if _escalation_key(d) in existing or (d["provider"], d["slug"]) in resolved:
                continue
            fh.write(json.dumps(d) + "\n")
            existing.add(_escalation_key(d))
            written += 1
    return written


def resolve_escalation(path: Path, provider: str, slug: str, note: str) -> None:
    """Close an escalation. Appended, never edited - the history is the audit trail."""
    row = {
        "kind": "resolved",
        "provider": provider,
        "slug": slug,
        "note": note,
        "raised_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
