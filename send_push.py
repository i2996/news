#!/usr/bin/env python3
"""briefing.json의 주요 뉴스 상위 3건을 웹푸시로 전송 (GitHub Actions에서 실행)
필요한 Secrets: PUSH_SUBSCRIPTION (앱에서 복사한 JSON), VAPID_PRIVATE_KEY (앱에서 복사한 값)
실패 원인은 Actions 요약 화면의 Annotations(::error)에 한글로 표시된다.
"""
import base64
import json
import os
import sys
from urllib.parse import urlsplit


def fail(msg):
    print(f"::error title=푸시 전송 실패::{msg}")
    print(msg, file=sys.stderr)
    sys.exit(1)


def b64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


try:
    from pywebpush import WebPushException, webpush
except ImportError:
    fail("pywebpush 설치에 실패했어요. push.yml의 'pip install pywebpush' 단계를 확인하세요.")

# 1) 알림 내용
try:
    d = json.load(open("briefing.json", encoding="utf-8"))
except Exception as e:
    fail(f"briefing.json을 읽지 못했어요: {e}")
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
    print(f"::notice title=푸시 건너뜀::보낼 주요 뉴스가 없어요 (수집 상태: {d.get('status', {}).get('state')}). 먼저 update-briefing을 실행하세요.")
    sys.exit(0)

# 2) Secrets 점검 (값은 출력하지 않음)
raw_sub = os.environ.get("PUSH_SUBSCRIPTION", "").strip()
key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
if not raw_sub:
    fail("Secret 'PUSH_SUBSCRIPTION'이 비어 있어요. 저장소 Settings → Secrets and variables → Actions에 이름을 정확히 PUSH_SUBSCRIPTION으로 저장했는지 확인하세요.")
if not key:
    fail("Secret 'VAPID_PRIVATE_KEY'가 비어 있어요. 이름을 정확히 VAPID_PRIVATE_KEY로 저장했는지 확인하세요.")
try:
    sub = json.loads(raw_sub)
except Exception:
    fail("PUSH_SUBSCRIPTION 값이 JSON 형식이 아니에요. 앱 기록 탭의 '① PUSH_SUBSCRIPTION 복사' 값을 그대로(앞뒤 따옴표 없이) 붙여넣었는지 확인하세요.")
if not (isinstance(sub, dict) and sub.get("endpoint") and sub.get("keys", {}).get("p256dh") and sub.get("keys", {}).get("auth")):
    fail("PUSH_SUBSCRIPTION에 endpoint 또는 keys(p256dh/auth)가 없어요. 앱에서 푸시를 다시 켠 뒤 값을 새로 복사하세요.")
try:
    if len(b64u(key)) != 32:
        raise ValueError
except Exception:
    fail("VAPID_PRIVATE_KEY 형식이 맞지 않아요. 앱의 '② VAPID_PRIVATE_KEY 복사' 값(영문·숫자·-_ 로 된 43자 정도의 짧은 문자열)이어야 해요.")

# 3) 전송
host = urlsplit(sub["endpoint"]).netloc
payload = json.dumps({"title": "The Press", "body": "\n".join(lines)}, ensure_ascii=False)
try:
    webpush(subscription_info=sub, data=payload, vapid_private_key=key,
            vapid_claims={"sub": "mailto:briefing-bot@users.noreply.github.com"}, timeout=20)
    print(f"::notice title=푸시 전송 완료::{host}로 전송했어요.")
except WebPushException as e:
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    body = (getattr(resp, "text", "") or "")[:200].replace("\n", " ")
    if status in (404, 410):
        fail(f"구독이 만료됐거나 삭제됐어요(HTTP {status}). 앱 기록 탭에서 '다시 구독' 후 PUSH_SUBSCRIPTION과 VAPID_PRIVATE_KEY를 둘 다 새로 저장하세요.")
    if status in (400, 401, 403):
        fail(f"푸시 서버({host})가 거부했어요(HTTP {status}): {body} — 구독을 만든 VAPID 키와 Secret의 VAPID_PRIVATE_KEY가 짝이 안 맞을 가능성이 커요. 앱 데이터를 지웠다면 키가 새로 만들어졌으니, 앱에서 '다시 구독' 후 두 값을 모두 다시 저장하세요.")
    fail(f"푸시 서버({host}) 응답 오류 HTTP {status}: {body or e}")
except Exception as e:
    fail(f"전송 중 오류({type(e).__name__}): {str(e)[:200]}")
