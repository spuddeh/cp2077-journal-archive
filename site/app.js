"use strict";

/* Queries journal.db over HTTP range requests via sql.js-httpvfs. Every query runs in the
   browser; the server only hands out byte ranges of the database file. The address bar
   holds the page state (tab, search, query, open record), so a link reproduces a view. */

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ROW_LIMIT = 1000;
const CELL_LIMIT = 4000;

let DB = null;
let WORKER = null;
let editor = null;

/* ------------------------------------------------------------------ boot -- */

async function boot() {
  try {
    // The worker resolves relative URLs against its own script, not this page.
    const abs = (p) => new URL(p, location.href).toString();
    const w = await createDbWorker(
      [{ from: "jsonconfig", configUrl: abs("data/config.json") }],
      abs("vendor/sqljs-httpvfs/sqlite.worker.js"),
      abs("vendor/sqljs-httpvfs/sql-wasm.wasm")
    );
    DB = w.db;
    WORKER = w.worker;
  } catch (e) {
    $("#counts").innerHTML = '<span class="err">database connection failed: ' + esc(String(e)) +
      " - serve this folder over HTTP with Range support (python explore.py), not file://</span>";
    return;
  }
  const [kinds, schema] = await Promise.all([
    run("SELECT kind, n FROM kinds ORDER BY n DESC"),
    run("SELECT m.name, p.name FROM sqlite_master m, pragma_table_info(m.name) p " +
        "WHERE m.type IN ('table','view') AND m.name NOT LIKE 'sqlite_%' " +
        "AND m.name NOT LIKE 'search!_%' ESCAPE '!'"),
  ]);
  if (!kinds.error) {
    const total = kinds.rows.reduce((a, r) => a + r[1], 0);
    $("#counts").textContent = total.toLocaleString() + " entries · " +
      kinds.rows.map((r) => r[0] + " " + r[1].toLocaleString()).join("  ");
    $("#kind").innerHTML = '<option value="">every kind</option>' +
      kinds.rows.map((r) => `<option value="${esc(r[0])}">${esc(r[0])}</option>`).join("");
  }
  const tables = {};
  for (const [t, c] of schema.rows || []) (tables[t] = tables[t] || []).push(c);
  initEditor(tables);
  window.addEventListener("hashchange", route);
  route();
}

async function run(sql, params) {
  // exec() takes the bind values as ONE array; spread values bind nothing.
  const started = performance.now();
  try {
    const res = await DB.exec(sql, params && params.length ? params : undefined);
    const last = res.length ? res[res.length - 1] : { columns: [], values: [] };
    return { cols: last.columns, rows: last.values, ms: Math.round(performance.now() - started) };
  } catch (e) {
    return { error: String(e.message || e).replace(/^(Error: )?(SQLite: )?/, "") };
  } finally {
    updateStats();
  }
}

async function updateStats() {
  try {
    const s = await WORKER.getStats();
    if (s) $("#stats").textContent =
      (s.totalFetchedBytes / 1048576).toFixed(1) + " MB fetched · " + s.totalRequests + " requests";
  } catch (e) { /* the counter is decoration */ }
}

/* ----------------------------------------------------------------- state -- */

// #search?q=..&kind=..&limit=..&id=..   #sql?q=..&id=..   #about
function readHash() {
  const raw = location.hash.slice(1);
  const i = raw.indexOf("?");
  const tab = (i < 0 ? raw : raw.slice(0, i)) || "search";
  return { tab, p: new URLSearchParams(i < 0 ? "" : raw.slice(i + 1)) };
}

function writeHash(tab, params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  const s = p.toString();
  const next = "#" + tab + (s ? "?" + s : "");
  if (location.hash !== next) location.hash = next;
  else route();
}

function currentParams() {
  const { tab, p } = readHash();
  return { tab, params: Object.fromEntries(p.entries()) };
}

let lastRun = "";   // the tab+query last executed, so opening a record does not re-run it

