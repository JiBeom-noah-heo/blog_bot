"""leave-lab 파이프라인: _inbox 메모 → 초안 → 검토 시간 → 품질 게이트 + 사실 검증 → 발행.

  draft()    status: raw 메모마다 ko(+th) 초안을 output/leavelab/<메모이름>/ 에 만든다. 발행하지 않는다.
  publish()  초안(사람이 고친 내용 그대로)을 품질 게이트와 사실 검증(메모 대조)에 통과시킨 뒤
             leave-lab 저장소로 발행하고 메모를 status: used 로 바꾼다. push가 곧 배포다.
  auto()     draft() 후, 만든 지 LEAVE_LAB_REVIEW_HOURS(기본 24)시간 지난 초안만 publish(). 예약 실행용.
  hold()     초안을 자동 발행에서 빼거나(on) 되돌린다(off).
  status()   글감·초안 현황 출력.
  threads_cmd() 발행 글의 쓰레드 3줄 초안(output/threads/) 보기·만들기·올림 표시. 자동 게시 아님.

발행은 leave-lab 저장소의 blog_bot/publishers/static_site.py 를 그대로 쓴다(단일 진실).
환경변수: LEAVE_LAB_REPO (필수), LEAVE_LAB_BRANCH, LEAVE_LAB_NO_PUSH, LEAVE_LAB_REVIEW_HOURS
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from . import inbox, threads
from .writer import call_claude

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "leavelab"

# 검증일지 본문 고정 형식 (leave-lab CLAUDE.md)
SECTIONS = {
    "ko": ["질문", "막힌 지점", "확인한 것", "판정", "다음 행동"],
    "th": ["คำถาม", "จุดที่ติด", "สิ่งที่ตรวจสอบ", "ผลการตัดสิน", "ขั้นต่อไป"],
}
MAX_REVISIONS = 2
# 원고 글감은 발행 대기 초안이 이만큼 되도록만 미리 만든다. 새 초안은 24시간 검토 뒤에야 나가므로
# 하루 발행 편수의 2배는 있어야 매일 그 편수가 끊기지 않는다.
BACKLOG_BUFFER = 2 * int(os.getenv("LEAVE_LAB_MAX_PUBLISH_PER_RUN", "1"))
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

    pending = _pending(repo)
    fresh = [n for n in notes if _order(n, pending) is None and not drafted(n)]
    backlog = sorted(
        (n for n in notes if _order(n, pending) is not None and not drafted(n)),
        key=lambda n: _order(n, pending),
    )
    need = max(0, BACKLOG_BUFFER - _ready_count(repo))
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
                     "backlog_order": _order(note, pending)})
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


# 홈 탭 분류. leave-lab src/ui.ts의 CATEGORY_HINTS와 같은 낱말표를 쓴다(앞 항목 우선). 못 찾으면 비워 두고 사이트가 추정한다.
CATEGORY_HINTS = [
    ("육아", ["육아", "휴직", "아이", "돌봄", "어린이집"]),
    ("수익", ["위탁판매", "매출", "수익", "결산", "판매", "쇼피", "스마트스토어", "광고", "수수료", "애드센스", "도매", "가격", "주문", "전자책", "강의"]),
    ("자동화", ["자동화", "봇", "파이프라인", "예약", "스크립트", "크롤링", "스케줄"]),
    ("개발", ["개발", "api", "코드", "계산기", "배포", "빌드", "버그", "정적사이트", "클로드코드", "디자인", "앱"]),
]
CARD_FIELDS = ("hypothesis", "method", "result")  # 글 상단 EXPERIMENT CARD. 생성 응답의 HYPOTHESIS/METHOD/RESULT 줄


def _category(note: inbox.Note, title: str = "") -> str | None:
    hay = " ".join([*note.tags, title, note.kind]).lower()
    for cat, words in CATEGORY_HINTS:
        if any(w in hay for w in words):
            return cat
    return None


def _frontmatter(note: inbox.Note, lang: str, out: dict) -> dict:
    fm = {
        "title": out["title"],
        "description": out["description"],
        "date": date.today(),
        "lang": lang,
        "series": note.series,
        "verdict": note.verdict,
        "category": _category(note, out["title"]),
        "project": str(note.meta.get("project") or "").strip() or None,  # 사이트가 연재(시리즈)로 묶는 기준
        # 메모 태그는 한국어라 th 글에는 생성된 태그를 쓴다
        "tags": (note.tags if lang == "ko" else []) or out["tags"],
        "sources": note.sources,
    }
    for k in CARD_FIELDS:
        fm[k] = (out.get(k) or "").strip() or None
    fm["draft"] = False
    return fm


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
        .replace("{{HYPOTHESIS}}", out.get("hypothesis", ""))
        .replace("{{METHOD}}", out.get("method", ""))
        .replace("{{RESULT}}", out.get("result", ""))
        .replace("{{CONTENT}}", out["content"])
    )
    revised = _parse_response(call_claude(prompt, timeout=300))
    if not revised["content"]:
        return out
    for k in CARD_FIELDS:  # 고쳐 쓴 응답이 카드 줄을 빠뜨리면 원래 값을 유지한다
        revised[k] = revised.get(k) or out.get(k, "")
    return revised


def _parse_response(text: str) -> dict:
    def line(key):
        # 값이 비어 있으면 빈 문자열이어야 한다. \s*는 줄바꿈까지 먹어서 다음 줄의 표시어(METHOD: …)를 값으로 가져왔다.
        m = re.search(rf"^{key}:[ \t]*(\S.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    content = re.search(r"^CONTENT:\s*\n(.*)", text, re.MULTILINE | re.DOTALL)
    return {
        "title": line("TITLE"),
        "description": line("DESCRIPTION"),
        "tags": [t.strip() for t in line("TAGS").split(",") if t.strip()],
        "hypothesis": line("HYPOTHESIS"),
        "method": line("METHOD"),
        "result": line("RESULT"),
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
    for k in CARD_FIELDS:
        v = fm.get(k)
        if v and len(str(v)) > 160:
            issues.append(f"실험 카드 {k}가 너무 김 ({len(str(v))}자, 한 문장 60자 안팎)")
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
    issues += completeness_issues(body)
    return issues


SUMMARY_PARTS = ("**측정한 것**", "**확실한 것**", "**아직 모르는 것**")
SENTENCE_END = tuple('.!?:)"”’」』`*…')


def completeness_issues(body: str) -> list[str]:
    """출력 형식이 깨진 초안을 잡는다: 잘린 표, 빠진 섹션, 끊긴 문장, 짝 안 맞는 괄호·마크다운."""
    issues = []
    if not re.search(r"^##\s*(다음 행동|ขั้นต่อไป)", body, re.MULTILINE):
        issues.append("필수 섹션 누락: ## 다음 행동")
    m = re.search(r"^##\s*(정리|สรุป)\s*$(.*)", body, re.MULTILINE | re.DOTALL)
    if not m:
        issues.append("필수 섹션 누락: ## 정리")
    elif body.count("## ") and m.group(1) == "정리":
        for part in SUMMARY_PARTS:
            if part not in m.group(2):
                issues.append(f"정리 블록에 {part} 없음")

    lines, in_code = body.splitlines(), False
    table: list[str] = []

    def check_table(rows: list[str]) -> None:
        if len(rows) < 2 or not re.match(r"^\|\s*:?-{3,}", rows[1].strip()):
            issues.append(f"표 머리글·구분선이 깨짐: {rows[0][:40]}")
            return
        widths = {r.strip().strip("|").count("|") for r in rows}
        if len(widths) > 1:
            issues.append(f"표의 열 개수가 행마다 다름: {rows[0][:40]}")

    for line in lines + [""]:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("|"):
            table.append(stripped)
            continue
        if table:
            check_table(table)
            table = []
        if not stripped or stripped.startswith(("#", "-", "*", ">", "※")) or re.match(r"^\d+\.", stripped):
            continue
        if not stripped.endswith(SENTENCE_END) and not stripped.endswith(("다", "요")):
            issues.append(f"문장이 끝나지 않음: …{stripped[-30:]}")
    if in_code:
        issues.append("코드 블록(```)이 닫히지 않음")
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    if text.count("`") % 2:
        issues.append("인라인 코드(`) 짝이 맞지 않음")
    if text.count("**") % 2:
        issues.append("굵게(**) 짝이 맞지 않음")
    for o, c in ("()", "[]", "（）"):
        if text.count(o) != text.count(c):
            issues.append(f"괄호 {o} 짝이 맞지 않음")
    return issues


# ---------- 발행 ----------

def fact_check(note_text: str, fm: dict, body: str) -> list[str]:
    """초안을 원본 메모와 대조한다. 메모에 없는 사실·숫자·출처, 민감정보, 고유명사를 잡는다."""
    template = (PROJECT_ROOT / "prompts" / "leavelab_review.md").read_text(encoding="utf-8")
    card = "".join(f"{label}: {fm[k]}\n" for k, label in
                   (("hypothesis", "가설"), ("method", "방법"), ("result", "결과")) if fm.get(k))
    draft_text = f"제목: {fm.get('title')}\n설명: {fm.get('description')}\n{card}\n{body}"
    out = call_claude(template.replace("{{NOTE}}", note_text).replace("{{DRAFT}}", draft_text), timeout=300).strip()
    if out.splitlines() and out.splitlines()[0].strip().upper() == "OK":
        return []
    issues = [l.lstrip("-• ").strip() for l in out.splitlines() if l.strip().startswith(("-", "•"))]
    return issues or [f"사실 검증 응답 해석 불가: {out[:200]}"]


FACT_CHECK_PASSES = 2  # 발행 전 사실 검증은 연속으로 이만큼 통과해야 한다(AI 검증의 실행마다 편차 대비)


def fact_check_twice(note_text: str, fm: dict, body: str, label: str = "") -> list[str]:
    """사실 검증을 FACT_CHECK_PASSES번 연속으로 돌린다. 한 번이라도 지적이 나오면 그 지적을 돌려준다."""
    for i in range(FACT_CHECK_PASSES):
        print(f"[LeaveLab] 사실 검증 {i + 1}/{FACT_CHECK_PASSES}: {label}")
        issues = fact_check(note_text, fm, body)
        if issues:
            return issues
    return []


def auto() -> None:
    """예약 실행. 초안을 만들고, 사용자가 approve한 초안 중 검토 시간이 지난 것만 발행한다
    (2026-10-12 E4 브리프: 사람 검토 없이 발행한 글 0편)."""
    draft()
    publish(
        min_age_hours=float(os.getenv("LEAVE_LAB_REVIEW_HOURS", "24")),
        limit=int(os.getenv("LEAVE_LAB_MAX_PUBLISH_PER_RUN", "1")),
    )


def _pending(repo: Path) -> list[tuple[str, float, Path]]:
    """아직 발행 안 된(raw·hold) 원고 글감: (프로젝트, 순서, 경로)."""
    out = []
    for p in (repo / "_inbox").glob("*.md"):
        try:
            n = inbox.parse(p)
        except Exception:
            continue
        src = n.meta.get("source")
        if src and n.meta.get("status") in ("raw", "hold"):
            out.append((str(n.meta.get("project", "")), float(src.get("order", 0)), p))
    return out


def _order(note: inbox.Note, pending) -> float | None:
    """발행 순서. 원고 글감(source 있음)만 source.order로 줄을 선다.
    작업 세션 메모는 None: 같은 프로젝트에 원고 글감이 남아 있어도 기다리지 않고 바로 초안·발행한다
    (2026-10-12 사용자 결정. 그때그때 생긴 기록이 원고 28편 뒤로 밀려 한 달 뒤에 나가는 것을 막는다)."""
    src = note.meta.get("source")
    if src:
        return float(src.get("order", 0))
    return None


def _blocked_by(note: inbox.Note, pending) -> Path | None:
    """같은 프로젝트에서 앞 순서 글감이 아직 발행 전이면 그 경로."""
    order = _order(note, pending)
    if order is None:
        return None
    project = str(note.meta.get("project", ""))
    earlier = [(o, p) for proj, o, p in pending if proj == project and o < order and p != note.path]
    return min(earlier)[1] if earlier else None


def _ready_count(repo: Path) -> int:
    """지금 순서상 발행할 수 있는(보류·순서 대기 아닌) 초안 수."""
    if not OUTPUT_DIR.exists():
        return 0
    pending = _pending(repo)
    count = 0
    for p in OUTPUT_DIR.glob("*/meta.json"):
        m = _load_meta(p)
        if m.get("stage") != "drafted" or m.get("hold"):
            continue
        try:
            if _blocked_by(inbox.parse(Path(m["note"])), pending):
                continue
        except Exception:
            continue
        count += 1
    return count


def hold(name: str, on: bool = True) -> None:
    meta_path = OUTPUT_DIR / name / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"초안 없음: {name}  (python main.py status 로 이름 확인)")
    meta = _load_meta(meta_path)
    meta["hold"] = on
    _save_meta(meta_path, meta)
    print(f"[LeaveLab] {name}: {'자동 발행 보류' if on else '보류 해제'}")


def approve(name: str, on: bool = True) -> None:
    """사용자 승인. 제목·메타 설명·카드·본문 수치를 직접 확인한 뒤 실행한다. 승인 뒤 초안 파일을 고치면 승인이 풀린다."""
    meta_path = OUTPUT_DIR / name / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"초안 없음: {name}  (python main.py status 로 이름 확인)")
    meta = _load_meta(meta_path)
    if on:
        if meta.get("stage") != "drafted":
            raise SystemExit(f"{name}: 초안 상태가 아니라({meta.get('stage')}) 승인할 수 없다")
        review(name)
        meta["approved_at"] = datetime.now().isoformat(timespec="seconds")
        meta.pop("hold", None)
    else:
        meta.pop("approved_at", None)
    _save_meta(meta_path, meta)
    print(f"[LeaveLab] {name}: {'승인 — 검토 시간이 지나면 다음 실행에서 발행' if on else '승인 취소'}")


def _drafted() -> list[tuple[Path, dict]]:
    """초안 상태(drafted)인 작업 폴더와 meta를 발행 순서대로."""
    out = []
    for d in sorted(OUTPUT_DIR.glob("*"), key=_queue_key):
        if (d / "meta.json").exists():
            meta = _load_meta(d / "meta.json")
            if meta.get("stage") == "drafted":
                out.append((d, meta))
    return out


def approve_all(on: bool = True) -> None:
    """승인 대기 초안(승인 안 됨·승인 뒤 수정됨)을 한 번에 승인한다. hold 초안은 건너뛴다.
    `review`로 전부 읽은 뒤에만 쓴다. 게이트는 그대로다(검토 시간·품질 게이트·사실 검증 2회·하루 상한).
    on=False 면 아직 발행되지 않은 승인을 모두 취소한다."""
    drafted = _drafted()
    if on:
        targets = [(d, m) for d, m in drafted if not m.get("hold") and _approval(d, m)]
        held = [d.name for d, m in drafted if m.get("hold")]
    else:
        targets = [(d, m) for d, m in drafted if m.get("approved_at")]
        held = []
    if not targets:
        print("[LeaveLab] " + ("승인 대기 초안 없음" if on else "승인된 미발행 초안 없음"))
        return
    now = datetime.now().isoformat(timespec="seconds")
    for d, meta in targets:
        fm, _ = _read_draft(d / next(iter(meta["files"].values())))
        if on:
            meta["approved_at"] = now
        else:
            meta.pop("approved_at", None)
        _save_meta(d / "meta.json", meta)
        print(f"[LeaveLab] {'승인' if on else '승인 취소'}: {d.name}  · {fm.get('title')}")
    for name in held:
        print(f"[LeaveLab] hold 상태라 건너뜀: {name}  (unhold 뒤 approve)")
    if on:
        per_run = int(os.getenv("LEAVE_LAB_MAX_PUBLISH_PER_RUN", "1"))
        total = sum(1 for d, m in _drafted() if not _approval(d, m))
        days = (total + per_run - 1) // per_run
        print(f"[LeaveLab] {len(targets)}편 승인. 승인된 미발행 초안 {total}편 → 하루 {per_run}편씩 약 {days}일에 걸쳐 발행. "
              f"빼려면 unapprove <초안>, 전부 되돌리려면 unapprove all")
    else:
        print(f"[LeaveLab] {len(targets)}편 승인 취소")


def _approval(workdir: Path, meta: dict) -> str | None:
    """발행 가능하면 None, 아니면 이유. 승인 뒤에 초안 파일이 바뀌었으면 다시 승인해야 한다."""
    at = meta.get("approved_at")
    if not at:
        return "승인 대기 (python main.py review/approve <초안>)"
    approved = datetime.fromisoformat(at).timestamp()
    changed = [f for f in meta.get("files", {}).values() if (workdir / f).stat().st_mtime > approved + 1]
    if changed:
        return f"승인 뒤 수정됨({', '.join(changed)}) — 다시 approve"
    return None


def review(name: str | None = None) -> None:
    """승인 전에 볼 것을 한 화면에 모은다: 제목·메타 설명·카드·출처·숫자. 이름이 없으면 승인 대기 초안 전부."""
    if not name:
        waiting = [d for d, m in _drafted() if _approval(d, m)]
        if not waiting:
            print("[LeaveLab] 승인 대기 초안 없음")
            return
        print(f"[LeaveLab] 승인 대기 {len(waiting)}편\n")
        for d in waiting:
            review(d.name)
            print()
        return
    workdir = OUTPUT_DIR / name
    meta = _load_meta(workdir / "meta.json")
    if not meta.get("files"):
        raise SystemExit(f"초안 없음: {name}")
    for lang, fname in meta["files"].items():
        fm, body = _read_draft(workdir / fname)
        print(f"==== {name}/{fname}")
        print(f"제목({len(fm.get('title', ''))}자): {fm.get('title')}")
        print(f"설명({len(fm.get('description', ''))}자): {fm.get('description')}")
        print(f"분류: {fm.get('series')} · {fm.get('category')} · project={fm.get('project')} · verdict={fm.get('verdict')}")
        print(f"태그: {', '.join(str(t) for t in fm.get('tags') or [])}")
        for k in CARD_FIELDS:
            if fm.get(k):
                print(f"{k}: {fm[k]}")
        for src in fm.get("sources") or []:
            print(f"출처: {src.get('title')}  {src.get('url')}")
        nums = sorted(set(re.findall(r"\d[\d,.]*(?:%|원|건|시간|분|초|개|편|일|h|만|억|배)", body)))
        print(f"본문 {len(body)}자 · 숫자 표현 {len(nums)}개: {' / '.join(nums[:30])}")
        print(f"파일: {workdir / fname}")
    print("확인할 것: 제목·메타 설명이 본문과 맞나 / 숫자가 메모와 맞나 / 내가 직접 만든 숫자·화면·결정이 1개 이상 있나")
    print("         / 상품명·브랜드·가족 정보가 없나. 고칠 게 있으면 파일을 고친 뒤 approve.")


def _age_hours(meta: dict) -> float:
    at = meta.get("drafted_at")
    return (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 3600 if at else 0.0


def _queue_key(workdir: Path):
    """작업 세션 메모가 먼저(최근 메모부터. 그때그때의 기록이 묵은 기록보다 먼저 나간다),
    원고 글감은 원고 순서대로, 같으면 먼저 만든 초안부터."""
    m = _load_meta(workdir / "meta.json")
    order = m.get("backlog_order")
    name = Path(m.get("note") or "").name
    if order is None:
        try:
            recent = -datetime.strptime(name[:10], "%Y-%m-%d").toordinal()
        except ValueError:
            recent = 0
        return (False, recent, name, m.get("drafted_at") or "")
    return (True, order, name, m.get("drafted_at") or "")


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
        if meta.get("hold"):
            print(f"[LeaveLab] 보류 중이라 건너뜀: {workdir.name}")
            continue
        why = _approval(workdir, meta)
        if why:
            print(f"[LeaveLab] {why}: {workdir.name}")
            continue
        # 예약 실행은 하루 한 번이라 실행 시각이 몇 초만 당겨져도 하루가 밀린다. 1시간 여유를 둔다.
        if min_age_hours and _age_hours(meta) + 1 < min_age_hours:
            print(f"[LeaveLab] 검토 시간 대기 ({_age_hours(meta):.1f}/{min_age_hours:g}h): {workdir.name}")
            continue

        note_path = Path(meta["note"])
        before = _blocked_by(inbox.parse(note_path), _pending(repo))
        if before:
            print(f"[LeaveLab] 순서 대기 (앞 글감 {before.name} 먼저): {workdir.name}")
            continue
        note_text = note_path.read_text(encoding="utf-8")
        # 걸리면 지적대로 고쳐 쓰고 같은 실행 안에서 바로 다시 검증한다(검토 시간을 다시 세지 않는다).
        # MAX_REVISIONS를 넘기면 사람 몫으로 남긴다.
        while True:
            posts, blocked = _check(workdir, meta, note_text)
            if not blocked:
                break
            if meta.get("revisions", 0) >= MAX_REVISIONS:
                meta["last_error"] = "자동 수정 한도 초과 — 초안을 직접 고친 뒤 publish"
                _save_meta(meta_path, meta)
                break
            for lang, issues in blocked.items():
                fm, body = posts[lang]
                out = {"title": fm.get("title", ""), "description": fm.get("description", ""),
                       "tags": [str(t) for t in fm.get("tags") or []], "content": body,
                       **{k: str(fm.get(k) or "") for k in CARD_FIELDS}}
                out = _revise(note_text, out, issues)
                fm.update(title=out["title"], description=out["description"],
                          **{k: (out.get(k) or "").strip() or None for k in CARD_FIELDS})
                (workdir / meta["files"][lang]).write_text(_render(fm, out["content"]), encoding="utf-8")
            meta["revisions"] = meta.get("revisions", 0) + 1
            meta["last_error"] = f"검증 미통과 → 자동 수정 {meta['revisions']}회차"
            _save_meta(meta_path, meta)
            print(f"[LeaveLab] 자동 수정함 ({meta['revisions']}/{MAX_REVISIONS}), 다시 검증: {workdir.name}")
        if blocked:
            continue
        if _approval(workdir, meta):
            meta["last_error"] = "검증 지적대로 자동 수정함 — 고친 글을 review 하고 다시 approve"
            _save_meta(meta_path, meta)
            print(f"[LeaveLab] 자동 수정 뒤라 승인을 다시 받아야 함: {workdir.name}")
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
                    category=fm.get("category"),
                    # project 필드가 생기기 전에 만든 초안은 메모에서 직접 가져온다
                    project=fm.get("project") or str(inbox.parse(note_path).meta.get("project") or "").strip() or None,
                    hypothesis=fm.get("hypothesis"),
                    method=fm.get("method"),
                    result=fm.get("result"),
                )
                path = site.publish(post)
                published[lang] = path.relative_to(repo).as_posix()
                _save_meta(meta_path, meta)
                print(f"[LeaveLab] 발행 완료 ({lang}): {published[lang]}")
                if lang == "ko":
                    try:
                        tp = threads.write(fm, published[lang])
                        if tp:
                            print(f"[LeaveLab] 쓰레드 초안: {tp.relative_to(PROJECT_ROOT).as_posix()}  (복사해 올린 뒤 threads posted {tp.stem})")
                    except Exception as e:  # 발행은 끝났다. 초안은 threads backfill 로 다시 만들 수 있다
                        print(f"[LeaveLab] 쓰레드 초안 실패(발행은 완료): {e}")
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


def _check(workdir: Path, meta: dict, note_text: str) -> tuple[dict, dict]:
    """언어별 초안을 품질 게이트·사실 검증에 통과시킨다. (posts, blocked) 반환."""
    posts, blocked = {}, {}
    for lang, fname in meta["files"].items():
        fm, body = _read_draft(workdir / fname)
        issues = quality_gate(fm, body)
        if not issues:
            issues = fact_check_twice(note_text, fm, body, f"{workdir.name} ({lang})")
        if issues:
            blocked[lang] = issues
            print(f"[LeaveLab] 발행 보류 {workdir.name}/{fname}:")
            for i in issues:
                print(f"  - {i}")
        posts[lang] = (fm, body)
    return posts, blocked


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
                extra.append("승인됨" if not _approval(p.parent, m) else _approval(p.parent, m).split(" (")[0])
            if m.get("hold"):
                extra.append("보류")
            if m.get("last_error"):
                extra.append(f"오류: {m['last_error']}")
            print(f"  - {p.parent.name}  {m.get('stage')}  {' · '.join(extra)}".rstrip())


# ---------- 쓰레드 초안 ----------

def threads_cmd(sub: str | None = None, arg: str | None = None) -> None:
    """threads            올리지 않은 쓰레드 초안을 본문째 보여 준다
       threads backfill [N]  최근 N일(기본 3) 안에 발행된 글의 초안을 만든다(있는 것은 건드리지 않음)
       threads posted <이름> 올렸다고 표시한다"""
    if sub == "posted":
        if not arg:
            raise SystemExit("사용법: threads posted <이름>")
        p = threads.mark_posted(arg)
        print(f"[LeaveLab] 올림 표시: {p.name}")
        return
    if sub == "backfill":
        days = int(arg) if arg else 3
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        made = skipped = 0
        for p in sorted(OUTPUT_DIR.glob("*/meta.json")):
            meta = _load_meta(p)
            ko = (meta.get("published") or {}).get("ko")
            if meta.get("stage") != "published" or not ko:
                continue
            if Path(ko).stem[:10] < cutoff:
                continue
            fm, _ = _read_draft(p.parent / meta["files"]["ko"])
            out = threads.write(fm, ko)
            if out:
                made += 1
                print(f"[LeaveLab] 쓰레드 초안: {out.name}")
            else:
                skipped += 1
        print(f"[LeaveLab] 최근 {days}일 발행 글 → 초안 {made}개 생성, {skipped}개는 이미 있음")
        return
    items = threads.pending()
    if not items:
        print("[LeaveLab] 올릴 쓰레드 초안 없음")
        return
    print(f"[LeaveLab] 올릴 쓰레드 초안 {len(items)}개 (올린 뒤: threads posted <이름>)")
    for p, head, body in items:
        print(f"\n==== {p.stem}  ({len(body)}자)")
        print(body)


def _load_meta(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"stage": None}


def _save_meta(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
