"""Local query console for data/journal.db.

    python explore.py            # serves on http://127.0.0.1:8777 and opens a browser
    python explore.py --port N   # a different port
    python explore.py --no-open  # do not open a browser

The database is opened read-only and the SQL endpoint refuses anything that is not a
single SELECT, so a typo in the console cannot damage the archive.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DB = Path(__file__).resolve().parent / "data" / "journal.db"

ROW_LIMIT = 1000          # rows returned to the browser
CELL_LIMIT = 4000         # characters per cell before truncation
QUERY_TIMEOUT = 15.0      # seconds before a query is aborted

# A statement must start with one of these. WITH is allowed because a CTE that ends in a
# SELECT is still a read - the write forms (INSERT/UPDATE/DELETE after a CTE) are caught by
# the keyword scan below.
READ_STARTS = ("select", "with", "explain")
WRITE_WORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex", "begin", "commit",
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.text_factory = str
    return conn


def guard(sql: str) -> str | None:
    """Return an error message if the statement is not a single read."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "Nothing to run."
    if ";" in stripped:
        return "One statement at a time."
    lowered = stripped.lower()
    if not lowered.startswith(READ_STARTS):
        return "Read-only console: statements must start with SELECT, WITH or EXPLAIN."
    words = set(lowered.replace("(", " ").replace(")", " ").replace(",", " ").split())
    hit = words & set(WRITE_WORDS)
    if hit:
        return f"Read-only console: {sorted(hit)[0].upper()} is not allowed."
    return None


def run(sql: str, params: tuple = ()) -> dict:
    conn = connect()
    deadline = time.monotonic() + QUERY_TIMEOUT
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    started = time.monotonic()
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = []
        for row in cur:
            rows.append([cell(v) for v in row])
            if len(rows) >= ROW_LIMIT:
                break
        return {
            "cols": cols,
            "rows": rows,
            "ms": round((time.monotonic() - started) * 1000),
            "truncated": len(rows) >= ROW_LIMIT,
        }
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg:
            msg = f"Query aborted after {QUERY_TIMEOUT:g}s."
        return {"error": msg}
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def cell(value) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) > CELL_LIMIT:
        return text[:CELL_LIMIT] + f"… [{len(text)} chars]"
    return text


def search(term: str, kind: str, limit: int) -> dict:
    where = "search MATCH ?"
    params: list = [term]
    if kind:
        where += " AND e.kind = ?"
        params.append(kind)
    params.append(limit)
    sql = f"""
        SELECT e.id, e.kind, e.source, e.title,
               snippet(search, 3, '\x02', '\x03', '…', 14) AS match,
               e.speaker, e.scene
        FROM search s JOIN entries e ON e.id = s.id
        WHERE {where} ORDER BY rank LIMIT ?
    """
    return run(sql, tuple(params))


def stats() -> dict:
    conn = connect()
    try:
        kinds = conn.execute(
            "SELECT kind, count(*) FROM entries GROUP BY kind ORDER BY 2 DESC"
        ).fetchall()
        total = sum(c for _, c in kinds)
        return {"kinds": [{"kind": k, "n": n} for k, n in kinds], "total": total}
    finally:
        conn.close()


