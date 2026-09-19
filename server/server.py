"""OneDrive に集まったセッション状態ファイルを読み、ダッシュボードとして配信する。

使い方: py -3 server.py [--port 8765] [--dir <agent-dashboard フォルダ>]
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
ENDED_VISIBLE = timedelta(minutes=30)


def load_sessions(root: Path):
    now = datetime.now(timezone.utc)
    sessions = []
    for f in root.glob("*/*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8-sig"))
            updated = datetime.fromisoformat(s["updated_at"].replace("Z", "+00:00"))
        except (OSError, ValueError, KeyError):
            continue  # 同期途中・破損ファイルは次回に回す
        if s.get("state") == "ended" and now - updated > ENDED_VISIBLE:
            continue
        sessions.append(s)
    return sessions


class Handler(BaseHTTPRequestHandler):
    root: Path

    def do_GET(self):
        if self.path == "/api/sessions":
            body = json.dumps(load_sessions(self.root), ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        elif self.path == "/":
            body = (HERE / "index.html").read_bytes()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dir", default=os.path.join(os.environ.get("OneDrive", ""), "agent-dashboard"))
    args = ap.parse_args()
    Handler.root = Path(args.dir)
    print(f"watching {Handler.root}\nhttp://localhost:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