async function route() {
  const { tab, p } = readHash();
  showTab(tab);
  const key = tab + "|" + (p.get("q") || "") + "|" + (p.get("kind") || "") + "|" + (p.get("limit") || "");
  if (tab === "search") {
    $("#q").value = p.get("q") || "";
    $("#kind").value = p.get("kind") || "";
    if (p.get("limit")) $("#limit").value = p.get("limit");
    if (p.get("q") && key !== lastRun) { lastRun = key; await doSearch(); }
  } else if (tab === "sql") {
    if (p.get("q") && key !== lastRun) { lastRun = key; setSql(p.get("q")); await doSql(); }
  }
  if (p.get("id")) openRecord(p.get("id"));
  else $("#side").classList.remove("open");
}

function showTab(which) {
  if (!["search", "sql", "about"].includes(which)) which = "search";
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === which));
  for (const t of ["search", "sql", "about"]) $("#pane-" + t).classList.toggle("hidden", t !== which);
  document.body.classList.toggle("about", which === "about");
  if (which === "sql" && editor) editor.refresh();
}

/* ---------------------------------------------------------------- render -- */

function setStatus(text, bad) {
  const el = $("#status");
  el.textContent = text;
  el.className = bad ? "err" : "";
}

function cellText(v) {
  if (v === null || v === undefined) return "";
  const t = typeof v === "string" ? v : String(v);
  return t.length > CELL_LIMIT ? t.slice(0, CELL_LIMIT) + `… [${t.length} chars]` : t;
}

function render(res) {
  if (res.error) { setStatus(res.error, true); $("#out").innerHTML = ""; return; }
  const n = res.rows.length;
  const capped = n > ROW_LIMIT;
  const rows = capped ? res.rows.slice(0, ROW_LIMIT) : res.rows;
  setStatus(`${rows.length}${capped ? "+ (capped)" : ""} row${rows.length === 1 ? "" : "s"} · ${res.ms} ms`);
  if (!n) { $("#out").innerHTML = '<div class="empty">No rows.</div>'; return; }
  const idCol = res.cols.indexOf("id");
  const sceneCol = res.cols.indexOf("scene");
  const speakerCol = res.cols.findIndex((c) => c === "speaker_key" || c === "speaker");
  let html = "<table><thead><tr>" + res.cols.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>";
  for (const row of rows) {
    html += "<tr>";
    row.forEach((raw, i) => {
      const v = cellText(raw);
      let cls = "", cell;
      if (i === idCol && v) {
        cls = "key"; cell = `<a class="link" data-entry="${esc(v)}">${esc(v)}</a>`;
      } else if (i === sceneCol && v) {
        cls = "key"; cell = `<a class="link" data-scene="${esc(v)}">${esc(v)}</a>`;
      } else if (i === speakerCol && v) {
        cls = "key"; cell = `<a class="link" data-speaker="${esc(v)}">${esc(v)}</a>`;
      } else if (typeof raw === "number") {
        cls = "num"; cell = esc(v);
      } else {
        // \x02 / \x03 are the snippet() markers the search asks for
        cell = esc(v).split("\u0002").join("<mark>").split("\u0003").join("</mark>");
      }
      html += `<td class="${cls}">${cell}</td>`;
    });
    html += "</tr>";
  }
  $("#out").innerHTML = html + "</tbody></table>";
  $("#out").scrollTop = 0;
}

/* ---------------------------------------------------------------- search -- */

// `field:value` filters on a column of `entries`. The full-text index covers title and text
// only, so an unknown field reaches FTS5 and comes back as "no such column". A key column is
// matched on its normalised form, the only way to catch a name authored with inconsistent case.
const FIELDS = {
  speaker: ["speaker_key", true],
  addressee: ["addressee_key", true],
  speaker_key: ["speaker_key", true],
  addressee_key: ["addressee_key", true],
  kind: ["kind", false],
  source: ["source", false],
  scene: ["scene", false],
  contact: ["contact", false],
  category: ["category", false],
  quest_type: ["quest_type", false],
  address: ["address", false],
  id: ["id", false],
};
const USABLE_FIELDS = Object.keys(FIELDS).filter((f) => !f.endsWith("_key")).sort();

