// Keeps the trainer on the phone after the first visit: the engine (15 MB) and the game
// load from here next time, and it works offline. build.py renames VERSION on every build.
// The engine (Pyodide) and the avatars sit in their own cache, kept across updates, so an
// update only downloads the game's own files (about 1 MB).
const VERSION = "ftt-db9192b375";
const ENGINE = "ftt-engine-pyodide-0.27.8";
const APP = ["./", "index.html", "app.css", "app.js", "worker.js", "app.zip", "manifest.webmanifest"];
const PYODIDE = ["pyodide/pyodide.js", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm",
  "pyodide/python_stdlib.zip", "pyodide/pyodide-lock.json"];
const lasting = (url) => /\/(pyodide|avatars)\//.test(url);

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    // fresh copies of the game's files (not the browser's HTTP cache, which may be older)
    const app = await caches.open(VERSION);
    await app.addAll(APP.map((u) => new Request(u, { cache: "reload" })));
    const eng = await caches.open(ENGINE);
    for (const u of PYODIDE) if (!(await eng.match(u))) await eng.add(u);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION && k !== ENGINE)
    .map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
    if (res.ok && new URL(e.request.url).origin === location.origin) {
      const copy = res.clone();
      caches.open(lasting(e.request.url) ? ENGINE : VERSION).then((c) => c.put(e.request, copy));
    }
    return res;
  })));
});
