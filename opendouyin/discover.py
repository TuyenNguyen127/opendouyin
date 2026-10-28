"""Bước 1 — Tìm "mỏ" content: lấy video hot trên Bilibili và chấm điểm độ viral."""
import hashlib
import html
import math
import re
import time
import urllib.parse

import requests

from .utils import log

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Bảng hoán vị khoá WBI của Bilibili (bắt buộc cho API search)
_MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
          33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
          26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
          20, 34, 44, 52]


class Bilibili:
    API = "https://api.bilibili.com"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        self._mixin_key: str | None = None

    def _ensure_cookies(self):
        if "buvid3" in self.s.cookies:
            return
        try:
            d = self.s.get(f"{self.API}/x/frontend/finger/spi", timeout=10).json()["data"]
            self.s.cookies.set("buvid3", d["b_3"], domain=".bilibili.com")
            self.s.cookies.set("buvid4", d["b_4"], domain=".bilibili.com")
        except Exception:
            self.s.get("https://www.bilibili.com/", timeout=10)

    def _wbi_key(self) -> str:
        if not self._mixin_key:
            img = self.s.get(f"{self.API}/x/web-interface/nav", timeout=10).json()["data"]["wbi_img"]
            stem = lambda u: u.rsplit("/", 1)[1].split(".")[0]
            orig = stem(img["img_url"]) + stem(img["sub_url"])
            self._mixin_key = "".join(orig[i] for i in _MIXIN)[:32]
        return self._mixin_key

    def _sign(self, params: dict) -> dict:
        params = dict(params, wts=int(time.time()))
        params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(params.items())}
        query = urllib.parse.urlencode(params)
        params["w_rid"] = hashlib.md5((query + self._wbi_key()).encode()).hexdigest()
        return params

    def _get(self, path: str, params: dict | None = None, wbi: bool = False) -> dict:
        self._ensure_cookies()
        params = self._sign(params or {}) if wbi else (params or {})
        r = self.s.get(f"{self.API}{path}", params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"Bilibili API lỗi {body.get('code')}: {body.get('message')}")
        data = body["data"]
        if isinstance(data, dict) and "v_voucher" in data and len(data) == 1:
            raise RuntimeError("Bilibili tạm chặn bằng captcha — thử từ khoá ngắn hơn (1 từ) hoặc đợi vài phút")
        return data

    # --- nguồn video ---
    def popular(self, pages: int = 1) -> list[dict]:
        items = []
        for pn in range(1, pages + 1):
            items += self._get("/x/web-interface/popular", {"ps": 50, "pn": pn}).get("list", [])
        return [_from_archive(v) for v in items]

    def ranking(self, rid: int = 0) -> list[dict]:
        data = self._get("/x/web-interface/ranking/v2", {"rid": rid, "type": "all"})
        return [_from_archive(v) for v in data.get("list", [])]

    def search(self, keyword: str, pages: int = 1, order: str = "click") -> list[dict]:
        items = []
        for page in range(1, pages + 1):
            data = self._get(
                "/x/web-interface/wbi/search/type",
                {"search_type": "video", "keyword": keyword, "page": page, "order": order},
                wbi=True,
            )
            items += data.get("result") or []
        return [_from_search(v) for v in items if v.get("bvid")]


def _from_archive(v: dict) -> dict:
    st = v.get("stat", {})
    return {
        "bvid": v["bvid"],
        "url": f"https://www.bilibili.com/video/{v['bvid']}",
        "title": v.get("title", ""),
        "author": v.get("owner", {}).get("name", ""),
        "views": st.get("view", 0),
        "likes": st.get("like", 0),
        "coins": st.get("coin", 0),
        "favorites": st.get("favorite", 0),
        "shares": st.get("share", 0),
        "comments": st.get("reply", 0),
        "duration": v.get("duration", 0),
        "pubdate": v.get("pubdate", 0),
        "cover": v.get("pic", ""),
    }


def _parse_duration(s) -> int:
    if isinstance(s, int):
        return s
    total = 0
    for part in str(s).split(":"):
        total = total * 60 + int(part or 0)
    return total


def _from_search(v: dict) -> dict:
    cover = v.get("pic", "")
    return {
        "bvid": v["bvid"],
        "url": f"https://www.bilibili.com/video/{v['bvid']}",
        "title": html.unescape(re.sub(r"<[^>]+>", "", v.get("title", ""))),
        "author": v.get("author", ""),
        "views": v.get("play", 0) or 0,
        "likes": v.get("like", 0) or 0,
        "coins": 0,
        "favorites": v.get("favorites", 0) or 0,
        "shares": 0,
        "comments": v.get("review", 0) or 0,
        "duration": _parse_duration(v.get("duration", 0)),
        "pubdate": v.get("pubdate", 0),
        "cover": ("https:" + cover) if cover.startswith("//") else cover,
    }


def score(v: dict) -> float:
    """Điểm viral = quy mô view × tỉ lệ tương tác × độ mới."""
    views = max(v["views"], 1)
    engagement = (v["likes"] + 2 * v["coins"] + 2 * v["favorites"] + 3 * v["shares"]) / views
    days = max((time.time() - (v["pubdate"] or time.time())) / 86400, 0)
    recency = 1 / (1 + days / 30) ** 0.3
    return round(math.log10(views + 1) * (1 + 20 * engagement) * recency, 3)


def discover(cfg) -> list[dict]:
    c = cfg.discover
    bili = Bilibili()
    if c.source == "search":
        if not c.keyword:
            raise ValueError("discover.source=search cần --keyword")
        videos = bili.search(c.keyword, pages=c.pages, order=c.order)
    elif c.source == "ranking":
        videos = bili.ranking()
    else:
        videos = bili.popular(pages=c.pages)
    log.info("Lấy được %d video từ Bilibili (%s)", len(videos), c.source)

    seen, out = set(), []
    for v in videos:
        if v["bvid"] in seen:
            continue
        seen.add(v["bvid"])
        if v["views"] < c.min_views or not (c.min_duration <= v["duration"] <= c.max_duration):
            continue
        v["engagement"] = round(
            (v["likes"] + v["coins"] + v["favorites"] + v["shares"]) / max(v["views"], 1), 4
        )
        v["score"] = score(v)
        out.append(v)
    out.sort(key=lambda v: v["score"], reverse=True)
    return out[: c.limit]
