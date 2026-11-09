#!/usr/bin/env python3
"""매일 뉴스 큐레이션 → Claude 글 작성 → 카드 이미지 → 인스타/스레드/X 업로드.

사용법:
  .venv/bin/python autopost.py            # 실제 업로드
  .venv/bin/python autopost.py --dry-run  # 글·이미지만 만들고 업로드 안 함
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
OUTPUT = ROOT / "output"
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

IG_API = "https://graph.instagram.com/v23.0"
THREADS_API = "https://graph.threads.net/v1.0"


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ---------- 1. 뉴스 모으기 ----------
def collect_news(cfg, posted_urls):
    per_feed, seen = [], set()
    for url in cfg["feeds"]:
        items = []
        per_feed.append(items)
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except Exception as e:
            log(f"⚠️ 피드 실패 {url[:60]}: {e}")
            continue
        feed = feedparser.parse(resp.content)
        for e in feed.entries:
            link = e.get("link", "")
            title = re.sub(r"\s+", " ", e.get("title", "")).strip()
            if not link or link in posted_urls or title in seen:
                continue
            seen.add(title)
            summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:300]
            items.append({"title": title, "link": link, "summary": summary,
                          "source": e.get("source", {}).get("title", "")})
    # 피드마다 골고루 섞어서 후보를 고름
    mixed, i = [], 0
    while len(mixed) < cfg["max_candidates"] and any(i < len(f) for f in per_feed):
        mixed += [f[i] for f in per_feed if i < len(f)]
        i += 1
    mixed = mixed[: cfg["max_candidates"]]
    log(f"뉴스 후보 {len(mixed)}건")
    return mixed


# ---------- 2. Claude 로 글 쓰기 ----------
PROMPT = """너는 SNS 뉴스 큐레이터다. 아래 오늘의 뉴스 후보 중에서 주제 "{topic}"에 가장 중요하고
독자({audience})에게 유용한 소식 3개를 골라 오늘의 게시물을 만들어라. 말투: {tone}.
사실만 쓰고, 후보에 없는 내용을 지어내지 마라.

반드시 아래 JSON 하나만 출력해라(코드블록·설명 금지):
{{
  "card_title": "카드 맨 위 제목 (20자 이내)",
  "card_points": ["카드에 들어갈 핵심 3줄, 각 40자 이내"],
  "instagram": "인스타 캡션 (소식 3개 요약 + 마지막 줄에 해시태그 5~8개, 1500자 이내, 링크 넣지 말 것)",
  "threads": "스레드 글 (450자 이내, 소식 3개 요약)",
  "x": "X 글 (한글 기준 120자 이내, 가장 중요한 소식 1개 + 해시태그 1~2개, 링크 넣지 말 것)",
  "x_link": "X 글에 붙일 해당 기사 링크 (후보의 link 그대로)",
  "used_links": ["고른 3개 기사의 link"]
}}

뉴스 후보:
{news}
"""


def write_with_claude(cfg, news):
    prompt = PROMPT.format(topic=cfg["topic"], audience=cfg["audience"], tone=cfg["tone"],
                           news=json.dumps(news, ensure_ascii=False, indent=1))
    r = subprocess.run([CLAUDE_BIN, "-p", "--output-format", "text"], input=prompt,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {(r.stderr or r.stdout)[-500:]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        raise RuntimeError(f"Claude 응답에서 JSON 을 못 찾음: {r.stdout[:500]}")
    post = json.loads(m.group(0))
    post["threads"] = post["threads"][:500]
    return post


# ---------- 3. 카드 이미지 ----------
def wrap(draw, text, font, width):
    """띄어쓰기 단위로 줄바꿈 (한 단어가 너무 길면 글자 단위로)."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if draw.textlength(cur + ch, font=font) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_card(cfg, post, path):
    c = cfg["card"]
    W, H, P = 1080, 1350, 90
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    f_date = ImageFont.truetype(FONT_PATH, 36, index=0)
    f_title = ImageFont.truetype(FONT_PATH, 76, index=6)   # Bold
    f_body = ImageFont.truetype(FONT_PATH, 44, index=2)
    f_brand = ImageFont.truetype(FONT_PATH, 34, index=0)

    y = P
    d.text((P, y), f"{dt.date.today():%Y.%m.%d}  오늘의 소식", font=f_date, fill=c["accent"])
    y += 80
    for line in wrap(d, post["card_title"], f_title, W - 2 * P):
        d.text((P, y), line, font=f_title, fill=c["text"])
        y += 100
    y += 30
    d.rectangle([P, y, P + 120, y + 8], fill=c["accent"])
    y += 70
    for i, pt in enumerate(post["card_points"][:3], 1):
        d.text((P, y), f"{i:02d}", font=f_title, fill=c["accent"])
        ly = y + 10
        for line in wrap(d, pt, f_body, W - 2 * P - 130):
            d.text((P + 130, ly), line, font=f_body, fill=c["text"])
            ly += 62
        y = max(ly, y + 100) + 50
    d.text((P, H - P - 34), c["brand"], font=f_brand, fill=c["accent"])
    img.save(path, "JPEG", quality=92)
    log(f"카드 이미지 저장: {path}")


def upload_image(path):
    key = os.environ.get("IMGBB_API_KEY")
    if not key:
        raise RuntimeError("IMGBB_API_KEY 가 없음 (.env 확인)")
    r = requests.post("https://api.imgbb.com/1/upload",
                      data={"key": key, "image": base64.b64encode(path.read_bytes())},
                      timeout=60)
    r.raise_for_status()
    return r.json()["data"]["url"]


