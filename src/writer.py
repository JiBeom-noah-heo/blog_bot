import os
import shutil
import subprocess


def _find_claude_cmd() -> str:
    cmd = os.getenv("CLAUDE_CMD")
    if cmd:
        return cmd
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if found:
        return found
    raise FileNotFoundError(
        "claude CLI를 찾을 수 없습니다. "
        "CLAUDE_CMD 환경변수를 설정하거나 PATH에 claude를 추가하세요."
    )


def call_claude(prompt: str, timeout: int = 180) -> str:
    """claude CLI(--print)에 프롬프트를 넣고 응답 텍스트를 돌려준다. 구독 인증을 쓰도록 API 키는 뺀다."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    result = subprocess.run(
        [_find_claude_cmd(), "--print"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI 오류: {result.stderr[:300]}")

    return result.stdout