PRESETS = [
    ("Read a scene in order",
     "SELECT line, speaker, addressee, text FROM entries\n"
     "WHERE scene = 'quest/q101/q101_07_ripperdoc' ORDER BY line;"),
    ("Everything one character says",
     "SELECT scene, line, text FROM entries\n"
     "WHERE speaker_key = 'judy' ORDER BY scene, line;"),
    ("One exchange between two characters",
     "SELECT scene, line, text FROM entries\n"
     "WHERE speaker_key = 'johnny' AND addressee_key = 'alt' ORDER BY scene, line;"),
    ("Where the hits cluster",
     "SELECT scene, count(*) c FROM entries\n"
     "WHERE speaker_key = 'johnny' AND addressee_key = 'alt'\n"
     "GROUP BY scene ORDER BY c DESC;"),
    ("Who talks to whom, most first",
     "SELECT speaker_key, addressee_key, count(*) c FROM entries\n"
     "WHERE kind = 'subtitle' AND addressee_key <> ''\n"
     "GROUP BY 1, 2 ORDER BY c DESC LIMIT 30;"),
    ("A whole SMS thread with one contact",
     "SELECT title, text FROM entries WHERE kind = 'sms' AND contact = 'Judy Alvarez';"),
    ("Busiest speakers",
     "SELECT speaker_key, count(*) c FROM entries\n"
     "WHERE kind = 'subtitle' AND speaker_key <> ''\n"
     "GROUP BY 1 ORDER BY c DESC LIMIT 40;"),
    ("Lines that differ by V's gender",
     "SELECT scene, line, text, json_extract(data, '$.text_male') male FROM entries\n"
     "WHERE json_extract(data, '$.text_male') IS NOT NULL LIMIT 100;"),
    ("Every LocKey for one entry",
     "SELECT id, title, json_extract(data, '$.lockeys') FROM entries\n"
     "WHERE kind = 'shard' AND title LIKE '%Arasaka%';"),
]


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Night City text archive</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{
  --bg:#0d0f12; --panel:#14181d; --panel2:#191e25; --line:#262d36;
  --fg:#d8dee6; --dim:#7d8894; --accent:#fcee0a; --accent2:#00e5ff; --bad:#ff5c57;
  --mono:'Cascadia Mono',Consolas,'DejaVu Sans Mono',monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 'Segoe UI',system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:16px;padding:10px 16px;
  background:var(--panel);border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:15px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);font-weight:600}
header .counts{color:var(--dim);font-size:12px;font-family:var(--mono)}
.tabs{display:flex;gap:2px;margin-left:auto}
.tabs button{background:var(--panel2);color:var(--dim);border:1px solid var(--line);
  padding:5px 14px;cursor:pointer;font:inherit;font-size:13px}
.tabs button.on{color:var(--bg);background:var(--accent);border-color:var(--accent);font-weight:600}
main{flex:1;display:flex;min-height:0}
#left{flex:1;display:flex;flex-direction:column;min-width:0}
.bar{display:flex;gap:8px;padding:10px 16px;border-bottom:1px solid var(--line);align-items:center}
input,select,textarea,button{font:inherit;color:var(--fg);background:var(--panel2);
  border:1px solid var(--line);padding:7px 10px;outline:none}
input:focus,textarea:focus{border-color:var(--accent)}
input[type=search]{flex:1;font-family:var(--mono)}
button.go{background:var(--accent);color:#000;border-color:var(--accent);font-weight:600;cursor:pointer}
button.go:hover{background:#fff45a}
textarea{width:100%;font-family:var(--mono);font-size:13px;resize:vertical;min-height:110px;
  background:var(--panel2);white-space:pre;tab-size:2}
.sqlwrap{padding:10px 16px;border-bottom:1px solid var(--line)}
.sqlrow{display:flex;gap:8px;margin-top:8px;align-items:center}
.hint{color:var(--dim);font-size:12px}
#status{padding:6px 16px;font-family:var(--mono);font-size:12px;color:var(--dim);
  border-bottom:1px solid var(--line);min-height:27px}
#status.err{color:var(--bad)}
#out{flex:1;overflow:auto;padding:0 0 40px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{position:sticky;top:0;background:var(--panel);color:var(--accent2);text-align:left;
  padding:7px 10px;border-bottom:1px solid var(--line);font-weight:600;
  font-family:var(--mono);font-size:12px;white-space:nowrap;z-index:2}
td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top;
  max-width:640px;overflow-wrap:anywhere}
tbody tr:hover{background:#1b2129}
td.num{text-align:right;font-family:var(--mono);color:var(--dim);white-space:nowrap}
td.key{font-family:var(--mono);font-size:12px;color:var(--accent2);white-space:nowrap}
mark{background:rgba(252,238,10,.22);color:var(--accent);padding:0 1px}
a.link{color:var(--accent2);cursor:pointer;text-decoration:none;border-bottom:1px dotted}
a.link:hover{color:var(--accent)}
#side{width:0;flex:none;background:var(--panel);border-left:1px solid var(--line);
  overflow:auto;transition:width .12s}
