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
SCHEMA_VERSION = 3
MAX_JSON_BYTES = 32 * 1024
MAX_HOSTS = 100
MAX_SESSIONS_PER_HOST = 100
ENDED_VISIBLE = timedelta(minutes=30)
HOST_STALE_AFTER = timedelta(minutes=3)
HOST_UNMONITORABLE_AFTER = timedelta(minutes=10)
SESSION_STALE_AFTER = timedelta(minutes=5)
STALE_SESSION_VISIBLE = timedelta(hours=24)
FUTURE_CLOCK_TOLERANCE = timedelta(minutes=1)
KNOWN_STATES = {"running", "waiting", "idle", "error", "ended"}
KNOWN_TOOLS = {"claude", "codex"}
KNOWN_ACTIVITIES = {"tool_running", "tool_failed", "tool_interrupted", "approval_pending", "approval_denied", "turn_failed", "interrupted"}
KNOWN_FAILURE_REASONS = {"rate_limit", "overloaded", "authentication_failed", "oauth_org_not_allowed", "account_on_hold", "billing_error", "invalid_request", "model_not_found", "server_error", "max_output_tokens", "cloud_credential_error", "unknown"}
KNOWN_PROJECT_STATUSES = {"available", "cwd_missing", "cwd_root", "cwd_error"}
KNOWN_BRANCH_STATUSES = {"branch", "detached", "non_git", "unavailable"}
SETTINGS_FILE_NAME = "settings.json"


class DataError(ValueError):
    pass


def default_root():
    onedrive = os.environ.get("OneDrive")
    return Path(onedrive, "agent-dashboard") if onedrive else None


def settings_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(base, "Agent Dashboard", SETTINGS_FILE_NAME) if base else HERE / SETTINGS_FILE_NAME


def parse_time(value):
    if not isinstance(value, str):
        raise DataError("timestamp_type")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DataError("timestamp_format") from error
    if parsed.tzinfo is None:
        raise DataError("timestamp_timezone")
    return parsed.astimezone(timezone.utc)


def iso_time(value):
    return value.isoformat().replace("+00:00", "Z")


