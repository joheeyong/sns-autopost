"""핵심 로직 테스트. 실행: .venv/bin/python -m unittest discover tests -v"""
import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autopost as a  # noqa: E402
import requests  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

NEWS = [
    {"title": "이강인 60분, 아틀레티코 1-2 역전패", "link": "https://n/1", "summary": "", "source": "스포츠타임스"},
    {"title": "양승우 FA컵 이변", "link": "https://n/2", "summary": "", "source": "SPOTV NEWS"},
    {"title": "아스널 5연승", "link": "https://n/3", "summary": "", "source": "이투데이"},
    {"title": "맨유 18위", "link": "https://n/4", "summary": "", "source": "TSN KOREA"},
]


def good_post(**over):
    post = {
        "card_title": "오늘의 해외축구 소식",
        "card_points": [{"head": f"소식 {i}", "desc": f"설명 {i}"} for i in range(1, 4)],
        "instagram": "본문\n\n#해외축구 #이강인",
        "threads": "스레드 본문",
        "x": "X 본문 #이강인",
        "x_link": "https://n/1",
        "used_links": ["https://n/1", "https://n/2", "https://n/3"],
        "extra_links": [],
    }
    post.update(over)
    return post


class XLength(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(a.x_weighted_len("abc 123"), 7)       # 영문·숫자 1
        self.assertEqual(a.x_weighted_len("가나다"), 6)         # 한글 2
        self.assertEqual(a.x_weighted_len("https://example.com/very/long/path?q=1"), 23)  # 링크 23


class Validate(unittest.TestCase):
    def test_good_post_passes(self):
        self.assertEqual(a.validate(good_post(), NEWS), [])

    def test_made_up_link_rejected(self):
        probs = a.validate(good_post(used_links=["https://n/1", "https://n/2", "https://fake/9"]), NEWS)
        self.assertTrue(any("후보에 없는 링크" in p for p in probs))

    def test_made_up_extra_link_rejected(self):
        probs = a.validate(good_post(extra_links=["https://fake/9"]), NEWS)
        self.assertTrue(any("후보에 없는 링크" in p for p in probs))

    def test_duplicate_links_rejected(self):
        probs = a.validate(good_post(used_links=["https://n/1"] * 3), NEWS)
        self.assertTrue(any("서로 다른 3개" in p for p in probs))

    def test_too_long_x_rejected(self):
        probs = a.validate(good_post(x="가" * 130), NEWS)
        self.assertTrue(any(p.startswith("x 가 너무 김") for p in probs))

    def test_link_in_body_rejected(self):
        probs = a.validate(good_post(threads="자세히는 https://n/1"), NEWS)
        self.assertTrue(any("threads 본문에 링크" in p for p in probs))

    def test_wrong_card_points_rejected(self):
        probs = a.validate(good_post(card_points=[{"head": "하나", "desc": "둘"}]), NEWS)
        self.assertTrue(any("card_points" in p for p in probs))

    def test_stat_from_article_passes(self):
        pts = [{"head": "h", "desc": "d", "stat": "60분"}] + [{"head": "h", "desc": "d"}] * 2
        self.assertEqual(a.validate(good_post(card_points=pts), NEWS), [])

    def test_made_up_stat_rejected(self):
        pts = [{"head": "h", "desc": "d", "stat": "99골"}] + [{"head": "h", "desc": "d"}] * 2
        probs = a.validate(good_post(card_points=pts), NEWS)
        self.assertTrue(any("지어낸 숫자" in p for p in probs))

    def test_real_score_passes(self):
        m = {"home": "아틀레티코", "away": "바르셀로나", "home_score": 1, "away_score": 2}
        pts = [{"head": "h", "desc": "d", "match": m}] + [{"head": "h", "desc": "d"}] * 2
        self.assertEqual(a.validate(good_post(card_points=pts), NEWS), [])

    def test_made_up_score_rejected(self):
        m = {"home": "아틀레티코", "away": "바르셀로나", "home_score": 3, "away_score": 0}
        pts = [{"head": "h", "desc": "d", "match": m}] + [{"head": "h", "desc": "d"}] * 2
        probs = a.validate(good_post(card_points=pts), NEWS)
        self.assertTrue(any("지어낸 스코어" in p for p in probs))

    def test_empty_field_rejected(self):
        self.assertIn("instagram 가 비어 있음", a.validate(good_post(instagram=""), NEWS))


class Sources(unittest.TestCase):
    def test_sources_before_hashtags_and_include_extra(self):
        post = a.add_sources(good_post(extra_links=["https://n/4"]), NEWS)
        self.assertEqual(post["sources"], ["스포츠타임스", "SPOTV NEWS", "이투데이", "TSN KOREA"])
        lines = post["instagram"].split("\n")
        self.assertTrue(lines[-1].startswith("#"))  # 해시태그가 맨 끝
        self.assertIn("출처: 스포츠타임스, SPOTV NEWS, 이투데이, TSN KOREA", post["instagram"])

    def test_threads_stays_under_limit(self):
        post = a.add_sources(good_post(threads="가" * 499), NEWS)
        self.assertLessEqual(len(post["threads"]), a.LIMITS["threads"])
        self.assertTrue(post["threads"].endswith("이투데이"))


class Wrap(unittest.TestCase):
    def test_lines_fit_and_keep_words(self):
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        f = ImageFont.truetype(a.FONT_PATH, 40)
        text = "선두 바르셀로나에 1-2로 지며 7연승 행진 마감 그리고 아주 긴 문장"
        lines = a.wrap(d, text, f, 400)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(d.textlength(l, font=f) <= 400 for l in lines))
        self.assertEqual(" ".join(lines), text)  # 단어가 잘리지 않음


