import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("dashboard_server", Path(__file__).with_name("server.py"))
dashboard_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard_server)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class LoadDashboardTests(unittest.TestCase):
    def test_returns_setup_error_when_root_is_not_available(self):
        result = dashboard_server.load_dashboard(None)

        self.assertEqual([], result["hosts"])
        self.assertIn("起点フォルダ", result["source_error"])

    def test_exposes_host_and_redacts_legacy_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pc = root / "DEV-PC01"
            pc.mkdir()
            now = datetime.now(timezone.utc).isoformat()
            write_json(pc / "host.json", {"pc": "DEV-PC01", "last_seen_at": now, "tools": {"claude": {"configured": True}, "codex": {"configured": False}}})
            write_json(pc / "claude-1.json", {"pc": "DEV-PC01", "tool": "claude", "session_id": "1", "cwd": "C:\\secret\\project", "first_prompt": "password=secret", "last_prompt": "do not expose", "project": "project", "state": "running", "updated_at": now})

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("online", result["hosts"][0]["status"])
            self.assertTrue(result["hosts"][0]["tools"]["claude"])
            self.assertEqual("project", result["sessions"][0]["project"])
            self.assertNotIn("cwd", result["sessions"][0])
            self.assertNotIn("first_prompt", result["sessions"][0])
            self.assertNotIn("last_prompt", result["sessions"][0])

    def test_marks_old_session_stale_and_old_heartbeat_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pc = root / "DEV-PC02"
            pc.mkdir()
            old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            write_json(pc / "host.json", {"pc": "DEV-PC02", "last_seen_at": old, "tools": {}})
            write_json(pc / "codex-1.json", {"pc": "DEV-PC02", "tool": "codex", "session_id": "1", "project": "app", "state": "running", "updated_at": old})

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("offline", result["hosts"][0]["status"])
            self.assertEqual("stale", result["sessions"][0]["state"])

    def test_ignores_invalid_json_without_breaking_the_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pc = root / "BROKEN"
            pc.mkdir()
            (pc / "host.json").write_text("not json", encoding="utf-8")

            result = dashboard_server.load_dashboard(root)

            self.assertEqual([], result["hosts"])
            self.assertEqual(1, result["data_errors"])

    def test_hides_unended_sessions_older_than_a_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pc = root / "OLD-PC"
            pc.mkdir()
            old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            write_json(pc / "codex-1.json", {"pc": "OLD-PC", "tool": "codex", "session_id": "1", "project": "old", "state": "idle", "updated_at": old})

            result = dashboard_server.load_dashboard(root)

            self.assertEqual([], result["sessions"])


class SettingsTests(unittest.TestCase):
    def test_saves_and_loads_existing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            root.mkdir()
            config = Path(directory) / "settings" / "settings.json"

            dashboard_server.save_root(config, root)

            self.assertEqual(root.resolve(), dashboard_server.load_saved_root(config))

    def test_rejects_missing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "存在しない"):
                dashboard_server.resolve_root(str(Path(directory) / "missing"))

    def test_update_changes_root_and_persists_it(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            config = Path(directory) / "settings.json"
            state = dashboard_server.DashboardState(first, config)

            result = state.update_root(str(second))

            self.assertEqual(second.resolve(), result)
            self.assertEqual(second.resolve(), state.root())
            self.assertEqual(second.resolve(), dashboard_server.load_saved_root(config))


class HookStateTransitionTests(unittest.TestCase):
    powershell = shutil.which("powershell") or shutil.which("pwsh")

    def setUp(self):
        if not self.powershell:
            self.skipTest("PowerShell is required for hook tests")
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.environment = os.environ.copy()
        self.environment.update({"OneDrive": str(self.root), "COMPUTERNAME": "TEST-PC", "USERNAME": "dashboard-test"})

    def tearDown(self):
        self.directory.cleanup()

    def send(self, event, tool="claude"):
        subprocess.run(
            [self.powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).parents[1] / "hook" / "dashboard-hook.ps1"), "-Tool", tool],
            input=json.dumps(event), text=True, encoding="utf-8", env=self.environment, check=True, capture_output=True,
        )

    def session(self, tool="claude"):
        path = next((self.root / "agent-dashboard").glob(f"*/{tool}-*.json"))
        return path, json.loads(path.read_text(encoding="utf-8"))

    def event(self, name, **extra):
        return {"session_id": "raw/session-id:must-not-leak", "hook_event_name": name, "cwd": str(self.root / "same-name-project"), **extra}

    def test_tool_success_clears_running_activity_and_hides_raw_session_id(self):
        self.send(self.event("UserPromptSubmit"))
        self.send(self.event("PreToolUse", tool_name="Bash"))
        self.send(self.event("PostToolUse", tool_name="Bash"))

        path, state = self.session()

        self.assertEqual("running", state["state"])
        self.assertIsNone(state["activity"])
        self.assertEqual(3, state["schema_version"])
        self.assertNotIn("session_id", state)
        self.assertNotIn("raw/session-id", path.name)
        self.assertEqual("project-", state["project_id"][:8])

    def test_tool_failure_is_distinct_from_turn_failure(self):
        self.send(self.event("UserPromptSubmit"))
        self.send(self.event("PostToolUseFailure", tool_name="Bash"))
        _, tool_failure = self.session()
        self.send(self.event("StopFailure"))
        _, turn_failure = self.session()

        self.assertEqual(("running", "tool_failed"), (tool_failure["state"], tool_failure["activity"]))
        self.assertIsNotNone(tool_failure["activity_expires_at"])
        self.assertEqual(("error", "turn_failed"), (turn_failure["state"], turn_failure["activity"]))
        self.assertIsNone(turn_failure["activity_expires_at"])

    def test_permission_denied_then_retry_and_stop_follow_state_table(self):
        self.send(self.event("PermissionRequest", tool_name="Bash"), "codex")
        self.send(self.event("PermissionDenied", tool_name="Bash"), "codex")
        _, denied = self.session("codex")
        self.send(self.event("UserPromptSubmit"), "codex")
        self.send(self.event("Stop"), "codex")
        _, stopped = self.session("codex")

        self.assertEqual(("running", "approval_denied"), (denied["state"], denied["activity"]))
        self.assertEqual(("idle", None), (stopped["state"], stopped["activity"]))

    def test_subagent_event_does_not_change_parent_session(self):
        self.send(self.event("UserPromptSubmit"))
        _, before = self.session()
        self.send(self.event("Stop", agent_id="worker-1", agent_type="general-purpose"))
        _, after = self.session()

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
