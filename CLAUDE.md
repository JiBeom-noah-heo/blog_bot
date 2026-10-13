# blog_bot — leave-lab 발행 봇

`C:\leave-lab\_inbox\`에 쌓인 경험 메모(형식은 `~/.claude/CLAUDE.md`)를 leave-lab 블로그 글로 바꿔 발행한다.

## 흐름
```
python main.py status          # 글감·초안 현황 (경과 시간, 보류, 오류)
python main.py draft           # raw 메모 → output/leavelab/<메모이름>/{ko,th}.md (발행 안 함)
python main.py publish         # 초안을 지금 바로 품질 게이트 → 사실 검증 → 발행
python main.py auto            # draft + 검토 시간 지난 초안만 publish (예약 실행용)
python main.py review <메모이름> # 승인 전에 볼 것(제목·설명·카드·출처·숫자)을 한 화면에
python main.py approve <메모이름># 사용자 승인. 승인된 초안만 발행된다 (unapprove로 취소)
python main.py approve all       # 승인 대기 전부 승인. review로 전부 읽은 뒤에만 (unapprove all로 전부 취소)
python main.py hold <메모이름>   # auto 발행에서 제외 (unhold로 해제)
```
- 예약 실행: Windows 작업 스케줄러 `leave-lab blog_bot`이 매일 09:00에 `run_auto.cmd`를 돌린다(로그인 상태에서만 실행). 메모 → 그날 초안 → 사용자 `approve` → 다음 실행에서 발행. 로그는 `output/logs/YYYY-MM-DD.log`.
- **사람 검토 게이트(2026-10-12부터, E4 브리프 `C:\leave-lab\docs\E04_블로그계산기허브_blog_bot.md`)**: 봇은 초안만 만든다. 사용자가 `review`로 제목·메타 설명·카드·출처·숫자를 보고, 글에 직접 만든 숫자·화면·결정이 1개 이상 있는지 확인한 뒤 `approve`한 초안만 발행된다. 승인 뒤 초안 파일이 바뀌면(직접 수정, 봇의 자동 수정 포함) 승인이 풀려 다시 approve해야 한다. `publish`도 승인된 초안만 낸다. 사람 검토 없이 발행한 글 0편이 지표다.
- 발행 순서: 승인된 초안 중 하루 `LEAVE_LAB_MAX_PUBLISH_PER_RUN`(기본 1)편. 원고 글감(frontmatter `source.order`)은 순서대로 나가고, 같은 `project`에 앞 순서 글감(raw·hold)이 남아 있으면 뒤 글은 초안이 있어도 기다린다. 작업 세션 메모(`source` 없음)는 원고 순서와 상관없이 바로 초안을 만들고 원고 글감보다 먼저 발행한다(최근 메모부터). 원고 초안은 발행 가능한 초안이 하루 발행 편수의 2배가 되도록만 미리 만든다(검토 24시간 때문). 2026-10-12부터 하루 2편(.env).
- 안전장치 3단계: 검토 시간(`LEAVE_LAB_REVIEW_HOURS`, 기본 24h) → 품질 게이트(형식·완결성) → 사실 검증(`prompts/leavelab_review.md`, 메모와 대조, **연속 2회 통과** 필요). 걸리면 지적대로 자동 수정하고 같은 실행에서 바로 다시 검증한다(최대 2회, 검토 시간은 다시 세지 않음). 한도를 넘기면 발행하지 않고 `status`에 오류로 남기며, 같은 프로젝트의 뒤 글도 멈춘다.
- 검토 시간을 0으로 줄이거나 사실 검증을 빼거나 승인 게이트를 우회하지 않는다(구글 대량 생성 콘텐츠 정책 리스크). 바꾸려면 사용자에게 먼저 묻는다. 광고 배치·광고 수익을 위한 작업은 하지 않는다(브리프).
- 발행은 leave-lab 저장소의 `blog_bot/publishers/static_site.py`를 불러 쓴다. 발행 규칙은 거기서만 고친다.
- 글 frontmatter에 `category`(홈 탭 분류, 태그 낱말표로 추정)와 실험 카드 `hypothesis`·`method`·`result`(생성 응답의 HYPOTHESIS/METHOD/RESULT 한 줄씩)를 넣는다. 카드 문장도 사실 검증 대상이다. 없으면 비워 두며 사이트는 그대로 빌드된다. 메모의 `project`는 글 frontmatter `project`로 그대로 넘기고, 사이트의 `src/series.ts`가 이를 연재(시리즈) 이름으로 묶는다. 새 프로젝트를 시작하면 그 파일에 한 줄 추가해야 연재로 보인다.
- 글 생성·검증은 `claude --print --tools ""`(구독 인증, 도구 없음)로 한다. API 키를 쓰지 않는다.

## 파일
- `README.md`: 승인 규칙, 파이썬 함수별 역할, 생성 파일(meta.json 필드·로그) 설명. 코드가 바뀌면 같이 고친다
- `src/inbox.py`: 메모 파싱, 검증, `used` 표시
- `src/leavelab.py`: 초안 생성, 품질 게이트, 사실 검증, 발행, auto/hold, 현황
- `src/writer.py`: claude CLI 호출
- `config/leavelab_style.md`, `prompts/leavelab_{ko,th,review}.md`: 문체·형식, 생성·검증 프롬프트
- `output/`: git에서 제외. `meta.json`의 stage(drafted/published), drafted_at, approved_at, hold로 상태를 이어간다

## 환경변수 (.env)
`LEAVE_LAB_REPO`(필수, `C:\leave-lab`), `LEAVE_LAB_BRANCH`(기본 main), `LEAVE_LAB_REVIEW_HOURS`(기본 24), `LEAVE_LAB_NO_PUSH=1`(커밋까지만), `CLAUDE_CMD`(선택)
