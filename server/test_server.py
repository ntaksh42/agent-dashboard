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


def now_text(offset=timedelta()):
    return (datetime.now(timezone.utc) + offset).isoformat()


def host_data(host_id, heartbeat=None, tools=None):
    return {
        "schema_version": 3,
        "host_id": host_id,
        "pc": "DEV-PC01",
        "user": "alice",
        "last_heartbeat_at": heartbeat or now_text(),
        "tools": tools or {
            "claude": {"configured": True, "trust": "not_applicable"},
            "codex": {"configured": True, "trust": "unverified"},
        },
    }


def session_data(host_id, session="a1b2c3d4", state="running", updated=None, **changes):
    result = {
        "schema_version": 3,
        "host_id": host_id,
        "pc": "DEV-PC01",
        "user": "alice",
        "tool": "claude",
        "session": session,
        "project": "project",
        "project_id": "project-deadbeef",
        "project_status": "available",
        "branch": "main",
        "branch_status": "branch",
        "activity": None,
        "activity_expires_at": None,
        "state": state,
        "started_at": now_text(timedelta(minutes=-1)),
        "updated_at": updated or now_text(),
    }
    result.update(changes)
    return result


class LoadDashboardTests(unittest.TestCase):
    def make_host(self, root, host_id="host-abc"):
        directory = root / host_id
        directory.mkdir()
        write_json(directory / "host.json", host_data(host_id))
        return directory

    def test_exposes_valid_data_and_redacts_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.make_host(root)
            value = session_data("host-abc", cwd="C:\\secret", session_id="private", first_prompt="secret")
            write_json(host / "claude-a1b2c3d4.json", value)

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("healthy", result["hosts"][0]["health"])
            self.assertEqual("project", result["sessions"][0]["project"])
            self.assertNotIn("cwd", result["sessions"][0])
            self.assertNotIn("session_id", result["sessions"][0])
            self.assertEqual(0, result["data_errors"])

    def test_unmonitorable_host_downgrades_live_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.make_host(root)
            write_json(host / "host.json", host_data("host-abc", now_text(timedelta(minutes=-11))))
            write_json(host / "claude-a1b2c3d4.json", session_data("host-abc"))

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("unmonitorable", result["hosts"][0]["health"])
            self.assertEqual(("stale", "host_health"), (result["sessions"][0]["state"], result["sessions"][0]["state_reason"]))

    def test_schema_error_is_isolated_to_its_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = self.make_host(root, "host-good")
            broken = root / "host-broken"
            broken.mkdir()
            write_json(broken / "host.json", {"schema_version": 2})
            write_json(good / "claude-a1b2c3d4.json", session_data("host-good"))

            result = dashboard_server.load_dashboard(root)

            self.assertEqual(2, len(result["hosts"]))
            self.assertEqual(["schema_version"], result["hosts"][0]["data_errors"])
            self.assertEqual("project", result["sessions"][0]["project"])

    def test_future_clock_and_expired_transient_activity_are_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.make_host(root)
            write_json(host / "host.json", host_data("host-abc", now_text(timedelta(minutes=2))))
            write_json(host / "claude-a1b2c3d4.json", session_data("host-abc", activity="tool_failed", activity_expires_at=now_text(timedelta(seconds=-1))))

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("clock_skew", result["hosts"][0]["health"])
            self.assertEqual("stale", result["sessions"][0]["state"])
            self.assertIsNone(result["sessions"][0]["activity"])

    def test_invalid_tool_contract_preserves_host_and_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.make_host(root)
            write_json(host / "host.json", host_data("host-abc", tools={"claude": "invalid", "codex": {"configured": True, "trust": "unverified"}}))

            result = dashboard_server.load_dashboard(root)

            self.assertEqual("healthy", result["hosts"][0]["health"])
            self.assertIn("tool_contract", result["hosts"][0]["data_errors"])
            self.assertFalse(result["hosts"][0]["tools"]["claude"]["configured"])

    def test_session_identity_mismatch_is_rejected_per_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = self.make_host(root)
            write_json(host / "claude-a1b2c3d4.json", session_data("host-abc", user="other"))

            result = dashboard_server.load_dashboard(root)

            self.assertEqual([], result["sessions"])
            self.assertIn("session_identity_mismatch", result["hosts"][0]["data_errors"])

    def test_returns_setup_error_when_root_is_unavailable(self):
        result = dashboard_server.load_dashboard(None)

        self.assertEqual([], result["hosts"])
        self.assertIn("起点フォルダ", result["source_error"])


class SettingsTests(unittest.TestCase):
    def test_saves_and_loads_existing_root_and_host_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            root.mkdir()
            config = Path(directory) / "settings" / "settings.json"

            dashboard_server.save_settings(config, root, {"host-abc": "開発PC"})

            self.assertEqual((root.resolve(), {"host-abc": "開発PC"}), dashboard_server.load_settings(config))

    def test_update_changes_root_and_persists_it(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            config = Path(directory) / "settings.json"
            state = dashboard_server.DashboardState(first, config)

            result = state.update(str(second), {"host-abc": "Build PC"})

            self.assertTrue(result["valid"])
            self.assertEqual((second.resolve(), {"host-abc": "Build PC"}), dashboard_server.load_settings(config))

    def test_rejects_missing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "存在しない"):
                dashboard_server.resolve_root(str(Path(directory) / "missing"))


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
