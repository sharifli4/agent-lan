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


if __name__ == "__main__":
    unittest.main()