function parseFilters(term) {
  const found = [];
  const rest = term.replace(/(\w+)\s*:\s*("[^"]*"|'[^']*'|\S+)/g, (m, field, value) => {
    const f = FIELDS[field.toLowerCase()];
    if (!f) return m;   // an unknown field stays in the full-text term
    value = value.replace(/^["']|["']$/g, "");
    if (f[1]) value = value.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
    found.push([f[0], value]);
    return " ";
  });
  return [rest.split(/\s+/).filter(Boolean).join(" "), found];
}

async function doSearch() {
  const term = $("#q").value.trim();
  if (!term) return;
  const kind = $("#kind").value;
  const limit = Math.min(+$("#limit").value || 200, ROW_LIMIT);
  const [text, filters] = parseFilters(term);
  if (kind) filters.push(["kind", kind]);
  const clauses = filters.map(([c]) => `e.${c} = ?`);
  let sql, params = filters.map(([, v]) => v);

  if (text) {
    // search.rowid is entries.rowid, so the join is an integer lookup
    sql = `SELECT e.id, e.kind, e.source, e.title,
             snippet(search, -1, char(2), char(3), '…', 14) AS match,
             e.speaker, e.addressee, e.scene
           FROM search s JOIN entries e ON e.rowid = s.rowid
           WHERE ${["search MATCH ?"].concat(clauses).join(" AND ")} ORDER BY s.rank LIMIT ?`;
    params = [text].concat(params, [limit]);
  } else if (filters.length) {
    // filters alone: no text to rank on, so read in authored order
    sql = `SELECT e.id, e.kind, e.source, e.title, e.text, e.speaker, e.addressee, e.scene, e.line
           FROM entries e WHERE ${clauses.join(" AND ")}
           ORDER BY e.scene, e.line, e.id LIMIT ?`;
    params = params.concat([limit]);
  } else {
    return;
  }
  setStatus("searching…");
  const res = await run(sql, params);
  if (res.error && res.error.startsWith("no such column")) {
    const column = res.error.split(":").pop().trim();
    res.error = `'${column}:' is not a filter. Full text covers title: and text: only; ` +
      `the fields that filter are ${USABLE_FIELDS.join(", ")}. Anything else, use the SQL tab.`;
  } else if (res.error && /fts5: syntax error/.test(res.error)) {
    res.error += ` - put a word holding punctuation in double quotes, "like this".`;
  }
  render(res);
}

function submitSearch() {
  const q = $("#q").value.trim();
  if (!q) return;
  lastRun = "";
  writeHash("search", { q, kind: $("#kind").value, limit: $("#limit").value === "200" ? "" : $("#limit").value });
}

/* ------------------------------------------------------------------- sql -- */

const PRESETS = [
  ["Read a scene in order",
   "SELECT line, speaker, addressee, text FROM entries\nWHERE scene = 'quest/q101/q101_07_ripperdoc' ORDER BY line"],
  ["Everything one character says",
   "SELECT scene, line, text FROM entries\nWHERE speaker_key = 'judy' ORDER BY scene, line"],
  ["One exchange between two characters",
   "SELECT scene, line, text FROM entries\nWHERE speaker_key = 'johnny' AND addressee_key = 'alt' ORDER BY scene, line"],
  ["Where the hits cluster",
   "SELECT scene, count(*) c FROM entries\nWHERE speaker_key = 'johnny' AND addressee_key = 'alt'\nGROUP BY scene ORDER BY c DESC"],
  ["Who talks to whom, most first",
   "SELECT speaker_key, addressee_key, count(*) c FROM entries\nWHERE kind = 'subtitle' AND addressee_key <> ''\nGROUP BY 1, 2 ORDER BY c DESC LIMIT 30"],
  ["A whole SMS thread with one contact",
   "SELECT id, title, text FROM entries WHERE kind = 'sms' AND contact = 'Judy Alvarez'"],
  ["Busiest speakers",
   "SELECT speaker_key, count(*) c FROM entries\nWHERE kind = 'subtitle' AND speaker_key <> ''\nGROUP BY 1 ORDER BY c DESC LIMIT 40"],
  ["Lines that differ by V's gender",
   "SELECT scene, line, text, json_extract(data, '$.text_male') male FROM entries\nWHERE json_extract(data, '$.text_male') IS NOT NULL ORDER BY scene, line LIMIT 100"],
  ["Every LocKey for one entry",
   "SELECT e.id, e.title, json_extract(e.data, '$.lockeys') lockeys\nFROM search s JOIN entries e ON e.rowid = s.rowid\nWHERE search MATCH 'title:arasaka' AND e.kind = 'shard'"],
];

const getSql = () => (editor ? editor.getValue() : $("#sql").value);
function setSql(text) {
  if (editor) { editor.setValue(text); editor.refresh(); editor.setCursor(editor.lineCount(), 0); }
  else $("#sql").value = text;
}

async function doSql() {
  let sql = getSql().trim().replace(/;\s*$/, "");
  if (!sql) return;
  if (sql.includes(";")) return render({ error: "One statement at a time." });
  if (!/^(select|with|explain)\b/i.test(sql)) {
    return render({ error: "Statements must start with SELECT, WITH or EXPLAIN." });
  }
  // One row past the cap, so a capped result can say so.
  if (!/^explain\b/i.test(sql) && !/\blimit\s+\d+\s*(offset\s+\d+\s*)?$/i.test(sql)) {
    sql += "\nLIMIT " + (ROW_LIMIT + 1);
  }
  setStatus("running… a query no index answers downloads the rows it reads");
  render(await run(sql));
}

function submitSql() {
  const q = getSql().trim();
  if (!q) return;
  lastRun = "";
  writeHash("sql", { q });
}

function showSql(sql) {
  setSql(sql);
  lastRun = "";
  writeHash("sql", { q: sql });
}

function initEditor(tables) {
  if (typeof CodeMirror === "undefined") return;
  editor = CodeMirror.fromTextArea($("#sql"), {
    mode: "text/x-sqlite",
    lineNumbers: true,
    matchBrackets: true,
    autoCloseBrackets: true,
    viewportMargin: Infinity,
    extraKeys: {
      "Ctrl-Enter": () => submitSql(),
      "Cmd-Enter": () => submitSql(),
      "Ctrl-Space": "autocomplete",
      Tab: (cm) => cm.replaceSelection("  "),
    },
    hintOptions: { tables, completeSingle: false },
  });
  // completing as you type: only on a word, never inside a string or after a digit
  editor.on("inputRead", (cm, change) => {
    if (change.origin !== "+input") return;
    if (!/[\w.]/.test(change.text[0])) return;
    const token = cm.getTokenAt(cm.getCursor());
    if (token.type === "string" || token.type === "comment" || token.type === "number") return;
    if (token.string.length < 2) return;
    cm.showHint({ completeSingle: false });
  });
}

/* ---------------------------------------------------------------- record -- */

const SHOWN = new Set(["id", "kind", "source", "title", "text", "text_male", "speaker", "addressee",
  "scene", "line", "contact", "category", "quest_type", "address", "lockeys", "spoken_original"]);

async function openRecord(id) {
  $("#sideTitle").textContent = id;
  $("#side").classList.add("open");
  const res = await run("SELECT data FROM entries WHERE id = ?", [id]);
  if (res.error || !res.rows.length) {
    $("#sideBody").innerHTML = `<div class="rec">${esc(res.error || "no such id")}</div>`;
    return;
  }
  let rec;
  try { rec = JSON.parse(res.rows[0][0]); } catch (e) { rec = null; }
  if (!rec) { $("#sideBody").innerHTML = `<div class="rec"><pre>${esc(res.rows[0][0])}</pre></div>`; return; }

  const row = (k, v) => (v === undefined || v === null || v === "" ? "" : `<dt>${esc(k)}</dt><dd>${v}</dd>`);
  let h = '<div class="rec"><dl>';
  h += row("kind", esc(rec.kind) + " · " + esc(rec.source));
  if (rec.speaker) {
    h += row("speaker", `<a class="link" data-speaker="${esc(rec.speaker)}">${esc(rec.speaker)}</a>` +
      (rec.addressee ? " → " + esc(rec.addressee) : ""));
  }
  if (rec.scene) h += row("scene", `<a class="link" data-scene="${esc(rec.scene)}">${esc(rec.scene)}</a> · line ${esc(rec.line)}`);
  for (const k of ["contact", "category", "quest_type", "address"]) h += row(k, rec[k] ? esc(rec[k]) : "");
  if (Array.isArray(rec.lockeys) && rec.lockeys.length) h += row("lockeys", esc(rec.lockeys.join(", ")));
  h += "</dl>";
  if (rec.title) h += `<h3>title</h3><div class="body">${esc(rec.title)}</div>`;
  if (rec.text) h += `<h3>text</h3><div class="body">${esc(rec.text)}</div>`;
  if (rec.text_male) h += `<h3>text_male</h3><div class="body alt">${esc(rec.text_male)}</div>`;
  if (rec.spoken_original) h += `<h3>spoken_original</h3><div class="body alt">${esc(rec.spoken_original)}</div>`;
  const extra = Object.keys(rec).filter((k) => !SHOWN.has(k));
  h += `<h3>full record${extra.length ? " (also: " + esc(extra.join(", ")) + ")" : ""}</h3>`;
  h += `<pre>${esc(JSON.stringify(rec, null, 2))}</pre></div>`;
  $("#sideBody").innerHTML = h;
  $("#side").scrollTop = 0;
}

function closeRecord() {
  const { tab, params } = currentParams();
  delete params.id;
  writeHash(tab, params);
}

/* ---------------------------------------------------------------- events -- */

document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    const tab = b.dataset.tab;
    if (tab === "search") writeHash("search", { q: $("#q").value.trim(), kind: $("#kind").value });
    else if (tab === "sql") writeHash("sql", { q: readHash().tab === "sql" ? readHash().p.get("q") : "" });
    else writeHash(tab, {});
  };
});

