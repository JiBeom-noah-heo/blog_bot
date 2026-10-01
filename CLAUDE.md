# blog_bot — leave-lab 발행 봇

`C:\leave-lab\_inbox\`에 쌓인 경험 메모(형식은 `~/.claude/CLAUDE.md`)를 leave-lab 블로그 글로 바꿔 발행한다.

## 흐름
```
python main.py status          # 글감·초안 현황 (경과 시간, 보류, 오류)
python main.py draft           # raw 메모 → output/leavelab/<메모이름>/{ko,th}.md (발행 안 함)
python main.py publish         # 초안을 지금 바로 품질 게이트 → 사실 검증 → 발행
python main.py auto            # draft + 검토 시간 지난 초안만 publish (예약 실행용)
python main.py hold <메모이름>   # auto 발행에서 제외 (unhold로 해제)
```
- 예약 실행: Windows 작업 스케줄러 `leave-lab blog_bot`이 매일 09:00에 `run_auto.cmd`를 돌린다(로그인 상태에서만 실행). 메모 → 그날 초안 → 다음 날 발행. 로그는 `output/logs/YYYY-MM-DD.log`.
- 안전장치 3단계: 검토 시간(`LEAVE_LAB_REVIEW_HOURS`, 기본 24h) → 품질 게이트(형식) → 사실 검증(`prompts/leavelab_review.md`, 메모와 대조). 하나라도 걸리면 발행하지 않고 `status`에 오류로 남긴다.
- 검토 시간을 0으로 줄이거나 사실 검증을 빼지 않는다(구글 대량 생성 콘텐츠 정책 리스크). 바꾸려면 사용자에게 먼저 묻는다.
- 발행은 leave-lab 저장소의 `blog_bot/publishers/static_site.py`를 불러 쓴다. 발행 규칙은 거기서만 고친다.
- 글 생성·검증은 `claude --print --tools ""`(구독 인증, 도구 없음)로 한다. API 키를 쓰지 않는다.

## 파일
- `src/inbox.py`: 메모 파싱, 검증, `used` 표시
- `src/leavelab.py`: 초안 생성, 품질 게이트, 사실 검증, 발행, auto/hold, 현황
- `src/writer.py`: claude CLI 호출
- `config/leavelab_style.md`, `prompts/leavelab_{ko,th,review}.md`: 문체·형식, 생성·검증 프롬프트
- `output/`: git에서 제외. `meta.json`의 stage(drafted/published), drafted_at, hold로 상태를 이어간다

## 환경변수 (.env)
`LEAVE_LAB_REPO`(필수, `C:\leave-lab`), `LEAVE_LAB_BRANCH`(기본 main), `LEAVE_LAB_REVIEW_HOURS`(기본 24), `LEAVE_LAB_NO_PUSH=1`(커밋까지만), `CLAUDE_CMD`(선택)
