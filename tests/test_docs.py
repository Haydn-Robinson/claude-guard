"""Keeps docs/verified-behaviour.md honest and linked: every fact is well formed, and every
`VB-xx` cited anywhere in the project exists there."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "verified-behaviour.md"
ID = re.compile(r"VB-[A-Z]\d+")


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def fact_blocks() -> dict[str, str]:
    """Each `### VB-xx` section, keyed by id."""
    parts = re.split(r"^### (VB-[A-Z]\d+)\b", doc_text(), flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def all_ids() -> set[str]:
    table_ids = set(re.findall(r"^\| (VB-U\d+) \|", doc_text(), flags=re.MULTILINE))
    return set(fact_blocks()) | table_ids


def test_every_fact_has_status_and_evidence_or_reliance():
    blocks = fact_blocks()
    assert len(blocks) > 30
    for fid, body in blocks.items():
        m = re.search(r"^\*\*Status:\*\* (VERIFIED|DOCUMENTED|UNVERIFIED)\b", body, re.MULTILINE)
        assert m, f"{fid}: missing or invalid **Status:**"
        assert "**Fact:**" in body, f"{fid}: missing **Fact:**"
        assert "**Evidence:**" in body or "**Relied on by:**" in body, f"{fid}: no evidence"
        if m.group(1) == "VERIFIED":
            assert "**Evidence:**" in body, f"{fid}: VERIFIED needs **Evidence:**"


def test_ids_are_unique():
    headings = re.findall(r"^### (VB-[A-Z]\d+)\b", doc_text(), flags=re.MULTILINE)
    assert len(headings) == len(set(headings))


def test_open_questions_are_listed():
    ids = all_ids()
    assert {f"VB-U{n}" for n in range(1, 14)} <= ids


def test_every_cited_id_exists():
    known = all_ids()
    scanned = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.toml"),
        *ROOT.glob("tests/*.py"),
        ROOT / "README.md",
        *ROOT.glob("docs/*.md"),
    ]
    missing = {}
    for path in scanned:
        if path == Path(__file__):
            continue
        for cited in set(ID.findall(path.read_text(encoding="utf-8"))):
            if cited not in known:
                missing.setdefault(cited, []).append(path.name)
    assert not missing, f"cited but not defined in verified-behaviour.md: {missing}"


def test_source_actually_cites_the_reference():
    cited = set()
    for path in ROOT.glob("src/**/*.*"):
        if path.suffix in (".py", ".toml"):
            cited |= set(ID.findall(path.read_text(encoding="utf-8")))
    assert {"VB-S8", "VB-H3", "VB-E1", "VB-R1"} <= cited
