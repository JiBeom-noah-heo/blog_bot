#!/usr/bin/env python3
"""
leave-lab 블로그 봇: _inbox 메모 → 초안 → (사람 검수) → 발행
"""
import os
import sys

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()


def main():
    from src import leavelab

    actions = {"draft": leavelab.draft, "publish": leavelab.publish, "status": leavelab.status}
    command = sys.argv[1] if len(sys.argv) > 1 else "status"

    if command not in actions:
        print(f"사용법: python main.py [{'|'.join(actions)}]")
        print()
        print("  status   - 글감(_inbox raw 메모)과 초안 현황")
        print("  draft    - 글감으로 초안 생성 (output/leavelab/, 발행 안 함)")
        print("  publish  - 검수한 초안을 품질 게이트 후 발행 (push = 배포)")
        sys.exit(1)

    actions[command]()


if __name__ == "__main__":
    main()
