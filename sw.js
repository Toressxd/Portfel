// Keeps the app shell on the phone so it opens instantly and works offline; the data itself
// always comes from Supabase. A new VERSION (set by build.py) replaces the old cache.
const VERSION = "2026-11-02-1";
const SHELL = ["./", "index.html", "config.js?v=" + VERSION, "backend.js?v=" + VERSION, "manifest.webmanifest",
  "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"];

self.addEventListener("install", e => { e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return; // Supabase and fonts go straight to the network
  // page: network first so updates arrive, cache when offline; other files: cache first
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put("index.html", copy)); return r; }).catch(() => caches.match("index.html")));
  } else {
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
  }
});