$("#goSearch").onclick = submitSearch;
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") submitSearch(); });
$("#goSql").onclick = submitSql;
$("#sql").addEventListener("keydown", (e) => {   // the fallback textarea; the editor keymaps its own
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitSql(); }
});
$("#preset").innerHTML = '<option value="">a query that answers something…</option>' +
  PRESETS.map((x, i) => `<option value="${i}">${esc(x[0])}</option>`).join("");
$("#preset").onchange = (e) => {
  if (e.target.value !== "") showSql(PRESETS[+e.target.value][1]);
  e.target.selectedIndex = 0;
};

function onLink(e) {
  const a = e.target.closest("a.link");
  if (!a) return;
  if (a.dataset.entry) {
    const { tab, params } = currentParams();
    writeHash(tab, Object.assign(params, { id: a.dataset.entry }));
  } else if (a.dataset.scene) {
    showSql(`SELECT id, line, speaker, addressee, text FROM entries\nWHERE scene = '${a.dataset.scene.replace(/'/g, "''")}' ORDER BY line`);
  } else if (a.dataset.speaker) {
    const k = a.dataset.speaker.toLowerCase().replace(/\s+/g, " ").trim().replace(/'/g, "''");
    showSql(`SELECT id, scene, line, addressee, text FROM entries\nWHERE speaker_key = '${k}' ORDER BY scene, line`);
  }
}
$("#out").addEventListener("click", onLink);
$("#sideBody").addEventListener("click", onLink);
$("#sideClose").onclick = closeRecord;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#side").classList.contains("open")) closeRecord();
});

$("#fieldList").innerHTML = USABLE_FIELDS.map((f) => `<span class="chip">${f}:</span>`).join("");
$("#fieldList").addEventListener("click", (e) => {
  const c = e.target.closest(".chip");
  if (!c) return;
  const box = $("#q");
  box.value = (box.value.trim() + " " + c.textContent).trim();
  box.focus();
});

boot();
