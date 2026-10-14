from __future__ import annotations

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