#side.open{width:min(46vw,720px)}
#side .head{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}
#side .head b{color:var(--accent);font-family:var(--mono);font-size:12px;overflow-wrap:anywhere}
#side .head button{margin-left:auto;cursor:pointer}
#side pre{margin:0;padding:14px;font-family:var(--mono);font-size:12px;
  white-space:pre-wrap;overflow-wrap:anywhere;color:#c3ccd6}
.empty{padding:24px 16px;color:var(--dim)}
kbd{font-family:var(--mono);font-size:11px;background:var(--panel2);border:1px solid var(--line);
  padding:1px 5px}
.hidden{display:none}
</style></head><body>

<header>
  <h1>Night City text archive</h1>
  <span class="counts" id="counts"></span>
  <div class="tabs">
    <button id="tabSearch" class="on">Search</button>
    <button id="tabSql">SQL</button>
  </div>
</header>

<main>
<div id="left">
  <div class="bar" id="searchBar">
    <input type="search" id="q" placeholder="full-text: blackwall &nbsp;|&nbsp; &quot;night city&quot; &nbsp;|&nbsp; denzel OR cryer &nbsp;|&nbsp; NEAR(arasaka tower, 5)" autofocus>
    <select id="kind"><option value="">every kind</option></select>
    <select id="limit">
      <option>50</option><option selected>200</option><option>1000</option>
    </select>
    <button class="go" id="goSearch">Search</button>
  </div>

  <div class="sqlwrap hidden" id="sqlBar">
    <textarea id="sql" spellcheck="false">SELECT id, kind, title FROM entries WHERE kind = 'shard' LIMIT 50;</textarea>
    <div class="sqlrow">
      <button class="go" id="goSql">Run</button>
      <select id="preset"><option value="">a query that answers something…</option></select>
      <span class="hint"><kbd>Ctrl</kbd>+<kbd>Enter</kbd> runs. Read-only: SELECT / WITH only.</span>
    </div>
  </div>

  <div id="status"></div>
  <div id="out"><div class="empty">Type a term and hit Search. Click any <b>id</b> for the whole record, any <b>scene</b> to read it in order.</div></div>
</div>

<div id="side">
  <div class="head"><b id="sideTitle"></b><button id="sideClose">close</button></div>
  <pre id="sideBody"></pre>
</div>
</main>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let lastCols = [];

async function post(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body)});
  return r.json();
}

function setStatus(text, bad){
  const el = $('#status'); el.textContent = text; el.className = bad ? 'err' : '';
}

