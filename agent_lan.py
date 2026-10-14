from __future__ import annotations

import argparse
import errno
import hmac
import json
import os
import pathlib
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass


@dataclass(frozen=True)
class Process:
    pid: int
    comm: str
    cmdline: str
    cwd: str


@dataclass(frozen=True)
class Matched:
    name: str
    pid: int
    project: str
    file: str
    action: str


def resolve_dir(raw: str, cwd: str, home: str) -> str:
    if raw == "~" or raw.startswith("~/"):
        raw = home + raw[1:]
    if raw.startswith("/"):
        return raw
    if not raw:
        return cwd
    return cwd.rstrip("/") + "/" + raw


def _basename(cmdline: str) -> str:
    parts = cmdline.split()
    if not parts:
        return ""
    return parts[0].rsplit("/", 1)[-1]


def _flag(cmdline: str, flag: str) -> str:
    parts = cmdline.split()
    if flag not in parts:
        return ""
    index = parts.index(flag)
    if index + 1 >= len(parts):
        return ""
    return parts[index + 1]


def match_agents(processes: list[Process], own_pid: int, home: str) -> list[Matched]:
    found: list[Matched] = []
    saw_mcp = False
    for proc in processes:
        if proc.pid == own_pid:
            continue
        base = _basename(proc.cmdline)
        if proc.comm == "systemd-inhibit" or base == "systemd-inhibit":
            continue
        if (proc.comm == "grok" or base == "grok") and "grok-bot" not in proc.cmdline:
            found.append(Matched("Grok", proc.pid, proc.cwd, "", ""))
            continue
        if "grok-bot" in proc.cmdline and "--type=" not in proc.cmdline:
            found.append(Matched("Grok Bot", proc.pid, proc.cwd, "", ""))
            continue
        if "cursor-agent" in proc.cmdline and "--worker-dir" in proc.cmdline:
            project = resolve_dir(_flag(proc.cmdline, "--worker-dir"), proc.cwd, home)
            found.append(Matched("Cursor agent", proc.pid, project, "", ""))
            continue
        if proc.comm == "claude" or base == "claude":
            found.append(Matched("Claude", proc.pid, proc.cwd, "", ""))
            continue
        if proc.comm == "codex" or base == "codex":
            found.append(Matched("Codex", proc.pid, proc.cwd, "", ""))
            continue
        if "mcp-server" in proc.cmdline or "mcp-proxy" in proc.cmdline or "-mcp-" in proc.cmdline:
            saw_mcp = True
    if saw_mcp:
        found.append(Matched("MCP", 0, "", "", ""))
    return found


def choose_file(events: list[tuple[str, str]]) -> tuple[str, str]:
    edited = ""
    read = ""
    for kind, path in events:
        if not path:
            continue
        if kind == "edit":
            edited = path
        elif kind == "read":
            read = path
    if edited:
        return edited, "editing"
    if read:
        return read, "reading"
    return "", ""


def _walk(obj, visit) -> None:
    if isinstance(obj, dict):
        visit(obj)
        for value in obj.values():
            _walk(value, visit)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, visit)


def _events_from_jsonl(text: str, visit) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []

    def collect(obj: dict) -> None:
        found = visit(obj)
        if found:
            events.append(found)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        _walk(parsed, collect)
    return events


