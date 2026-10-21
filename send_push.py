#!/usr/bin/env python3
"""briefing.json의 주요 뉴스 상위 3건을 알림으로 전송 (GitHub Actions에서 실행)
채널 1) 웹푸시: Secrets PUSH_SUBSCRIPTION, VAPID_PRIVATE_KEY (브라우저 구독이 만료되면 실패할 수 있음)
채널 2) ntfy  : Secret NTFY_TOPIC (ntfy 앱으로 수신, 구독이 만료되지 않음)
하나라도 성공하면 성공으로 처리하고, 실패 원인은 Actions 요약 화면의 Annotations에 한글로 표시한다.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def ann(kind, title, msg):
    print(f"::{kind} title={title}::{msg}")


def b64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------- 알림 내용 ----------
try:
    d = json.load(open("briefing.json", encoding="utf-8"))
except Exception as e:
    ann("error", "푸시 전송 실패", f"briefing.json을 읽지 못했어요: {e}")
    sys.exit(1)
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
    ann("notice", "푸시 건너뜀", f"보낼 주요 뉴스가 없어요 (수집 상태: {d.get('status', {}).get('state')}). 먼저 update-briefing을 실행하세요.")
    sys.exit(0)
body = "\n".join(lines)


SUB = "mailto:briefing-bot@users.noreply.github.com"   # VAPID 연락처(sub)


def vapid_headers(key, endpoint):
    """py_vapid의 sub 검증 오류를 피하려고 VAPID(JWT, ES256) 헤더를 직접 서명한다(RFC 8292)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    priv = ec.derive_private_key(int.from_bytes(b64u(key), "big"), ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    u = urlsplit(endpoint)

    def enc(o):
        return base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).rstrip(b"=").decode()

    signing = enc({"typ": "JWT", "alg": "ES256"}) + "." + enc({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUB})
    r, s_ = utils.decode_dss_signature(priv.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    sig = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s_.to_bytes(32, "big")).rstrip(b"=").decode()
    return {"Authorization": f"vapid t={signing}.{sig}, k={base64.urlsafe_b64encode(pub).rstrip(b'=').decode()}"}


def versions():
    try:
        from importlib.metadata import version
        return f"pywebpush {version('pywebpush')}, py_vapid {version('py-vapid')}"
    except Exception:
        return "버전 확인 불가"


# ---------- 채널 1: 웹푸시 ----------
def send_webpush():
    raw_sub = os.environ.get("PUSH_SUBSCRIPTION", "").strip()
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not raw_sub and not key:
        return None                                   # 설정 안 함
    if not raw_sub or not key:
        return "PUSH_SUBSCRIPTION과 VAPID_PRIVATE_KEY는 둘 다 있어야 해요. 하나가 비어 있어요."
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return "pywebpush 설치에 실패했어요. push.yml의 'pip install pywebpush' 단계를 확인하세요."
    try:
        sub = json.loads(raw_sub)
    except Exception:
        return "PUSH_SUBSCRIPTION 값이 JSON 형식이 아니에요. 앱의 '① PUSH_SUBSCRIPTION 복사' 값을 그대로 붙여넣었는지 확인하세요."
    if not (isinstance(sub, dict) and sub.get("endpoint") and sub.get("keys", {}).get("p256dh") and sub.get("keys", {}).get("auth")):
        return "PUSH_SUBSCRIPTION에 endpoint 또는 keys가 없어요. 앱에서 푸시를 다시 켠 뒤 값을 새로 복사하세요."
    try:
        if len(b64u(key)) != 32:
            raise ValueError
    except Exception:
        return "VAPID_PRIVATE_KEY 형식이 맞지 않아요. 앱의 '② VAPID_PRIVATE_KEY 복사' 값(43자 정도의 짧은 문자열)이어야 해요."
    host = urlsplit(sub["endpoint"]).netloc
    data = json.dumps({"title": "The Press", "body": body}, ensure_ascii=False)
    try:
        try:
            webpush(subscription_info=sub, data=data, vapid_private_key=key, vapid_claims={"sub": SUB}, timeout=20)
        except Exception as e:
            if type(e).__name__ != "VapidException":
                raise
            print(f"py_vapid가 sub를 거부해서 직접 서명으로 다시 시도해요 ({versions()}): {e}")
            webpush(subscription_info=sub, data=data, headers=vapid_headers(key, sub["endpoint"]), timeout=20)
        return True
    except WebPushException as e:
        resp = getattr(e, "response", None)
        st = getattr(resp, "status_code", None)
        txt = (getattr(resp, "text", "") or "")[:150].replace("\n", " ")
        if st in (404, 410):
            return f"웹푸시 구독이 만료됐어요(HTTP {st}). 브라우저가 구독을 지운 거예요. 앱 기록 탭에서 '다시 구독' 후 두 값을 새로 저장하거나, 아래 ntfy를 사용하세요."
        if st in (400, 401, 403):
            return f"푸시 서버({host})가 거부했어요(HTTP {st}): {txt} — 구독과 VAPID 키가 짝이 안 맞을 수 있어요."
        return f"푸시 서버({host}) 오류 HTTP {st}: {txt or e}"
    except Exception as e:
        return f"웹푸시 전송 중 오류({type(e).__name__}): {str(e)[:150]} [{versions()}]"


# ---------- 채널 2: ntfy ----------
def send_ntfy():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return None
    if not (1 <= len(topic) <= 64 and all(c.isalnum() or c in "-_" for c in topic)):
        return "NTFY_TOPIC에는 영문·숫자·-_ 만 쓸 수 있어요(64자 이하). 앱에서 만든 주제 이름을 그대로 저장했는지 확인하세요."
    repo, owner = os.environ.get("GITHUB_REPOSITORY", "/").split("/")[-1], os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    click = f"https://{owner}.github.io/" + ("" if repo == f"{owner}.github.io" else repo + "/") if owner else None
    msg = {"topic": topic, "title": "The Press", "message": body[:3500], "tags": ["newspaper"], "priority": 3}
    if click:
        msg["click"] = click
    req = urllib.request.Request("https://ntfy.sh", data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True if 200 <= r.status < 300 else f"ntfy 응답 HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"ntfy 서버가 거부했어요(HTTP {e.code}). 주제 이름을 확인하거나 잠시 후 다시 시도하세요."
    except Exception as e:
        return f"ntfy 전송 중 오류({type(e).__name__}): {str(e)[:150]}"


results = {"웹푸시": send_webpush(), "ntfy": send_ntfy()}
ok = [k for k, v in results.items() if v is True]
bad = {k: v for k, v in results.items() if isinstance(v, str)}
if not ok and not bad:
    ann("error", "푸시 전송 실패", "보낼 곳이 설정되지 않았어요. Secrets에 PUSH_SUBSCRIPTION+VAPID_PRIVATE_KEY 또는 NTFY_TOPIC을 저장하세요.")
    sys.exit(1)
for k, v in bad.items():
    ann("warning" if ok else "error", f"{k} 전송 실패", v)
if ok:
    ann("notice", "알림 전송 완료", f"{', '.join(ok)}로 보냈어요.")
    sys.exit(0)
sys.exit(1)
