const VER = "top5-v27";
const SHELL = ["./", "index.html", "manifest.json", "icons/icon-192.png", "icons/icon-512.png"];
const DATA = new URL("briefing.json", self.registration.scope).href;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VER).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("top5-") && k !== VER).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  if (req.url.startsWith(DATA)) {            // 브리핑 데이터: 네트워크 우선, 실패 시 캐시
    e.respondWith(fetch(req).then(res => {
      const copy = res.clone();
      caches.open(VER).then(c => c.put(DATA, copy));
      return res;
    }).catch(() => caches.match(DATA)));
    return;
  }
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));   // 앱 껍데기: 캐시 우선
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({type: "window"}).then(cs => cs.length ? cs[0].focus() : clients.openWindow("./")));
});

// 브라우저가 구독을 갱신·만료시키면(주소 교체) 같은 키로 즉시 다시 구독해 구독이 사라지지 않게 한다.
// 새 주소는 GitHub Secret에 다시 저장해야 하므로 알림으로 알려 준다. (키는 페이지가 push-meta 캐시에 저장해 둔 공개키를 사용)
async function pushKey(old){
  const k = old && old.options && old.options.applicationServerKey;
  if(k) return k;
  try { const r = await (await caches.open("push-meta")).match("vapid-pub"); if(r){ const s = (await r.text()).replace(/-/g, "+").replace(/_/g, "/"); return Uint8Array.from(atob(s.padEnd(Math.ceil(s.length / 4) * 4, "=")), c => c.charCodeAt(0)); } } catch(_) {}
  return null;
}
self.addEventListener("pushsubscriptionchange", e => {
  e.waitUntil((async () => {
    let sub = e.newSubscription || await self.registration.pushManager.getSubscription();
    if(!sub){ const k = await pushKey(e.oldSubscription); if(k) try { sub = await self.registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: k}); } catch(_) {} }
    await self.registration.showNotification("푸시 구독 주소가 바뀌었어요", {
      body: sub ? "앱 기록 탭에서 ① PUSH_SUBSCRIPTION을 다시 복사해 GitHub Secret을 업데이트하세요." : "구독이 만료됐어요. 앱 기록 탭에서 '구독'을 다시 눌러 주세요.",
      icon: "icons/icon-192.png", badge: "icons/icon-192.png", tag: "briefing-sys"
    });
  })());
});

// 서버에서 온 웹푸시 (앱이 닫혀 있어도 표시됨)
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "오늘의 핵심 Top 5", {
    body: d.body || "새 브리핑이 도착했어요",
    icon: "icons/icon-192.png",
    badge: "icons/icon-192.png",
    tag: "briefing"
  }));
});
