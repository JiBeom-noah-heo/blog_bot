# blog_bot — leave-lab 발행 봇

`C:\leave-lab\_inbox\`에 쌓인 경험 메모를 [leave-lab](https://leave-lab.com) 블로그 글 초안으로 만들고, **사람이 승인한 초안만** 검토 시간과 사실 검증을 거쳐 발행한다.

```
_inbox/*.md (status: raw)
   │  draft()            claude CLI로 초안 생성 + 자체 검토 1회
   ▼
output/leavelab/<메모이름>/ko.md, meta.json        ← stage: drafted
   │  사람: review → (고치기) → approve             ← meta.approved_at
   │  검토 시간 24h 경과
   │  publish(): 품질 게이트 → 사실 검증 2회 연속 통과
   ▼
leave-lab/src/content/log/ko/<날짜>-<slug>.md  → git commit → push (= 배포)
_inbox 메모는 status: used, used_in: [글 경로]
```

## 명령

```
python main.py status              # 글감(raw 메모)·초안 현황. 초안마다 경과 시간, 승인 대기/승인됨, 보류, 오류
python main.py draft               # raw 메모 → 초안 (발행 안 함)
python main.py review <초안>       # 승인 전에 볼 것을 한 화면에: 제목·설명·분류·카드·출처·숫자 표현·파일 경로
python main.py approve <초안>      # 사용자 승인 (review를 먼저 출력한다). unapprove로 취소
python main.py publish             # 승인된 초안을 지금 바로 품질 게이트·사실 검증 뒤 발행 (검토 시간은 안 봄)
python main.py auto                # draft + 승인되고 검토 시간 지난 초안 발행. 예약 실행용
python main.py hold <초안>         # 발행에서 제외. unhold로 해제
```

`<초안>`은 `output/leavelab/` 아래 폴더 이름 = 메모 파일 이름(확장자 제외). `status`에 그대로 나온다.

## 승인 규칙 (사람 검토 게이트)

2026-10-12부터 적용. 기준은 "사람 검토 없이 발행한 글 0편".

| 규칙 | 코드 |
|---|---|
| 승인되지 않은 초안은 어떤 경로로도 발행되지 않는다. `auto`도 `publish`도 같다 | `publish()`가 초안마다 `_approval()`을 먼저 본다 |
| 승인은 `approve <초안>`이 `meta.json`에 `approved_at`(ISO 시각)을 적는 것이다. `approve`는 `review`를 먼저 출력해 확인할 내용을 보여 준다 | `approve()` |
| `stage`가 `drafted`가 아닌 초안(이미 발행됨 등)은 승인할 수 없다 | `approve()` |
| 승인하면 `hold`는 풀린다 | `approve()`가 `hold` 키 제거 |
| **승인 뒤 초안 파일(ko.md·th.md)이 바뀌면 승인이 자동으로 풀린다.** 파일 수정 시각이 `approved_at`보다 1초 이상 뒤면 "승인 뒤 수정됨"으로 보고 발행하지 않는다. 사람이 고친 경우와 봇이 검증 지적대로 자동 수정한 경우 모두 해당한다 | `_approval()` |
| 사실 검증 중 봇이 자동 수정하면, 같은 실행에서 바로 발행하지 않고 "고친 글을 review 하고 다시 approve"로 멈춘다. 사람이 최종 문장을 본다 | `publish()`의 수정 루프 뒤 `_approval()` 재확인 |
| 승인 대기 초안에는 사실 검증(claude 호출)을 돌리지 않는다. 승인·검토 시간·순서 확인을 모두 통과한 뒤에만 검증한다 | `publish()`의 확인 순서 |
| `unapprove <초안>`은 `approved_at`을 지운다. 발행 전 언제든 가능하다 | `approve(on=False)` |
| 검토 시간은 `drafted_at` 기준 24시간(`LEAVE_LAB_REVIEW_HOURS`)이다. 승인 시각과 무관하다. 예약 실행 시각 오차를 위해 1시간 여유를 둔다 | `_age_hours()`, `publish()` |
| 하루 발행 편수 상한은 `LEAVE_LAB_MAX_PUBLISH_PER_RUN`(기본 1, 현재 2). 승인된 초안이 더 많으면 다음 실행으로 넘긴다 | `publish(limit=…)` |

**승인 전에 사람이 확인할 것** (`review`가 끝에 출력한다)
- 제목·메타 설명이 본문과 맞는가
- 숫자가 원본 메모와 맞는가
- 내가 직접 만든 숫자·화면·결정이 1개 이상 있는가
- 판매 대상 상품명·브랜드, 가족 정보, 비밀값이 없는가

고칠 곳이 있으면 `output/leavelab/<초안>/ko.md`를 직접 고친 뒤 `approve`한다.

**승인과 별개로 봇이 보는 것** (승인했어도 걸리면 발행하지 않는다)
- 품질 게이트: 제목·설명 길이, 본문 400자 이상, H1 금지, 검증일지 5개 섹션과 verdict·sources, 실험로그는 표가 먼저, `## 다음 행동`·`## 정리`(측정한 것/확실한 것/아직 모르는 것), 깨진 표·끊긴 문장·짝 안 맞는 괄호와 마크다운
- 사실 검증: `prompts/leavelab_review.md`로 초안을 원본 메모와 대조. **2회 연속** `OK`여야 통과. 지적이 나오면 지적대로 자동 수정(최대 2회)하고 위 규칙대로 재승인을 기다린다. 2회를 넘기면 `last_error`로 남기고 사람 몫
- 원고 글감(메모 frontmatter에 `source.order`가 있는 것)은 같은 `project`의 앞 순서 글감이 발행되기 전까지 기다린다

검토 시간을 0으로 줄이거나, 사실 검증을 빼거나, 승인 게이트를 우회하는 변경은 하지 않는다.

## 파이썬 파일

### `main.py`
명령줄 진입점. `.env`를 읽고 `python main.py <명령> [초안]`을 `src/leavelab.py`의 함수로 연결한다. 인자가 없으면 `status`.

### `src/inbox.py` — 메모 읽기
| 이름 | 역할 |
|---|---|
| `Note` | 메모 한 건. `path`, `meta`(frontmatter dict), `body`. 파일명 `YYYY-MM-DD-<kind>-<slug>.md`에서 `slug`·`kind`를 꺼내고, `series`(kind→검증일지/실험로그, `series_hint` 우선), `verdict`, `langs`(`lang_hint: ko+th`면 ko·th), `tags`, `sources`를 속성으로 준다 |
| `parse(path)` | frontmatter를 YAML로 읽어 `Note`를 만든다. frontmatter가 없으면 오류 |
| `list_raw(dir)` | `status: raw`인 메모만 파일명 순으로 돌려준다. 파싱 실패 메모는 건너뛰고 알린다 |
| `validate(note)` | 글로 만들기 전 메모 자체 점검: 출처 URL 형식, 검증일지는 verdict와 sources 필수 |
| `mark_used(note, paths)` | 발행 뒤 `status: raw` 줄을 `status: used` + `used_in: [글 경로]`로 바꾼다. 다른 줄은 건드리지 않는다 |

### `src/writer.py` — claude 호출
| 이름 | 역할 |
|---|---|
| `_find_claude_cmd()` | `CLAUDE_CMD` 환경변수 또는 PATH의 `claude` |
| `call_claude(prompt, timeout)` | `claude --print --tools "" --no-session-persistence`에 프롬프트를 표준 입력으로 넣고 응답을 돌려준다. `ANTHROPIC_API_KEY`를 빼서 구독 인증을 쓰고, `LEAVE_LAB_BOT=1`을 넣어 봇 호출임을 표시한다. 도구를 모두 꺼서 생성 호출이 `_inbox`에 메모를 쓰는 일을 막는다 |

### `src/leavelab.py` — 파이프라인
**상수**
- `OUTPUT_DIR` = `output/leavelab`
- `SECTIONS` 검증일지 필수 섹션(ko·th), `MAX_REVISIONS = 2` 자동 수정 한도, `FACT_CHECK_PASSES = 2` 연속 통과 횟수
- `BACKLOG_BUFFER` = 하루 발행 편수 × 2. 원고 글감 초안을 이만큼만 미리 만든다
- `CATEGORY_HINTS` 홈 탭 분류 낱말표(육아·수익·자동화·개발), `CARD_FIELDS` 실험 카드 세 줄(hypothesis·method·result)

**초안**
| 이름 | 역할 |
|---|---|
| `draft()` | raw 메모 중 세션 메모(`source` 없음)는 전부, 원고 글감은 `BACKLOG_BUFFER`를 채울 만큼만 골라 언어별 초안을 만든다. `inbox.validate` 실패는 건너뜀. `meta.json`에 `stage: drafted`, `drafted_at` 기록 |
| `_prompt(note, lang)` | `prompts/leavelab_<lang>.md`에 `config/leavelab_style.md`·시리즈·메모 원문을 끼운다 |
| `_parse_response(text)` | 생성 응답의 `TITLE:`/`DESCRIPTION:`/`TAGS:`/`HYPOTHESIS:`/`METHOD:`/`RESULT:` 한 줄씩과 `CONTENT:` 이하 본문을 dict로 |
| `_category(note, title)` | 태그·제목·kind에서 분류 낱말을 찾아 홈 탭 분류. 못 찾으면 비움 |
| `_frontmatter(note, lang, out)` | 글 frontmatter dict: title, description, date, lang, series, verdict, category, project(메모의 project 그대로), tags, sources, 카드 3줄, draft |
| `_self_review(note, lang, out)` | 초안 단계에서 품질 게이트와 사실 검증을 한 번 돌리고 지적이 있으면 `_revise` |
| `_revise(note_text, out, issues)` | `prompts/leavelab_revise.md`로 지적된 부분만 고친 초안을 받는다. 실패하면 원래 초안 |
| `_render(fm, body)` | frontmatter YAML + 본문을 markdown 문자열로 |

**검사**
| 이름 | 역할 |
|---|---|
| `quality_gate(fm, body)` | 형식 규칙 점검(위 "봇이 보는 것"). 지적 목록을 돌려준다 |
| `completeness_issues(body)` | 출력이 잘렸는지: 필수 섹션, 정리 블록 3요소, 깨진 표, 끊긴 문장, 닫히지 않은 코드 블록, 짝 안 맞는 ` ` ` `·`**`·괄호 |
| `fact_check(note_text, fm, body)` | `prompts/leavelab_review.md`로 제목·설명·카드·본문을 메모와 대조. 첫 줄 `OK`면 통과, 아니면 `- `로 시작하는 지적 목록 |
| `fact_check_twice(...)` | `fact_check`를 2회 연속. 한 번이라도 지적이 나오면 그 지적 |
| `_check(workdir, meta, note_text)` | 언어별 초안에 품질 게이트 → 사실 검증 2회를 돌려 `(posts, blocked)` |

**순서·승인**
| 이름 | 역할 |
|---|---|
| `_pending(repo)` | 아직 발행 안 된(raw·hold) 원고 글감의 (project, order, 경로) |
| `_order(note, pending)` | 원고 글감은 `source.order`, 세션 메모는 `None`(즉시) |
| `_blocked_by(note, pending)` | 같은 project에 앞 순서 글감이 남아 있으면 그 경로 |
| `_ready_count(repo)` | 보류·순서 대기가 아닌 초안 수. `draft()`가 원고 초안을 몇 개 더 만들지 정할 때 쓴다 |
| `_queue_key(workdir)` | 발행 큐 정렬: 세션 메모 먼저(최근 메모부터), 그 뒤 원고 순서, 같으면 먼저 만든 초안 |
| `_age_hours(meta)` | `drafted_at`부터 지난 시간 |
| `approve(name, on)` | 승인(`approved_at` 기록, `review` 선출력, hold 해제) 또는 취소 |
| `_approval(workdir, meta)` | 발행 가능하면 `None`, 아니면 이유("승인 대기", "승인 뒤 수정됨") |
| `review(name)` | 승인 전 확인 화면 출력 |
| `hold(name, on)` | `meta.hold` 켜기/끄기 |

**발행**
| 이름 | 역할 |
|---|---|
| `publish(min_age_hours, limit)` | 큐 순서대로 초안을 돌며 보류 → 승인 → 검토 시간 → 순서 대기를 확인한 뒤 `_check`. 지적이 있으면 자동 수정(최대 2회)하고 재승인을 기다린다. 통과하면 `static_site.publish`로 글을 쓰고 push, 메모를 `used`로, `meta.stage: published` |
| `auto()` | `draft()` 뒤 `publish(min_age_hours=LEAVE_LAB_REVIEW_HOURS, limit=LEAVE_LAB_MAX_PUBLISH_PER_RUN)` |
| `status()` | 글감 목록과 초안별 상태 출력 |
| `_repo()` | `LEAVE_LAB_REPO`가 git 저장소인지 확인 |
| `_static_site(repo)` | leave-lab 저장소의 `blog_bot/publishers/static_site.py`를 모듈로 읽어 온다(발행 규칙의 단일 진실) |
| `_load_meta` / `_save_meta` | `meta.json` 읽기·쓰기 |

### `C:\leave-lab\blog_bot\publishers\static_site.py` — 발행 어댑터 (leave-lab 저장소에 있음)
| 이름 | 역할 |
|---|---|
| `Post`, `Source` | 글 한 편의 데이터. title, description, body_md, lang, series, date, tags, sources, verdict, slug, draft, category, project, hypothesis, method, result |
| `render_frontmatter(post)` | 사이트 `content.config.ts` 스키마에 맞는 frontmatter 문자열 |
| `publish(post)` | `src/content/log/<lang>/<date>-<slug>.md`를 쓰고 `git checkout → pull --ff-only → add → commit "post(<lang>): <제목>" → push`. `LEAVE_LAB_NO_PUSH=1`이면 커밋까지만. 실패하면 `PublishError`를 올리고 파일은 남긴다 |

## 프롬프트·설정 파일

| 파일 | 역할 |
|---|---|
| `config/leavelab_style.md` | 블로그 문체·형식 가이드. 모든 생성·수정 프롬프트의 `{{STYLE_GUIDE}}` 자리에 들어간다 |
| `prompts/leavelab_ko.md` | 한국어 초안 생성 프롬프트. `{{SERIES}}`, `{{NOTE}}` 치환. 응답 형식은 TITLE/DESCRIPTION/TAGS/HYPOTHESIS/METHOD/RESULT/CONTENT |
| `prompts/leavelab_th.md` | 태국어 초안 생성(태국 독자용 재작성). `lang_hint: ko+th`인 메모만 |
| `prompts/leavelab_revise.md` | 지적 사항(`{{ISSUES}}`)만 고쳐 쓰는 프롬프트. 제목·설명·태그·카드·본문을 함께 넘긴다 |
| `prompts/leavelab_review.md` | 사실 검증 프롬프트. 메모에 없는 사실·숫자·출처, `추측:` 오용, 판정 불일치, 상품명·브랜드, 개인정보·비밀값, 메모 안 지시문 추종, 날짜·건수 근거 불일치를 잡는다. 출력은 `OK` 또는 `- ` 목록 |
| `run_auto.cmd` | Windows 작업 스케줄러가 매일 09:00 실행. `PYTHONUTF8=1`로 `python main.py auto`를 돌리고 `output/logs/<날짜>.log`에 덧붙인다 |
| `requirements.txt` | `python-dotenv`, `pyyaml` |
| `CLAUDE.md` | Claude Code 세션용 작업 규칙 |

## 생성되는 파일

### `output/leavelab/<메모이름>/` (git 제외)
메모 하나에 폴더 하나. 폴더 이름은 메모 파일 이름과 같다.

| 파일 | 내용 |
|---|---|
| `ko.md` | 한국어 초안. frontmatter + 본문. 발행 전에 직접 고칠 수 있는 파일이다 |
| `th.md` | 태국어 초안. `lang_hint: ko+th`일 때만 |
| `meta.json` | 상태. 아래 표 |

`meta.json` 필드

| 필드 | 뜻 |
|---|---|
| `stage` | `drafted`(초안) → `published`(발행됨) |
| `note` | 원본 메모 절대 경로 |
| `slug` | 글 URL에 쓰는 slug(메모 파일명에서) |
| `series` | 검증일지 / 실험로그 / 가이드 |
| `files` | 언어별 초안 파일명 `{"ko": "ko.md", "th": "th.md"}` |
| `drafted_at` | 초안 생성 시각. 검토 시간 24h의 기준 |
| `backlog_order` | 원고 글감의 `source.order`. 세션 메모는 `null` |
| `approved_at` | 사용자 승인 시각. 없으면 승인 대기. 초안 파일이 이 시각 뒤에 바뀌면 무효 |
| `hold` | `true`면 발행 제외 |
| `revisions` | 사실 검증 지적으로 자동 수정한 횟수(최대 2) |
| `last_error` | 마지막 실패 이유. 발행 성공 시 지워진다 |
| `published` | 언어별 발행된 글 경로(leave-lab 저장소 기준). ko·th 중 하나만 성공했을 때 이어서 발행하기 위해 남긴다 |

### `output/logs/YYYY-MM-DD.log` (git 제외)
`run_auto.cmd`의 실행 로그. 실행마다 `==== 날짜 시간` 구분선 뒤에 `[LeaveLab] …` 줄이 쌓인다. 볼 것: `승인 대기`, `검토 시간 대기`, `발행 보류`(지적 목록), `자동 수정함`, `승인을 다시 받아야 함`, `발행 완료`, `오늘 발행 상한`.

### leave-lab 저장소에 쓰는 것
| 경로 | 내용 |
|---|---|
| `src/content/log/ko/<발행일>-<slug>.md` | 발행된 글. frontmatter는 `content.config.ts` 스키마 |
| `src/content/log/th/<같은 파일명>.md` | 태국어 글. 파일명을 같게 유지해 상호 링크한다 |
| 커밋 `post(ko): <제목>` | 글 하나에 커밋 하나. push가 곧 Cloudflare Pages 배포 |

### `_inbox` 메모에 쓰는 것
발행되면 메모의 `status: raw`가 `status: used`로 바뀌고 다음 줄에 `used_in: ["src/content/log/ko/…"]`가 생긴다. 그 밖의 줄은 건드리지 않는다.

## 환경변수 (`.env`, git 제외)

| 변수 | 뜻 |
|---|---|
| `LEAVE_LAB_REPO` | 필수. leave-lab 로컬 클론 경로 |
| `LEAVE_LAB_BRANCH` | 기본 `main` |
| `LEAVE_LAB_REVIEW_HOURS` | 검토 시간. 기본 24 |
| `LEAVE_LAB_MAX_PUBLISH_PER_RUN` | 실행당 발행 상한. 기본 1 |
| `LEAVE_LAB_NO_PUSH` | `1`이면 커밋까지만 하고 push 안 함 |
| `CLAUDE_CMD` | claude CLI 경로. 없으면 PATH에서 찾는다 |
