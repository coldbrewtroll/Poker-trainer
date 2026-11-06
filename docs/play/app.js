// Final Table Trainer, phone / browser version. The poker (bots, your Monker ranges, ICM,
// the coach) is the desktop trainer's own Python, running in worker.js; this file is the
// table on the screen.
"use strict";

const $ = (id) => document.getElementById(id);
const SUITS = { s: "♠", h: "♥", d: "♦", c: "♣" };
// physical seat -> spot around the table (you at the bottom, then clockwise)
const SEAT_XY = [[50, 86], [22, 78], [10.5, 52], [10.5, 25], [50, 13], [89.5, 25], [89.5, 52], [78, 78]];
// bet chips and the dealer button: px offsets from each seat's centre
const BET_OFF = [[0, -62], [54, -46], [70, -2], [70, -2], [0, 70], [-70, -2], [-70, -2], [-54, -46]];
const BTN_OFF = [[38, -26], [36, -16], [36, -16], [36, -16], [36, -12], [-36, -16], [-36, -16], [-36, -16]];
const SPEEDS = { Slow: [950, 380], Normal: [540, 210], Fast: [270, 105], Turbo: [90, 35] };
const POOLS = ["$500K", "$1M", "$1.5M"];

let settings = Object.assign(
  { pool: "$1M", custom: 2000000, levels: 30, skip: true, speed: "Normal", hint: false, table: 0 },
  JSON.parse(localStorage.getItem("ftt-settings") || "{}"));
const save = () => { try { localStorage.setItem("ftt-settings", JSON.stringify(settings)); } catch (e) {} };

// ---------------------------------------------------------------- the engine (worker)
const worker = new Worker("worker.js");
let reqId = 0;
const pending = {};
function call(msg) {
  return new Promise((res, rej) => {
    const id = ++reqId;
    pending[id] = { res, rej };
    worker.postMessage({ id, msg });
  });
}
worker.onmessage = (e) => {
  const d = e.data;
  if (d.type === "progress") $("loadtext").textContent = d.text;
  else if (d.type === "ready") start();
  else if (d.type === "error") $("loadtext").textContent = "Couldn't start: " + d.text;
  else if (d.type === "reply") {
    const p = pending[d.id];
    delete pending[d.id];
    if (!p) return;
    d.error ? p.rej(new Error(d.error)) : p.res(d.out);
  }
};

// ---------------------------------------------------------------- state
let V = null;          // the table as the engine last sent it
let shownEvents = 0;   // events of this hand already on screen
let handKey = null;
let seat = {};         // physical seat -> {stack, bet, folded, act, kind, cards, res, pct}
let board = [];
let pot = 0;
let busy = false;
let turn = null;
let lastGraded = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmt = (x) => (Math.round(x * 100) / 100).toString();
const money = (x) => "$" + Math.round(x).toLocaleString("en-US");
const moneyK = (x) => (x >= 1e6 ? "$" + (x / 1e6).toFixed(2) + "M" : x >= 1000 ? "$" + (x / 1000).toFixed(1) + "K" : "$" + Math.round(x));
const slug = (n) => n.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

function cardEl(c, cls = "") {
  const el = document.createElement("div");
  if (!c) { el.className = "card back " + cls; return el; }
  el.className = `card ${c[1]} ${cls}`;
  el.innerHTML = `<span class="r">${c[0] === "T" ? "10" : c[0]}</span><span class="u">${SUITS[c[1]]}</span>`;
  return el;
}

