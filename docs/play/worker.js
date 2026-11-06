// Runs the trainer's Python (Pyodide) off the page's main thread: the page stays smooth while
// the bots and the ICM maths think.
importScripts("pyodide/pyodide.js");

let handle = null;

async function boot() {
  postMessage({ type: "progress", text: "Starting the poker engine…" });
  const py = await loadPyodide({ indexURL: new URL("pyodide/", self.location).href });
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
  const { id, msg } = e.data;
  try {
    const out = handle(JSON.stringify(msg));
    postMessage({ type: "reply", id, out: JSON.parse(out) });
  } catch (err) {
    postMessage({ type: "reply", id, error: String(err) });
  }
};
