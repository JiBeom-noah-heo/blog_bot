#!/usr/bin/env python3
"""
leave-lab 블로그 봇: _inbox 메모 → 초안 → 검토 시간 → 품질 게이트 + 사실 검증 → 발행
"""
import os
import sys

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()


def main():
    from src import leavelab

    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    actions = {
        "status": leavelab.status,
        "draft": leavelab.draft,
        "publish": leavelab.publish,
        "auto": leavelab.auto,
    }
    if command in ("hold", "unhold") and arg:
        leavelab.hold(arg, on=command == "hold")
    elif command in actions:
        actions[command]()
    else:
        print("사용법: python main.py [status|draft|publish|auto|hold <초안>|unhold <초안>]")
        print()
        print("  status          - 글감(_inbox raw 메모)과 초안 현황")
        print("  draft           - 글감으로 초안 생성 (output/leavelab/, 발행 안 함)")
        print("  publish         - 초안을 지금 바로 품질 게이트·사실 검증 후 발행 (push = 배포)")
        print("  auto            - draft + 검토 시간(LEAVE_LAB_REVIEW_HOURS, 기본 24h) 지난 초안 발행. 예약 실행용")
        print("  hold <초안>     - 해당 초안을 auto 발행에서 제외 / unhold 로 해제")
        sys.exit(1)


if __name__ == "__main__":
    main()
