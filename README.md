# Agent LAN

A page that lists which of your own computers are running an AI agent, which project it is in, and which file it last edited.

Open `http://127.0.0.1:8787` on the computer that runs the board. The table refreshes every 2 seconds. One row per agent. When nothing is running, the page says "No agent is running."

The project cell is the folder name. The file cell is the file name plus `editing` or `reading`. Hover a cell to see the full path.

## What you need

Python 3.11 or newer. Linux. No packages to install.

The board and the other computers must be on the same home network.

## Start the board

On the computer where you want to look at the page:

```bash
python agent_lan.py board
```

The first run writes `~/.config/agent-lan/config.json`. It holds the board address and a shared word. Copy that file to every other computer before you start the reporter there.

The address written into the file is `http://192.168.0.69:8787`. If the board computer is not `192.168.0.69`, change `HUB` at the top of the wiring section in `agent_lan.py` before the first run, or edit `hub` in the config and copy the file again.

Then open `http://127.0.0.1:8787`.

## Start a reporter

On every computer you want listed, including the board computer:

```bash
python agent_lan.py report
```

The reporter checks that computer every 3 seconds. It sends a note only when an agent is running. The board drops a computer about 12 seconds after the last note, so closing the reporter removes that computer from the page.

`python agent_lan.py report --once` sends a single note and exits.

## What shows up

| Row | When |
| --- | --- |
| Grok | The `grok` program is running. The file comes from that session's notes. |
| Grok Bot | The Grok Bot window is open. The file cell stays empty. |
| Cursor agent | A Cursor agent worker is running. The project is its `--worker-dir`. The file comes from that project's newest transcript. |
| Claude | The `claude` program is running. |
| Codex | The `codex` program is running. |
| MCP | Any MCP server process is running. Several of them share one row. |

An edit wins over a later read. If the agent has only read a file, the row says `reading`. If it has not touched a file, the file cell is empty.

The note never includes the command line, the chat, or the file contents. Posting a note requires the shared word from the config file. Anyone on the same network can open the page.

## Tests

```bash
python -m unittest test_agent_lan -v
```
