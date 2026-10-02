"""leave-lab 파이프라인: _inbox 메모 → 초안 → 검토 시간 → 품질 게이트 + 사실 검증 → 발행.

  draft()    status: raw 메모마다 ko(+th) 초안을 output/leavelab/<메모이름>/ 에 만든다. 발행하지 않는다.
  publish()  초안(사람이 고친 내용 그대로)을 품질 게이트와 사실 검증(메모 대조)에 통과시킨 뒤
             leave-lab 저장소로 발행하고 메모를 status: used 로 바꾼다. push가 곧 배포다.
  auto()     draft() 후, 만든 지 LEAVE_LAB_REVIEW_HOURS(기본 24)시간 지난 초안만 publish(). 예약 실행용.
  hold()     초안을 자동 발행에서 빼거나(on) 되돌린다(off).
  status()   글감·초안 현황 출력.

발행은 leave-lab 저장소의 blog_bot/publishers/static_site.py 를 그대로 쓴다(단일 진실).
환경변수: LEAVE_LAB_REPO (필수), LEAVE_LAB_BRANCH, LEAVE_LAB_NO_PUSH, LEAVE_LAB_REVIEW_HOURS
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime
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
MAX_REVISIONS = 2
BACKLOG_BUFFER = 2  # 원고 글감은 발행 대기 초안이 이만큼 되도록만 미리 만든다
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

    # 작업 세션 메모는 전부 초안으로 만든다. 원고에서 가져온 메모(source 있음)는 순서대로,
    # 발행 대기 초안이 BACKLOG_BUFFER개가 되도록만 채운다.
    def drafted(n):
        return _load_meta(OUTPUT_DIR / n.path.stem / "meta.json").get("stage") in ("drafted", "published")

    fresh = [n for n in notes if not n.meta.get("source") and not drafted(n)]
    backlog = sorted(
        (n for n in notes if n.meta.get("source") and not drafted(n)),
        key=lambda n: n.meta["source"].get("order", 0),
    )
    need = max(0, BACKLOG_BUFFER - _ready_count())
    targets = fresh + backlog[:need]
    if backlog:
        print(f"[LeaveLab] 원고 글감 {len(backlog)}개 대기, 이번에 초안 {min(need, len(backlog))}개")

    for note in targets:
        workdir = OUTPUT_DIR / note.path.stem
        meta_path = workdir / "meta.json"
        meta = _load_meta(meta_path)

        issues = inbox.validate(note)
        if issues:
            print(f"[LeaveLab] 메모 보완 필요 {note.path.name}:")
            for i in issues:
                print(f"  - {i}")
            continue

        workdir.mkdir(parents=True, exist_ok=True)
        meta.update({"note": str(note.path), "slug": note.slug, "series": note.series, "files": {},
                     "backlog_order": (note.meta.get("source") or {}).get("order")})
        try:
            for lang in note.langs:
                print(f"[LeaveLab] 초안 생성 중 ({lang}): {note.path.name}")
                out = _parse_response(call_claude(_prompt(note, lang), timeout=300))
                out = _self_review(note, lang, out)
                fm = _frontmatter(note, lang, out)
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
        meta["drafted_at"] = datetime.now().isoformat(timespec="seconds")
        _save_meta(meta_path, meta)
        print(f"[LeaveLab] 초안 완료 → {workdir}")


def _prompt(note: inbox.Note, lang: str) -> str:
    style = (PROJECT_ROOT / "config" / "leavelab_style.md").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / "prompts" / f"leavelab_{lang}.md").read_text(encoding="utf-8")
    return (
        template
        .replace("{{STYLE_GUIDE}}", style)
        .replace("{{SERIES}}", note.series)
        .replace("{{NOTE}}", note.text)
    )


def _frontmatter(note: inbox.Note, lang: str, out: dict) -> dict:
    return {
        "title": out["title"],
        "description": out["description"],
        "date": date.today(),
        "lang": lang,
        "series": note.series,
        "verdict": note.verdict,
        # 메모 태그는 한국어라 th 글에는 생성된 태그를 쓴다
        "tags": (note.tags if lang == "ko" else []) or out["tags"],
        "sources": note.sources,
        "draft": False,
    }


def _self_review(note: inbox.Note, lang: str, out: dict) -> dict:
    """초안 단계에서 품질 게이트·사실 검증을 한 번 돌리고, 지적이 있으면 그 부분만 고쳐 쓴다. 발행 때 다시 검증한다."""
    fm = _frontmatter(note, lang, out)
    issues = quality_gate(fm, out["content"]) + fact_check(note.text, fm, out["content"])
    if not issues:
        return out
    print(f"  [자체 검토] {lang}: {len(issues)}건 지적 → 고쳐 쓰는 중")
    return _revise(note.text, out, issues)


def _revise(note_text: str, out: dict, issues: list[str]) -> dict:
    """지적된 부분만 고친 초안을 돌려준다. 실패하면 원래 초안을 돌려준다."""
    style = (PROJECT_ROOT / "config" / "leavelab_style.md").read_text(encoding="utf-8")
    template = (PROJECT_ROOT / "prompts" / "leavelab_revise.md").read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{{STYLE_GUIDE}}", style)
        .replace("{{NOTE}}", note_text)
        .replace("{{ISSUES}}", "\n".join(f"- {i}" for i in issues))
        .replace("{{TITLE}}", out["title"])
        .replace("{{DESCRIPTION}}", out["description"])
        .replace("{{TAGS}}", ", ".join(out["tags"]))
        .replace("{{CONTENT}}", out["content"])
    )
    revised = _parse_response(call_claude(prompt, timeout=300))
    return revised if revised["content"] else out


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

def fact_check(note_text: str, fm: dict, body: str) -> list[str]:
    """초안을 원본 메모와 대조한다. 메모에 없는 사실·숫자·출처, 민감정보, 고유명사를 잡는다."""
    template = (PROJECT_ROOT / "prompts" / "leavelab_review.md").read_text(encoding="utf-8")
    draft_text = f"제목: {fm.get('title')}\n설명: {fm.get('description')}\n\n{body}"
    out = call_claude(template.replace("{{NOTE}}", note_text).replace("{{DRAFT}}", draft_text), timeout=300).strip()
    if out.splitlines() and out.splitlines()[0].strip().upper() == "OK":
        return []
    issues = [l.lstrip("-• ").strip() for l in out.splitlines() if l.strip().startswith(("-", "•"))]
    return issues or [f"사실 검증 응답 해석 불가: {out[:200]}"]


def auto() -> None:
    draft()
    publish(
        min_age_hours=float(os.getenv("LEAVE_LAB_REVIEW_HOURS", "24")),
        limit=int(os.getenv("LEAVE_LAB_MAX_PUBLISH_PER_RUN", "1")),
    )


def _ready_count() -> int:
    """발행 대기 중인(보류 아닌) 초안 수."""
    if not OUTPUT_DIR.exists():
        return 0
    return sum(
        1 for p in OUTPUT_DIR.glob("*/meta.json")
        if (m := _load_meta(p)).get("stage") == "drafted" and not m.get("hold")
    )


def hold(name: str, on: bool = True) -> None:
    meta_path = OUTPUT_DIR / name / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"초안 없음: {name}  (python main.py status 로 이름 확인)")
    meta = _load_meta(meta_path)
    meta["hold"] = on
    _save_meta(meta_path, meta)
    print(f"[LeaveLab] {name}: {'자동 발행 보류' if on else '보류 해제'}")


def _age_hours(meta: dict) -> float:
    at = meta.get("drafted_at")
    return (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 3600 if at else 0.0


def _queue_key(workdir: Path):
    """작업 세션 메모가 먼저, 원고 글감은 원고 순서대로, 같으면 먼저 만든 초안부터."""
    m = _load_meta(workdir / "meta.json")
    order = m.get("backlog_order")
    return (order is not None, order or 0, m.get("drafted_at") or "")


def publish(min_age_hours: float = 0, limit: int | None = None) -> None:
    repo = _repo()
    site = _static_site(repo)
    workdirs = sorted(
        (p for p in OUTPUT_DIR.glob("*") if (p / "meta.json").exists()) if OUTPUT_DIR.exists() else [],
        key=_queue_key,
    )
    done = 0

    for workdir in workdirs:
        if limit is not None and done >= limit:
            print(f"[LeaveLab] 오늘 발행 상한({limit}편) 도달, 나머지는 다음 실행에")
            break
        meta_path = workdir / "meta.json"
        meta = _load_meta(meta_path)
        if meta.get("stage") != "drafted":
            continue
        if min_age_hours and meta.get("hold"):
            print(f"[LeaveLab] 보류 중이라 건너뜀: {workdir.name}")
            continue
        # 예약 실행은 하루 한 번이라 실행 시각이 몇 초만 당겨져도 하루가 밀린다. 1시간 여유를 둔다.
        if min_age_hours and _age_hours(meta) + 1 < min_age_hours:
            print(f"[LeaveLab] 검토 시간 대기 ({_age_hours(meta):.1f}/{min_age_hours:g}h): {workdir.name}")
            continue

        note_path = Path(meta["note"])
        note_text = note_path.read_text(encoding="utf-8")
        posts, blocked = {}, {}
        for lang, fname in meta["files"].items():
            fm, body = _read_draft(workdir / fname)
            issues = quality_gate(fm, body)
            if not issues:
                print(f"[LeaveLab] 사실 검증 중 ({lang}): {workdir.name}")
                issues = fact_check(note_text, fm, body)
            if issues:
                blocked[lang] = issues
                print(f"[LeaveLab] 발행 보류 {workdir.name}/{fname}:")
                for i in issues:
                    print(f"  - {i}")
            posts[lang] = (fm, body)
        if blocked:
            # 지적대로 자동으로 고쳐 쓰고 검토 시간을 다시 시작한다. MAX_REVISIONS를 넘기면 사람 몫으로 남긴다.
            if meta.get("revisions", 0) < MAX_REVISIONS:
                for lang, issues in blocked.items():
                    fm, body = posts[lang]
                    out = {"title": fm.get("title", ""), "description": fm.get("description", ""),
                           "tags": [str(t) for t in fm.get("tags") or []], "content": body}
                    out = _revise(note_text, out, issues)
                    fm.update(title=out["title"], description=out["description"])
                    (workdir / meta["files"][lang]).write_text(_render(fm, out["content"]), encoding="utf-8")
                meta["revisions"] = meta.get("revisions", 0) + 1
                meta["drafted_at"] = datetime.now().isoformat(timespec="seconds")
                meta["last_error"] = f"검증 미통과 → 자동 수정 {meta['revisions']}회차. 검토 시간 후 다시 검증"
                print(f"[LeaveLab] 자동 수정함 ({meta['revisions']}/{MAX_REVISIONS}): {workdir.name}")
            else:
                meta["last_error"] = "자동 수정 한도 초과 — 초안을 직접 고친 뒤 publish"
            _save_meta(meta_path, meta)
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

        inbox.mark_used(inbox.parse(note_path), list(published.values()))
        done += 1
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
            extra = []
            if m.get("stage") == "drafted":
                extra.append(f"{_age_hours(m):.1f}h 경과")
            if m.get("hold"):
                extra.append("보류")
            if m.get("last_error"):
                extra.append(f"오류: {m['last_error']}")
            print(f"  - {p.parent.name}  {m.get('stage')}  {' · '.join(extra)}".rstrip())


def _load_meta(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"stage": None}


def _save_meta(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
