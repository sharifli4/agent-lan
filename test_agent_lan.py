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

    def test_grok_bot_helper_is_not_mcp(self):
        processes = [
            agent_lan.Process(
                2,
                "grok-bot",
                "/opt/Grok Bot/grok-bot --type=renderer --sand-mcp-app",
                "/home/kenan",
            )
        ]
        self.assertEqual(agent_lan.match_agents(processes, 0, "/home/kenan"), [])

    def test_worker_dir_keeps_spaces_from_proc(self):
        with tempfile.TemporaryDirectory() as root:
            entry = os.path.join(root, "4")
            os.makedirs(entry)
            with open(os.path.join(entry, "comm"), "w") as handle:
                handle.write("MainThread\n")
            with open(os.path.join(entry, "cmdline"), "wb") as handle:
                handle.write(b"cursor-agent\x00--worker-dir\x00/home/kenan/My Projects/app\x00")
            os.symlink("/tmp", os.path.join(entry, "cwd"))
            processes = agent_lan.read_processes(root)
        found = agent_lan.match_agents(processes, 0, "/home/kenan")
        self.assertEqual(found[0].name, "Cursor agent")
        self.assertEqual(found[0].project, "/home/kenan/My Projects/app")
        self.assertNotIn("api-key", repr(found))


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


class FakeResponse:
    def __init__(self, status=200):
        self.status = status
    def read(self):
        return b""
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False


class TestReporter(unittest.TestCase):
    def test_proc_and_note_hide_the_command_line(self):
        with tempfile.TemporaryDirectory() as root:
            entry = os.path.join(root, "9")
            os.makedirs(entry)
            with open(os.path.join(entry, "comm"), "w") as handle:
                handle.write("grok\n")
            with open(os.path.join(entry, "cmdline"), "wb") as handle:
                handle.write(b"grok\x00--api-key\x00crsr_secret\x00")
            os.symlink("/home/kenan", os.path.join(entry, "cwd"))
            processes = agent_lan.read_processes(root)
        self.assertEqual(processes[0].comm, "grok")
        self.assertEqual(processes[0].cwd, "/home/kenan")
        matched = [agent_lan.Matched("Grok", 9, "/home/kenan", "/home/kenan/a.py", "editing")]
        note = agent_lan.note_for("asus-rog", "192.168.0.69", matched)
        encoded = json.dumps(note)
        self.assertNotIn("crsr_secret", encoded)
        self.assertNotIn("cmdline", encoded)
        self.assertIsNone(agent_lan.note_for("asus-rog", "192.168.0.69", []))

    def test_local_ip_uses_the_socket(self):
        class Sock:
            def getsockname(self):
                return ("192.168.0.69", 40000)
            def close(self):
                pass
        self.assertEqual(agent_lan.local_ip_toward("192.168.0.254", 9, lambda host, port: Sock()), "192.168.0.69")

        def boom(host, port):
            raise OSError("network down")
        self.assertEqual(agent_lan.local_ip_toward("192.168.0.254", 9, boom), "127.0.0.1")

    def test_retry_lines(self):
        speak = agent_lan.Speak()
        self.assertEqual(speak.connection_failed(), "board is not answering")
        self.assertIsNone(speak.connection_failed())
        self.assertEqual(speak.ok(), "board is back")
        self.assertIsNone(speak.ok())
        self.assertEqual(speak.refused(), "board refused the word")
        self.assertIsNone(speak.refused())
        self.assertIsNone(speak.ok())
        self.assertEqual(speak.refused(), "board refused the word")

    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            made = agent_lan.ensure_config(path, "http://192.168.0.69:8787", "secret")
            again = agent_lan.ensure_config(path, "http://192.168.0.69:8787", "other")
            self.assertEqual(made["token"], "secret")
            self.assertEqual(again["token"], "secret")
            self.assertIn(str(path), agent_lan.copy_instruction(path))
            missing = pathlib.Path(directory) / "missing.json"
            with self.assertRaises(SystemExit) as caught:
                agent_lan.require_config(missing)
            self.assertIn(str(missing), str(caught.exception))

    def test_report_once(self):
        matched = [agent_lan.Matched("Grok", 9, "/home/kenan", "", "")]
        config = {"hub": "http://192.168.0.69:8787", "token": "secret"}
        seen = {}
        def urlopen(req, timeout):
            seen["url"] = req.full_url
            seen["token"] = req.get_header("X-agent-lan-token")
            return FakeResponse()
        speak = agent_lan.Speak()
        speak.connection_failed()
        line = agent_lan.report_once(config, matched, "asus-rog", "192.168.0.69", speak, urlopen)
        self.assertEqual(line, "board is back")
        self.assertEqual(seen["url"], "http://192.168.0.69:8787/api/report")
        self.assertEqual(seen["token"], "secret")
        self.assertIsNone(agent_lan.report_once(config, [], "asus-rog", "192.168.0.69", speak, urlopen))
        self.assertEqual(agent_lan.REPORT_INTERVAL, 3)


class TestWire(unittest.TestCase):
    def test_dispatch_get_and_post(self):
        board = agent_lan.Board("secret", now=lambda: 10.0)
        status, body, content_type = agent_lan.dispatch("GET", "/api/agents", {}, b"", board)
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(body)["agents"], [])
        status, body, content_type = agent_lan.dispatch("GET", "/", {}, b"", board)
        self.assertIn(b"No agent is running.", body)
        self.assertIn("text/html", content_type)
        note = json.dumps({
            "hostname": "asus-rog",
            "ip": "192.168.0.69",
            "agents": [{"name": "Grok", "project": "/home/kenan", "file": "", "action": ""}],
        }).encode()
        status, _body, _kind = agent_lan.dispatch(
            "POST", "/api/report", {"X-Agent-Lan-Token": "secret"}, note, board
        )
        self.assertEqual(status, 200)
        self.assertEqual(agent_lan.dispatch("GET", "/api/agents", {}, b"", board)[1].count(b"Grok"), 1)
        refused, _body, _kind = agent_lan.dispatch("POST", "/api/report", {}, note, board)
        self.assertEqual(refused, 401)
        self.assertEqual(agent_lan.BIND, ("0.0.0.0", 8787))
        self.assertEqual(agent_lan.HUB, "http://192.168.0.69:8787")

    def test_prepare_board_creates_config_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            first = agent_lan.prepare_board(path)
            second = agent_lan.prepare_board(path)
            self.assertEqual(first["hub"], "http://192.168.0.69:8787")
            self.assertEqual(second["token"], first["token"])
            self.assertGreaterEqual(len(first["token"]), 16)
            code = agent_lan.main(["report", "--config", str(pathlib.Path(directory) / "missing.json")])
            self.assertEqual(code, 1)

    def test_collect_reads_fake_proc(self):
        with tempfile.TemporaryDirectory() as root:
            entry = os.path.join(root, "9")
            os.makedirs(entry)
            with open(os.path.join(entry, "comm"), "w") as handle:
                handle.write("grok\n")
            with open(os.path.join(entry, "cmdline"), "wb") as handle:
                handle.write(b"grok\x00")
            os.symlink("/home/kenan", os.path.join(entry, "cwd"))
            found = agent_lan.collect(root, 0, "/home/kenan", "/missing-grok", "/missing-cursor")
        self.assertEqual(found[0].name, "Grok")
        self.assertEqual(found[0].project, "/home/kenan")


if __name__ == "__main__":
    unittest.main()
