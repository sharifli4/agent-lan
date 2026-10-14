import json
import os
import tempfile
import unittest

import agent_lan


class TestMatch(unittest.TestCase):
    def test_labels_and_skips(self):
        processes = [
            agent_lan.Process(10, "grok", "grok", "/home/kenan"),
            agent_lan.Process(2, "grok-bot", "/opt/Grok Bot/grok-bot --type=zygote", "/home/kenan"),
            agent_lan.Process(3, "grok-bot", "/opt/Grok Bot/grok-bot --ozone-platform=wayland", "/home/kenan"),
            agent_lan.Process(
                4,
                "MainThread",
                "cursor-agent worker start --worker-dir /home/kenan/repositories/app --api-key crsr_secret",
                "/home/kenan",
            ),
            agent_lan.Process(5, "systemd-inhibit", "systemd-inhibit --who=grok sleep infinity", "/home/kenan"),
            agent_lan.Process(6, "python", "python awslabs.aws-iac-mcp-server", "/home/kenan"),
            agent_lan.Process(7, "python", "python mcp-proxy-for-aws", "/home/kenan"),
            agent_lan.Process(8, "claude", "claude", "/work/claude"),
            agent_lan.Process(9, "codex", "codex", "/work/codex"),
            agent_lan.Process(99, "grok", "grok", "/skip"),
        ]
        found = agent_lan.match_agents(processes, own_pid=99, home="/home/kenan")
        names = [item.name for item in found]
        self.assertEqual(names, ["Grok", "Grok Bot", "Cursor agent", "Claude", "Codex", "MCP"])
        self.assertEqual(found[3].project, "/work/claude")
        self.assertEqual(found[4].project, "/work/codex")
        cursor = found[2]
        self.assertEqual(cursor.project, "/home/kenan/repositories/app")
        self.assertNotIn("crsr_secret", repr(found))
        self.assertEqual(found[1].project, "/home/kenan")
        self.assertEqual(found[1].file, "")
        self.assertEqual(found[-1].project, "")
        self.assertEqual(found[-1].file, "")
        self.assertEqual(len([item for item in found if item.name == "MCP"]), 1)

    def test_tilde_worker_dir(self):
        processes = [
            agent_lan.Process(
                4,
                "MainThread",
                "cursor-agent --worker-dir ~/repositories/dosty/walks/walks-mobile-api-nestjs",
                "/tmp",
            )
        ]
        found = agent_lan.match_agents(processes, own_pid=0, home="/home/kenan")
        self.assertEqual(
            found[0].project,
            "/home/kenan/repositories/dosty/walks/walks-mobile-api-nestjs",
        )


class TestNotes(unittest.TestCase):
    def test_edit_beats_later_read(self):
        events = [("edit", "/home/kenan/a.py"), ("read", "/home/kenan/b.py")]
        self.assertEqual(agent_lan.choose_file(events), ("/home/kenan/a.py", "editing"))

    def test_read_only(self):
        self.assertEqual(
            agent_lan.choose_file([("read", "/home/kenan/b.py")]),
            ("/home/kenan/b.py", "reading"),
        )

    def test_no_path(self):
        self.assertEqual(agent_lan.choose_file([]), ("", ""))

    def test_grok_object_and_json_string(self):
        text = "\n".join([
            json.dumps({"name": "write", "arguments": {"file_path": "/home/kenan/a.py"}}),
            json.dumps({"name": "read_file", "arguments": json.dumps({"target_file": "/home/kenan/b.py"})}),
            json.dumps({"name": "search_replace", "arguments": {"path": "/home/kenan/c.py"}}),
        ])
        self.assertEqual(
            agent_lan.choose_file(agent_lan.grok_events(text)),
            ("/home/kenan/c.py", "editing"),
        )

    def test_cursor_tools_and_slug(self):
        text = "\n".join([
            json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write", "input": {"path": "/home/kenan/a.ts"}}]}}),
            json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {"path": "/home/kenan/b.ts"}}]}}),
        ])
        self.assertEqual(
            agent_lan.choose_file(agent_lan.cursor_events(text)),
            ("/home/kenan/a.ts", "editing"),
        )
        project = agent_lan.resolve_dir(
            "~/repositories/dosty/walks/walks-mobile-api-nestjs",
            "/tmp",
            "/home/kenan",
        )
        self.assertEqual(
            agent_lan.cursor_slug(project),
            "home-kenan-repositories-dosty-walks-walks-mobile-api-nestjs",
        )

    def test_grok_history_path(self):
        self.assertEqual(
            agent_lan.grok_history_path("/home/kenan/.grok", "/home/kenan", "abc"),
            "/home/kenan/.grok/sessions/%2Fhome%2Fkenan/abc/chat_history.jsonl",
        )

    def test_newest_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            older = os.path.join(directory, "old.jsonl")
            nested = os.path.join(directory, "sub")
            os.makedirs(nested)
            newer = os.path.join(nested, "new.jsonl")
            open(older, "w").close()
            open(newer, "w").close()
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            self.assertEqual(agent_lan.newest_jsonl(directory), newer)
        self.assertIsNone(agent_lan.newest_jsonl("/missing/agent-notes"))


if __name__ == "__main__":
    unittest.main()
