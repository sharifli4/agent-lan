# Agent LAN board

A page on this PC that lists which of your own computers are running an agent, which project that agent is in, and which file it last edited.

## What you see

Open `http://127.0.0.1:8787` on this machine (`asus-rog`, `192.168.0.69`). The page is one table. It refreshes every 2 seconds. Columns are Computer, IP, Agent, Project, and File.

One row per running agent. Grok and Cursor on the same PC are two rows with the same IP. When nothing is running anywhere, the page says "No agent is running."

The Project cell shows the last part of the folder path. The File cell shows the file name, plus the word `editing` or `reading`. The full path is in the cell's tooltip. A running agent that has not touched a file yet still gets a row, and the File cell is empty.

## How the pieces fit

One Python program, `agent_lan.py`, in this project. It uses the Python standard library only. Two commands:

- `python agent_lan.py board` runs on this PC. It keeps the slips and serves the page.
- `python agent_lan.py report` runs on every PC you want listed, including this one.

The board listens on `0.0.0.0:8787`, so other computers on the home Wi-Fi can deliver notes to `http://192.168.0.69:8787`. You still open the page on this PC.

Each reporter posts one note every 3 seconds, and only when at least one agent is running. The note is the full list of agents on that computer. The board replaces that computer's rows with the new list. A computer that stops posting drops off 12 seconds after the board received its last note. The board uses its own clock for that, so a laptop with a wrong clock cannot leave a ghost row.

The note looks like this:

```json
{
  "hostname": "asus-rog",
  "ip": "192.168.0.69",
  "agents": [
    {
      "name": "Cursor agent",
      "project": "/home/kenan/repositories/dosty/walks/walks-mobile-api-nestjs",
      "file": "/home/kenan/repositories/dosty/walks/walks-mobile-api-nestjs/src/user.service.ts",
      "action": "editing"
    }
  ]
}
```

`action` is `editing`, `reading`, or an empty string when `file` is empty. The note never includes a command line, a chat, or file contents.

`GET /api/agents` returns the current rows as JSON, including `seen_at` (the board's receipt time, `time.time()` as a float). Rows whose age is 12 seconds or more are left out. `GET /` returns the HTML page. If a refresh fails, the page keeps the table it already showed. Both reads are open to the home LAN. `POST /api/report` requires the shared word.

## Setup

The first time `board` runs, if `~/.config/agent-lan/config.json` is missing, it creates it:

```json
{
  "hub": "http://192.168.0.69:8787",
  "token": "<random>"
}
```

The token is `secrets.token_urlsafe(16)`. The board prints the path and tells you to copy that file to each other PC. `report` exits with that same instruction when the file is missing.

Copy the file as-is. The `hub` value is this PC's address, and every reporter posts there. The token is the shared word. The board compares it with `hmac.compare_digest` against the `X-Agent-Lan-Token` header. A missing or wrong word gets HTTP 401 and the body is ignored.

The reporter's IP is the local address of a UDP socket connected toward the hub, so a Docker bridge address is not the one that shows up. The computer name is `socket.gethostname()`. Rows are stored by hostname. Two PCs with the same hostname overwrite each other.

If port 8787 is already taken, the board prints `port 8787 is in use` and exits.

## Which programs count

The reporter reads `/proc`. It may look at a command line long enough to match the rules below. It does not write that command line into the note, the log, or an error message. It skips its own pid.

| Row label | A process counts when |
| --- | --- |
| Grok | `comm` is `grok`, or the program basename is `grok`. Skip `grok-bot` and `systemd-inhibit`. |
| Grok Bot | The command line contains `grok-bot` and does not contain `--type=`. Helper processes (zygote, gpu, renderer, crashpad) do not get a row. |
| Cursor agent | The command line contains both `cursor-agent` and `--worker-dir`. |
| Claude | `comm` is `claude`, or the program basename is `claude`. |
| Codex | `comm` is `codex`, or the program basename is `codex`. |
| MCP | The command line contains `mcp-server`, `mcp-proxy`, or `-mcp-`. All of those processes fold into one row. Project and file stay empty. |

Grok, Cursor agent, Claude, and Codex each get one row per matching pid. MCP gets a single row per computer.

## Project and file

The file cell prefers the newest edit. A later read does not replace it. When the notes contain no edit, the newest read is shown with `reading`. When the notes name no file, the file and action are empty.

**Grok.** `~/.grok/active_sessions.json` is a list of `{session_id, pid, cwd, opened_at}`. The entry whose `pid` matches the process supplies the project (`cwd`) and the session. The notes file is:

`~/.grok/sessions/<quoted cwd>/<session_id>/chat_history.jsonl`

`<quoted cwd>` is `urllib.parse.quote(cwd, safe="")`, so `/home/kenan` becomes `%2Fhome%2Fkenan`.

Walk the JSONL in order. An object counts when its `name` is `search_replace` or `write` (an edit) or `read_file` (a read). The path is `file_path`, `target_file`, or `path` inside `arguments`. `arguments` may be a JSON string or an object.

**Cursor agent.** The project is the `--worker-dir` value. A leading `~` is expanded, then the path is made absolute against that process's cwd. The notes folder is `~/.cursor/projects/<slug>/agent-transcripts/`. The slug is the absolute project path with the leading slash removed and every remaining slash turned into a hyphen. `/home/kenan/repositories/dosty/walks/walks-mobile-api-nestjs` maps to `home-kenan-repositories-dosty-walks-walks-mobile-api-nestjs`.

Use the `*.jsonl` transcript in that folder with the newest mtime. Walk it in order. An object counts when `type` is `tool_use` and `name` is `StrReplace` or `Write` (an edit) or `Read` (a read). The path is `input.path`.

**Grok Bot, Claude, and Codex.** The project is that process's cwd. The file and action are empty. Grok Bot's desktop window does not keep a file note we can read. The MCP row has an empty project as well.

## When something goes wrong

The reporter remembers whether the board is currently answering.

- The first failed connection prints `board is not answering`, then it waits for the next 3-second tick.
- Further failures print nothing.
- The next success prints `board is back`.
- The first HTTP 401 prints `board refused the word`. It does not repeat until a post has succeeded and a later post is refused again.
- A note that is not JSON, or that has no `hostname`, gets HTTP 400 and does not change the board.

## Checks

Tests live in `test_agent_lan.py` and use the standard-library `unittest` runner. They build fake process records and fake notes. They do not read a live command line.

- A fake Grok process, a Cursor worker with `--worker-dir`, a `grok-bot` helper that has `--type=`, and an MCP server produce Grok, Cursor agent, and one MCP row. The helper produces nothing.
- A notes file with an edit, then a later read, yields the edit and `editing`.
- A notes file with only a read yields that file and `reading`.
- A notes file with no path yields an empty file.
- A Cursor project path becomes the hyphenated slug above.
- A report with `seen_at` 12 seconds before the test clock is absent from `GET /api/agents`. A report 11 seconds before the clock is still present. The test supplies the clock.
- A post without the token does not change the board.

Before calling the work done, run the tests, then start `board` and `report` on this PC and open the page. With Grok or Cursor actually running, this PC's name, IP, agent, project, and file show up.

## Out of scope

A systemd service, Windows, macOS, reading other computers' traffic, showing file contents or chat text, a file path for Grok Bot, and a layout built for a phone.
