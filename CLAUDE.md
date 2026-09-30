# blog_bot — leave-lab 발행 봇

`C:\leave-lab\_inbox\`에 쌓인 경험 메모(형식은 `~/.claude/CLAUDE.md`)를 leave-lab 블로그 글로 바꿔 발행한다.

## 흐름
```
python main.py status    # 글감·초안 현황
python main.py draft     # raw 메모 → output/leavelab/<메모이름>/{ko,th}.md (발행 안 함)
python main.py publish   # 사람이 검수·수정한 초안 → 품질 게이트 → leave-lab push(=배포) → 메모 status: used
```
- 기본은 초안까지만 만든다. 사람 검수 없이 자동 발행하는 흐름으로 바꾸지 않는다(구글 대량 생성 콘텐츠 정책 리스크).
- 발행은 leave-lab 저장소의 `blog_bot/publishers/static_site.py`를 불러 쓴다. 발행 규칙은 거기서만 고친다.
- 글 형식·문체는 `config/leavelab_style.md`, 프롬프트는 `prompts/leavelab_{ko,th}.md`에 있다.
- 글 생성은 `claude --print`(구독 인증)로 한다. API 키를 쓰지 않는다.

## 파일
- `src/inbox.py`: 메모 파싱, 검증, `used` 표시
- `src/leavelab.py`: 초안 생성, 품질 게이트, 발행, 현황
- `src/writer.py`: claude CLI 호출
- `output/`: git에서 제외. `meta.json`의 stage(drafted/published)로 재실행 시 이어서 진행한다

## 환경변수 (.env)
`LEAVE_LAB_REPO`(필수, `C:\leave-lab`), `LEAVE_LAB_BRANCH`(기본 main), `LEAVE_LAB_NO_PUSH=1`(커밋까지만), `CLAUDE_CMD`(선택)
