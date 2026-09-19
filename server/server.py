"""OneDrive に集まった Agent Dashboard の状態を localhost で配信する。"""
import argparse
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent
ENDED_VISIBLE = timedelta(minutes=30)
HOST_STALE_AFTER = timedelta(minutes=3)
HOST_OFFLINE_AFTER = timedelta(minutes=10)
SESSION_STALE_AFTER = timedelta(minutes=5)
STALE_SESSION_VISIBLE = timedelta(hours=24)
PUBLIC_SESSION_FIELDS = {"pc", "tool", "session_id", "project", "branch", "activity", "state", "updated_at"}
KNOWN_STATES = {"running", "waiting", "idle", "error", "ended"}
KNOWN_TOOLS = {"claude", "codex"}
SETTINGS_FILE_NAME = "settings.json"


def default_root():
    onedrive = os.environ.get("OneDrive")
    return Path(onedrive, "agent-dashboard") if onedrive else None


def settings_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(base, "Agent Dashboard", SETTINGS_FILE_NAME) if base else HERE / SETTINGS_FILE_NAME


def resolve_root(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("起点フォルダを入力してください。")
    root = Path(os.path.expandvars(os.path.expanduser(value.strip()))).resolve()
    if not root.is_dir():
        raise ValueError("指定したフォルダが存在しないか、フォルダではありません。")
    return root


def load_saved_root(path):
    try:
        return resolve_root(read_json(path)["root_dir"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_root(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps({"root_dir": str(root)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class DashboardState:
    def __init__(self, root, config_path):
        self._lock = threading.Lock()
        self._root = root
        self.config_path = config_path

    def root(self):
        with self._lock:
            return self._root

    def settings(self):
        root = self.root()
        default = default_root()
        return {
            "root_dir": str(root) if root else "",
            "default_root_dir": str(default) if default else "",
        }

    def update_root(self, value):
        root = resolve_root(value)
        with self._lock:
            save_root(self.config_path, root)
            self._root = root
        return root


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def host_status(age):
    if age <= HOST_STALE_AFTER:
        return "online"
    if age <= HOST_OFFLINE_AFTER:
        return "stale"
    return "offline"


def load_dashboard(root):
    now = datetime.now(timezone.utc)
    hosts = []
    sessions = []
    errors = 0
    known_pcs = set()

    if root is None or not root.is_dir():
        return {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "hosts": hosts,
            "sessions": sessions,
            "data_errors": errors,
            "source_error": "起点フォルダが未設定か、アクセスできません。設定から有効なフォルダを指定してください。",
        }

    try:
        host_paths = sorted(root.glob("*/host.json"))
        session_paths = sorted(root.glob("*/*.json"))
    except OSError:
        return {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "hosts": hosts,
            "sessions": sessions,
            "data_errors": errors,
            "source_error": "起点フォルダを読み取れません。アクセス権を確認してください。",
        }

    for path in host_paths:
        try:
            source = read_json(path)
            seen = parse_time(source["last_seen_at"])
            pc = source["pc"]
            if not isinstance(pc, str) or not pc:
                raise ValueError("pc is required")
            age = max(timedelta(), now - seen)
            tools = source.get("tools") if isinstance(source.get("tools"), dict) else {}
            hosts.append({
                "pc": pc,
                "last_seen_at": seen.isoformat().replace("+00:00", "Z"),
                "status": host_status(age),
                "tools": {
                    "claude": bool(tools.get("claude", {}).get("configured")),
                    "codex": bool(tools.get("codex", {}).get("configured")),
                },
            })
            known_pcs.add(pc)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            errors += 1

    for path in session_paths:
        if path.name == "host.json":
            continue
        try:
            source = read_json(path)
            updated = parse_time(source["updated_at"])
            pc = source["pc"]
            state = source["state"]
            tool = source.get("tool")
            if not isinstance(pc, str) or state not in KNOWN_STATES or tool not in KNOWN_TOOLS:
                raise ValueError("invalid session")
            if state == "ended" and now - updated > ENDED_VISIBLE:
                continue
            if state != "ended" and now - updated > STALE_SESSION_VISIBLE:
                continue
            session = {key: source.get(key) for key in PUBLIC_SESSION_FIELDS}
            session["pc"] = pc
            session["updated_at"] = updated.isoformat().replace("+00:00", "Z")
            if state != "ended" and now - updated > SESSION_STALE_AFTER:
                session["state"] = "stale"
            sessions.append(session)
            if pc not in known_pcs:
                hosts.append({"pc": pc, "last_seen_at": session["updated_at"], "status": "unknown", "tools": {}})
                known_pcs.add(pc)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            errors += 1

    hosts.sort(key=lambda host: host["pc"].casefold())
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "hosts": hosts,
        "sessions": sessions,
        "data_errors": errors,
    }


class Handler(BaseHTTPRequestHandler):
    state: DashboardState

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self.send_json(200, load_dashboard(self.state.root()))
            return
        if path == "/api/settings":
            self.send_json(200, self.state.settings())
            return
        if path == "/":
            body = (HERE / "index.html").read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if urlparse(self.path).path != "/api/settings":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("設定内容が不正です。")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            root = self.state.update_root(payload.get("root_dir"))
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error) or "設定を保存できませんでした。"})
            return
        self.send_json(200, {"root_dir": str(root)})

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dir", help="起点フォルダ。指定時は保存済み設定より優先します")
    args = parser.parse_args()
    config_path = settings_path()
    if args.dir:
        try:
            root = resolve_root(args.dir)
        except ValueError as error:
            parser.error(str(error))
    else:
        root = load_saved_root(config_path) or default_root()
    Handler.state = DashboardState(root, config_path)
    print(f"watching {root or 'no folder configured'}\nhttp://localhost:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
