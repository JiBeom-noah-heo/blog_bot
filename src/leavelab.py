"""leave-lab 파이프라인: _inbox 메모 → 초안 → (사람 검수) → 발행.

  draft()    status: raw 메모마다 ko(+th) 초안을 output/leavelab/<메모이름>/ 에 만든다. 발행하지 않는다.
  publish()  초안(사람이 고친 내용 그대로)을 품질 게이트에 통과시킨 뒤 leave-lab 저장소로 발행하고
             메모를 status: used 로 바꾼다. push가 곧 배포다.
  status()   글감·초안 현황 출력.

발행은 leave-lab 저장소의 blog_bot/publishers/static_site.py 를 그대로 쓴다(단일 진실).
환경변수: LEAVE_LAB_REPO (필수), LEAVE_LAB_BRANCH, LEAVE_LAB_NO_PUSH
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from . import inbox
from .writer import call_claude

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "leavelab"

# 검증일지 본문 고정 형식 (leave-lab CLAUDE.md)
SECTIONS = {
    "ko": ["질문", "막힌 지점", "확인한 것", "판정", "다음 행동"],
    "th": ["คำถาม", "จุดที่ติด", "สิ่งที่ตรวจสอบ", "ผลการตัดสิน", "ขั้นต่อไป"],
}
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def _repo() -> Path:
    repo = Path(os.getenv("LEAVE_LAB_REPO", "")).expanduser()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise RuntimeError("LEAVE_LAB_REPO가 leave-lab git 저장소를 가리키지 않습니다 (.env 확인)")
    return repo


def _static_site(repo: Path):
    path = repo / "blog_bot" / "publishers" / "static_site.py"
    spec = importlib.util.spec_from_file_location("leavelab_static_site", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass가 모듈을 sys.modules에서 찾는다
    spec.loader.exec_module(mod)
    return mod


# ---------- 초안 ----------

def draft() -> None:
    repo = _repo()
    notes = inbox.list_raw(repo / "_inbox")
    if not notes:
        print("[LeaveLab] 새 글감(status: raw) 없음")
        return

    for note in notes:
        workdir = OUTPUT_DIR / note.path.stem
        meta_path = workdir / "meta.json"
        meta = _load_meta(meta_path)
        if meta.get("stage") in ("drafted", "published"):
            print(f"[LeaveLab] 이미 초안 있음: {workdir}")
            continue

        issues = inbox.validate(note)
        if issues:
            print(f"[LeaveLab] 메모 보완 필요 {note.path.name}:")
            for i in issues:
                print(f"  - {i}")
            continue

        workdir.mkdir(parents=True, exist_ok=True)
        meta.update({"note": str(note.path), "slug": note.slug, "series": note.series, "files": {}})
        try:
            for lang in note.langs:
                print(f"[LeaveLab] 초안 생성 중 ({lang}): {note.path.name}")
                out = _parse_response(call_claude(_prompt(note, lang), timeout=300))
                fm = {
                    "title": out["title"],
                    "description": out["description"],
                    "date": date.today(),
                    "lang": lang,
                    "series": note.series,
                    "verdict": note.verdict,
                    "tags": note.tags or out["tags"],
                    "sources": note.sources,
                    "draft": False,
                }
                path = workdir / f"{lang}.md"
                path.write_text(_render(fm, out["content"]), encoding="utf-8")
                meta["files"][lang] = path.name
                for issue in quality_gate(fm, out["content"]):
                    print(f"  [게이트] {lang}: {issue}")
        except Exception as e:
            meta["last_error"] = str(e)
            _save_meta(meta_path, meta)
            print(f"[LeaveLab] 오류: {e}")
            continue

        meta["stage"] = "drafted"
        _save_meta(meta_path, meta)
        print(f"[LeaveLab] 초안 완료 → {workdir}  (검수·수정 후 `python main.py publish`)")


def _prompt(note: inbox.Note, lang: str) -> str:
    style = (PROJECT_ROOT / "config" / "leavelab_style.md").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / "prompts" / f"leavelab_{lang}.md").read_text(encoding="utf-8")
    return (
        template
        .replace("{{STYLE_GUIDE}}", style)
        .replace("{{SERIES}}", note.series)
        .replace("{{NOTE}}", note.text)
    )


def _parse_response(text: str) -> dict:
    def line(key):
        m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    content = re.search(r"^CONTENT:\s*\n(.*)", text, re.MULTILINE | re.DOTALL)
    return {
        "title": line("TITLE"),
        "description": line("DESCRIPTION"),
        "tags": [t.strip() for t in line("TAGS").split(",") if t.strip()],
        "content": content.group(1).strip() if content else "",
    }


def _render(fm: dict, body: str) -> str:
    fm = {k: v for k, v in fm.items() if v is not None}
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=None, width=10_000)
    return f"---\n{head}---\n\n{body.strip()}\n"


# ---------- 품질 게이트 ----------

def quality_gate(fm: dict, body: str) -> list[str]:
    issues = []
    lang, series = fm.get("lang"), fm.get("series")
    if len(fm.get("title") or "") < 4:
        issues.append("제목 누락 또는 4자 미만")
    desc = fm.get("description") or ""
    if not 20 <= len(desc) <= 160:
        issues.append(f"description {len(desc)}자 (20~160 필수, 권장 80~120)")
    if len(body) < 400:
        issues.append(f"본문이 너무 짧음 ({len(body)}자)")
    if "{{" in body or "추측:" in body:
        issues.append("치환 안 된 마커나 메모용 '추측:' 표기가 남아 있음")
    if re.search(r"^#\s", body, re.MULTILINE):
        issues.append("본문에 H1이 있음 (제목은 레이아웃이 출력)")
    if series == "검증일지":
        if fm.get("verdict") not in inbox.VERDICTS:
            issues.append("검증일지에 verdict 없음")
        if not fm.get("sources"):
            issues.append("검증일지에 sources 없음")
        for name in SECTIONS.get(lang, []):
            if not re.search(rf"^##\s*{re.escape(name)}", body, re.MULTILINE):
                issues.append(f"검증일지 섹션 누락: ## {name}")
    if series == "실험로그":
        first = next((l for l in body.splitlines() if l.strip() and not l.startswith("#")), "")
        if not first.lstrip().startswith("|"):
            issues.append("실험로그는 매출·비용·시간·실패 표가 서술보다 먼저 와야 함")
    for s in fm.get("sources") or []:
        if not str(s.get("url", "")).startswith(("http://", "https://")):
            issues.append(f"출처 URL 형식 오류: {s.get('url')!r}")
    return issues


# ---------- 발행 ----------

def publish() -> None:
    repo = _repo()
    site = _static_site(repo)
    workdirs = sorted(p for p in OUTPUT_DIR.glob("*") if (p / "meta.json").exists()) if OUTPUT_DIR.exists() else []

    for workdir in workdirs:
        meta_path = workdir / "meta.json"
        meta = _load_meta(meta_path)
        if meta.get("stage") != "drafted":
            continue

        posts, blocked = {}, False
        for lang, fname in meta["files"].items():
            fm, body = _read_draft(workdir / fname)
            issues = quality_gate(fm, body)
            if issues:
                blocked = True
                print(f"[LeaveLab] 발행 보류 {workdir.name}/{fname}:")
                for i in issues:
                    print(f"  - {i}")
            posts[lang] = (fm, body)
        if blocked:
            continue

        # ko·th는 같은 날짜·slug로 발행해 파일명이 같게 유지한다
        today = date.today()
        published = meta.setdefault("published", {})
        try:
            for lang, (fm, body) in posts.items():
                if lang in published:
                    continue
                post = site.Post(
                    title=fm["title"],
                    description=fm["description"],
                    body_md=body,
                    lang=lang,
                    series=fm["series"],
                    date=today,
                    tags=[str(t) for t in fm.get("tags") or []],
                    sources=[site.Source(title=s["title"], url=s["url"]) for s in fm.get("sources") or []],
                    verdict=fm.get("verdict"),
                    slug=meta["slug"],
                    draft=bool(fm.get("draft", False)),
                )
                path = site.publish(post)
                published[lang] = path.relative_to(repo).as_posix()
                _save_meta(meta_path, meta)
                print(f"[LeaveLab] 발행 완료 ({lang}): {published[lang]}")
        except Exception as e:
            meta["last_error"] = str(e)
            _save_meta(meta_path, meta)
            print(f"[LeaveLab] 발행 실패 {workdir.name}: {e}  (파일은 남아 있음, 다시 실행하면 이어서 발행)")
            continue

        inbox.mark_used(inbox.parse(Path(meta["note"])), list(published.values()))
        meta["stage"] = "published"
        meta.pop("last_error", None)
        _save_meta(meta_path, meta)


def _read_draft(path: Path) -> tuple[dict, str]:
    m = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"초안 frontmatter 없음: {path}")
    return yaml.safe_load(m.group(1)) or {}, m.group(2).strip()


# ---------- 현황 ----------

def status() -> None:
    repo = _repo()
    notes = inbox.list_raw(repo / "_inbox")
    print(f"글감(raw) {len(notes)}개")
    for n in notes:
        print(f"  - {n.path.name}  [{n.series}{' ' + n.verdict if n.verdict else ''}] {'/'.join(n.langs)}")
    if OUTPUT_DIR.exists():
        print("초안")
        for p in sorted(OUTPUT_DIR.glob("*/meta.json")):
            m = _load_meta(p)
            err = f"  오류: {m['last_error']}" if m.get("last_error") else ""
            print(f"  - {p.parent.name}  {m.get('stage')}{err}")


def _load_meta(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"stage": None}


def _save_meta(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
