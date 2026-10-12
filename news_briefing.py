#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
실제 RSS 기사 수집 -> 개별 기사 -> 관련 기사 그룹 -> 분야 분류 -> TOP 5
원칙: 샘플/가짜 기사는 만들지 않는다. 수집에 실패해도 실패 상태만 기록한다.
  python news_briefing.py --out briefing.json
"""
import argparse, hashlib, json, math, os, re, sys, time, urllib.error, urllib.request
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

KST = timezone(timedelta(hours=9))
CATS = ["정치", "경제", "사회", "국제", "과학·IT", "문화·생활", "주식"]
UA = "Mozilla/5.0 (compatible; todays-news-pwa/2.0)"
GQ = "hl=ko&gl=KR&ceid=KR:ko"
TOP_URL = f"https://news.google.com/rss?{GQ}"


def gt(t):
    return f"https://news.google.com/rss/headlines/section/topic/{t}?{GQ}"


YNA, KHAN, HK = "https://www.yna.co.kr/rss/", "https://www.khan.co.kr/rss/rssdata/", "https://www.hankyung.com/feed/"
MK, SBS, MBC = "https://www.mk.co.kr/rss/", "https://news.sbs.co.kr/news/", "https://imnews.imbc.com/rss/news/"
DONGA, CHOSUN, HANI = "https://rss.donga.com/", "https://www.chosun.com/arc/outboundfeeds/rss/", "https://www.hani.co.kr/rss/"
SBS_HEAD, MK_HEAD = SBS + "headlineRssFeed.do?plink=RSSREADER", MK + "30000001/"
HEAD = {TOP_URL, SBS_HEAD, MK_HEAD}   # 편집 헤드라인류 피드: 노출 순서를 중요도 신호 중 하나로만 사용


def gs(q):   # RSS가 없는 매체는 구글 뉴스 검색(site:)으로 대체 -> 링크가 구글 중계라 본문·이미지는 못 가져옴
    return f"https://news.google.com/rss/search?q={quote(q)}+when:1d&{GQ}"


def sec(src, base, items, suffix=""):   # (분야, 경로) 목록을 피드 튜플로 변환
    return [(src, base + path + suffix, cat, cat) for cat, path in items]


# (매체명 | None=구글이 제공, URL, 분야 | (후보분야...) | None, 표시용 이름)
# 매체가 직접 분류한 RSS 섹션을 우선 사용하므로 제목 키워드 하나로 분야가 정해지지 않는다.
FEEDS = [
    # ---- 종합일간지
    ("한겨레", HANI, None, "전체"),
    *sec("한겨레", HANI, [("정치", "politics/"), ("경제", "economy/"), ("사회", "society/"), ("국제", "international/"), ("문화·생활", "culture/")]),
    *sec("경향신문", KHAN, [("정치", "politic_news.xml"), ("경제", "economy_news.xml"), ("사회", "society_news.xml"), ("국제", "kh_world.xml"),
                         ("문화·생활", "culture_news.xml"), ("과학·IT", "science_news.xml"), ("문화·생활", "life_news.xml")]),
    ("조선일보", CHOSUN + "?outputType=xml", None, "전체"),
    *sec("조선일보", CHOSUN + "category/", [("정치", "politics"), ("경제", "economy"), ("사회", "national"), ("국제", "international"), ("문화·생활", "culture-life")], "/?outputType=xml"),
    ("중앙일보", "https://www.joongang.co.kr/rss", None, "전체"),
    (None, gs("site:joongang.co.kr"), None, "중앙일보(구글 검색)"),
    ("동아일보", DONGA + "total.xml", None, "전체"),
    *sec("동아일보", DONGA, [("정치", "politics.xml"), ("사회", "national.xml"), ("경제", "economy.xml"), ("국제", "international.xml"),
                           ("과학·IT", "science.xml"), ("문화·생활", "culture.xml")]),
    # ---- 방송사
    (None, gs("site:news.kbs.co.kr"), None, "KBS(구글 검색)"),
    *sec("MBC", MBC, [("정치", "news_01.xml"), ("정치", "news_02.xml"), ("국제", "news_03.xml"), ("경제", "news_04.xml"), ("사회", "news_05.xml"),
                      ("문화·생활", "news_06.xml"), ("과학·IT", "news_08.xml")]),
    ("SBS", SBS_HEAD, None, "이 시각 이슈"),
    ("SBS", SBS + "newsflashRssFeed.do?plink=RSSREADER", None, "최신"),
    *sec("SBS", SBS + "SectionRssFeed.do?sectionId=", [("정치", "01"), ("경제", "02"), ("사회", "03"), ("국제", "07"), ("문화·생활", "08"), ("문화·생활", "14")], "&plink=RSSREADER"),
    (None, gs("site:ytn.co.kr"), None, "YTN(구글 검색)"),
    # ---- 경제지
    *sec("한국경제", HK, [("주식", "finance"), ("경제", "economy"), ("경제", "realestate"), ("과학·IT", "it"), ("정치", "politics"),
                        ("사회", "society"), ("국제", "international"), ("문화·생활", "life"), ("문화·생활", "entertainment")]),
    ("매일경제", MK_HEAD, None, "헤드라인"),
    *sec("매일경제", MK, [("경제", "30100041/"), ("경제", "50100032/"), ("경제", "50300009/"), ("주식", "50200011/"), ("국제", "30300018/"), ("사회", "50400012/")]),
    ("매일경제", MK + "50000001/", None, "전체 기사"),
    # ---- 통신사
    *sec("연합뉴스", YNA, [("정치", "politics.xml"), ("경제", "economy.xml"), ("사회", "society.xml"), ("국제", "international.xml"), ("문화·생활", "culture.xml")]),
    # ---- 구글 뉴스
    (None, gt("NATION"), ("정치", "사회"), "NATION"), (None, gt("WORLD"), "국제", "WORLD"), (None, gt("BUSINESS"), "경제", "BUSINESS"),
    (None, gt("TECHNOLOGY"), "과학·IT", "TECHNOLOGY"), (None, gt("SCIENCE"), "과학·IT", "SCIENCE"),
    (None, gt("ENTERTAINMENT"), "문화·생활", "ENTERTAINMENT"), (None, gt("HEALTH"), "문화·생활", "HEALTH"),
    (None, TOP_URL, None, "주요 뉴스"),  # 구글 뉴스 주요 뉴스
]

KW = {  # 분야가 불분명한 피드에서만 사용: 서로 다른 키워드 2개 이상 + 2위와 격차가 있어야 분류
    "정치": "대통령 국회 여당 야당 국민의힘 민주당 총리 장관 대통령실 탄핵 선거 공천 특검 법안 국정감사 의원 청문회",
    "경제": "금리 환율 물가 수출 무역 기업 부동산 실적 투자 은행 예산 세금 관세 성장률 고용 대출 아파트 분양",
    "사회": "경찰 검찰 법원 재판 화재 붕괴 사망 구조 폭행 학교 교육 노조 파업 병원 한파 폭우 지진 구속 체포 실종",
    "국제": "미국 중국 일본 러시아 우크라이나 유럽 이스라엘 가자 트럼프 푸틴 시진핑 유엔 나토 이란 대만 백악관 영국 프랑스 독일 중동",
    "과학·IT": "AI 인공지능 반도체 스마트폰 갤럭시 아이폰 소프트웨어 우주 로켓 연구팀 배터리 로봇 누리호 챗GPT 클라우드 해킹",
    "문화·생활": "영화 드라마 가수 배우 콘서트 예능 공연 전시 여행 음식 건강 패션 웹툰 앨범 개봉 시청률 축제",
    "주식": "코스피 코스닥 증시 주가 종목 외국인 순매수 상장 공모주 시가총액 ETF 급등 급락 개미 투자자",
}
KW = {k: v.split() for k, v in KW.items()}
COMPAT = {frozenset(p) for p in [("정치", "사회"), ("경제", "주식"), ("경제", "과학·IT")]}

FINAL = ["타결", "철회", "진화", "해제", "종료", "종식", "합의", "정상화", "운행재개", "완료", "검거", "마무리", "동결", "확정", "가결"]
HEDGE = ["불발", "무산", "결렬", "난항", "요구", "촉구", "거부", "반발", "가능성", "전망", "임박", "예고", "검토", "시사"]
ONGOING = HEDGE + ["돌입", "확산", "발생", "시작", "대치", "진행", "속보", "긴급", "비상", "경고", "발령", "급증", "격화"]
DERIV = ["이후", "여파", "파장", "에도"]
_STATE = re.compile("(?:" + "|".join(map(re.escape, sorted(set(FINAL + ONGOING), key=len, reverse=True))) + ")"
                    "(?:에도|에는|으로|로|은|는|이|가|을|를|도|됐다|했다|한다|된다|되며|하며|하고)?")
STOP = set("""단독 종합 속보 오늘 어제 내일 관련 대한 위해 이번 지난 최근 전격 결국 극적 사진 영상 포토 현장 논란 상황 한편 기자
사고 사건 지시 발표 계획 추진 논의 의혹 조사 수사 대책 회의 회장 대표 정부 올해 다시 이후 여파 파장 영향 전망 가능성 오전 오후
개최 예정 확대 강화 추가 처음 최대 최고 역대 신규 주요 공개 제기 주장 요구 촉구 강조 밝혀 밝혔다 대통령 노조 총파업 파업 협상""".split())  # 여러 사건에 공통으로 나오는 일반어는 근거에서 제외
JOSA2, JOSA1 = ("에서", "으로", "에게", "까지", "부터"), tuple("은는이가을를의에도로과와")


# ---------------------------------------------------------------- 수집
def clean(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_dt(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=KST)).astimezone(KST)


def canon(u):
    p = urlsplit(u)
    q = [] if p.netloc.endswith("news.google.com") else [
        (k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in ("fbclid", "gclid")]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urlencode(q), ""))


_BRACKET = re.compile(r"^[\(\[【<〈《]\s*[^\)\]】>〉》]{1,25}[\)\]】>〉》]\s*")
_BYLINE = re.compile(r"^(?:[가-힣A-Za-z0-9·]{1,10}\s+)?[가-힣]{2,4}\s*(?:선임|수석|인턴)?(?:기자|특파원|통신원)(?![가-힣])\s*(?:[=ㅣ|/]\s*)?")
_OUTLET = re.compile(r"기자|특파원|뉴스|뉴시스|연합|일보|신문|경제|방송|미디어|통신|TV")


def clean_summary(title, d):
    """매체 제공 요약 정리: 제목 반복·(서울=연합뉴스)·기자 이름·사진/저작권 문구를 지우고, 잘린 끝은 마지막 완결 문장까지만 남김"""
    t = d.strip()
    if t.startswith(title):
        t = t[len(title):].strip()
    elif title[:12] in t[:len(title) + 10]:
        return ""                                            # 제목을 변형해 반복한 설명(구글 뉴스형)은 요약이 아님
    for _ in range(4):                                      # 앞쪽 군더더기를 반복해서 제거
        n = t
        t = re.sub(r"^(?:입력|등록|수정)\s*\d{4}[-.]\d{1,2}[-.]\d{1,2}\.?\s*(?:\d{1,2}:\d{2})?\s*", "", t)
        t = _BRACKET.sub("", t)                             # (서울=연합뉴스) [속보] 【앵커】 등
        t = _BYLINE.sub("", t)                              # 홍길동 기자 =
        m = re.match(r"^(.{0,40}?)\s*=\s*", t)             # 서울=뉴시스 홍길동 기자 = 본문
        if m and _OUTLET.search(m.group(1)):
            t = t[m.end():]
        t = t.lstrip(" -·:|ㅣ=)]】")
        if t == n:
            break
    t = re.sub(r"[\(\[]\s*(?:사진|영상|자료사진|그래픽)[^\)\]]{0,40}[\)\]]", " ", t)          # (사진=OOO)
    t = re.sub(r"\S+@\S+\.\S+", " ", t)                                                 # 이메일
    t = re.split(r"ⓒ|무단\s*전재|저작권자|\(끝\)|▶", t)[0]                                  # 저작권/연관기사 문구 이후 삭제
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 25:
        return ""
    if not re.search(r"[.!?。”\"')\]]$", t):                  # 잘린 끝 -> 마지막 완결 문장까지
        k = max(t.rfind(". "), t.rfind("? "), t.rfind("! "))
        t = t[:k + 1] if k >= 20 else ""
    return t


def item_image(it, raw_desc):
    """RSS 항목의 이미지: media:thumbnail/content, enclosure, 설명 속 <img> 순서"""
    for ch in it:
        t, u = ch.tag.rsplit("}", 1)[-1], ch.attrib.get("url", "")
        if u and (t == "thumbnail" or (t in ("content", "enclosure") and (
                "image" in ch.attrib.get("type", "") + ch.attrib.get("medium", "") or re.search(r"\.(jpe?g|png|webp)", u, re.I)))):
            return u.replace("http://", "https://", 1)
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw_desc or "")
    return m.group(1).replace("http://", "https://", 1) if m else ""


def parse_feed(xml, feed_src):
    out = []
    for it in ET.fromstring(xml).iter("item"):
        title, link = clean(it.findtext("title")), (it.findtext("link") or "").strip()
        t = parse_dt(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if not title or not link or not t:
            continue                                   # 발행 시각을 알 수 없는 항목은 쓰지 않음
        s = it.find("source")
        src = clean(s.text) if s is not None and s.text else (feed_src or "")
        if src and title.endswith(" - " + src):
            title = title[:-len(src) - 3]
        elif not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        if not src:
            src = urlsplit(link).netloc.replace("www.", "")   # 출처 표기가 없으면 도메인으로 표시
        if not src:
            continue
        d = clean(it.findtext("description"))
        summ = clean_summary(title, d)
        out.append({"title": title.strip(), "url": link, "source": src.strip(), "t": t,
                    "summary": summ[:500],
                    "image": item_image(it, it.findtext("description"))})
    return out


def fix_tz(items, now):
    """일부 매체 RSS는 한국시간 값을 GMT로 표기해서, 그대로 변환하면 9시간 뒤(미래)로 나온다.
    가장 최신 기사가 현재보다 1.5시간 넘게 미래이고 9시간을 빼면 현재 이내로 들어오면 그 피드 전체를 -9시간 보정."""
    if len(items) < 3:
        return 0
    top = max(a["t"] for a in items)
    if top > now + timedelta(hours=1.5) and top - timedelta(hours=9) <= now + timedelta(minutes=30):
        for a in items:
            a["t"] -= timedelta(hours=9)
        return -9
    return 0


def kw_classify(title, allowed=None):
    sc = sorted(((sum(k in title for k in KW[c]), c) for c in (allowed or CATS)), reverse=True)
    second = sc[1][0] if len(sc) > 1 else 0
    return sc[0][1] if sc[0][0] >= 2 and sc[0][0] - second >= 1 else "미분류"


def collect(hours, now):
    def job(f):
        try:
            req = urllib.request.Request(f[1], headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return f, parse_feed(r.read(), f[0]), None
        except Exception as e:
            return f, [], f"{type(e).__name__}: {e}"[:100]

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(job, FEEDS))
    since, arts, status, seen = now - timedelta(hours=hours), {}, [], {}
    for f, items, err in results:
        fix = fix_tz(items, now)
        status.append({"name": f"{f[0] or 'Google 뉴스'} · {f[3] if len(f) > 3 else urlsplit(f[1]).path.rsplit('/', 1)[-1]}",
                       "ok": err is None, "count": len(items), "error": err, "tz_fix": fix})
        for pos, a in enumerate(items):
            if not (since <= a["t"] <= now + timedelta(hours=1)):
                continue
            fc = f[2]
            cat = fc if isinstance(fc, str) else kw_classify(a["title"], fc)
            tp = 1 - pos / len(items) if f[1] in HEAD else 0
            key, key2 = canon(a["url"]), (a["source"], re.sub(r"\W", "", a["title"]).lower())
            prev = arts.get(key) or seen.get(key2)       # 같은 URL 또는 같은 매체의 동일 제목만 중복 처리
            rel = isinstance(fc, str)                          # 매체가 직접 분류한 분야인지(키워드 추정보다 우선)
            if prev:
                prev["tp"] = max(prev["tp"], tp)
                if cat != "미분류" and (prev["cat"] == "미분류" or (rel and not prev.get("rel"))):
                    prev["cat"], prev["rel"] = cat, rel
                if not prev.get("image") and a.get("image"):
                    prev["image"] = a["image"]
                if not prev.get("summary") and a.get("summary"):
                    prev["summary"] = a["summary"]
                continue
            a.update(id=hashlib.sha1(key.encode()).hexdigest()[:10], cat=cat, tp=tp, rel=rel)
            arts[key] = seen[key2] = a
    for a in arts.values():
        if a["cat"] == "경제" and sum(k in a["title"] for k in KW["주식"]) >= 2:
            a["cat"] = "주식"
        if a["cat"] == "미분류" and a["tp"]:
            a["cat"] = kw_classify(a["title"])
    return list(arts.values()), status


# ---------------------------------------------------------------- 관련 기사 묶기
def stage(title):
    t = title.replace(" ", "")
    if any(w in t for w in DERIV):
        return "neutral"
    fin = any(w in t for w in FINAL)
    if fin and not any(h in t for h in HEDGE):
        return "final"
    return "ongoing" if any(w in t for w in ONGOING) else "neutral"


def prep(a):
    a["stage"] = stage(a["title"])
    s = re.sub(r"[^\w\s]", " ", _STATE.sub(" ", a["title"]))
    toks = []
    for t in re.findall(r"[가-힣]{2,}|[A-Za-z0-9]{2,}", s):
        if t.isascii():
            t = t.lower()
        elif len(t) >= 4 and t.endswith(JOSA2):
            t = t[:-2]
        elif len(t) >= 3 and t.endswith(JOSA1):
            t = t[:-1]
        if len(t) >= 4 and t.endswith(("사고", "사건")):
            t = t[:-2]                                   # 붕괴사고 -> 붕괴
        if t not in STOP and not t.endswith(("됐다", "했다", "한다", "된다")):
            toks.append(t)
    a["tok"] = set(toks)


def link_score(a, b, w):
    """같은 사건일 때만 0보다 큰 값. 애매하면 0(=합치지 않음)."""
    if abs((a["t"] - b["t"]).total_seconds()) > 30 * 3600:
        return 0
    if "미분류" not in (a["cat"], b["cat"]) and a["cat"] != b["cat"] and frozenset((a["cat"], b["cat"])) not in COMPAT:
        return 0                                         # 분야가 다르면 합치지 않음
    if len(a["tok"]) < 3 or len(b["tok"]) < 3:
        return 0
    sh = a["tok"] & b["tok"]
    if not (len(sh) >= 3 or (len(sh) == 2 and any(len(t) >= 3 for t in sh))):  # 공통 3개 이상, 또는 2개 중 고유어(3자↑) 포함
        return 0
    return sum(w[t] for t in sh) / sum(w[t] for t in a["tok"] | b["tok"])  # 희귀 단어일수록 가중


def cluster(arts, w, link=0.3, avg=0.2):
    n, sim = len(arts), {}
    for i in range(n):
        for j in range(i + 1, n):
            s = link_score(arts[i], arts[j], w)
            if s >= link:
                sim[(i, j)] = s
    cl, where = {i: [i] for i in range(n)}, list(range(n))
    for (i, j), s in sorted(sim.items(), key=lambda x: -x[1]):
        ci, cj = where[i], where[j]
        if ci == cj:
            continue
        A, B = cl[ci], cl[cj]
        if sum(sim.get((min(x, y), max(x, y)), 0) for x in A for y in B) / (len(A) * len(B)) >= avg:  # 연쇄 병합 방지
            cl[ci] = A + B
            for x in B:
                where[x] = ci
            del cl[cj]
    return [[arts[i] for i in c] for c in cl.values()]


# ---------------------------------------------------------------- 그룹/TOP 5
def make_group(members, now):
    m = sorted(members, key=lambda a: a["t"])
    pool = [a for a in m if a["stage"] != "neutral"] or m          # 기존 기사 중 최신의 결정적 보도를 대표로 (제목을 새로 만들지 않음)
    last = max(a["t"] for a in pool)
    rep = sorted([a for a in pool if a["t"] == last], key=lambda a: a["stage"] != "final")[0]
    cats = Counter(a["cat"] for a in m if a["cat"] != "미분류")
    srcs, tp = len({a["source"] for a in m}), max(a["tp"] for a in m)
    age = (now - m[-1]["t"]).total_seconds() / 3600
    rec = 1.0 if age <= 6 else 0.6 if age <= 12 else 0.3 if age <= 24 else 0
    summ = rep["summary"] or next((a["summary"] for a in reversed(m) if a["summary"]), "")
    reasons = [f"{srcs}개 언론사 보도"] if srcs > 1 else []
    if tp >= 0.4:
        reasons.append("주요 뉴스 피드 상위 노출")
    if age <= 6:
        reasons.append("최근 6시간 내 보도")
    return {"id": "g_" + rep["id"], "rep_id": rep["id"], "article_ids": [a["id"] for a in m],
            "category": cats.most_common(1)[0][0] if cats else "미분류", "summary": summ, "summary_src": "feed" if summ else None,
            "status": "상황 종료" if rep["stage"] == "final" and len(m) > 1 else None,
            "source_count": srcs, "latest": m[-1]["t"], "tp": tp, "age": age,
            "score": round(2 * min(srcs, 6) + 5 * tp + 3 * rec + 0.5 * math.log1p(len(m)), 2), "reasons": reasons}


def build(arts, now, per_cat=40):
    for a in arts:
        prep(a)
    df = Counter(t for a in arts for t in a["tok"])
    w = {t: math.log((len(arts) + 1) / (c + 1)) + 1 for t, c in df.items()}
    groups = [make_group(c, now) for c in cluster(arts, w)]
    # 헤드라인(1위) + TOP 5(2~6위) = 6건: 근거(여러 언론사 보도 또는 구글 주요 뉴스 상위 노출)가 있는 그룹만, 같은 사건은 1회만
    ev = sorted([g for g in groups if (g["source_count"] > 1 or g["tp"] >= 0.4) and g["age"] <= 24],
                key=lambda g: -g["score"])
    mode = "ranked" if len(ev) >= 6 else ("list" if ev else "none")
    top_items = [{"group_id": g["id"], "rank": i + 1 if mode == "ranked" else None, "score": g["score"],
                  "reasons": g["reasons"]} for i, g in enumerate(ev[:6])]
    by_cat = {c: [g["id"] for g in sorted((g for g in groups if g["category"] == c), key=lambda g: g["latest"], reverse=True)[:per_cat]]
              for c in CATS + ["미분류"]}
    keep = {i for ids in by_cat.values() for i in ids} | {t["group_id"] for t in top_items}
    kg = [g for g in groups if g["id"] in keep]
    ids = {i for g in kg for i in g["article_ids"]}
    out_arts = [{"id": a["id"], "title": a["title"], "url": a["url"], "source": a["source"],
                 "published_at": a["t"].isoformat(), "category": a["cat"], "summary": a["summary"], "image": a.get("image", "")}
                for a in arts if a["id"] in ids]
    for g in kg:
        g["latest"] = g["latest"].isoformat()
        for k in ("tp", "age", "score", "reasons"):
            g.pop(k)
    note = "보도량·노출 신호로 앱이 자동 선정한 결과이며 언론사·편집국의 공식 순위가 아닙니다."
    if mode == "list":
        note = "주요 이슈가 충분하지 않아 순위 없이 '주요 뉴스'로 보여드려요. " + note
    return out_arts, kg, {"mode": mode, "note": note, "items": top_items}, by_cat


SUM_PROMPT = ("아래 기사 본문을 읽고, 독자가 원문을 보지 않아도 사건의 핵심(누가·무엇을·언제·왜, 현재 상태)을 알 수 있게 "
              "3~4문장으로 요약하세요. 본문에 없는 내용은 쓰지 말고, 각 문장은 '~다.'로 끝내고, 요약문만 출력하세요.")


_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "nav", "aside", "footer", "header", "form", "iframe", "svg", "button", "figcaption", "select"}
_SKIPCLS = re.compile(r"(?:^|[-_\s])(?:ads?|adv\w*|adsense|banner|comment\w*|reply|related\w*|recommend\w*|share|sns|copyright|"
                      r"byline|reporter|login|promo\w*|sponsor\w*|popular|rank\w*|breadcrumb|toolbar)(?:[-_\s]|$)", re.I)


class _Body(HTMLParser):
    """<p> 문단만 모으되 광고·댓글·추천기사·공유·푸터 같은 영역 안의 문단은 버린다. <article> 안의 문단을 우선 사용."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.st, self.cur, self.all, self.art = [], None, [], []

    def blocked(self):
        return any(b for _, b in self.st)

    def flush(self):
        if self.cur is not None:
            t = "".join(self.cur).strip()
            if t:
                self.all.append(t)
                if any(x == "article" for x, _ in self.st):
                    self.art.append(t)
        self.cur = None

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        if tag == "p":
            self.flush()
        cls = " ".join(v or "" for k, v in attrs if k in ("class", "id"))
        self.st.append((tag, tag in _SKIP or bool(_SKIPCLS.search(cls))))
        if tag == "p" and not self.blocked():
            self.cur = []

    def handle_endtag(self, tag):
        if tag in _VOID or tag not in [t for t, _ in self.st]:
            return
        if tag == "p":
            self.flush()
        while self.st and self.st.pop()[0] != tag:
            pass

    def handle_data(self, d):
        if self.cur is not None and not self.blocked():
            self.cur.append(d)