def text(value, name, maximum, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise DataError(f"{name}_type")
    if required and not value:
        raise DataError(f"{name}_required")
    if len(value) > maximum:
        raise DataError(f"{name}_too_long")
    if any(ord(character) < 32 for character in value):
        raise DataError(f"{name}_control_character")
    return value


def read_json(path):
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise DataError("file_too_large")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise DataError("file_unreadable") from error
    except json.JSONDecodeError as error:
        raise DataError("json_invalid") from error
    if not isinstance(value, dict):
        raise DataError("json_object_required")
    return value


def require_schema(source):
    if source.get("schema_version") != SCHEMA_VERSION:
        raise DataError("schema_version")


def parse_tool(value, errors):
    if not isinstance(value, dict):
        errors.append("tool_contract")
        return {"configured": False, "trust": "unknown"}
    configured = value.get("configured")
    trust = value.get("trust", "unknown")
    if not isinstance(configured, bool) or trust not in {"not_applicable", "unverified"}:
        errors.append("tool_contract")
        return {"configured": False, "trust": "unknown"}
    return {"configured": configured, "trust": trust}


def validate_host(source, directory_id):
    require_schema(source)
    host_id = text(source.get("host_id"), "host_id", 64, required=True)
    if host_id != directory_id:
        raise DataError("host_id_mismatch")
    heartbeat = parse_time(source.get("last_heartbeat_at"))
    errors = []
    raw_tools = source.get("tools")
    if not isinstance(raw_tools, dict):
        errors.append("tools_contract")
        raw_tools = {}
    return {
        "id": host_id,
        "pc": text(source.get("pc"), "pc", 64, required=True),
        "user": text(source.get("user"), "user", 64, required=True),
        "last_heartbeat_at": iso_time(heartbeat),
        "tools": {tool: parse_tool(raw_tools.get(tool), errors) for tool in KNOWN_TOOLS},
        "data_errors": errors,
    }, heartbeat


def validate_hook_health(source, directory_id):
    require_schema(source)
    if text(source.get("host_id"), "host_id", 64, required=True) != directory_id:
        raise DataError("hook_host_id_mismatch")
    return parse_time(source.get("last_hook_at"))


def validate_session(source, directory_id):
    require_schema(source)
    if text(source.get("host_id"), "host_id", 64, required=True) != directory_id:
        raise DataError("session_host_id_mismatch")
    tool = source.get("tool")
    state = source.get("state")
    activity = source.get("activity")
    if tool not in KNOWN_TOOLS:
        raise DataError("tool_value")
    if state not in KNOWN_STATES:
        raise DataError("state_value")
    if activity not in KNOWN_ACTIVITIES and activity is not None:
        raise DataError("activity_value")
    failure_reason = source.get("failure_reason")
    if failure_reason is not None and failure_reason not in KNOWN_FAILURE_REASONS:
        raise DataError("failure_reason")
    project_status = source.get("project_status")
    branch_status = source.get("branch_status")
    if project_status not in KNOWN_PROJECT_STATUSES or branch_status not in KNOWN_BRANCH_STATUSES:
        raise DataError("metadata_status")
    updated = parse_time(source.get("updated_at"))
    started = parse_time(source.get("started_at"))
    expires = source.get("activity_expires_at")
    if expires is not None:
        expires = parse_time(expires)
    return {
        "host_id": directory_id,
        "pc": text(source.get("pc"), "pc", 64, required=True),
        "user": text(source.get("user"), "user", 64, required=True),
        "tool": tool,
        "session": text(source.get("session"), "session", 16, required=True),
        "project": text(source.get("project"), "project", 96),
        "project_id": text(source.get("project_id"), "project_id", 32),
        "project_status": project_status,
        "branch": text(source.get("branch"), "branch", 128),
        "branch_status": branch_status,
        "activity": activity,
        "activity_expires_at": iso_time(expires) if expires else None,
        "failure_reason": failure_reason,
        "state": state,
        "started_at": iso_time(started),
        "updated_at": iso_time(updated),
    }, updated


def health_status(heartbeat, now):
    if heartbeat > now + FUTURE_CLOCK_TOLERANCE:
        return "clock_skew"
    age = now - heartbeat
    if age <= HOST_STALE_AFTER:
        return "healthy"
    if age <= HOST_UNMONITORABLE_AFTER:
        return "heartbeat_delayed"
    return "unmonitorable"


def unknown_host(directory_id):
    return {
        "id": directory_id,
        "pc": directory_id,
        "user": "",
        "last_heartbeat_at": None,
        "last_hook_at": None,
        "health": "data_error",
        "tools": {tool: {"configured": False, "trust": "unknown"} for tool in KNOWN_TOOLS},
        "data_errors": [],
    }


def load_dashboard(root):
    now = datetime.now(timezone.utc)
    result = {"generated_at": iso_time(now), "hosts": [], "sessions": [], "data_errors": 0}
    if root is None or not root.is_dir():
        result["source_error"] = "起点フォルダが未設定か、アクセスできません。設定から有効なフォルダを指定してください。"
        return result
    try:
        all_directories = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        result["source_error"] = "起点フォルダを読み取れません。アクセス権を確認してください。"
        return result
    directories = [path for path in all_directories if path.name.startswith("host-")]
    if not directories and all_directories:
        result["source_error"] = "起点フォルダの構造が正しくありません。agent-dashboard フォルダを指定してください。"
        return result
    if len(directories) > MAX_HOSTS:
        result["source_error"] = f"監視端末数が上限（{MAX_HOSTS}）を超えています。"
        return result

    for directory in directories:
        host = unknown_host(directory.name)
        host_errors = []
        heartbeat = None
        try:
            source = read_json(directory / "host.json")
            host, heartbeat = validate_host(source, directory.name)
            host["last_hook_at"] = None
            host_errors.extend(host.pop("data_errors"))
            host["health"] = health_status(heartbeat, now)
        except DataError as error:
            host_errors.append(str(error))
        hook_path = directory / "hook.json"
        if hook_path.exists():
            try:
                host["last_hook_at"] = iso_time(validate_hook_health(read_json(hook_path), directory.name))
            except DataError as error:
                host_errors.append(str(error))
        try:
            files = sorted(path for path in directory.glob("*.json") if path.name not in {"host.json", "hook.json"})
        except OSError:
            files = []
            host_errors.append("session_folder_unreadable")
        if len(files) > MAX_SESSIONS_PER_HOST:
            host_errors.append("session_limit")
            files = files[:MAX_SESSIONS_PER_HOST]
        seen_sessions = set()
        for path in files:
            try:
                session, updated = validate_session(read_json(path), directory.name)
                identity = (session["tool"], session["session"])
                if identity in seen_sessions:
                    raise DataError("duplicate_session")
                seen_sessions.add(identity)
                if heartbeat is None:
                    host["pc"] = session["pc"]
                    host["user"] = session["user"]
                if session["pc"] != host["pc"] or session["user"] != host["user"]:
                    raise DataError("session_identity_mismatch")
                if session["activity_expires_at"] and parse_time(session["activity_expires_at"]) <= now:
                    session["activity"] = None
                    session["activity_expires_at"] = None
                if session["state"] == "ended" and now - updated > ENDED_VISIBLE:
                    continue
                if session["state"] != "ended" and now - updated > STALE_SESSION_VISIBLE:
                    continue
                session["last_state"] = session["state"]
                if host["health"] != "healthy" or updated > now + FUTURE_CLOCK_TOLERANCE:
                    session["state"] = "stale"
                    session["state_reason"] = "host_health" if host["health"] != "healthy" else "clock_skew"
                elif now - updated > SESSION_STALE_AFTER:
                    session["state"] = "stale"
                    session["state_reason"] = "session_stale"
                result["sessions"].append(session)
            except DataError as error:
                host_errors.append(str(error))
        if host_errors:
            host["data_errors"] = sorted(set(host_errors))
            if heartbeat is None:
                host["health"] = "data_error"
        else:
            host["data_errors"] = []
        result["hosts"].append(host)

    result["hosts"].sort(key=lambda host: host["id"])
    result["data_errors"] = sum(len(host["data_errors"]) for host in result["hosts"])
    return result


def inspect_root(root):
    data = load_dashboard(root)
    try:
        has_host_layout = any(path.is_dir() and path.name.startswith("host-") and (path / "host.json").is_file() for path in root.iterdir())
    except OSError:
        has_host_layout = False
    return {
        "valid": "source_error" not in data,
        "structure_valid": root.name.casefold() == "agent-dashboard" or has_host_layout,
        "hosts": len(data["hosts"]),
        "data_errors": data["data_errors"],
        "source_error": data.get("source_error"),
    }


def resolve_root(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("起点フォルダを入力してください。")
    root = Path(os.path.expandvars(os.path.expanduser(value.strip()))).resolve()
    if not root.is_dir():
        raise ValueError("指定したフォルダが存在しないか、フォルダではありません。")
    return root


def load_settings(path):
    try:
        source = read_json(path)
        root = resolve_root(source["root_dir"])
        names = source.get("host_names", {})
        if not isinstance(names, dict) or any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 64 for key, value in names.items()):
            names = {}
        return root, names
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, DataError):
        return None, {}


