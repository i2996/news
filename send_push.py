#!/usr/bin/env python3
"""briefing.json의 주요 뉴스 상위 3건을 웹푸시로 전송 (GitHub Actions에서 실행)
필요한 Secrets: PUSH_SUBSCRIPTION (앱에서 복사한 JSON), VAPID_PRIVATE_KEY (앱에서 복사한 값)
"""
import json
import os
import sys

from pywebpush import WebPushException, webpush

d = json.load(open("briefing.json", encoding="utf-8"))
groups = {g["id"]: g for g in d.get("groups", [])}
arts = {a["id"]: a for a in d.get("articles", [])}
top = d.get("top", {})
lines = []
for n, it in enumerate(top.get("items", [])[:3], 1):
    g = groups.get(it["group_id"])
    a = arts.get(g["rep_id"]) if g else None
    if a:
        lines.append(f"{n}. {a['title']}" if top.get("mode") == "ranked" else f"· {a['title']}")
if not lines:
    print(f"보낼 주요 뉴스가 없어 푸시를 건너뜁니다. (수집 상태: {d.get('status', {}).get('state')})")
    sys.exit(0)

sub = json.loads(os.environ["PUSH_SUBSCRIPTION"].strip())
key = os.environ["VAPID_PRIVATE_KEY"].strip()
payload = json.dumps({"title": "오늘의 핵심", "body": "\n".join(lines)}, ensure_ascii=False)
try:
    webpush(subscription_info=sub, data=payload, vapid_private_key=key,
            vapid_claims={"sub": "mailto:briefing-bot@users.noreply.github.com"})
    print("푸시 전송 완료")
except WebPushException as e:
    status = getattr(e.response, "status_code", None)
    if status in (404, 410):
        sys.exit("구독이 만료됐어요. 앱 > 기록 탭에서 푸시를 다시 켜고 Secrets를 갱신하세요.")
    sys.exit(f"푸시 실패: {e}")
