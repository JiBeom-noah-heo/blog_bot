"""leave-lab `_inbox` 메모 읽기·상태 갱신.

메모 형식은 ~/.claude/CLAUDE.md의 '경험 기록' 규칙을 따른다.
파일명: YYYY-MM-DD-<kind>-<slug>.md, frontmatter의 status가 raw인 것만 글감이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-([^-]+)-(.+)$")
KIND_SERIES = {"검증": "검증일지", "실험": "실험로그", "실패": "실험로그", "구축": "실험로그"}
VERDICTS = ("GO", "NO-GO", "HOLD")


@dataclass
class Note:
    path: Path
    meta: dict
    body: str

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def slug(self) -> str:
        m = FILENAME.match(self.path.stem)
        return m.group(3) if m else self.path.stem

    @property
    def kind(self) -> str:
        m = FILENAME.match(self.path.stem)
        return self.meta.get("kind") or (m.group(2) if m else "")

    @property
    def series(self) -> str:
        return self.meta.get("series_hint") or KIND_SERIES.get(self.kind, "실험로그")

    @property
    def verdict(self) -> str | None:
        v = self.meta.get("verdict")
        return v if v in VERDICTS else None

    @property
    def langs(self) -> list[str]:
        return ["ko", "th"] if str(self.meta.get("lang_hint", "ko")) == "ko+th" else ["ko"]

    @property
    def tags(self) -> list[str]:
        return [str(t) for t in (self.meta.get("tags") or [])]

    @property
    def sources(self) -> list[dict]:
        return [
            {"title": str(s.get("title", "")), "url": str(s.get("url", ""))}
            for s in (self.meta.get("sources") or [])
            if isinstance(s, dict)
        ]


def parse(path: Path) -> Note:
    m = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"frontmatter 없음: {path.name}")
    meta = yaml.safe_load(m.group(1)) or {}
    return Note(path=path, meta=meta, body=m.group(2))


def list_raw(inbox_dir: Path) -> list[Note]:
    notes = []
    for path in sorted(inbox_dir.glob("*.md")):
        try:
            note = parse(path)
        except Exception as e:
            print(f"[Inbox] 건너뜀 {path.name}: {e}")
            continue
        if note.meta.get("status") == "raw":
            notes.append(note)
    return notes


def validate(note: Note) -> list[str]:
    """글로 만들기 전에 메모 자체가 갖춰야 할 것."""
    issues = []
    for s in note.sources:
        if not s["url"].startswith(("http://", "https://")):
            issues.append(f"출처 URL 형식 오류: {s['url']!r}")
    if note.series == "검증일지":
        if not note.verdict:
            issues.append("검증 메모에 verdict(GO/NO-GO/HOLD) 없음")
        if not note.sources:
            issues.append("검증 메모에 sources 없음 (링크 없는 판정은 발행 금지)")
    return issues


def mark_used(note: Note, used_in: list[str]) -> None:
    """status: raw → used, used_in 추가. 나머지 메모 내용은 건드리지 않는다."""
    text = note.text
    new, n = re.subn(
        r"^status:\s*raw\b.*$",
        "status: used\nused_in: [" + ", ".join(f'"{p}"' for p in used_in) + "]",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise ValueError(f"status: raw 줄을 찾지 못함: {note.path.name}")
    note.path.write_text(new, encoding="utf-8")
