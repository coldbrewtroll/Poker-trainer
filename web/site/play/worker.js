// Runs the trainer's Python (Pyodide) off the page's main thread: the page stays smooth while
// the bots and the ICM maths think.
importScripts("pyodide/pyodide.js");

let handle = null;
let py = null;
const CHARTS = "/home/pyodide/my_charts";
// (the numpy build for this Pyodide version, from the Pyodide project's CDN; the service
// worker keeps a copy, so it loads once)
const NUMPY_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.8/full/numpy-2.0.2-cp312-cp312-pyodide_2024_0_wasm32.whl";

async function boot() {
  postMessage({ type: "progress", text: "Starting the poker engine…" });
  py = await loadPyodide({ indexURL: new URL("pyodide/", self.location).href });
  // numpy runs the exact ICM game solves (icm_cfr); without it (offline on the first visit,
  // or a slow network) the engine solves those games the quicker, rougher way
  postMessage({ type: "progress", text: "Loading the ICM solver…" });
  try {
    await Promise.race([py.loadPackage(NUMPY_URL),
      new Promise((_, no) => setTimeout(() => no(new Error("timeout")), 25000))]);
  } catch (e) { /* fictitious-play solves */ }
  postMessage({ type: "progress", text: "Loading your ranges and the bots…" });
  const buf = await (await fetch("app.zip")).arrayBuffer();
  py.unpackArchive(buf, "zip", { extractDir: "/home/pyodide/app" });
  py.runPython(`
import os, sys
os.chdir("/home/pyodide/app")
sys.path[:0] = ["/home/pyodide/app", "/home/pyodide/app/web/py"]
import webgame
webgame.load_ranges("ranges.json")
`);
  handle = py.pyimport("webgame").handle;
  postMessage({ type: "ready" });
}

const ready = boot().catch((e) => postMessage({ type: "error", text: String(e) }));

onmessage = async (e) => {
  await ready;
  const { id, msg, files } = e.data;
  try {
    if (msg.cmd === "charts") {
      // your own charts: written to this tab's memory only, then read by the engine
      py.FS.mkdirTree(CHARTS);
      for (const f of py.FS.readdir(CHARTS)) if (f !== "." && f !== "..") py.FS.unlink(`${CHARTS}/${f}`);
      for (const f of files || []) py.FS.writeFile(`${CHARTS}/${f.name.replace(/[\/\\]/g, "_")}`, new Uint8Array(f.buf));
      msg.folder = files && files.length ? CHARTS : null;
    }
    const out = handle(JSON.stringify(msg));
    postMessage({ type: "reply", id, out: JSON.parse(out) });
  } catch (err) {
    postMessage({ type: "reply", id, error: String(err) });
  }
};