def extract_paras(h):
    p = _Body()
    try:
        p.feed(h)
        p.flush()
    except Exception:
        pass
    out = []
    for t in (p.art if len(p.art) >= 2 else p.all):
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) < 30:
            continue
        if re.search(r"ⓒ|무단\s*전재|저작권자|재배포\s*금지", t):
            break                                            # 저작권 문구부터는 본문이 아님
        if re.search(r"\S+@\S+\.\S+|▶|▷|구독하기|기사제보|댓글", t):
            continue
        out.append(t)
    if out:
        out[0] = clean_summary("\x00", out[0]) or out[0]      # 첫 문단의 (서울=연합뉴스) 기자 표기 제거
    return out


def cut_body(paras, cap):
    """보관할 본문 분량 제한(문단 단위). cap<=0이면 본문을 저장하지 않음."""
    if cap <= 0:
        return [], False
    out, n = [], 0
    for p in paras:
        if n >= cap:
            return out, True
        out.append(p)
        n += len(p)
    return out, False


def fetch_page(url):
    """기사 페이지에서 (요약 입력용 본문 텍스트, og:image, 본문 문단 목록) 추출"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as r:
            h = r.read().decode("utf-8", "ignore")
    except Exception:
        return "", "", []
    img = ""
    for pat in (r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
                r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']"):
        m = re.search(pat, h, re.I)
        if m:
            img = unescape(m.group(1)).strip()
            break
    img = "https:" + img if img.startswith("//") else img.replace("http://", "https://", 1)
    paras = extract_paras(h)
    t = " ".join(paras)[:3500]
    return (t if len(t) >= 300 else ""), (img if img.startswith("https://") else ""), paras


def fetch_text(url):
    return fetch_page(url)[0]


def prio(top, by_cat, n):
    """처리 우선순위: TOP -> 분야별 최신 기사를 분야 번갈아"""
    order = [i["group_id"] for i in top["items"]]
    for r in range(n):
        order += [by_cat[c][r] for c in CATS + ["미분류"] if len(by_cat.get(c, [])) > r]
    return order


NO_BODY = ("SBS",)   # 본문 추출 결과가 기사 목록 등으로 잘못 잡히는 매체는 본문을 저장하지 않음


def nobody(a):
    return any(k in a["source"] for k in NO_BODY)


def fill_pages(arts, groups, top, by_cat, old):
    """원문 페이지에서 대표 이미지(og:image)와 본문 일부를 추출(링크가 직접 열리는 기사만).
    광고·댓글 영역은 건너뛰고, 본문은 NEWS_BODY_CHARS(기본 1200자)까지만 보관. 결과(없음 포함)는 캐시."""
    A, G = {a["id"]: a for a in arts}, {g["id"]: g for g in groups}
    cap = int(os.environ.get("NEWS_BODY_CHARS") or 1200)
    for a in arts:
        o = old.get(a["id"], {})
        a["image"] = a.get("image") or o.get("image", "")
        if "body" in o and not nobody(a):
            a["body"], a["body_more"] = o["body"], o.get("body_more", False)
    todo = []
    for gid in prio(top, by_cat, 40):
        g = G.get(gid)
        if not g:
            continue
        ids = [g["rep_id"]] + [x for x in g["article_ids"] if x != g["rep_id"]]
        if any(A[i].get("body") for i in ids) and any(A[i]["image"] for i in ids):
            continue
        c = next((A[i] for i in ids if "news.google.com" not in A[i]["url"] and "body" not in A[i] and not nobody(A[i])), None)
        if c:
            todo.append(c)
    todo = todo[:int(os.environ.get("NEWS_PAGE_MAX") or 60)]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda a: fetch_page(a["url"]), todo))
    for a, (text, img, paras) in zip(todo, results):
        a["image"] = a["image"] or img
        a["_text"] = text
        a["body"], a["body_more"] = cut_body(paras, cap)
    for g in groups:
        g["image"] = next((A[i]["image"] for i in [g["rep_id"]] + g["article_ids"] if A[i]["image"]), "")


LAST_ERR = ""


def ask_llm(prompt):
    """Gemini API(무료 등급 가능). 429(분당 한도)면 한 번 기다렸다가 재시도."""
    global LAST_ERR
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash-lite"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.2}}).encode()
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 data=body, headers={"x-goog-api-key": key, "content-type": "application/json"})
    for wait in (0, 25):
        time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                parts = json.load(r)["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip() or None
        except urllib.error.HTTPError as e:
            if e.code != 429:
                LAST_ERR = f"HTTP {e.code} (모델명 '{model}' 또는 키 확인)"
                print("  요약 실패: " + LAST_ERR, file=sys.stderr)
                return None
        except Exception as e:
            LAST_ERR = str(e)[:80]
            print(f"  요약 실패: {e}", file=sys.stderr)
            return None
    LAST_ERR = "429 사용 한도 초과"
    return None


def add_summaries(groups, arts, top, by_cat, prev):
    """노출되는 모든 그룹을 요약. 같은 대표 기사는 캐시 재사용, 1회 실행당 신규 요약 상한(NEWS_SUMMARY_MAX, 기본 40).
    우선순위: TOP -> 분야별 최신 기사를 분야 번갈아 가며. 본문을 못 읽으면 요약하지 않음(지어내지 않음)."""
    A, G = {a["id"]: a for a in arts}, {g["id"]: g for g in groups}
    for g in groups:
        if prev.get(g["rep_id"]):
            g["summary"], g["summary_src"] = prev[g["rep_id"]], "ai"
    if not os.environ.get("GEMINI_API_KEY"):
        return {"state": "no_key"}
    order = prio(top, by_cat, 15)
    todo, seen = [], set()
    for gid in order:
        g = G.get(gid)
        if g and gid not in seen and g.get("summary_src") != "ai":
            seen.add(gid)
            todo.append(g)
    todo = todo[:int(os.environ.get("NEWS_SUMMARY_MAX", "30"))]

    def work(g):
        texts = []
        for i in [g["rep_id"]] + [x for x in g["article_ids"] if x != g["rep_id"]]:
            a = A[i]
            t = a.get("_text") or ("" if "news.google.com" in a["url"] else fetch_text(a["url"]))  # 구글 중계 링크는 본문 접근 불가
            if t:
                texts.append(f"[{a['source']}] {a['title']}\n{t}")
            if len(texts) == 2:
                break
        if not texts:
            return g, None, False
        return g, ask_llm(SUM_PROMPT + "\n\n" + "\n\n".join(texts)), True

    done = fails = no_body = failed = 0
    for g in todo:
        g, summ, called = work(g)
        if not called:
            no_body += 1                    # 본문을 읽을 수 없는 기사(구글 중계 링크 등)
            continue
        if summ:
            g["summary"], g["summary_src"], done, fails = summ, "ai", done + 1, 0
        else:
            failed, fails = failed + 1, fails + 1
            if fails >= 4:                  # 연속 실패 = 일일/분당 한도 가능성 -> 이번 실행은 중단
                print("  연속 실패로 이번 실행의 AI 요약을 중단합니다.", file=sys.stderr)
                break
        time.sleep(4)                       # 무료 등급 분당 한도 보호
    cached = sum(1 for g in groups if g.get("summary_src") == "ai") - done
    print(f"  AI 요약 신규 {done}건 (대상 {len(todo)}건, 본문 없음 {no_body}건, 실패 {failed}건, 캐시 {cached}건)", file=sys.stderr)
    return {"state": "ok", "new": done, "targets": len(todo), "no_body": no_body, "failed": failed, "cached": cached, "error": LAST_ERR}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="briefing.json")
    ap.add_argument("--hours", type=int, default=36)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    now = datetime.now(KST)
    arts, feeds = collect(args.hours, now)
    ok = sum(f["ok"] for f in feeds)
    state = "failed" if not ok else "empty" if not arts else "partial" if ok < len(feeds) else "ok"
    msg = {"failed": "모든 뉴스 피드 수집에 실패했어요.", "empty": "피드는 응답했지만 최근 기사가 없어요.",
           "partial": f"일부 피드 {len(feeds) - ok}곳 수집에 실패했어요.", "ok": ""}[state]
    last_ok = now.isoformat() if state != "failed" else None
    if state == "failed" and os.path.exists(args.out):
        try:
            last_ok = json.load(open(args.out, encoding="utf-8")).get("status", {}).get("last_success_at")
        except Exception:
            pass
    a_out, groups, top, by_cat = build(arts, now) if arts else ([], [], {"mode": "none", "note": "", "items": []}, {})
    old = {}
    if os.path.exists(args.out):
        try:
            old = json.load(open(args.out, encoding="utf-8"))
        except Exception:
            old = {}
    prev = {g["rep_id"]: g["summary"] for g in old.get("groups", []) if g.get("summary_src") == "ai"}
    fill_pages(a_out, groups, top, by_cat, {a["id"]: a for a in old.get("articles", [])})
    sm = add_summaries(groups, a_out, top, by_cat, prev)
    for a in a_out:
        a.pop("_text", None)                                   # 요약 입력용 임시 텍스트는 저장하지 않음
    data = {"generated_at": now.isoformat(),
            "status": {"state": state, "message": msg, "last_success_at": last_ok, "article_count": len(arts), "feeds": feeds, "summary": sm},
            "articles": a_out, "groups": groups, "top": top, "by_category": by_cat}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[{state}] 피드 {ok}/{len(feeds)} 성공, 기사 {len(arts)}건, 그룹 {len(groups)}개, TOP 모드={top['mode']}", file=sys.stderr)
    for f in feeds:
        if not f["ok"]:
            print(f"  ✗ {f['name']}: {f['error']}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
