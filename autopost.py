#!/usr/bin/env python3
"""매일 뉴스 큐레이션 → Claude 글 작성 → 검사 → 카드 이미지 → 인스타/스레드/X 업로드.

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
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

IG_API = "https://graph.instagram.com/v23.0"
THREADS_API = "https://graph.threads.net/v1.0"

REQUIRED_KEYS = {
    "instagram": ["IG_USER_ID", "IG_ACCESS_TOKEN"],
    "threads": ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN"],
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"],
}
LIMITS = {"instagram": 2200, "threads": 500, "x_weighted": 280, "hashtags": 30}
RETRY_WAITS = [60, 180]  # 일시적 오류일 때 1분, 3분 뒤 다시 시도


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def notify(title, msg):
    """맥 알림 센터에 알림 띄우기."""
    safe = lambda s: s.replace("\\", "").replace('"', "'")[:200]
    subprocess.run(["osascript", "-e",
                    f'display notification "{safe(msg)}" with title "{safe(title)}" sound name "Basso"'],
                   capture_output=True)


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ[k.strip()] = v.strip()


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
            resp = requests.get(url, timeout=30, headers=UA)
            resp.raise_for_status()
        except Exception as e:
            log(f"⚠️ 피드 실패 {url[:60]}: {e}")
            continue
        feed = feedparser.parse(resp.content)
        feed_name = feed.feed.get("title", "")
        for e in feed.entries:
            link = e.get("link", "")
            title = re.sub(r"\s+", " ", e.get("title", "")).strip()
            source = e.get("source", {}).get("title", "")
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            if not link or link in posted_urls or title in seen:
                continue
            seen.add(title)
            summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))
            summary = "" if summary.startswith(title[:20]) else summary[:300]
            items.append({"title": title, "link": link, "summary": summary,
                          "source": source or ("BBC Sport" if "BBC" in feed_name else feed_name)})
    # 피드마다 골고루 섞어서 후보를 고름
    mixed, i = [], 0
    while len(mixed) < cfg["max_candidates"] and any(i < len(f) for f in per_feed):
        mixed += [f[i] for f in per_feed if i < len(f)]
        i += 1
    mixed = mixed[: cfg["max_candidates"]]
    log(f"뉴스 후보 {len(mixed)}건")
    return mixed


def resolve_google_news(url):
    """news.google.com 중간 주소 → 실제 기사 주소. 실패하면 원래 주소 그대로."""
    if "news.google.com" not in url or "/articles/" not in url:
        return url
    try:
        aid = url.split("/articles/")[1].split("?")[0]
        html = requests.get(f"https://news.google.com/rss/articles/{aid}", headers=UA, timeout=20).text
        sig = re.search(r'data-n-a-sg="([^"]+)"', html).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html).group(1)
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                                 None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                 aid, int(ts), sig]
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          data={"f.req": json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])},
                          headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          timeout=20)
        real = json.loads(json.loads(r.text.split("\n\n")[1])[0][2])[1]
        return real if real.startswith("http") else url
    except Exception as e:
        log(f"⚠️ 원문 주소 변환 실패(구글 주소 사용): {e}")
        return url


# ---------- 2. Claude 로 글 쓰기 ----------
PROMPT = """너는 SNS 뉴스 큐레이터다. 아래 오늘의 뉴스 후보 중에서 주제 "{topic}"에 가장 중요하고
독자({audience})에게 유용한 소식 3개를 골라 오늘의 게시물을 만들어라. 말투: {tone}.
사실만 쓰고, 후보의 제목·요약에 없는 내용(숫자, 기록, 인용)을 지어내지 마라.
같은 사건을 다룬 기사는 하나로 묶어라. 출처 표기는 프로그램이 자동으로 붙이니 쓰지 마라.