# ---------- 4. 업로드 ----------
def meta_wait(api, container_id, token):
    for _ in range(30):
        r = requests.get(f"{api}/{container_id}",
                         params={"fields": "status_code", "access_token": token}, timeout=30)
        status = r.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"미디어 처리 실패: {r.text}")
        time.sleep(5)
    raise RuntimeError("미디어 처리 시간 초과")


def check(r):
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r.json()


def post_instagram(post, image_url, tokens):
    uid, tok = os.environ["IG_USER_ID"], tokens["ig"]
    c = check(requests.post(f"{IG_API}/{uid}/media", timeout=60, data={
        "image_url": image_url, "caption": post["instagram"], "access_token": tok}))
    meta_wait(IG_API, c["id"], tok)
    res = check(requests.post(f"{IG_API}/{uid}/media_publish", timeout=60,
                              data={"creation_id": c["id"], "access_token": tok}))
    return res["id"]


def post_threads(post, image_url, tokens):
    uid, tok = os.environ["THREADS_USER_ID"], tokens["threads"]
    data = {"text": post["threads"], "access_token": tok}
    data.update({"media_type": "IMAGE", "image_url": image_url} if image_url else {"media_type": "TEXT"})
    c = check(requests.post(f"{THREADS_API}/{uid}/threads", data=data, timeout=60))
    meta_wait(THREADS_API, c["id"], tok)
    res = check(requests.post(f"{THREADS_API}/{uid}/threads_publish", timeout=60,
                              data={"creation_id": c["id"], "access_token": tok}))
    return res["id"]


def post_x(post):
    import tweepy
    client = tweepy.Client(consumer_key=os.environ["X_API_KEY"],
                           consumer_secret=os.environ["X_API_SECRET"],
                           access_token=os.environ["X_ACCESS_TOKEN"],
                           access_token_secret=os.environ["X_ACCESS_SECRET"])
    text = post["x"].strip()
    if post.get("x_link"):
        text += "\n" + post["x_link"]
    res = client.create_tweet(text=text)
    return res.data["id"]


# ---------- Meta 토큰 자동 갱신 (60일 만료 방지) ----------
def refresh_tokens(cfg):
    path = STATE / "tokens.json"
    t = load_json(path, {})
    t.setdefault("ig", os.environ.get("IG_ACCESS_TOKEN", ""))
    t.setdefault("threads", os.environ.get("THREADS_ACCESS_TOKEN", ""))
    last = dt.datetime.fromisoformat(t.get("refreshed_at", "2000-01-01"))
    if dt.datetime.now() - last > dt.timedelta(days=7):
        for name, url, grant in [
            ("ig", "https://graph.instagram.com/refresh_access_token", "ig_refresh_token"),
            ("threads", "https://graph.threads.net/refresh_access_token", "th_refresh_token"),
        ]:
            if not (cfg["platforms"].get("instagram" if name == "ig" else name) and t[name]):
                continue
            r = requests.get(url, params={"grant_type": grant, "access_token": t[name]}, timeout=30)
            if r.ok:
                t[name] = r.json()["access_token"]
                log(f"{name} 토큰 갱신 완료")
            else:
                log(f"⚠️ {name} 토큰 갱신 실패: {r.text[:200]}")
        t["refreshed_at"] = dt.datetime.now().isoformat()
        save_json(path, t)
        os.chmod(path, 0o600)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    cfg = load_json(ROOT / "config.json", {})
    posted = load_json(STATE / "posted.json", {"urls": [], "runs": []})
    today = dt.date.today().isoformat()

    if not args.dry_run and any(r["date"] == today and r.get("ok") for r in posted["runs"]):
        log("오늘은 이미 올렸음 → 종료")
        return

    news = collect_news(cfg, set(posted["urls"]))
    if len(news) < 3:
        log("뉴스 후보가 부족해서 종료")
        return

    post = write_with_claude(cfg, news)
    day_dir = OUTPUT / today
    day_dir.mkdir(parents=True, exist_ok=True)
    save_json(day_dir / "post.json", post)
    card = day_dir / "card.jpg"
    make_card(cfg, post, card)

    if args.dry_run:
        log(f"[테스트 모드] 업로드하지 않음. 결과 확인: {day_dir}")
        print(json.dumps(post, ensure_ascii=False, indent=2))
        return

    tokens = refresh_tokens(cfg)
    p = cfg["platforms"]
    image_url = upload_image(card) if (p.get("instagram") or p.get("threads")) else None

    results = {}
    jobs = [("instagram", lambda: post_instagram(post, image_url, tokens)),
            ("threads", lambda: post_threads(post, image_url, tokens)),
            ("x", lambda: post_x(post))]
    for name, fn in jobs:
        if not p.get(name):
            continue
        try:
            results[name] = {"ok": True, "id": fn()}
            log(f"✅ {name} 업로드 성공")
        except Exception as e:  # 한 곳이 실패해도 나머지는 계속
            results[name] = {"ok": False, "error": str(e)}
            log(f"❌ {name} 실패: {e}")

    ok = any(r["ok"] for r in results.values())
    posted["runs"].append({"date": today, "ok": ok, "results": results})
    if ok:
        posted["urls"] = (posted["urls"] + post.get("used_links", []))[-500:]
    save_json(STATE / "posted.json", posted)
    if not all(r["ok"] for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
