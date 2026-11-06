// Runs the trainer's Python (Pyodide) off the page's main thread: the page stays smooth while
// the bots and the ICM maths think.
importScripts("pyodide/pyodide.js");

let handle = null;
let py = null;
const CHARTS = "/home/pyodide/my_charts";

async function boot() {
  postMessage({ type: "progress", text: "Starting the poker engine…" });
  py = await loadPyodide({ indexURL: new URL("pyodide/", self.location).href });
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
