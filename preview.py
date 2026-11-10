#!/usr/bin/env python3
"""output/날짜/ 의 post.json + card.jpg 로 플랫폼별 미리보기(preview.html, preview.pdf)를 만듦.

사용법:
  .venv/bin/python preview.py              # 오늘
  .venv/bin/python preview.py 2026-11-10   # 특정 날짜
"""
import base64
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def esc(s):
    return html.escape(s or "")


def rich(text, tag_class="tag"):
    """줄바꿈 유지 + 해시태그 색칠."""
    out = esc(text).replace("\n", "<br>")
    return re.sub(r"(#[^\s<#]+)", rf'<span class="{tag_class}">\1</span>', out)


def gauge(n, limit):
    pct = min(100, round(n / limit * 100))
    state = "ok" if pct <= 90 else "warn"
    return f'<div class="gauge {state}"><span style="width:{pct}%"></span></div><small>{n} / {limit}</small>'


def build(day_dir: Path):
    sys.path.insert(0, str(ROOT))
    from autopost import x_weighted_len, LIMITS

    post = json.loads((day_dir / "post.json").read_text())
    cfg = json.loads((ROOT / "config.json").read_text())
    brand = cfg["card"]["brand"].lstrip("@")
    cards = [day_dir / "card.jpg"] + sorted(day_dir.glob("card_*.jpg"))
    imgs = ["data:image/jpeg;base64," + base64.b64encode(c.read_bytes()).decode() for c in cards]
    img = imgs[0]
    slides = "".join(f'<img src="{u}" alt="카드 {i}/{len(imgs)}">' for i, u in enumerate(imgs, 1))
    dots = "".join(f'<i class="{"on" if i == 0 else ""}"></i>' for i in range(len(imgs)))
    date = day_dir.name
    x_text = post["x"].strip()
    x_link = post.get("x_link_real") or ""
    x_len = x_weighted_len(x_text + ("\n" + x_link if x_link else ""))
    domain = re.sub(r"^https?://(www\.|m\.)?", "", x_link).split("/")[0] if x_link else ""

    links = "".join(
        f'<li><b>{i}.</b> {esc(pt["head"])}<br><a href="{esc(u)}">{esc(u)}</a></li>'
        for i, (pt, u) in enumerate(zip(post["card_points"], post.get("real_links", post["used_links"])), 1))

    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>게시물 미리보기 {date}</title>
<style>
:root {{
  --bg:#f4f5f7; --panel:#fff; --ink:#111418; --sub:#6b7280; --line:#e5e7eb;
  --ig:#0095f6; --x:#1d9bf0; --ok:#22c55e; --warn:#f59e0b; --tag:#00376b;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0b0d10; --panel:#16191d; --ink:#e8eaed; --sub:#9aa0a6; --line:#2a2f35; --tag:#7cc0ff;
  }}
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,"Apple SD Gothic Neo",system-ui,sans-serif; line-height:1.55 }}
header {{ max-width:1240px; margin:0 auto; padding:28px 16px 8px }}
header h1 {{ margin:0 0 4px; font-size:22px }}
header p {{ margin:0; color:var(--sub); font-size:14px }}
.grid {{ max-width:1240px; margin:0 auto; padding:16px; display:grid;
  grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:20px; align-items:start }}
.col h2 {{ font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--sub);
  margin:0 0 8px; display:flex; justify-content:space-between; align-items:center; gap:8px }}
