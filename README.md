# sns-autopost

매일 해외축구 뉴스를 모아 Claude가 글을 쓰고, 카드 이미지를 만들어 **인스타그램 · 스레드 · X**에 자동으로 올리는 프로그램입니다. macOS의 `launchd`로 매일 오전 8시에 실행됩니다.

```
뉴스 수집(RSS) → Claude 글 작성(claude -p) → 업로드 전 검사 → 카드 이미지 4장 + 미리보기
   → 인스타그램 · X: 전용 Chrome으로 웹 화면에서 게시 (키 불필요)
   → 스레드: Threads API (이미지는 imgbb로 공개 주소 생성)
```

## 플랫폼별 게시 방식

| 플랫폼 | 기본 방식 | 필요한 것 | 다른 방식 (`config.json`) |
|---|---|---|---|
| 인스타그램 | 브라우저 게시: 카드 4장 캐러셀(4:5) + 캡션 | 전용 Chrome에 로그인 | `instagram_mode: "api"` → Meta API 키 |
| X | 브라우저 게시: 글 + 원문 링크 | 전용 Chrome에 로그인 | `x_mode: "manual"` → 글쓰기 창만 열어 줌 / `"api"` → API 키 + 크레딧 |
| 스레드 | Threads API: 카드 4장 + 글 | Threads API 키, imgbb 키 | 없음 |

> ⚠️ 브라우저 게시는 인스타그램 · X 약관상 자동화 도구 사용에 해당해 **계정 제재 위험**이 있습니다. 공식 API(`api` 방식)는 이 위험이 없습니다.

## 기능

- **뉴스 큐레이션**: 구글 뉴스 · BBC 피드에서 후보를 모아 Claude가 중요한 소식 3개를 고름. 이미 올린 기사는 제외
- **캐러셀 이미지 4장**: 표지(소식 3개 요약) + 소식별 카드 3장. 팀 대표색 배경, 축구장 라인, 리그 태그, 기사 속 핵심 숫자를 크게 표시. 경기 결과는 스코어보드로 표시
- **플랫폼별 글**: 인스타 캡션(해시태그 포함), 스레드(500자 이내), X(가중치 280자 이내 + 원문 링크)
- **업로드 전 검사**: 글자 수, 빈 항목, 본문 링크, 후보에 없는 링크, 기사에 없는 숫자 · 스코어(지어낸 내용)를 검사하고, 걸리면 이유를 알려 주고 한 번 더 쓰게 함
- **출처 자동 표기**: 참고한 기사의 언론사 이름을 프로그램이 직접 붙임
- **원문 링크**: 구글 뉴스 중간 주소를 실제 기사 주소로 변환
- **미리보기**: 매 실행마다 `output/날짜/preview.html` · `preview.pdf` 생성. 플랫폼별로 올라간 모습, 글자 수, 원문 링크, X 글쓰기 창 열기 버튼 포함
- **브라우저 게시**: 평소 쓰는 Chrome과 분리된 **전용 Chrome 프로필**(`state/browser-profile`)을 Playwright로 조종. 사진은 파일 선택 창 없이 바로 넣음
- **재시도 · 알림**
  - 일시적 오류는 1분 · 3분 뒤 재시도, 실패하면 맥 알림
  - 게시 확정 단계와 브라우저 게시는 중복 게시를 막기 위해 재시도하지 않음
  - 브라우저 게시가 실패하면 그때 화면을 `output/날짜/fail_instagram.png` · `fail_x.png`로 저장. X는 평소 쓰는 Chrome에 글이 채워진 글쓰기 창을 열어 줘서 게시 버튼만 누르면 됨
- **토큰 자동 갱신**: API 방식의 Meta 토큰(인스타 · 스레드)을 7일마다 갱신해 60일 만료를 막음
- **반복 방지**: 최근 7일 동안 올린 소식을 Claude에게 알려 줘서, 새로운 진전이 없으면 다시 고르지 않음
- **하루 한 번**: 같은 날 이미 성공했으면 다시 올리지 않음
- **준비 안 된 플랫폼은 건너뜀**: API 방식인데 `.env`에 키가 없으면 그 플랫폼만 건너뜀. 올릴 곳이 하나도 없으면 Claude를 부르기 전에 종료

## 설치

