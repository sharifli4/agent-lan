import json
import os
import pathlib
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


class TestAttach(unittest.TestCase):
    def test_grok_session_file_wins(self):
        text = json.dumps({"name": "read_file", "arguments": {"target_file": "/home/kenan/only.py"}})
        def read_text(path):
            self.assertEqual(
                path,
                agent_lan.grok_history_path("/home/kenan/.grok", "/work/session", "abc"),
            )
            return text
        matched = [agent_lan.Matched("Grok", 7, "/home/kenan", "", "")]
        sessions = [{"pid": 7, "cwd": "/work/session", "session_id": "abc"}]
        out = agent_lan.attach_files(
            matched, sessions, read_text, "/home/kenan/.grok", "/home/kenan/.cursor/projects"
        )
        self.assertEqual(out[0].project, "/work/session")
        self.assertEqual(out[0].file, "/home/kenan/only.py")
        self.assertEqual(out[0].action, "reading")

    def test_cursor_uses_newest_transcript(self):
        with tempfile.TemporaryDirectory() as projects:
            slug = "home-kenan-repositories-app"
            folder = os.path.join(projects, slug, "agent-transcripts")
            os.makedirs(folder)
            path = os.path.join(folder, "one.jsonl")
            with open(path, "w") as handle:
                handle.write(json.dumps({
                    "type": "tool_use",
                    "name": "StrReplace",
                    "input": {"path": "/home/kenan/repositories/app/src/user.ts"},
                }))
            matched = [agent_lan.Matched("Cursor agent", 4, "/home/kenan/repositories/app", "", "")]

            def read_text(path):
                with open(path, encoding="utf-8") as handle:
                    return handle.read()

            out = agent_lan.attach_files(matched, [], read_text, "/home/kenan/.grok", projects)
            self.assertEqual(out[0].file, "/home/kenan/repositories/app/src/user.ts")
            self.assertEqual(out[0].action, "editing")

    def test_other_agents_stay_blank(self):
        matched = [
            agent_lan.Matched("Grok Bot", 3, "/home/kenan", "", ""),
            agent_lan.Matched("MCP", 0, "", "", ""),
        ]
        out = agent_lan.attach_files(matched, [], lambda _path: None, "/home/kenan/.grok", "/missing")
        self.assertEqual(out, matched)


class TestBoard(unittest.TestCase):
    def note(self, agents):
        return json.dumps({"hostname": "asus-rog", "ip": "192.168.0.69", "agents": agents}).encode()

    def test_replace_expiry_and_token(self):
        clock = {"now": 100.0}
        board = agent_lan.Board("secret", now=lambda: clock["now"])
        first = self.note([
            {"name": "Grok", "project": "/home/kenan", "file": "/home/kenan/a.py", "action": "editing"},
            {"name": "Cursor agent", "project": "/repo", "file": "", "action": ""},
        ])
        self.assertEqual(board.post(first, "secret")[0], 200)
        self.assertEqual(len(board.agents()), 2)
        second = self.note([
            {"name": "Grok", "project": "/home/kenan", "file": "/home/kenan/c.py", "action": "editing"}
        ])
        board.post(second, "secret")
        rows = board.agents()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "/home/kenan/c.py")
        self.assertEqual(rows[0]["hostname"], "asus-rog")
        self.assertEqual(rows[0]["ip"], "192.168.0.69")
        self.assertEqual(rows[0]["seen_at"], 100.0)
        self.assertEqual(board.post(second, None)[0], 401)
        self.assertEqual(board.post(second, "wrong")[0], 401)
        self.assertEqual(len(board.agents()), 1)
        self.assertEqual(board.post(b"{", "secret")[0], 400)
        self.assertEqual(board.post(b"{}", "secret")[0], 400)
        self.assertEqual(len(board.agents()), 1)
        clock["now"] = 111.0
        self.assertEqual(len(board.agents()), 1)
        clock["now"] = 112.0
        self.assertEqual(board.agents(), [])
        self.assertEqual(agent_lan.STALE_SECONDS, 12)


class TestPage(unittest.TestCase):
    def test_table_and_safe_refresh(self):
        html = agent_lan.render_page()
        self.assertIn("No agent is running.", html)
        for label in ("Computer", "IP", "Agent", "Project", "File"):
            self.assertIn(label, html)
        self.assertIn("setInterval(refresh, 2000)", html)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("catch (e) {}", html)


if __name__ == "__main__":
    unittest.main()