def save_settings(path, root, host_names):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps({"root_dir": str(root), "host_names": host_names}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class DashboardState:
    def __init__(self, root, config_path, host_names=None):
        self._lock = threading.Lock()
        self._root = root
        self._host_names = host_names or {}
        self.config_path = config_path

    def snapshot(self):
        with self._lock:
            return self._root, dict(self._host_names)

    def dashboard(self):
        root, host_names = self.snapshot()
        data = load_dashboard(root)
        for host in data["hosts"]:
            host["display_name"] = host_names.get(host["id"], host["pc"])
        return data

    def settings(self):
        root, host_names = self.snapshot()
        default = default_root()
        return {"root_dir": str(root) if root else "", "default_root_dir": str(default) if default else "", "host_names": host_names}

    def preview(self, value):
        return inspect_root(resolve_root(value))

    def update(self, value, host_names=None):
        root = resolve_root(value)
        preview = inspect_root(root)
        if not preview["valid"]:
            raise ValueError(preview["source_error"])
        if not preview["structure_valid"]:
            raise ValueError("agent-dashboard フォルダ、または host-*\\host.json を含むフォルダを指定してください。")
        if host_names is not None:
            if not isinstance(host_names, dict) or any(not isinstance(key, str) or not isinstance(name, str) or not name.strip() or len(name) > 64 for key, name in host_names.items()):
                raise ValueError("PC 表示名が不正です。")
            cleaned_names = {key: name.strip() for key, name in host_names.items()}
        else:
            cleaned_names = self.snapshot()[1]
        with self._lock:
            save_settings(self.config_path, root, cleaned_names)
            self._root = root
            self._host_names = cleaned_names
        return preview

    def pick_folder(self, initial):
        try:
            import tkinter
            from tkinter import filedialog
            window = tkinter.Tk()
            window.withdraw()
            window.attributes("-topmost", True)
            selected = filedialog.askdirectory(initialdir=initial or str(self.snapshot()[0] or default_root() or HERE), title="Agent Dashboard の起点フォルダを選択")
            window.destroy()
        except Exception as error:
            raise ValueError("フォルダ選択を開けませんでした。パスを入力してください。") from error
        return selected


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
            self.send_json(200, self.state.dashboard())
            return
        if path == "/api/settings":
            self.send_json(200, self.state.settings())
            return
        if path == "/":
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_PUT(self):
        if urlparse(self.path).path != "/api/settings":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("設定内容が不正です。")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("設定内容が不正です。")
            preview = self.state.update(payload.get("root_dir"), payload.get("host_names"))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, DataError) as error:
            self.send_json(400, {"error": str(error) or "設定を保存できませんでした。"})
            return
        self.send_json(200, preview)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16_384:
                raise ValueError("設定内容が不正です。")
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("設定内容が不正です。")
            if path == "/api/settings/preview":
                self.send_json(200, self.state.preview(payload.get("root_dir")))
                return
            if path == "/api/settings/pick-folder":
                self.send_json(200, {"root_dir": self.state.pick_folder(payload.get("initial"))})
                return
            self.send_error(404)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, DataError) as error:
            self.send_json(400, {"error": str(error) or "設定を処理できませんでした。"})

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dir", help="起点フォルダ。指定時は保存済み設定より優先します")
    args = parser.parse_args()
    config_path = settings_path()
    saved_root, host_names = load_settings(config_path)
    if args.dir:
        try:
            root = resolve_root(args.dir)
        except ValueError as error:
            parser.error(str(error))
    else:
        root = saved_root or default_root()
    Handler.state = DashboardState(root, config_path, host_names)
    print(f"watching {root or 'no folder configured'}\nhttp://localhost:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