.col h2 small {{ font-weight:400; letter-spacing:0; text-transform:none }}
.gauge {{ height:4px; background:var(--line); border-radius:4px; overflow:hidden; margin:0 0 4px }}
.gauge span {{ display:block; height:100% }}
.gauge.ok span {{ background:var(--ok) }} .gauge.warn span {{ background:var(--warn) }}
.meter {{ display:flex; flex-direction:column; align-items:flex-end; min-width:110px }}
.meter .gauge {{ width:110px }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; overflow:hidden }}
.top {{ display:flex; align-items:center; gap:10px; padding:12px 14px }}
.avatar {{ width:34px; height:34px; border-radius:50%; flex:none;
  background:linear-gradient(135deg,#0B3D2E,#A3E635) }}
.name {{ font-weight:600; font-size:14px }} .name small {{ color:var(--sub); font-weight:400 }}
.photo {{ width:100%; display:block }}
.body {{ padding:12px 14px 16px; font-size:14px }}
.tag {{ color:var(--tag) }}
.ig-actions {{ padding:10px 14px 0; font-size:20px; letter-spacing:8px }}
.carousel {{ display:flex; overflow-x:auto; scroll-snap-type:x mandatory; scrollbar-width:none }}
.carousel::-webkit-scrollbar {{ display:none }}
.carousel img {{ flex:0 0 100%; width:100%; scroll-snap-align:start; display:block }}
.ig-actions {{ position:relative }}
.dots {{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); letter-spacing:0; display:flex; gap:4px }}
.dots i {{ width:6px; height:6px; border-radius:50%; background:var(--line); display:block }}
.dots i.on {{ background:var(--ig) }}
.strip {{ display:flex; gap:6px; overflow-x:auto; padding:0 14px 14px }}
.strip img {{ height:220px; border-radius:10px; flex:none }}
.th .body {{ padding-top:0 }}
.th .photo {{ border-radius:10px; width:calc(100% - 28px); margin:10px 14px 14px }}
.linkcard {{ margin:12px 0 0; border:1px solid var(--line); border-radius:14px; overflow:hidden }}
.linkcard div {{ padding:10px 12px; font-size:13px; color:var(--sub) }}
.linkcard b {{ display:block; color:var(--ink); font-weight:500 }}
section.info {{ max-width:1240px; margin:0 auto; padding:0 16px 40px }}
section.info .card {{ padding:16px 18px }}
section.info h3 {{ margin:0 0 8px; font-size:15px }}
section.info ul {{ margin:0; padding-left:0; list-style:none; font-size:13px }}
section.info li {{ margin:0 0 10px; overflow-wrap:anywhere }}
a {{ color:var(--x) }}
@page {{ size:A4; margin:14mm }}
@media print {{
  body {{ background:#fff }}
  header, section.info {{ max-width:560px }}
  .grid {{ display:block; max-width:560px; padding:0 16px }}
  .col {{ break-before:page; padding-top:8px }}
  .col:first-child {{ break-before:auto }}
  .th, .col:last-child {{ break-inside:avoid }}
  .carousel {{ display:grid; grid-template-columns:1fr 1fr; gap:4px; overflow:visible }}
  .dots {{ display:none }}
  .strip {{ display:grid; grid-template-columns:repeat(4,1fr); overflow:visible }}
  .strip img {{ height:auto; width:100% }}
  section.info {{ margin-top:20px }}
  section.info .card {{ break-inside:avoid }}
}}
</style></head><body>
<header>
  <h1>게시물 미리보기 · {date}</h1>
  <p>매일 오전 8시 자동 실행 때 올라갈 내용과 같은 형식입니다. 인스타 이미지는 옆으로 넘겨 볼 수 있습니다. 출처: {esc(", ".join(post.get("sources", [])))}</p>
</header>

<div class="grid">
  <div class="col">
    <h2>Instagram <span class="meter">{gauge(len(post["instagram"]), LIMITS["instagram"])}</span></h2>
    <div class="card">
      <div class="top"><div class="avatar"></div><div class="name">{esc(brand)}</div></div>
      <div class="carousel" id="ig">{slides}</div>
      <div class="ig-actions">♡ ◯ ➤ <span class="dots">{dots}</span></div>
      <div class="body"><b>{esc(brand)}</b> {rich(post["instagram"])}</div>
    </div>
  </div>

  <div class="col th">
    <h2>Threads <span class="meter">{gauge(len(post["threads"]), LIMITS["threads"])}</span></h2>
    <div class="card">
      <div class="top"><div class="avatar"></div><div class="name">{esc(brand)} <small>· 방금</small></div></div>
      <div class="body">{rich(post["threads"])}</div>
      <div class="strip">{slides}</div>
    </div>
  </div>

  <div class="col">
    <h2>X <span class="meter">{gauge(x_len, LIMITS["x_weighted"])}</span></h2>
    <div class="card">
      <div class="top"><div class="avatar"></div><div class="name">{esc(brand)} <small>@{esc(brand)} · 방금</small></div></div>
      <div class="body">{rich(x_text)}<br><a href="{esc(x_link)}">{esc(x_link[:60])}{"…" if len(x_link) > 60 else ""}</a>
        <div class="linkcard"><div><b>{esc(domain)}</b>링크 미리보기는 X가 기사 페이지에서 가져와 표시합니다</div></div>
      </div>
    </div>
  </div>
</div>

<section class="info"><div class="card">
  <h3>오늘 고른 기사 (원문 링크)</h3>
  <ul>{links}</ul>
</div></section>
<script>
const c=document.getElementById('ig'), d=[...document.querySelectorAll('.dots i')];
c && c.addEventListener('scroll',()=>{{const i=Math.round(c.scrollLeft/c.clientWidth);d.forEach((x,j)=>x.classList.toggle('on',i===j))}});
</script>
</body></html>"""
    out = day_dir / "preview.html"
    out.write_text(page)
    pdf = day_dir / "preview.pdf"
    if Path(CHROME).exists():
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", out.as_uri()], capture_output=True, timeout=120)
    return out, (pdf if pdf.exists() else None)


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    h, p = build(ROOT / "output" / day)
    print(h)
    if p:
        print(p)
