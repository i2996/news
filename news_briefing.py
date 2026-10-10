#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
오늘의 핵심 Top 5 브리핑 파이프라인

핵심 아이디어
  1) 기사 제목에서 '상태어(돌입/타결/철회...)'를 제거한 뒤 유사도를 계산한다.
     -> "총파업 돌입"과 "파업 철회"가 같은 토픽으로 묶인다.
  2) 토픽(Storyline) 단위로 점수를 매겨 Top N을 뽑는다. (토픽당 1슬롯)
  3) 토픽 안에서는 발행시각 순으로 정렬하고, 가장 최신의 '결정적(상태어 포함)' 기사를
     대표 헤드라인으로 삼는다. 타임라인은 상태가 바뀌는 지점만 요약한다.

의존성: 표준 라이브러리만 사용 (LLM 보정은 선택: pip install anthropic)

사용 예
  python news_briefing.py --demo                       # 샘플 데이터로 동작 확인
  python news_briefing.py                              # 구글 뉴스 RSS 기본 피드
  python news_briefing.py --feeds URL1 URL2 --hours 24 --top 5 --out briefing.json
  python news_briefing.py --llm                        # ANTHROPIC_API_KEY 필요, 토픽명/타임라인 문장 보정
"""
import argparse
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

KST = timezone(timedelta(hours=9))

DEFAULT_FEEDS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko",
]

# ---------------------------------------------------------------------------
# 상태어 사전 (도메인에 맞게 자유롭게 추가/수정)
# ---------------------------------------------------------------------------
# 최종 결과/종료 상태
FINAL_WORDS = [
    "타결", "철회", "진화", "해제", "종료", "종식", "합의", "정상화",
    "운행재개", "완료", "검거", "마무리", "귀가", "복구완료",
    "동결", "확정", "가결",
]
# 후속/파생 기사 (최종어가 있어도 '그 사건의 최종 상태 기사'로 보지 않음)
DERIVATIVE_WORDS = ["이후", "여파", "파장", "에도"]
# 이 단어가 같이 있으면 '최종'이 아니라 아직 진행 중으로 본다 (예: 철회 요구, 타결 임박)
HEDGE_WORDS = [
    "불발", "무산", "결렬", "난항", "요구", "촉구", "거부", "반발",
    "가능성", "전망", "임박", "예고", "검토", "시사", "압박",
]
# 진행/중간 과정
ONGOING_WORDS = HEDGE_WORDS + [
    "돌입", "확산", "발생", "시작", "대치", "진행", "속보", "긴급",
    "비상", "경고", "발령", "급증", "격화",
]
STATE_WORDS = sorted(set(FINAL_WORDS + ONGOING_WORDS), key=len, reverse=True)
# 상태어 뒤에 붙은 조사/어미("결렬로", "완료에도", "타결됐다")까지 함께 제거
_STATE_RE = re.compile(
    "(?:" + "|".join(re.escape(w) for w in STATE_WORDS) + ")"
    "(?:에도|에는|으로|로|은|는|이|가|을|를|도|됐다|했다|한다|된다|되며|하며|하고)?"
)

STOPWORDS = {
    "단독", "종합", "오늘", "어제", "관련", "대한", "위해", "이번", "지난", "최근",
    "이후", "여파", "파장", "전격", "결국", "극적", "사진", "영상", "포토", "현장", "논란", "상황", "한편",
    "정부", "뉴스", "기자", "대표", "오전", "오후", "내일", "올해", "다시",
}
# 여러 사건에 공통으로 나오는 일반어: 단어쌍 매칭에서 제외 (서로 다른 파업이 합쳐지는 것 방지)
GENERIC_WORDS = {
    "노조", "협상", "총파업", "파업", "회의", "발표", "계획", "추진", "논의",
    "의혹", "사건", "사고", "시위", "기자회견", "조사", "수사", "대책", "회장",
}
# 지역명 등: 단독으로는 같은 사건의 근거가 되지 못함
PLACE_GRAMS = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "한국", "미국", "중국", "일본"}
JOSA2 = ("에서", "으로", "에게", "까지", "부터")
JOSA1 = tuple("은는이가을를의에도로과와")


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class Article:
    title: str
    source: str
    link: str
    published_at: datetime
    stripped: str = ""                      # 상태어 제거 제목 (토픽명 후보)
    tokens: list = field(default_factory=list)
    grams: set = field(default_factory=set)
    pairs: set = field(default_factory=set)  # 인접 단어쌍 (예: ("강원","산불"))
    flat: str = ""                           # 공백 제거한 제목(일반어 포함 여부 확인용)
    stage: str = "neutral"                  # final | ongoing | neutral
    marker: str = ""                        # 매칭된 상태어


# ---------------------------------------------------------------------------
# 1. RSS 수집
# ---------------------------------------------------------------------------
def fetch_feed(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (news-briefing/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_rss(xml_bytes):
    """RSS 2.0 <item> 파싱. 구글 뉴스의 '제목 - 언론사' 형식도 분리한다."""
    root = ET.fromstring(xml_bytes)
    channel_title = (root.findtext("channel/title") or "").strip()
    out = []
    for it in root.iter("item"):
        title = unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        if not title or not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        elif not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        out.append(Article(title.strip(), (source or channel_title).strip(), link, dt.astimezone(KST)))
    return out


def collect(feeds, hours, now=None):
    now = now or datetime.now(KST)
    since = now - timedelta(hours=hours)
    seen, arts = set(), []
    for url in feeds:
        try:
            items = parse_rss(fetch_feed(url))
        except Exception as e:  # 한 피드 실패가 전체를 막지 않도록
            print(f"[경고] 피드 수집 실패: {url} ({e})", file=sys.stderr)
            continue
        for a in items:
            key = a.link or a.title
            if key in seen or not (since <= a.published_at <= now + timedelta(hours=1)):
                continue
            seen.add(key)
            arts.append(a)
    return arts


# ---------------------------------------------------------------------------
# 2. 기사 전처리: 상태 분류 + 토픽 시그니처
# ---------------------------------------------------------------------------
def classify(title):
    """(stage, marker) 반환. 최종어가 있어도 헤지어가 함께 있으면 진행 중으로 본다."""
    t = title.replace(" ", "")
    if any(w in t for w in DERIVATIVE_WORDS):
        return "neutral", ""
    hedge = any(h in t for h in HEDGE_WORDS)
    fin = next((w for w in FINAL_WORDS if w in t), None)
    if fin and not hedge:
        return "final", fin
    ong = next((w for w in ONGOING_WORDS if w in t), None)
    if ong:
        return "ongoing", ong
    return "neutral", ""


def tokenize(text):
    toks = []
    for t in re.findall(r"[가-힣]{2,}|[A-Za-z0-9]{2,}", text):
        if t.isascii():
            t = t.lower()
        elif len(t) >= 4 and t.endswith(JOSA2):
            t = t[:-2]
        elif len(t) >= 3 and t.endswith(JOSA1):
            t = t[:-1]
        if t in STOPWORDS or t.endswith(("됐다", "했다", "한다", "된다")):
            continue
        toks.append(t)
    return toks


def make_grams(tokens):
    """토큰 내부 문자 2-gram -> '총파업'/'파업', '시내버스'/'버스' 같은 형태 차이를 흡수."""
    g = set()
    for t in tokens:
        if t.isascii() or len(t) == 2:
            g.add(t)
        else:
            g.update(t[i:i + 2] for i in range(len(t) - 1))
    return g


def prepare(a):
    a.stage, a.marker = classify(a.title)
    stripped = _STATE_RE.sub(" ", a.title)          # 상태어 제거 후 비교 (핵심!)
    stripped = re.sub(r"[^\w\s]", " ", stripped)
    a.stripped = re.sub(r"\s+", " ", stripped).strip() or a.title
    a.flat = a.stripped.replace(" ", "")
    a.tokens = [t for t in tokenize(a.stripped) if t not in GENERIC_WORDS]  # 일반어는 유사도에서 제외
    a.grams = make_grams(a.tokens)
    a.pairs = set(zip(a.tokens, a.tokens[1:]))
    return a


def dice(a, b):
    if not a or not b:
        return 0.0, 0
    shared = len(a & b)
    return 2 * shared / (len(a) + len(b)), shared


# ---------------------------------------------------------------------------
# 3. 토픽 클러스터링 (Union-Find, 입력 순서 무관)
# ---------------------------------------------------------------------------
def is_same_topic(x, y, threshold):
    """링크 조건 (둘 중 하나)
       A) 문자 2-gram이 3개 이상 겹치고 Dice >= threshold
       B) 인접 단어쌍(장소+사건 등)을 공유하고 2-gram이 2개 이상 겹침
       C) 지역명이 아닌 고유 2-gram 1개 이상 + 동일한 일반 사건어 공유
    """
    sim, shared = dice(x.grams, y.grams)
    if shared >= 3 and sim >= threshold:
        return True
    if x.pairs & y.pairs and shared >= 2:
        return True
    # C) 고유 단서 1개 + 같은 일반 사건어(파업/수사 등) 공유 (예: "버스" + "파업")
    ng = (x.grams & y.grams) - PLACE_GRAMS
    return bool(ng) and any(g in x.flat and g in y.flat for g in GENERIC_WORDS)


def cluster(articles, threshold=0.45):
    n = len(articles)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if is_same_topic(articles[i], articles[j], threshold):
                parent[find(i)] = find(j)

    groups = defaultdict(list)
    for i, a in enumerate(articles):
        groups[find(i)].append(a)
    return [sorted(g, key=lambda a: a.published_at) for g in groups.values()]


# ---------------------------------------------------------------------------
# 4. 토픽 점수 / 최종 상태 / 타임라인
# ---------------------------------------------------------------------------
def topic_score(arts, now):
    sources = len({a.source for a in arts})
    latest = max(a.published_at for a in arts)
    age_h = max((now - latest).total_seconds() / 3600, 0)
    recency = 1.0 if age_h <= 6 else 0.5 if age_h <= 12 else 0.0
    return 2 * sources + math.log1p(len(arts)) + recency


def pick_headline(arts):
    """가장 최신의 '결정적' 기사 (final/ongoing). 없으면 최신 기사."""
    decisive = [a for a in arts if a.stage != "neutral"]
    pool = decisive or arts
    last_t = max(a.published_at for a in pool)
    tied = [a for a in pool if a.published_at == last_t]
    tied.sort(key=lambda a: a.stage == "final", reverse=True)
    return tied[0]


def medoid_topic(arts):
    """다른 기사들과 평균 유사도가 가장 높은 기사의 '상태어 제거 제목'을 토픽명으로."""
    if len(arts) == 1:
        return arts[0].stripped
    best, best_s = arts[0], -1
    for a in arts:
        s = sum(dice(a.grams, b.grams)[0] for b in arts if b is not a)
        if s > best_s:
            best, best_s = a, s
    return best.stripped


def build_timeline(arts, headline):
    """상태어가 바뀌는 지점만 남겨 하루 전개를 압축한다."""
    steps, last_key = [], object()
    for a in arts:
        key = a.marker or None
        if not steps or key != last_key or a is headline:
            if a.stage == "neutral" and steps and a is not headline:
                continue  # 중간의 상태어 없는 기사는 생략
            steps.append(a)
            last_key = key
    if len(steps) > 8:
        steps = steps[:1] + steps[-7:]
    day0 = arts[-1].published_at.date()
    out = []
    for a in steps:
        fmt = "%H:%M" if a.published_at.date() == day0 else "%m-%d %H:%M"
        out.append({
            "time": a.published_at.strftime(fmt),
            "summary": a.title,
            "source": a.source,
            "link": a.link,
        })
    return out


def build_item(arts, now):
    headline = pick_headline(arts)
    status = "상황 종료" if headline.stage == "final" else "진행 중"
    timeline = build_timeline(arts, headline)
    links, seen = [], set()
    ordered = [headline] + [a for a in arts if a is not headline]
    for a in ordered:
        if a.source in seen and len(links) >= 2:
            continue
        seen.add(a.source)
        links.append({"source": a.source, "title": a.title, "url": a.link})
        if len(links) >= 5:
            break
    return {
        "topic": medoid_topic(arts),
        "final_headline": headline.title,
        "status": status,
        "timeline": timeline,
        "timeline_text": " -> ".join(f"{t['time']} {t['summary']}" for t in timeline),
        "original_links": links,
        "article_count": len(arts),
        "source_count": len({a.source for a in arts}),
        "latest_at": max(a.published_at for a in arts).isoformat(),
        "_score": round(topic_score(arts, now), 3),
    }


# ---------------------------------------------------------------------------
# 5. (선택) LLM 보정: 토픽명/상태/타임라인 문장만 다듬는다. 실패 시 규칙 기반 결과 유지.
# ---------------------------------------------------------------------------
def llm_polish(item, arts):
    try:
        import anthropic
        client = anthropic.Anthropic()
        lines = "\n".join(
            f"{a.published_at:%m-%d %H:%M} | {a.source} | {a.title}" for a in arts[:30]
        )
        prompt = (
            "아래는 같은 사건을 다룬 기사 제목들(시간순)입니다. JSON만 출력하세요.\n"
            '{"topic":"핵심 토픽명(15자 내외)","status":"상황 종료 또는 진행 중",'
            '"timeline":[{"time":"HH:MM","summary":"한 줄 요약"}]}\n'
            "규칙: 가장 최신 기사를 기준으로 현재 상태를 판단하고, 기사에 없는 내용은 만들지 말고, "
            "시간순으로 최대 6개 단계만 쓰세요.\n\n" + lines
        )
        resp = client.messages.create(
            model=os.getenv("NEWS_LLM_MODEL", "claude-sonnet-5-5"),
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = re.sub(r"```json|```", "", resp.content[0].text).strip()
        data = json.loads(text)
        if data.get("topic"):
            item["topic"] = data["topic"]
        if data.get("status") in ("상황 종료", "진행 중"):
            item["status"] = data["status"]
        if isinstance(data.get("timeline"), list) and data["timeline"]:
            item["timeline"] = [
                {"time": s.get("time", ""), "summary": s.get("summary", "")}
                for s in data["timeline"]
            ]
            item["timeline_text"] = " -> ".join(
                f"{t['time']} {t['summary']}" for t in item["timeline"]
            )
    except Exception as e:
        print(f"[경고] LLM 보정 생략: {e}", file=sys.stderr)
    return item


# ---------------------------------------------------------------------------
# 6. 파이프라인
# ---------------------------------------------------------------------------
def run_pipeline(articles, top=5, threshold=0.45, use_llm=False, now=None):
    now = now or datetime.now(KST)
    articles = [prepare(a) for a in articles]
    groups = cluster(articles, threshold=threshold)
    ranked = sorted(groups, key=lambda g: topic_score(g, now), reverse=True)[:top]  # 토픽당 1슬롯
    result = []
    for rank, g in enumerate(ranked, 1):
        item = build_item(g, now)
        if use_llm:
            item = llm_polish(item, g)
        item["rank"] = rank
        result.append(item)
    return result


def to_text(result, now):
    lines = [f"=== 오늘의 핵심 Top {len(result)} ({now:%Y-%m-%d %H:%M} KST) ===", ""]
    for it in result:
        lines += [
            f"{it['rank']}. {it['topic']}  [{it['status']}]",
            f"   최종 헤드라인: {it['final_headline']}",
            f"   타임라인: {it['timeline_text']}",
            f"   (기사 {it['article_count']}건 / 언론사 {it['source_count']}곳)",
            "   원문: " + " | ".join(l["url"] for l in it["original_links"][:3]),
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 데모 데이터 (네트워크 없이 로직 검증용)
# ---------------------------------------------------------------------------
def demo_articles(now):
    d = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def A(h, m, title, src):
        return Article(title, src, f"https://example.com/{src}/{h:02d}{m:02d}", d + timedelta(hours=h, minutes=m))

    return [
        A(4, 10, "서울 시내버스 노조 협상 결렬로 총파업 돌입", "A일보"),
        A(5, 30, "서울 버스 총파업 돌입, 출근길 대란", "B뉴스"),
        A(8, 20, "버스 파업 이틀째 시민 불편 확산", "C방송"),
        A(11, 40, "서울 버스 노사 막판 협상 재개", "A일보"),
        A(15, 5, "서울 버스 노사 극적 타결, 임금 합의", "B뉴스"),
        A(15, 40, "서울 버스 파업 철회, 오후 4시부터 정상 운행", "D신문"),
        A(2, 15, "강원 산불 확산, 주민 대피령 발령", "C방송"),
        A(9, 0, "강원 산불 헬기 30대 투입, 진화 작업 진행", "A일보"),
        A(17, 30, "강원 산불 주불 진화 완료, 대피령 해제", "D신문"),
        A(18, 10, "강원 산불 진화 완료에도 잔불 감시 계속", "B뉴스"),
        A(10, 0, "한국은행 기준금리 동결 결정", "A일보"),
        A(10, 5, "한은 기준금리 동결, 연 2.5% 유지", "B뉴스"),
        A(10, 30, "한국은행 금리 동결 이후 환율 변동", "C방송"),
        A(13, 0, "반도체 수출 3개월 연속 증가", "D신문"),
        A(14, 0, "철강업계 노조 총파업 예고, 협상 난항", "C방송"),
    ]


def main():
    ap = argparse.ArgumentParser(description="오늘의 핵심 Top N 브리핑")
    ap.add_argument("--feeds", nargs="*", default=DEFAULT_FEEDS, help="RSS 피드 URL들")
    ap.add_argument("--hours", type=int, default=24, help="수집 시간 범위(시간)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=0.45, help="클러스터링 유사도(낮을수록 많이 병합)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--out", help="JSON 결과 저장 경로 (PWA 등에서 사용)")
    ap.add_argument("--llm", action="store_true", help="LLM으로 토픽명/타임라인 보정")
    ap.add_argument("--demo", action="store_true", help="샘플 데이터로 실행")
    args = ap.parse_args()

    now = datetime.now(KST)
    if args.demo:
        now = now.replace(hour=19, minute=0, second=0, microsecond=0)
        articles = demo_articles(now)
    else:
        articles = collect(args.feeds, args.hours, now)
    if not articles:
        print("수집된 기사가 없습니다.", file=sys.stderr)
        sys.exit(1)

    result = run_pipeline(articles, args.top, args.threshold, args.llm, now)
    payload = {"generated_at": now.isoformat(), "items": [{k: v for k, v in r.items() if k != "_score"} for r in result]}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(to_text(result, now))


if __name__ == "__main__":
    main()
