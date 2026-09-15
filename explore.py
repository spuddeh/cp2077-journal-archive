"""Local query console for data/journal.db - the same page GitHub Pages publishes.

    python explore.py            # serves on http://127.0.0.1:8777 and opens a browser
    python explore.py --port N   # a different port
    python explore.py --no-open  # do not open a browser

The page runs every query in the browser and reads the database by HTTP range requests,
so this server hands out files and byte ranges and nothing else. Python's http.server
ignores Range headers, which sql.js-httpvfs cannot work without, so the handler adds
single-range support. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
DB = HERE / "data" / "journal.db"
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

# The published site reads split parts through a generated config; locally the page reads
# the one database file. requestChunkSize must equal the database page size.
CONFIG = {"serverMode": "full", "requestChunkSize": 4096, "url": "journal.db"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, fmt, *args):  # quiet; the page is the interface
        pass

    def translate_path(self, path):
        if urlparse(path).path == "/data/journal.db":
            return str(DB)
        return super().translate_path(path)

    def send_head(self):
        path = urlparse(self.path).path
        if path == "/data/config.json":
            body = json.dumps(CONFIG).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

        m = RANGE_RE.match(self.headers.get("Range") or "")
        if not m or not (m.group(1) or m.group(2)):
            return super().send_head()

        target = self.translate_path(self.path)
        if not os.path.isfile(target):
            self.send_error(404)
            return None
        size = os.path.getsize(target)
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                  # suffix range: the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            self.send_error(416)
            return None

        fh = open(target, "rb")
        fh.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(target))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._range_length = end - start + 1
        return fh

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_length", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            block = source.read(min(65536, remaining))
            if not block:
                break
            outputfile.write(block)
            remaining -= len(block)
        self._range_length = None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        # On every response: the library probes with a plain GET and falls back to reading
        # the whole file when this header is missing.
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()


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