function render(res){
  if (res.error){ setStatus(res.error, true); $('#out').innerHTML=''; return; }
  const n = res.rows.length;
  setStatus(`${n}${res.truncated ? '+ (capped)' : ''} row${n===1?'':'s'} · ${res.ms} ms`);
  if (!n){ $('#out').innerHTML = '<div class="empty">No rows.</div>'; return; }
  lastCols = res.cols;
  const idCol = res.cols.indexOf('id');
  const sceneCol = res.cols.indexOf('scene');
  const speakerCol = res.cols.findIndex(c => c === 'speaker_key' || c === 'speaker');
  let html = '<table><thead><tr>' +
    res.cols.map(c => `<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>';
  for (const row of res.rows){
    html += '<tr>';
    row.forEach((v, i) => {
      let cls = '', cell;
      if (i === idCol && v){
        cls = 'key'; cell = `<a class="link" data-entry="${esc(v)}">${esc(v)}</a>`;
      } else if (i === sceneCol && v){
        cls = 'key'; cell = `<a class="link" data-scene="${esc(v)}">${esc(v)}</a>`;
      } else if (i === speakerCol && v){
        cls = 'key'; cell = `<a class="link" data-speaker="${esc(v)}">${esc(v)}</a>`;
      } else if (/^-?\d+$/.test(v)){
        cls = 'num'; cell = esc(v);
      } else {
        // \x02 / \x03 are the snippet() markers the server asked for
        cell = esc(v).split('\u0002').join('<mark>').split('\u0003').join('</mark>');
      }
      html += `<td class="${cls}">${cell}</td>`;
    });
    html += '</tr>';
  }
  $('#out').innerHTML = html + '</tbody></table>';
  $('#out').scrollTop = 0;
}

async function doSearch(){
  const term = $('#q').value.trim();
  if (!term) return;
  setStatus('searching…');
  render(await post('/api/search',
    {term, kind: $('#kind').value, limit: +$('#limit').value}));
}

async function doSql(sql){
  if (sql !== undefined) $('#sql').value = sql;
  setStatus('running…');
  render(await post('/api/sql', {sql: $('#sql').value}));
}

function showSql(sql){
  $('#tabSql').classList.add('on'); $('#tabSearch').classList.remove('on');
  $('#sqlBar').classList.remove('hidden'); $('#searchBar').classList.add('hidden');
  doSql(sql);
}

$('#tabSearch').onclick = () => {
  $('#tabSearch').classList.add('on'); $('#tabSql').classList.remove('on');
  $('#searchBar').classList.remove('hidden'); $('#sqlBar').classList.add('hidden');
  $('#q').focus();
};
$('#tabSql').onclick = () => {
  $('#tabSql').classList.add('on'); $('#tabSearch').classList.remove('on');
  $('#sqlBar').classList.remove('hidden'); $('#searchBar').classList.add('hidden');
  $('#sql').focus();
};

$('#goSearch').onclick = () => doSearch();
$('#q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
$('#goSql').onclick = () => doSql();
$('#sql').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)){ e.preventDefault(); doSql(); }
});
$('#preset').onchange = e => { if (e.target.value) showSql(e.target.value); e.target.selectedIndex = 0; };

$('#out').addEventListener('click', async e => {
  const a = e.target.closest('a.link');
  if (!a) return;
  if (a.dataset.entry){
    const res = await post('/api/entry', {id: a.dataset.entry});
    $('#sideTitle').textContent = a.dataset.entry;
    $('#sideBody').textContent = res.error || res.data;
    $('#side').classList.add('open');
  } else if (a.dataset.scene){
    showSql(`SELECT line, speaker, addressee, text FROM entries\nWHERE scene = '${a.dataset.scene.replace(/'/g, "''")}' ORDER BY line;`);
  } else if (a.dataset.speaker){
    const k = a.dataset.speaker.toLowerCase().replace(/\s+/g, ' ').trim().replace(/'/g, "''");
    showSql(`SELECT scene, line, addressee, text FROM entries\nWHERE speaker_key = '${k}' ORDER BY scene, line;`);
  }
});
$('#sideClose').onclick = () => $('#side').classList.remove('open');
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') $('#side').classList.remove('open');
});

(async () => {
  const s = await (await fetch('/api/stats')).json();
  $('#counts').textContent = s.total.toLocaleString() + ' entries · ' +
    s.kinds.map(k => `${k.kind} ${k.n.toLocaleString()}`).join('  ');
  $('#kind').innerHTML = '<option value="">every kind</option>' +
    s.kinds.map(k => `<option value="${k.kind}">${k.kind}</option>`).join('');
  const p = await (await fetch('/api/presets')).json();
  $('#preset').innerHTML = '<option value="">a query that answers something…</option>' +
    p.map(x => `<option value="${x[1].replace(/"/g,'&quot;')}">${x[0]}</option>`).join('');
})();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; the console is the interface
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/stats":
            self.send_json(stats())
        elif path == "/api/presets":
            self.send_json(PRESETS)
        else:
            self.send_json({"error": "no such path"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json({"error": "bad request body"}, 400)

        if path == "/api/search":
            term = (payload.get("term") or "").strip()
            limit = min(int(payload.get("limit") or 200), ROW_LIMIT)
            self.send_json(search(term, payload.get("kind") or "", limit))
        elif path == "/api/sql":
            sql = payload.get("sql") or ""
            problem = guard(sql)
            self.send_json({"error": problem} if problem else run(sql.strip().rstrip(";")))
        elif path == "/api/entry":
            res = run("SELECT data FROM entries WHERE id = ?", (payload.get("id") or "",))
            if res.get("error"):
                self.send_json(res)
            elif not res["rows"]:
                self.send_json({"error": "no such id"})
            else:
                try:
                    pretty = json.dumps(json.loads(res["rows"][0][0]),
                                        ensure_ascii=False, indent=2)
                except ValueError:
                    pretty = res["rows"][0][0]
                self.send_json({"data": pretty})
        else:
            self.send_json({"error": "no such path"}, 404)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"No database at {DB}. Run: python build.py --db-only", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"Night City text archive: {url}   (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
