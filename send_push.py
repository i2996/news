#!/usr/bin/env python3
"""briefing.json의 Top 3를 웹푸시로 전송 (GitHub Actions에서 실행)
필요한 Secrets: PUSH_SUBSCRIPTION (앱에서 복사한 JSON), VAPID_PRIVATE_KEY (앱에서 복사한 값)
"""
import json
import os
import sys

from pywebpush import WebPushException, webpush

sub = json.loads(os.environ["PUSH_SUBSCRIPTION"].strip())
key = os.environ["VAPID_PRIVATE_KEY"].strip()

items = json.load(open("briefing.json", encoding="utf-8")).get("items", [])
if not items:
    sys.exit("briefing.json에 항목이 없습니다.")

body = "\n".join(f"{i['rank']}. [{i['status']}] {i['final_headline']}" for i in items[:3])
payload = json.dumps({"title": "오늘의 핵심 Top 5", "body": body}, ensure_ascii=False)

try:
    webpush(
        subscription_info=sub,
        data=payload,
        vapid_private_key=key,
        vapid_claims={"sub": "mailto:briefing-bot@users.noreply.github.com"},
    )
    print("푸시 전송 완료")
except WebPushException as e:
    status = getattr(e.response, "status_code", None)
    if status in (404, 410):
        sys.exit("구독이 만료됐어요. 앱 > 기록 탭에서 푸시를 다시 켜고 Secrets를 갱신하세요.")
    sys.exit(f"푸시 실패: {e}")
