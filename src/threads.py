"""쓰레드(Threads) 유입 실험용 초안 (2026-10-21 결정, 11/4 판정).

발행된 글마다 3줄(질문 · 핵심 숫자와 판정 · 링크)을 output/threads/<날짜>-<slug>.md 에 써 둔다.
자동 게시하지 않는다. 사용자가 복사해 올리고 `threads posted <이름>` 으로 표시한다.
본문은 이미 사실 검증을 통과한 frontmatter(hypothesis·result·verdict)만 조합한다. 새 문장을 만들지 않는다.
링크에는 ?utm_source=threads 를 붙여 Cloudflare Web Analytics 에서 유입을 가른다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
THREADS_DIR = PROJECT_ROOT / "output" / "threads"
SITE_URL = os.getenv("LEAVE_LAB_SITE_URL", "https://leave-lab.com").rstrip("/")
UTM = "?utm_source=threads&utm_medium=social"
LIMIT = 500  # Threads 글자 수 상한
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def post_url(published_path: str) -> str:
    """src/content/log/ko/2026-10-25-slug.md → https://leave-lab.com/log/2026-10-25-slug/"""
    return f"{SITE_URL}/log/{Path(published_path).stem}/"


def compose(fm: dict, url: str) -> str:
    """3줄. 1) 질문(hypothesis, 없으면 제목) 2) 결과 한 줄(result, 검증일지는 판정 앞에) 3) utm 링크."""
    question = str(fm.get("hypothesis") or fm.get("title") or "").strip()
    result = str(fm.get("result") or fm.get("description") or "").strip()
    verdict = str(fm.get("verdict") or "").strip()
    if verdict and verdict not in result:
        result = f"판정 {verdict}. {result}"
    link = url + UTM
    room = LIMIT - len(question) - len(link) - 4  # 빈 줄 2개
    if len(result) > room:
        result = result[: max(room - 1, 0)].rstrip() + "…"
    return "\n\n".join(x for x in (question, result, link) if x)


def draft_path(published_path: str) -> Path:
    return THREADS_DIR / (Path(published_path).stem + ".md")


def write(fm: dict, published_path: str, force: bool = False) -> Path | None:
    """초안 파일을 쓴다. 이미 있으면(사용자가 고쳤을 수 있다) 건드리지 않고 None."""
    path = draft_path(published_path)
    if path.exists() and not force:
        return None
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    url = post_url(published_path)
    head = {
        "post": published_path,
        "url": url,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",  # pending | posted
    }
    body = compose(fm, url)
    path.write_text("---\n" + yaml.safe_dump(head, allow_unicode=True, sort_keys=False) + "---\n" + body + "\n",
                    encoding="utf-8")
    return path


def read(path: Path) -> tuple[dict, str]:
    m = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}, path.read_text(encoding="utf-8").strip()
    return yaml.safe_load(m.group(1)) or {}, m.group(2).strip()


def pending() -> list[tuple[Path, dict, str]]:
    """아직 올리지 않은 초안. 최근 글이 먼저."""
    if not THREADS_DIR.exists():
        return []
    out = []
    for p in sorted(THREADS_DIR.glob("*.md"), reverse=True):
        head, body = read(p)
        if head.get("status", "pending") == "pending":
            out.append((p, head, body))
    return out


def mark_posted(name: str) -> Path:
    path = THREADS_DIR / (name if name.endswith(".md") else name + ".md")
    if not path.exists():
        raise FileNotFoundError(f"쓰레드 초안 없음: {path}")
    head, body = read(path)
    head["status"] = "posted"
    head["posted_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text("---\n" + yaml.safe_dump(head, allow_unicode=True, sort_keys=False) + "---\n" + body + "\n",
                    encoding="utf-8")
    return path