class Cards(unittest.TestCase):
    def test_carousel_four_images(self):
        import json, tempfile
        cfg = json.loads((Path(a.ROOT) / "config.json").read_text())
        pts = [{"head": "맨유 18위", "desc": "설명", "league": "EPL", "stat": "18위",
                "stat_label": "순위", "color": "#DA291C"},
               {"head": "숫자 없는 소식", "desc": "설명", "color": "잘못된색"},
               {"head": "경기 결과", "desc": "설명", "match": {"home": "아틀레티코 마드리드", "away": "바르셀로나",
                "home_score": 1, "away_score": 2, "home_color": "#CB3524", "away_color": "#A50044"}}]
        with tempfile.TemporaryDirectory() as d:
            paths = a.make_cards(cfg, good_post(card_points=pts, sources=["A"]), Path(d))
            self.assertEqual(len(paths), 4)
            for p in paths:
                self.assertEqual(Image.open(p).size, (1080, 1350))


class XBrowser(unittest.TestCase):
    def test_click_success(self):
        seq = iter(["wait", "clicked", "false", "true"])
        with mock.patch.object(a, "chrome_js", side_effect=lambda js: next(seq)), mock.patch("time.sleep"):
            self.assertTrue(a.click_x_post())

    def test_logged_out_raises(self):
        with mock.patch.object(a, "chrome_js", return_value="login"), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                a.click_x_post()

    def test_failure_notifies(self):
        with mock.patch.object(a, "open_x_compose"), mock.patch.object(a, "notify") as n, \
             mock.patch.object(a, "click_x_post", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                a.post_x_browser({}, good_post(), None)
        n.assert_called_once()


class Platforms(unittest.TestCase):
    def test_missing_keys_skipped(self):
        cfg = {"platforms": {"instagram": True, "threads": True, "x": True}}
        env = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts"}
        with mock.patch.dict(os.environ, env, clear=True):
            p = a.available_platforms(cfg)
        self.assertEqual(p, {"instagram": False, "threads": False, "x": True})

    def test_manual_x_needs_no_keys(self):
        cfg = {"x_mode": "manual", "platforms": {"instagram": True, "threads": True, "x": True}}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(a.available_platforms(cfg), {"instagram": False, "threads": False, "x": True})

    def test_intent_url_has_text_and_link(self):
        from urllib.parse import urlparse, parse_qs
        post = good_post(x="이강인 60분 #이강인", x_link_real="https://news.example/a?b=1&c=2")
        q = parse_qs(urlparse(a.x_intent_url(post)).query)
        self.assertEqual(q["text"], ["이강인 60분 #이강인\nhttps://news.example/a?b=1&c=2"])

    def test_api_without_credits_falls_back_to_compose(self):
        cfg = {"x_mode": "api"}
        with mock.patch.object(a, "post_x", side_effect=a.XNoCredits("402")), \
             mock.patch.object(a, "post_x_browser", return_value="manual") as op:
            self.assertEqual(a.post_x_or_open(cfg, good_post(), None), "manual")
        op.assert_called_once()

    def test_recent_topics_only_successful_last_week(self):
        today = dt.date.today()
        d = lambda n: (today - dt.timedelta(days=n)).isoformat()
        posted = {"runs": [
            {"date": d(1), "ok": True, "topics": ["어제 소식"]},
            {"date": d(2), "ok": False, "topics": ["실패한 날"]},
            {"date": d(10), "ok": True, "topics": ["오래된 소식"]},
        ]}
        self.assertEqual(a.recent_topics(posted), ["어제 소식"])


class Retry(unittest.TestCase):
    def setUp(self):
        self.waits = a.RETRY_WAITS
        a.RETRY_WAITS = [0, 0]

    def tearDown(self):
        a.RETRY_WAITS = self.waits

    def test_retryable_then_success(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise a.Retryable("503")
            return "ok"
        self.assertEqual(a.with_retry("t", flaky), "ok")
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_limit(self):
        with self.assertRaises(a.Retryable):
            a.with_retry("t", mock.Mock(side_effect=a.Retryable("503")))

    def test_no_retry_is_not_retried(self):
        fn = mock.Mock(side_effect=a.NoRetry("timeout"))
        with self.assertRaises(a.NoRetry):
            a.with_retry("t", fn)
        self.assertEqual(fn.call_count, 1)

    def test_publish_timeout_is_no_retry(self):
        # 게시 확정 요청이 끊기면 중복 게시 위험 → 재시도 안 함
        with mock.patch("requests.post", side_effect=requests.Timeout("t")):
            with self.assertRaises(a.NoRetry):
                a.publish("https://x/publish", data={})

    def test_server_error_is_retryable(self):
        resp = mock.Mock(status_code=503, text="busy", ok=False)
        with self.assertRaises(a.Retryable):
            a.check(resp)


if __name__ == "__main__":
    unittest.main()
