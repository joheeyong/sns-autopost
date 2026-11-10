# sns-autopost

매일 해외축구 뉴스를 모아 Claude가 글을 쓰고, 카드 이미지를 만들어 **인스타그램 · 스레드 · X**에 자동으로 올리는 프로그램입니다. macOS의 `launchd`로 매일 정해진 시간에 실행됩니다.

```
뉴스 수집(RSS) → Claude 글 작성(claude -p) → 업로드 전 검사 → 카드 이미지 → 이미지 공개 URL(imgbb) → 인스타 · 스레드 · X 업로드
```

## 기능

- **뉴스 큐레이션**: 구글 뉴스 · BBC 피드에서 후보를 모아 Claude가 중요한 소식 3개를 고름. 이미 올린 기사는 제외
- **플랫폼별 글**: 인스타 캡션(해시태그 포함), 스레드(500자 이내), X(가중치 280자 이내 + 원문 링크)
- **업로드 전 검사**: 글자 수, 빈 항목, 본문 링크, 후보에 없는 링크(지어낸 링크)를 검사하고, 걸리면 이유를 알려 주고 한 번 더 쓰게 함
- **출처 자동 표기**: 참고한 기사의 언론사 이름을 프로그램이 직접 붙임
- **원문 링크**: 구글 뉴스 중간 주소를 실제 기사 주소로 변환
- **재시도 · 알림**: 일시적 오류는 1분·3분 뒤 재시도, 실패하면 맥 알림. 게시 확정 단계는 중복 게시를 막기 위해 재시도하지 않음
- **토큰 자동 갱신**: Meta(인스타·스레드) 토큰을 7일마다 갱신해 60일 만료를 막음
- **하루 한 번**: 같은 날 이미 성공했으면 다시 올리지 않음

## 설치

```bash
git clone https://github.com/joheeyong/sns-autopost.git ~/sns-autopost
cd ~/sns-autopost
python3 -m venv .venv
.venv/bin/pip install feedparser requests pillow tweepy
cp .env.example .env && chmod 600 .env
```

필요한 것: macOS(카드 글꼴로 Apple SD Gothic Neo 사용), Python 3.10+, [Claude Code](https://claude.com/claude-code) CLI

## 키 설정 (`.env`)

| 항목 | 발급 방법 |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | 터미널에서 `claude setup-token` (1년 유효) |
| `IMGBB_API_KEY` | https://api.imgbb.com 에서 가입 후 "Get API key" |
| `X_API_KEY` 외 3개 | https://developer.x.com → 앱 생성 → 권한 **Read and Write** → Keys and tokens |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | Meta 개발자 앱 → Instagram API(Instagram 로그인 방식) → 비즈니스/크리에이터 계정 연결 |
| `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` | Meta 개발자 앱 → Threads API 사용 사례 추가 |

`.env`와 `state/`(토큰·게시 기록)는 `.gitignore`에 들어 있어 저장소에 올라가지 않습니다.

## 실행

```bash
# 테스트: 글과 카드만 만들고 업로드하지 않음 → output/날짜/ 에 저장
.venv/bin/python autopost.py --dry-run

# 실제 업로드
.venv/bin/python autopost.py
```

## 매일 자동 실행 (오전 8시)

```bash
cp com.luxrobo.sns-autopost.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.luxrobo.sns-autopost.plist
```

- 실행 기록: `logs/autopost.log`
- 맥이 잠자기 중이었다면 깨어난 직후 실행, 꺼져 있었다면 그날은 건너뜀
- 시간 변경: plist의 `Hour`, `Minute` 수정 후 `launchctl unload` → `load`

## 설정 (`config.json`)

| 항목 | 설명 |
|---|---|
| `topic`, `audience`, `tone` | Claude에게 주는 주제 · 독자 · 말투 |
| `feeds` | 뉴스 RSS 주소 목록 |
| `platforms` | 플랫폼별 켜기/끄기 |
| `card` | 카드의 계정 이름(`brand`)과 색상 |