반드시 아래 JSON 하나만 출력해라(코드블록·설명 금지):
{{
  "card_title": "카드 맨 위 제목 (16자 이내)",
  "card_points": [
    {{"head": "소식 한 줄 요약 (24자 이내)", "desc": "보충 설명 한 줄 (40자 이내)"}}
  ],
  "instagram": "인스타 캡션 (소식 3개 요약, 1500자 이내, 링크 금지, 해시태그 5~8개는 맨 마지막 줄에만)",
  "threads": "스레드 글 (380자 이내, 소식 3개 요약, 해시태그·링크 금지)",
  "x": "X 글 (한글 기준 100자 이내, 가장 중요한 소식 1개 + 해시태그 1~2개, 링크 금지)",
  "x_link": "X 글에 해당하는 기사의 link (후보의 link 그대로)",
  "used_links": ["card_points 순서대로 고른 3개 기사의 link (후보의 link 그대로)"],
  "extra_links": ["used_links 외에 내용(숫자·이름·인용)을 가져온 다른 후보 기사의 link 전부, 없으면 빈 배열"]
}}
card_points 는 정확히 3개.

뉴스 후보:
{news}
"""


def ask_claude(prompt):
    # Claude Code 안에서 실행될 때 넘어오는 세션 변수를 빼야 자기 로그인으로 동작함
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE") or k in ("CLAUDECODE", "CLAUDE_PID", "AI_AGENT"))}
    # 자동 실행용 장기 토큰(claude setup-token 으로 발급, .env 에 저장)은 유지
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    r = subprocess.run([CLAUDE_BIN, "-p", "--output-format", "text"], input=prompt,
                       capture_output=True, text=True, timeout=600, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {(r.stderr or r.stdout)[-500:]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        raise ValueError(f"Claude 응답에서 JSON 을 못 찾음: {r.stdout[:300]}")
    return json.loads(m.group(0))


def write_with_claude(cfg, news, recent=()):
    prompt = PROMPT.format(topic=cfg["topic"], audience=cfg["audience"], tone=cfg["tone"],
                           news=json.dumps(news, ensure_ascii=False, indent=1))
    if recent:
        prompt += ("\n최근 7일 동안 이미 올린 소식(같은 사건은 새로운 진전이 없으면 고르지 마라. "
                   "진전이 있으면 무엇이 새로운지 중심으로 써라):\n- " + "\n- ".join(recent))
    problems = []
    for attempt in (1, 2):  # 검사에 걸리면 이유를 알려 주고 한 번 더 쓰게 함
        p = prompt if not problems else prompt + "\n\n직전 답변의 문제(반드시 고칠 것):\n- " + "\n- ".join(problems)
        try:
            post = ask_claude(p)
        except ValueError as e:
            problems = [str(e)]
            continue
        problems = validate(post, news)
        if not problems:
            return post
        log(f"⚠️ 글 검사 실패({attempt}차): {problems}")
    raise RuntimeError("글 검사를 통과하지 못함: " + "; ".join(problems))


# ---------- 3. 업로드 전 검사 ----------
def x_weighted_len(text):
    """X(트위터) 글자 수 계산: 한글·이모지는 2, 영문·숫자는 1, 링크는 23."""
    text = re.sub(r"https?://\S+", "x" * 23, text)
    n = 0
    for ch in text:
        c = ord(ch)
        n += 1 if (c <= 0x10FF or 0x2000 <= c <= 0x200D or 0x2010 <= c <= 0x201F or 0x2032 <= c <= 0x2037) else 2
    return n


def validate(post, news):
    probs = []
    links = {n["link"] for n in news}
    for key in ("card_title", "card_points", "instagram", "threads", "x", "used_links"):
        if not post.get(key):
            probs.append(f"{key} 가 비어 있음")
    if probs:
        return probs
    pts = post["card_points"]
    if len(pts) != 3 or not all(isinstance(p, dict) and p.get("head") and p.get("desc") for p in pts):
        probs.append("card_points 는 head/desc 를 가진 3개여야 함")
    if len(post["card_title"]) > 20:
        probs.append(f"card_title 이 너무 김({len(post['card_title'])}자)")
    for i, p in enumerate(pts if isinstance(pts, list) else [], 1):
        if isinstance(p, dict) and (len(p.get("head", "")) > 28 or len(p.get("desc", "")) > 46):
            probs.append(f"card_points {i}번이 너무 김")
    if len(post["instagram"]) > 1800:
        probs.append(f"instagram 이 너무 김({len(post['instagram'])}자, 1500자 이내)")
    if len(re.findall(r"#\S+", post["instagram"])) > LIMITS["hashtags"]:
        probs.append("instagram 해시태그가 30개를 넘음")
    if len(post["threads"]) > 400:
        probs.append(f"threads 가 너무 김({len(post['threads'])}자, 380자 이내)")
    if x_weighted_len(post["x"]) + 24 > LIMITS["x_weighted"]:
        probs.append(f"x 가 너무 김(가중치 {x_weighted_len(post['x'])}, 링크 포함 280 이하)")
    for key in ("instagram", "threads", "x"):
        if re.search(r"https?://", post[key]):
            probs.append(f"{key} 본문에 링크가 들어 있음")
    extra = post.get("extra_links") or []
    bad = [l for l in post["used_links"] + extra + [post.get("x_link", "")] if l and l not in links]
    if bad:
        probs.append(f"후보에 없는 링크 {len(bad)}개 (지어낸 링크)")
    if len(set(post["used_links"])) != 3:
        probs.append("used_links 는 서로 다른 3개여야 함")
    return probs


def add_sources(post, news):
    """출처(언론사) 표기를 코드에서 붙임 — Claude 가 틀릴 일이 없도록."""
    by_link = {n["link"]: n["source"] for n in news}
    refs = post["used_links"] + (post.get("extra_links") or [])
    names = list(dict.fromkeys(by_link[l] for l in refs if by_link.get(l)))
    post["sources"] = names
    if not names:
        return post
    line = "출처: " + ", ".join(names)
    ig = post["instagram"].rstrip().split("\n")
    if ig and ig[-1].lstrip().startswith("#"):  # 해시태그 줄 바로 위에 출처
        ig.insert(len(ig) - 1, line + "\n")
    else:
        ig.append("\n" + line)
    post["instagram"] = "\n".join(ig)
    th = post["threads"].rstrip()
    room = LIMITS["threads"] - len(line) - 2
    post["threads"] = (th if len(th) <= room else th[: room - 1] + "…") + "\n\n" + line
    return post


# ---------- 4. 카드 이미지 ----------
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
    font = lambda size, idx: ImageFont.truetype(FONT_PATH, size, index=idx)
    f_date, f_title, f_num = font(38, 2), font(84, 6), font(80, 6)
    f_head, f_desc, f_foot = font(50, 6), font(38, 2), font(32, 2)
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    tx = P + 140                  # 소식 글자가 시작하는 x
    tw = W - tx - P

    y = P
    d.text((P, y), f"{dt.date.today():%Y.%m.%d}  오늘의 소식", font=f_date, fill=c["accent"])
    y += 84
    for line in wrap(d, post["card_title"], f_title, W - 2 * P):
        d.text((P, y), line, font=f_title, fill=c["text"])
        y += 108
    y += 24
    d.rectangle([P, y, P + 120, y + 8], fill=c["accent"])
    y += 80

    # 소식 3개를 남은 공간에 고르게 배치
    blocks = []
    for pt in post["card_points"][:3]:
        heads, descs = wrap(d, pt["head"], f_head, tw), wrap(d, pt["desc"], f_desc, tw)
        blocks.append((heads, descs, len(heads) * 66 + 14 + len(descs) * 54))
    bottom = H - P - 70
    gap = max(60, min(110, (bottom - y - sum(b[2] for b in blocks)) // 3))
    for i, (heads, descs, _) in enumerate(blocks, 1):
        d.text((P, y - 6), f"{i:02d}", font=f_num, fill=c["accent"])
        ly = y
        for line in heads:
            d.text((tx, ly), line, font=f_head, fill=c["text"])
            ly += 66
        ly += 14
        for line in descs:
            d.text((tx, ly), line, font=f_desc, fill=c["muted"])
            ly += 54
        y = max(ly, y + 110) + gap

    d.line([P, H - P - 50, W - P, H - P - 50], fill=c["muted"], width=1)
    d.text((P, H - P - 32), c["brand"], font=f_foot, fill=c["accent"])
    if post.get("sources"):
        src = "출처 " + " · ".join(post["sources"])
        while d.textlength(src, font=f_foot) > W - 2 * P - 300 and " · " in src:
            src = src.rsplit(" · ", 1)[0] + " 외"
        d.text((W - P - d.textlength(src, font=f_foot), H - P - 32), src, font=f_foot, fill=c["muted"])
    img.save(path, "JPEG", quality=92)
    log(f"카드 이미지 저장: {path}")


def upload_image(path):
    key = os.environ.get("IMGBB_API_KEY")
    if not key:
        raise RuntimeError("IMGBB_API_KEY 가 없음 (.env 확인)")
    r = requests.post("https://api.imgbb.com/1/upload",
                      # 인스타·스레드가 가져간 뒤엔 필요 없으니 하루 뒤 자동 삭제
                      data={"key": key, "image": base64.b64encode(path.read_bytes()), "expiration": 86400},
                      timeout=60)
    check(r)
    return r.json()["data"]["url"]


# ---------- 5. 업로드 ----------
class Retryable(Exception):
    """잠시 뒤 다시 시도하면 될 수 있는 오류 (네트워크, 서버 5xx, 429)."""


class NoRetry(Exception):
    """다시 시도하면 안 되는 오류 (실제로는 올라갔을 수 있어서 중복 위험)."""


def check(r):
    if r.status_code == 429 or r.status_code >= 500:
        raise Retryable(f"{r.status_code} {r.text[:300]}")
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r.json()


def call(method, url, **kw):
    try:
        return check(requests.request(method, url, timeout=60, **kw))
    except (requests.ConnectionError, requests.Timeout) as e:
        raise Retryable(str(e))


def publish(url, **kw):
    """게시 확정 요청. 응답 없이 끊기면 이미 올라갔을 수 있으니 재시도하지 않음."""
    try:
        return check(requests.post(url, timeout=60, **kw))
    except (requests.ConnectionError, requests.Timeout) as e:
        raise NoRetry(f"게시 요청 중 연결 끊김 — 실제 게시 여부를 직접 확인하세요: {e}")
    except Retryable as e:
        raise NoRetry(f"게시 요청 서버 오류 — 실제 게시 여부를 직접 확인하세요: {e}")


def meta_wait(api, container_id, token):
    for _ in range(30):
        status = call("GET", f"{api}/{container_id}",
                      params={"fields": "status_code", "access_token": token}).get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError("미디어 처리 실패")
        time.sleep(5)
    raise Retryable("미디어 처리 시간 초과")


def post_instagram(post, image_url, tokens):
    uid, tok = os.environ["IG_USER_ID"], tokens["ig"]
    c = call("POST", f"{IG_API}/{uid}/media",
             data={"image_url": image_url, "caption": post["instagram"], "access_token": tok})
    meta_wait(IG_API, c["id"], tok)
    return publish(f"{IG_API}/{uid}/media_publish", data={"creation_id": c["id"], "access_token": tok})["id"]


def post_threads(post, image_url, tokens):
    uid, tok = os.environ["THREADS_USER_ID"], tokens["threads"]
    data = {"text": post["threads"], "access_token": tok}
    data.update({"media_type": "IMAGE", "image_url": image_url} if image_url else {"media_type": "TEXT"})
    c = call("POST", f"{THREADS_API}/{uid}/threads", data=data)
    meta_wait(THREADS_API, c["id"], tok)
    return publish(f"{THREADS_API}/{uid}/threads_publish",
                   data={"creation_id": c["id"], "access_token": tok})["id"]


def post_x(post):
    import tweepy
    client = tweepy.Client(consumer_key=os.environ["X_API_KEY"],
                           consumer_secret=os.environ["X_API_SECRET"],
                           access_token=os.environ["X_ACCESS_TOKEN"],
                           access_token_secret=os.environ["X_ACCESS_SECRET"])
    text = post["x"].strip() + ("\n" + post["x_link_real"] if post.get("x_link_real") else "")
    try:
        return client.create_tweet(text=text).data["id"]
    except tweepy.errors.TooManyRequests as e:
        raise Retryable(str(e))
    except tweepy.errors.TwitterServerError as e:
        raise NoRetry(f"X 서버 오류 — 실제 게시 여부를 직접 확인하세요: {e}")
    except requests.ConnectionError as e:
        raise NoRetry(f"X 연결 끊김 — 실제 게시 여부를 직접 확인하세요: {e}")


def with_retry(name, fn):
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return fn()
        except Retryable as e:
            if attempt == len(RETRY_WAITS):
                raise
            wait = RETRY_WAITS[attempt]
            log(f"↻ {name} 일시적 오류, {wait}초 뒤 다시 시도: {e}")
            time.sleep(wait)


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
                notify("SNS 자동 업로드", f"{name} 토큰 갱신 실패 — 토큰을 새로 발급해야 할 수 있어요")
        t["refreshed_at"] = dt.datetime.now().isoformat()
        save_json(path, t)
        os.chmod(path, 0o600)
    return t


def available_platforms(cfg):
    """켜져 있어도 키가 비어 있는 플랫폼은 끔 (키를 받는 대로 하나씩 켜지도록)."""
    p = dict(cfg["platforms"])
    for name, keys in REQUIRED_KEYS.items():
        missing = [k for k in keys if not os.environ.get(k)]
        if p.get(name) and missing:
            log(f"– {name}: 키 없음({', '.join(missing)}) → 건너뜀")
            p[name] = False
    return p


def recent_topics(posted, days=7):
    """최근 며칠 동안 실제로 올린 소식 제목들."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return [t for r in posted["runs"] if r.get("ok") and r["date"] >= since for t in r.get("topics", [])]