```bash
git clone https://github.com/joheeyong/sns-autopost.git ~/sns-autopost
cd ~/sns-autopost
python3 -m venv .venv
.venv/bin/pip install feedparser requests pillow tweepy playwright
cp .env.example .env && chmod 600 .env
```

필요한 것: macOS(카드 글꼴로 Apple SD Gothic Neo 사용), Python 3.10+, Google Chrome, [Claude Code](https://claude.com/claude-code) CLI

## 브라우저 게시 로그인 (처음 한 번)

```bash
.venv/bin/python browser_post.py login   # 전용 Chrome이 뜨면 인스타그램 · X 로그인 후 창 닫기
.venv/bin/python browser_post.py check   # 로그인 상태 확인
```

- 로그인 정보는 `state/browser-profile`에 저장되어 다음 실행부터 그대로 쓰입니다.
- 로그인이 풀리면 게시가 실패하고 알림이 옵니다. 그때 `login`을 다시 실행하면 됩니다.

## 키 설정 (`.env`)

| 항목 | 언제 필요 | 발급 방법 |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | 항상 | 터미널에서 `claude setup-token` (1년 유효) |
| `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` | 스레드 | Meta 개발자 앱 → Threads API 사용 사례 추가 |
| `IMGBB_API_KEY` | 스레드, 인스타 `api` 방식 | https://api.imgbb.com 에서 가입 후 "Get API key" |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | 인스타 `api` 방식 | Meta 개발자 앱 → Instagram API(Instagram 로그인 방식) → 프로페셔널(크리에이터/비즈니스) 계정 연결 |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | X `api` 방식 | https://developer.x.com → 앱 권한 **Read and write** → Keys and tokens. 글마다 요금이 붙는 방식이라 크레딧 충전 필요 |

`.env`와 `state/`(브라우저 프로필 · 토큰 · 게시 기록)는 `.gitignore`에 들어 있어 저장소에 올라가지 않습니다.

## 실행

```bash
# 테스트: 글과 카드만 만들고 업로드하지 않음 → output/날짜/ 에 저장
.venv/bin/python autopost.py --dry-run

# 실제 업로드
.venv/bin/python autopost.py

# 미리보기만 다시 만들기 (특정 날짜)
.venv/bin/python preview.py 2026-11-10
```

## 테스트

```bash
.venv/bin/python -m unittest discover tests -v
```

실제 SNS에 접속하지 않고 검사 · 카드 · 재시도 · 게시 방식 전환 로직을 확인합니다.

## 매일 자동 실행 (오전 8시)

```bash
cp com.luxrobo.sns-autopost.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.luxrobo.sns-autopost.plist

# 지금 바로 한 번 실행해 보기
launchctl kickstart gui/$(id -u)/com.luxrobo.sns-autopost
```

- 실행 기록: `logs/autopost.log`
- 맥이 잠자기 중이었다면 깨어난 직후 실행, 꺼져 있었다면 그날은 건너뜀
- 브라우저 게시는 맥에 로그인된 상태여야 합니다. 실행 중 전용 Chrome 창이 잠깐 떴다가 닫힙니다.
- 시간 변경: plist의 `Hour`, `Minute` 수정 후 `launchctl bootout gui/$(id -u)/com.luxrobo.sns-autopost` → 다시 `bootstrap`

## 설정 (`config.json`)

| 항목 | 설명 |
|---|---|
| `topic`, `audience`, `tone` | Claude에게 주는 주제 · 독자 · 말투 |
| `feeds` | 뉴스 RSS 주소 목록 |
| `max_candidates` | Claude에게 보여 줄 뉴스 후보 수 |
| `platforms` | 플랫폼별 켜기/끄기 |
| `instagram_mode` | `browser`(기본) / `api` |
| `x_mode` | `browser`(기본) / `manual` / `api` |
| `card` | 카드의 계정 이름(`brand`)과 색상 |

## 파일 구성

| 파일 | 역할 |
|---|---|
| `autopost.py` | 전체 흐름: 뉴스 수집 → 글 작성 · 검사 → 카드 → 게시 |
| `browser_post.py` | 전용 Chrome으로 인스타그램 · X 웹 게시, 로그인 도우미 |
| `preview.py` | 플랫폼별 미리보기 HTML · PDF |
| `config.json` | 주제 · 피드 · 게시 방식 · 카드 색상 |
| `com.luxrobo.sns-autopost.plist` | 매일 오전 8시 실행 (launchd) |
| `tests/` | 단위 테스트 |
