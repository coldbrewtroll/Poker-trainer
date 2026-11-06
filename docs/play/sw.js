// Keeps the trainer on the phone after the first visit: the engine (15 MB) and the game
// load from here next time, and it works offline. build.py renames VERSION on every build.
const VERSION = "ftt-acb1a85549";
const CORE = ["./", "index.html", "app.css", "app.js", "worker.js", "app.zip", "manifest.webmanifest",
  "pyodide/pyodide.js", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm",
  "pyodide/python_stdlib.zip", "pyodide/pyodide-lock.json"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
    if (res.ok && new URL(e.request.url).origin === location.origin) {
      const copy = res.clone();
      caches.open(VERSION).then((c) => c.put(e.request, copy));
    }
    return res;
  })));
});
