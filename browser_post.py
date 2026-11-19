#!/usr/bin/env python3
"""전용 Chrome 프로필로 인스타그램 · X 웹 화면을 조종해 게시 (API 키 없이).

⚠️ 인스타그램·X 모두 약관상 자동화 도구 사용 시 계정 제재 위험이 있음.

처음 한 번 로그인:
  .venv/bin/python browser_post.py login     # 전용 Chrome이 뜨면 인스타·X 로그인 후 창 닫기
화면 점검(게시는 안 함):
  .venv/bin/python browser_post.py check
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "state" / "browser-profile"   # 일반 Chrome과 분리된 전용 프로필 (로그인 정보 보관)


class LoginRequired(Exception):
    pass


def any_text(*words):
    return re.compile("^(" + "|".join(map(re.escape, words)) + ")$")


def open_browser(p, headless=False):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=headless,
        viewport={"width": 1280, "height": 900}, locale="ko-KR",
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"])


def shot(page, day_dir, name):
    """실패 원인을 볼 수 있게 화면 저장."""
    if day_dir:
        try:
            page.screenshot(path=str(Path(day_dir) / f"fail_{name}.png"))
        except Exception:
            pass


def click_first(page, locators, timeout=8000):
    """여러 후보 중 먼저 보이는 것을 클릭 (한국어·영어 화면 모두 대응)."""
    loc = locators[0]
    for other in locators[1:]:
        loc = loc.or_(other)
    loc.first.wait_for(state="visible", timeout=timeout)
    loc.first.click()


def dismiss_popups(page):
    for _ in range(3):
        btn = page.get_by_role("button", name=any_text("나중에 하기", "Not Now", "Not now"))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            page.wait_for_timeout(800)
        else:
            break


# ---------- 인스타그램 ----------
def instagram(page, images, caption, day_dir=None):
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if page.locator("input[name=username]").count() or "/accounts/login" in page.url:
        raise LoginRequired("인스타그램 로그인이 필요함 — browser_post.py login 실행")
    dismiss_popups(page)

    # 1) 만들기 → (메뉴가 뜨면) 게시물
    click_first(page, [page.locator('svg[aria-label="새로운 게시물"]'),
                       page.locator('svg[aria-label="New post"]'),
                       page.locator('svg[aria-label="만들기"]'),
                       page.locator('svg[aria-label="Create"]')])
    page.wait_for_timeout(1500)
    if not page.locator('div[role=dialog] input[type=file]').count():
        try:
            click_first(page, [page.locator('svg[aria-label="게시물"]'),
                               page.locator('svg[aria-label="Post"]'),
                               page.get_by_role("link", name=any_text("게시물", "Post"))], timeout=4000)
        except Exception:
            pass

    # 2) 사진 넣기 (파일 선택 창 없이 직접 지정)
    file_input = page.locator('div[role=dialog] input[type=file]').first
    file_input.wait_for(state="attached", timeout=15000)
    file_input.set_input_files([str(i) for i in images])
    page.wait_for_timeout(3000)

    # 3) 비율 4:5 (기본 1:1이면 카드 위아래가 잘림)
    try:
        click_first(page, [page.locator('svg[aria-label="자르기 선택"]'),
                           page.locator('svg[aria-label="Select crop"]')], timeout=6000)
        page.wait_for_timeout(600)
        click_first(page, [page.get_by_text("4:5", exact=True)], timeout=4000)
        page.wait_for_timeout(600)
    except Exception:
        pass  # 비율 버튼을 못 찾아도 게시는 계속

    # 4) 다음 → 다음 (자르기 → 필터 → 문구)
    for _ in range(2):
        click_first(page, [page.get_by_role("button", name=any_text("다음", "Next"))], timeout=15000)
        page.wait_for_timeout(2000)

    # 5) 문구 입력 (줄바꿈 유지)
    box = page.locator('div[role=dialog] div[contenteditable=true]').first
    box.wait_for(state="visible", timeout=15000)
    box.click()
    for i, line in enumerate(caption.split("\n")):
        if i:
            page.keyboard.press("Enter")
        if line:
            page.keyboard.insert_text(line)
    page.wait_for_timeout(800)

    # 6) 공유
    click_first(page, [page.get_by_role("button", name=any_text("공유하기", "Share"))], timeout=10000)
    done = page.get_by_text(re.compile("게시물이 공유되었습니다|Your post has been shared|공유되었습니다"))
    done.first.wait_for(state="visible", timeout=180000)
    return "browser"


# ---------- X ----------
def x(page, intent_url, day_dir=None):
    page.goto(intent_url, wait_until="domcontentloaded")
    btn = page.locator('[data-testid=tweetButton]')
    try:
        btn.first.wait_for(state="visible", timeout=30000)
    except Exception:
        if "/login" in page.url or "/i/flow" in page.url:
            raise LoginRequired("X 로그인이 필요함 — browser_post.py login 실행")
        raise
    page.wait_for_timeout(1500)
    if btn.first.get_attribute("aria-disabled") == "true":
        raise RuntimeError("X 게시 버튼이 비활성 (글이 비었거나 너무 김)")
    btn.first.click()
    btn.first.wait_for(state="detached", timeout=60000)  # 게시되면 글쓰기 화면이 사라짐
    return "browser"


def run(task, *args, day_dir=None, headless=False):
    """task: 'instagram' 또는 'x'. 실패하면 화면을 fail_<task>.png 로 저장 후 예외."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            return {"instagram": instagram, "x": x}[task](page, *args, day_dir=day_dir)
        except Exception:
            shot(page, day_dir, task)
            raise
        finally:
            page.wait_for_timeout(2000)
            ctx.close()


def login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p)
        ctx.pages[0].goto("https://www.instagram.com/accounts/login/")
        ctx.new_page().goto("https://x.com/login")
        print("전용 Chrome 창에서 인스타그램과 X에 로그인한 뒤 창을 닫으세요.")
        ctx.pages[0].wait_for_event("close", timeout=0)
        try:
            ctx.close()
        except Exception:
            pass


def check():
    """로그인 상태만 확인."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.pages[0]
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        ig = not (page.locator("input[name=username]").count() or "/accounts/login" in page.url)
        page.goto("https://x.com/home", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        xs = not ("/login" in page.url or "/i/flow" in page.url)
        ctx.close()
    print(f"instagram 로그인: {'OK' if ig else '필요'} / x 로그인: {'OK' if xs else '필요'}")
    return ig, xs


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"login": login, "check": check}[cmd]()
