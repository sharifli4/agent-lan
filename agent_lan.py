from __future__ import annotations

import json
import os
import urllib.parse
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
