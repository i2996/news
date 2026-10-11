const VER = "top5-v11";
const SHELL = ["./", "index.html", "manifest.json", "icons/icon-192.png", "icons/icon-512.png"];
const DATA = new URL("briefing.json", self.registration.scope).href;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VER).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VER).map(k => caches.delete(k)))).then(() => self.clients.claim()));
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