function toast(text, ms = 2600) {
  const t = $("toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), ms);
}

// ---------------------------------------------------------------- drawing
function drawTitle() {
  if (!V || !V.ready) return;
  const pool = V.pays.reduce((a, b) => a + b, 0);
  $("t1").textContent = `Final table #${V.table} · ${moneyK(pool)}`;
  const lvl = V.left_in_level ? ` · up in ${V.left_in_level}` : "";
  $("t2").textContent = `${V.blinds}${lvl} · ${V.left} left · ICM ${V.icm_stats.right}/${V.icm_stats.spots}`;
}

function drawBoard() {
  const cards = $("cards");
  cards.innerHTML = "";
  for (let i = 0; i < 5; i++) {
    if (i < board.length) cards.appendChild(cardEl(board[i], board.dealt && board.dealt.has(i) ? "deal" : ""));
    else { const s = document.createElement("div"); s.className = "slot"; cards.appendChild(s); }
  }
  board.dealt = new Set();
  const big = V ? V.big_blind_chips : 0;
  $("pot").textContent = pot ? `${Math.round(pot * big).toLocaleString("en-US")}  ·  ${fmt(pot)} bb` : "";
}

function drawSeats() {
  const box = $("seats");
  box.innerHTML = "";
  if (!V || !V.ready) return;
  const h = V.hand;
  for (let x = 0; x < 8; x++) {
    const p = V.players[x];
    const st = seat[x] || {};
    const inHand = h && h.seats.includes(x) && !p.out;
    const [px, py] = SEAT_XY[x];
    const el = document.createElement("div");
    el.className = "seat" + (x === 0 ? " me" : px < 30 ? " l" : px > 70 ? " r" : "") + (p.out && !inHand ? " out" : "") + (turn === x ? " turn" : "");
    el.style.left = px + "%";
    el.style.top = py + "%";
    const dim = st.folded || (p.out && !inHand);
    const img = document.createElement("img");
    img.className = "av";
    img.alt = p.name;
    img.src = `avatars/${slug(p.name)}_${x === 0 ? 60 : 52}${dim ? "_dim" : ""}.png`;
    el.appendChild(img);
    const plate = document.createElement("div");
    plate.className = "plate";
    const pos = h && h.positions[x] ? ` · ${h.positions[x]}` : "";
    const stack = st.stack !== undefined ? st.stack : p.stack;
    plate.innerHTML = `<div class="nm">${p.out ? p.out + (p.out === 1 ? "st" : p.out === 2 ? "nd" : p.out === 3 ? "rd" : "th") + " · " : ""}${p.name}${pos}</div>` +
      `<div class="st">${p.out && !inHand ? moneyK(p.prize) : fmt(stack) + " bb"}</div>` +
      (p.out && !inHand ? "" : st.pct !== undefined ?
        `<div class="eq"><span class="pct${st.lead ? " lead" : ""}">${Math.round(st.pct * 100)}% win</span></div>` :
        `<div class="eq">${moneyK(p.equity)}</div>`);
    el.appendChild(plate);
    if (st.cards && x !== 0) {
      const hole = document.createElement("div");
      hole.className = "hole";
      st.cards.forEach((c) => hole.appendChild(cardEl(c, "small" + (st.folded ? " dim" : ""))));
      el.appendChild(hole);
    } else if (inHand && !st.folded && x !== 0 && !(h.result)) {
      const hole = document.createElement("div");
      hole.className = "hole";
      hole.appendChild(cardEl(null, "small"));
      hole.appendChild(cardEl(null, "small"));
      el.appendChild(hole);
    }
    if (st.res) {
      const r = document.createElement("div");
      r.className = "res " + (st.res > 0 ? "win" : "lose");
      r.textContent = (st.res > 0 ? "+" : "") + fmt(st.res) + " bb";
      el.appendChild(r);
    } else if (st.act && !st.folded) {
      const a = document.createElement("div");
      a.className = "act " + st.kind;
      a.textContent = st.act;
      el.appendChild(a);
    } else if (st.act && st.folded) {
      const a = document.createElement("div");
      a.className = "act fold";
      a.textContent = "Fold";
      el.appendChild(a);
    }
    box.appendChild(el);
    if (st.bet > 0) {
      const b = document.createElement("div");
      b.className = "chipbet";
      b.textContent = fmt(st.bet);
      b.style.left = `calc(${px}% + ${BET_OFF[x][0]}px)`;
      b.style.top = `calc(${py}% + ${BET_OFF[x][1]}px)`;
      box.appendChild(b);
    }
    if (h && h.button === x) {
      const d = document.createElement("div");
      d.className = "dealer";
      d.textContent = "D";
      d.style.left = `calc(${px}% + ${BTN_OFF[x][0]}px)`;
      d.style.top = `calc(${py}% + ${BTN_OFF[x][1]}px)`;
      box.appendChild(d);
    }
  }
}

function drawHero() {
  const box = $("hero");
  box.innerHTML = "";
  const h = V && V.hand;
  if (h && h.hole && h.seats.includes(0)) {
    const folded = seat[0] && seat[0].folded;
    h.hole.forEach((c) => box.appendChild(cardEl(c, "big" + (folded ? " dim" : ""))));
  }
}

function draw() { drawTitle(); drawBoard(); drawSeats(); drawHero(); }

// ---------------------------------------------------------------- a hand on screen
function startHand() {
  const h = V.hand;
  handKey = h.no + ":" + V.table;
  shownEvents = 0;
  lastGraded = 0;
  seat = {};
  board = [];
  pot = 2.5;
  turn = null;
  for (const x of h.seats) {
    const pos = h.positions[x];
    let stack = h.start[x], bet = 0;
    if (pos === "SB" || (h.seats.length === 2 && pos === "BTN")) { bet = Math.min(0.5, stack); }
    if (pos === "BB") { bet = Math.min(1, Math.max(0, stack - 1)); stack -= 1; }
    seat[x] = { stack: stack - bet, bet, folded: false };
  }
  $("caption").classList.add("hidden");
  if (h.note) toast(h.note);
}

async function playEvents() {
  const h = V.hand;
  const [beat, foldBeat] = SPEEDS[settings.speed] || SPEEDS.Normal;
  while (shownEvents < h.events.length) {
    const e = h.events[shownEvents++];
    if (e.board.length > board.length) {           // a new street: bets into the pot, cards
      for (const x in seat) { seat[x].bet = 0; if (!seat[x].folded && seat[x].kind !== "allin") seat[x].act = null; }
      board.dealt = new Set();
      for (let i = board.length; i < e.board.length; i++) board.dealt.add(i);
      board = Object.assign(e.board.slice(), { dealt: board.dealt });
      turn = null;
      draw();
      await sleep(beat * 0.9);
    }
    turn = e.seat;
    seat[e.seat] = Object.assign(seat[e.seat] || {}, {
      stack: e.stack, bet: e.bet, folded: e.kind === "fold", act: e.label, kind: e.kind });
    pot = e.pot;
    draw();
    if (e.seat !== 0) await sleep(e.kind === "fold" ? foldBeat : beat);
  }
  turn = null;
}

async function showResult() {
  const h = V.hand, r = h.result;
  const [beat] = SPEEDS[settings.speed] || SPEEDS.Normal;
  // the pot that was played for: an uncalled bet goes back to whoever made it
  const put = Object.keys(seat).map((x) => Math.max(0, (h.start[x] || 0) - (seat[x].stack || 0))).sort((a, b) => b - a);
  if (put.length > 1) pot = put.reduce((a, b) => a + b, 0) - (put[0] - put[1]);
  for (const x in seat) { seat[x].bet = 0; }
  for (const [x, cards] of Object.entries(r.shown)) { if (seat[x]) seat[x].cards = cards; }
  const ro = r.runout;
  const setPct = (k) => {
    const eq = ro && ro.eq[String(k)];
    if (!eq) return;
    const best = Math.max(...Object.values(eq));
    for (const [x, v] of Object.entries(eq)) { if (seat[x]) { seat[x].pct = v; seat[x].lead = v >= best - 1e-9; } }
  };
  if (ro) {
    board = Object.assign(r.board.slice(0, ro.from), { dealt: new Set() });
    setPct(ro.from);
    draw();
    await sleep(Math.max(700, beat * 1.6));
    for (let k = ro.from; k < r.board.length; k++) {
      const next = k < 3 ? 3 : k + 1;
      board = Object.assign(r.board.slice(0, next), { dealt: new Set(Array.from({ length: next - k }, (_, i) => k + i)) });
      k = next - 1;
      setPct(next);
      draw();
      await sleep(Math.max(900, beat * 2));
    }
  } else {
    if (r.board.length > board.length) { board = Object.assign(r.board.slice(), { dealt: new Set() }); }
    draw();
  }
  for (const [x, v] of Object.entries(r.won)) {
    if (!seat[x]) continue;
    if (v) seat[x].res = v;
    seat[x].stack = Math.max(0, (h.start[x] || 0) + v);   // after the hand
  }
  draw();
  for (const b of r.busts) toast(`${b.name} out in ${b.place}${b.place === 1 ? "st" : b.place === 2 ? "nd" : b.place === 3 ? "rd" : "th"}: ${money(b.prize)}`, 3500);
}

function showGrade() {
  const g = V.hand.graded;
  if (g.length <= lastGraded) return;
  const last = g[g.length - 1];
  lastGraded = g.length;
  const c = $("caption");
  c.textContent = last.caption;
  c.style.background = last.colour;
  c.classList.remove("hidden");
  if (last.icm) toast((last.icm_right ? "✔ " : "✖ ") + last.icm, 3200);
}

function drawActions() {
  const box = $("actions");
  box.innerHTML = "";
  const d = V && V.decision;
  if (V && V.over) {
    const b = document.createElement("button");
    b.className = "next";
    b.textContent = "New session";
    b.onclick = () => newSession();
    box.appendChild(b);
    return;
  }
  if (d) {
    turn = 0;
    for (const o of d.options) {
      const b = document.createElement("button");
      b.className = o.kind + (settings.hint && o.label === d.hint ? " hintmove" : "");
      b.textContent = o.label;
      b.onclick = () => act(o.move);
      box.appendChild(b);
    }
    $("btnCoach").disabled = false;
    $("btnCoach").style.opacity = 1;
  } else {
    const b = document.createElement("button");
    b.className = "next";
    b.textContent = "Next hand";
    b.onclick = () => deal();
    box.appendChild(b);
    $("btnCoach").disabled = true;
    $("btnCoach").style.opacity = 0.4;
  }
}

function waitBar(text) {
  const box = $("actions");
  box.innerHTML = `<button class="wait" disabled>${text}</button>`;
}

async function present(v, fresh) {
  V = v;
  if (fresh && V.hand) startHand();
  draw();
  if (V.hand) {
    await playEvents();
    showGrade();
    if (V.hand.result) {
      await showResult();
      if (V.hand.skipped && !V.hand.graded.length) {
        toast("Clear fold: skipped", 900);
        if (!V.over) { await sleep(250); return deal(); }
      }
    }
  }
  if (V.over) showOver();
  draw();
  drawActions();
}

async function deal() {
  if (busy) return;
  busy = true;
  waitBar("Dealing…");
  try {
    const v = await call({ cmd: "deal" });
    busy = false;
    await present(v, true);
  } catch (e) { busy = false; toast("Error: " + e.message, 6000); drawActions(); }
}

async function act(move) {
  if (busy) return;
  busy = true;
  turn = null;
  waitBar("…");
  try {
    const v = await call({ cmd: "act", move });
    busy = false;
    await present(v, false);
  } catch (e) { busy = false; toast("Error: " + e.message, 6000); drawActions(); }
}

async function newSession() {
  closeSheet();
  busy = true;
  waitBar("New table…");
  const pool = POOLS.includes(settings.pool) ? settings.pool : Number(settings.custom) || 2000000;
  const v = await call({ cmd: "new", table: settings.table || null, pool, level_hands: settings.levels,
                         skip: settings.skip });
  busy = false;
  V = v;
  seat = {}; board = []; pot = 0;
  draw();
  deal();
}

// ---------------------------------------------------------------- sheets
function openSheet(title, html) {
  $("sheettitle").textContent = title;
  $("sheetcontent").innerHTML = html;
  $("sheet").classList.remove("hidden");
}
function closeSheet() { $("sheet").classList.add("hidden"); }
$("sheetclose").onclick = closeSheet;
$("sheet").onclick = (e) => { if (e.target.id === "sheet") closeSheet(); };

function esc(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

async function openCoach(deep) {
  if (!V || !V.decision || busy) return;
  openSheet("ICM Coach", `<p class="c-text">Thinking${deep ? " (the deep analysis takes a few seconds on a phone)" : ""}…</p>`);
  busy = true;
  try {
    const c = await call({ cmd: "coach", deep: !!deep });
    let html = "";
    for (const l of c.lines) {
      const col = l.colour && !["#eceff4", "#8b93a3"].includes(l.colour) ? ` style="color:${l.colour}"` : l.colour === "#8b93a3" ? ` style="color:var(--dim)"` : "";
      const text = l.style === "title" ? l.text.replace(/^ICM Coach\s*\u00b7\s*/, "") : l.text;
      html += `<div class="c-${l.style}"${l.style === "head" ? "" : col}>${esc(text)}</div>`;
    }
    if (!deep) html += `<button class="bigbtn" id="deepbtn">Deeper analysis: opens, 3-bets, 4-bets</button>`;
    $("sheetcontent").innerHTML = html;
    const db = $("deepbtn");
    if (db) db.onclick = () => { busy = false; openCoach(true); };
  } catch (e) { $("sheetcontent").innerHTML = "Couldn't build the coach: " + esc(e.message); }
  busy = false;
}

function openPays() {
  if (!V) return;
  const placeName = (k) => k + (k === 1 ? "st" : k === 2 ? "nd" : k === 3 ? "rd" : "th");
  let html = `<div class="set">Payouts</div>`;
  V.pays.forEach((z, i) => {
    const place = i + 1;
    const who = place === 9 ? "already paid" : (V.players.find((p) => p.out === place) || {}).name || (place === V.left ? "← next out" : "");
    html += `<div class="row${place === V.left ? " me" : ""}"><span>${placeName(place)} ${money(z)}</span><span>${esc(who)}</span></div>`;
  });
  html += `<div class="set">Standings</div>`;
  V.players.filter((p) => !p.out).sort((a, b) => b.stack - a.stack).forEach((p) => {
    html += `<div class="row${p.seat === 0 ? " me" : ""}"><span>${esc(p.name)} · ${fmt(p.stack)} bb</span><span>${money(p.equity)}</span></div>`;
  });
  html += `<div class="set">ICM decisions</div><div class="row"><span>Right</span><span>${V.icm_stats.right} / ${V.icm_stats.spots}</span></div>` +
          `<div class="row"><span>Mistakes cost</span><span>${money(V.icm_stats.lost)}</span></div>`;
  openSheet("Final table", html);
}

function chips(name, opts, cur) {
  return `<div class="chips">` + opts.map(([v, label]) =>
    `<button data-k="${name}" data-v="${v}" class="${String(v) === String(cur) ? "on" : ""}">${label}</button>`).join("") + `</div>`;
}

function openSettings() {
  const html =
    `<div class="set">Table</div>` +
    chips("table", [[0, "Random"]], settings.table ? "" : 0) +
    `<div style="margin-top:6px">or table # <input type="number" id="tableno" min="1" max="250" value="${settings.table || ""}" placeholder="1-250"></div>` +
    `<div class="set">Prize pool (WSOP Circuit payouts)</div>` + chips("pool", [["$500K", "$500K"], ["$1M", "$1M"], ["$1.5M", "$1.5M"], ["custom", "Custom"]], POOLS.includes(settings.pool) ? settings.pool : "custom") +
    `<div style="margin-top:6px">custom $ <input type="number" id="custom" min="1000" step="1000" value="${settings.custom}"></div>` +
    `<div class="set">Blinds up every</div>` + chips("levels", [[15, "15 hands"], [20, "20"], [30, "30"], [0, "Never"]], settings.levels) +
    `<div class="set">Speed</div>` + chips("speed", Object.keys(SPEEDS).map((k) => [k, k]), settings.speed) +
    `<div class="set">Clear folds</div>` + chips("skip", [[true, "Skip them"], [false, "Watch every hand"]], settings.skip) +
    `<div class="set">Hint</div>` + chips("hint", [[true, "Show"], [false, "Hide"]], settings.hint) +
    `<button class="bigbtn" id="newsess">New session with these settings</button>` +
    `<p class="c-text" style="color:var(--faint);font-size:12px;margin-top:14px">Speed, skip and hint apply right away; table, prize pool and blinds start a new session. ` +
    `On iPhone: Share → Add to Home Screen to keep it like an app.</p>`;
  openSheet("Settings", html);
  for (const b of document.querySelectorAll(".chips button")) {
    b.onclick = () => {
      const k = b.dataset.k;
      let v = b.dataset.v;
      if (k === "levels") v = Number(v);
      if (k === "skip" || k === "hint") v = v === "true";
      if (k === "table") v = 0;
      if (k === "pool" && v === "custom") v = "custom";
      settings[k] = v;
      save();
      for (const o of document.querySelectorAll(`.chips button[data-k="${k}"]`)) o.classList.toggle("on", o === b);
      if (k === "skip") call({ cmd: "skip", on: v });
      if (k === "hint" && V) drawActions();
    };
  }
  $("tableno").onchange = (e) => { const n = Number(e.target.value); settings.table = n >= 1 && n <= 250 ? n : 0; save(); };
  $("custom").onchange = (e) => { settings.custom = Number(e.target.value) || 2000000; save(); };
  $("newsess").onclick = () => newSession();
}

function showOver() {
  const [how, place, prize] = V.over;
  const text = how === "win" ? `You won the final table! ${money(prize)}` :
    how === "bust" ? `You finished ${place}${place === 1 ? "st" : place === 2 ? "nd" : place === 3 ? "rd" : "th"}: ${money(prize)}` :
    `Session ended: your seat was worth ${money(prize)}`;
  openSheet("Session over", `<p class="c-title">${text}</p><p class="c-text">ICM decisions right: ${V.icm_stats.right} / ${V.icm_stats.spots} · mistakes cost ${money(V.icm_stats.lost)}</p>` +
    `<button class="bigbtn" onclick="newSession()">New session</button>`);
}

$("btnMenu").onclick = openSettings;
$("btnPays").onclick = openPays;
$("btnCoach").onclick = () => openCoach(false);
$("btnHint").onclick = () => {
  settings.hint = !settings.hint;
  save();
  $("btnHint").classList.toggle("on", settings.hint);
  if (V) drawActions();
};
$("btnHint").classList.toggle("on", settings.hint);

// The table is laid out for a 374 x 468 area (an iPhone 13 in Safari); on a
// smaller screen the whole table is scaled down so nothing overlaps.
const DESIGN_W = 374, DESIGN_H = 468;
function fitTable() {
  const wrap = $("tablewrap"), t = $("table");
  const w = wrap.clientWidth - 16, h = wrap.clientHeight - 8;
  const k = Math.min(1, w / DESIGN_W, h / DESIGN_H);
  t.style.width = w / k + "px";
  t.style.height = h / k + "px";
  t.style.transform = k < 1 ? `scale(${k})` : "";
}
addEventListener("resize", fitTable);
if (window.ResizeObserver) new ResizeObserver(fitTable).observe($("tablewrap"));
fitTable();

async function start() {
  $("loading").classList.add("hidden");
  await newSession();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