def grok_events(text: str) -> list[tuple[str, str]]:
    edits = {"search_replace", "write"}
    reads = {"read_file"}

    def visit(obj: dict):
        name = obj.get("name")
        if name not in edits and name not in reads:
            return None
        args = obj.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None
        if not isinstance(args, dict):
            return None
        for key in ("file_path", "target_file", "path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                kind = "edit" if name in edits else "read"
                return kind, value
        return None

    return _events_from_jsonl(text, visit)


def cursor_events(text: str) -> list[tuple[str, str]]:
    def visit(obj: dict):
        if obj.get("type") != "tool_use":
            return None
        name = obj.get("name")
        if name not in {"StrReplace", "Write", "Read"}:
            return None
        payload = obj.get("input")
        if not isinstance(payload, dict):
            return None
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return None
        kind = "read" if name == "Read" else "edit"
        return kind, path

    return _events_from_jsonl(text, visit)


def cursor_slug(abs_path: str) -> str:
    return abs_path.lstrip("/").replace("/", "-")


def grok_history_path(grok_home: str, cwd: str, session_id: str) -> str:
    quoted = urllib.parse.quote(cwd, safe="")
    return grok_home.rstrip("/") + "/sessions/" + quoted + "/" + session_id + "/chat_history.jsonl"


def newest_jsonl(directory: str) -> str | None:
    if not os.path.isdir(directory):
        return None
    found: list[str] = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if name.endswith(".jsonl"):
                found.append(os.path.join(root, name))
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def attach_files(matched, sessions, read_text, grok_home: str, cursor_projects: str):
    by_pid = {item["pid"]: item for item in sessions}
    attached = []
    for item in matched:
        if item.name == "Grok":
            session = by_pid.get(item.pid)
            project = item.project
            file_path = ""
            action = ""
            if session:
                project = session.get("cwd") or project
                notes = grok_history_path(grok_home, project, session["session_id"])
                text = read_text(notes)
                if text:
                    file_path, action = choose_file(grok_events(text))
            attached.append(Matched(item.name, item.pid, project, file_path, action))
            continue
        if item.name == "Cursor agent":
            folder = (
                cursor_projects.rstrip("/")
                + "/"
                + cursor_slug(item.project)
                + "/agent-transcripts"
            )
            notes = newest_jsonl(folder)
            file_path = ""
            action = ""
            if notes:
                text = read_text(notes)
                if text:
                    file_path, action = choose_file(cursor_events(text))
            attached.append(Matched(item.name, item.pid, item.project, file_path, action))
            continue
        attached.append(item)
    return attached


STALE_SECONDS = 12


class Board:
    def __init__(self, token: str, now=None):
        self.token = token
        self.now = now or time.time
        self._hosts: dict[str, tuple[float, dict]] = {}

    def post(self, body: bytes, token_header: str | None) -> tuple[int, bytes]:
        if token_header is None or not hmac.compare_digest(token_header, self.token):
            return 401, b""
        try:
            data = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, b""
        if not isinstance(data, dict) or not data.get("hostname") or not isinstance(data.get("agents"), list):
            return 400, b""
        self._hosts[data["hostname"]] = (float(self.now()), data)
        return 200, b""

    def agents(self) -> list[dict]:
        now = float(self.now())
        rows = []
        for hostname, (seen, note) in self._hosts.items():
            if now - seen >= STALE_SECONDS:
                continue
            for agent in note["agents"]:
                rows.append({
                    "hostname": hostname,
                    "ip": note.get("ip", ""),
                    "name": agent.get("name", ""),
                    "project": agent.get("project", ""),
                    "file": agent.get("file", ""),
                    "action": agent.get("action", ""),
                    "seen_at": seen,
                })
        return rows


def render_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent LAN</title>
<style>
body { font-family: sans-serif; margin: 2rem; }
table { border-collapse: collapse; }
th, td { text-align: left; padding: 0.4rem 0.8rem; border-bottom: 1px solid #ccc; }
</style>
</head>
<body>
<h1>Agent LAN</h1>
<p id="empty">No agent is running.</p>
<table>
<thead><tr><th>Computer</th><th>IP</th><th>Agent</th><th>Project</th><th>File</th></tr></thead>
<tbody></tbody>
</table>
<script>
function base(path) {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}
function draw(agents) {
  const body = document.querySelector("tbody");
  const empty = document.querySelector("#empty");
  body.replaceChildren();
  if (!agents.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  for (const agent of agents) {
    const tr = document.createElement("tr");
    const file = base(agent.file || "");
    const cells = [
      agent.hostname || "",
      agent.ip || "",
      agent.name || "",
      base(agent.project || ""),
      file && agent.action ? file + " " + agent.action : file
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 3 && agent.project) td.title = agent.project;
      if (index === 4 && agent.file) td.title = agent.file;
      tr.appendChild(td);
    });
    body.appendChild(tr);
  }
}
async function refresh() {
  try {
    const res = await fetch("/api/agents");
    const data = await res.json();
    draw(data.agents || []);
  } catch (e) {}
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


REPORT_INTERVAL = 3


def read_processes(proc_root: str) -> list[Process]:
    found = []
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        entry = os.path.join(proc_root, name)
        try:
            with open(os.path.join(entry, "comm"), "r", encoding="utf-8", errors="replace") as handle:
                comm = handle.read().strip()
            with open(os.path.join(entry, "cmdline"), "rb") as handle:
                cmdline = handle.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            cwd = os.readlink(os.path.join(entry, "cwd"))
        except OSError:
            continue
        found.append(Process(int(name), comm, cmdline, cwd))
    return found


def note_for(hostname: str, ip: str, matched: list[Matched]):
    if not matched:
        return None
    return {
        "hostname": hostname,
        "ip": ip,
        "agents": [
            {"name": item.name, "project": item.project, "file": item.file, "action": item.action}
            for item in matched
        ],
    }


def local_ip_toward(host: str, port: int, opener) -> str:
    try:
        sock = opener(host, port)
    except OSError:
        return "127.0.0.1"
    try:
        return sock.getsockname()[0]
    finally:
        sock.close()


class Speak:
    def __init__(self):
        self._failing = False
        self._refused = False

    def connection_failed(self):
        if self._failing:
            return None
        self._failing = True
        return "board is not answering"

    def refused(self):
        if self._refused:
            return None
        self._refused = True
        return "board refused the word"

    def ok(self):
        line = "board is back" if self._failing else None
        self._failing = False
        self._refused = False
        return line


def copy_instruction(path) -> str:
    return f"Copy {path} to each other PC."


def ensure_config(path, hub: str, token: str) -> dict:
    path = pathlib.Path(path)
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"hub": hub, "token": token}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def require_config(path) -> dict:
    path = pathlib.Path(path)
    if not path.exists():
        raise SystemExit(copy_instruction(path))
    return json.loads(path.read_text())


def report_once(config, matched, hostname: str, ip: str, speak: Speak, urlopen):
    note = note_for(hostname, ip, matched)
    if note is None:
        return None
    request = urllib.request.Request(
        config["hub"].rstrip("/") + "/api/report",
        data=json.dumps(note).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Lan-Token": config["token"]},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            response.read()
        return speak.ok()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return speak.refused()
        return speak.connection_failed()
    except OSError:
        return speak.connection_failed()


HUB = "http://192.168.0.69:8787"
BIND = ("0.0.0.0", 8787)
CONFIG_PATH = pathlib.Path.home() / ".config" / "agent-lan" / "config.json"


def dispatch(method: str, path: str, headers: dict, body: bytes, board: Board):
    route = path.split("?", 1)[0]
    if method == "GET" and route == "/api/agents":
        payload = json.dumps({"agents": board.agents()}).encode()
        return 200, payload, "application/json"
    if method == "GET" and route == "/":
        return 200, render_page().encode(), "text/html; charset=utf-8"
    if method == "POST" and route == "/api/report":
        status, payload = board.post(body, headers.get("X-Agent-Lan-Token"))
        return status, payload, "application/json"
    return 404, b"", "text/plain"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send("GET", b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        self._send("POST", self.rfile.read(length))

    def _send(self, method: str, body: bytes):
        status, payload, content_type = dispatch(method, self.path, self.headers, body, self.server.board)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def prepare_board(path) -> dict:
    path = pathlib.Path(path)
    created = not path.exists()
    config = ensure_config(path, HUB, secrets.token_urlsafe(16))
    if created:
        print(copy_instruction(path))
    return config


def load_sessions(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and "pid" in item and "session_id" in item]


def collect(proc_root: str, own_pid: int, home: str, grok_home: str, cursor_projects: str):
    processes = read_processes(proc_root)
    matched = match_agents(processes, own_pid, home)
    sessions = load_sessions(os.path.join(grok_home, "active_sessions.json"))

    def read_text(path: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    return attach_files(matched, sessions, read_text, grok_home, cursor_projects)


def udp_opener(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((host, port))
    return sock


def run_board(config_path, port: int = 8787) -> int:
    prepare_board(config_path)
    config = require_config(config_path)
    board = Board(config["token"])
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print("port 8787 is in use")
            return 1
        raise
    server.board = board
    print("board listening on http://127.0.0.1:8787")
    server.serve_forever()
    return 0


def run_report(config_path, once: bool = False) -> int:
    try:
        config = require_config(config_path)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code)
        return 1
    home = str(pathlib.Path.home())
    hub = urllib.parse.urlparse(config["hub"])
    speak = Speak()
    while True:
        matched = collect(
            "/proc",
            os.getpid(),
            home,
            os.path.join(home, ".grok"),
            os.path.join(home, ".cursor", "projects"),
        )
        ip = local_ip_toward(hub.hostname or "127.0.0.1", hub.port or 80, udp_opener)
        line = report_once(config, matched, socket.gethostname(), ip, speak, urllib.request.urlopen)
        if line:
            print(line)
        if once:
            return 0
        time.sleep(REPORT_INTERVAL)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent_lan")
    parser.add_argument("command", choices=("board", "report"))
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "board":
        return run_board(args.config)
    return run_report(args.config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