def run(args):
    load_env()
    cfg = load_json(ROOT / "config.json", {})
    STATE.mkdir(exist_ok=True)
    posted = load_json(STATE / "posted.json", {"urls": [], "runs": []})
    today = dt.date.today().isoformat()

    p = cfg["platforms"]
    if not args.dry_run:
        if any(r["date"] == today and r.get("ok") for r in posted["runs"]):
            log("오늘은 이미 올렸음 → 종료")
            return True
        p = available_platforms(cfg)
        if not any(p.get(k) for k in REQUIRED_KEYS):  # Claude 를 부르기 전에 종료
            log("올릴 수 있는 플랫폼이 없음 (.env 키 확인) → 종료")
            return True
        if (p.get("instagram") or p.get("threads")) and not os.environ.get("IMGBB_API_KEY"):
            raise RuntimeError("인스타·스레드 이미지 업로드에 IMGBB_API_KEY 가 필요함")

    news = collect_news(cfg, set(posted["urls"]))
    if len(news) < 3:
        raise RuntimeError(f"뉴스 후보가 {len(news)}건뿐이라 게시 불가 (피드 확인 필요)")

    recent = recent_topics(posted)
    if recent:
        log(f"최근 7일 다룬 소식 {len(recent)}개 → 반복 피하도록 전달")
    post = add_sources(write_with_claude(cfg, news, recent), news)
    post["x_link_real"] = resolve_google_news(post.get("x_link") or post["used_links"][0])
    post["real_links"] = [resolve_google_news(l) for l in post["used_links"]]
    log("글 검사 통과")

    day_dir = OUTPUT / today
    day_dir.mkdir(parents=True, exist_ok=True)
    save_json(day_dir / "post.json", post)
    card = day_dir / "card.jpg"
    make_card(cfg, post, card)

    if args.dry_run:
        log(f"[테스트 모드] 업로드하지 않음. 결과 확인: {day_dir}")
        print(json.dumps(post, ensure_ascii=False, indent=2))
        return True

    tokens = refresh_tokens({**cfg, "platforms": p})
    image_url = with_retry("이미지 업로드", lambda: upload_image(card)) \
        if (p.get("instagram") or p.get("threads")) else None

    results = {}
    jobs = [("instagram", lambda: post_instagram(post, image_url, tokens)),
            ("threads", lambda: post_threads(post, image_url, tokens)),
            ("x", lambda: post_x(post))]
    for name, fn in jobs:
        if not p.get(name):
            continue
        try:
            results[name] = {"ok": True, "id": with_retry(name, fn)}
            log(f"✅ {name} 업로드 성공")
        except Exception as e:  # 한 곳이 실패해도 나머지는 계속
            results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            log(f"❌ {name} 실패: {e}")

    ok = any(r["ok"] for r in results.values())
    posted["runs"].append({"date": today, "ok": ok, "results": results,
                           "topics": [pt["head"] for pt in post["card_points"]]})
    if ok:
        posted["urls"] = (posted["urls"] + post["used_links"])[-500:]
    save_json(STATE / "posted.json", posted)

    failed = [k for k, r in results.items() if not r["ok"]]
    if failed:
        notify("SNS 자동 업로드 일부 실패", f"실패: {', '.join(failed)} — logs/autopost.log 확인")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        ok = run(args)
    except Exception as e:
        log(f"❌ 중단: {type(e).__name__}: {e}")
        notify("SNS 자동 업로드 중단", str(e))
        sys.exit(1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
