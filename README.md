# plr-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for
[PyLabRobot](https://github.com/PyLabRobot/pylabrobot). It exposes a liquid
handler, a plate reader, a thermocycler, and a heater-shaker as MCP tools, so
any MCP client (Claude Desktop, Claude Code, or your own agent) can run
lab-automation steps by calling tools instead of writing PyLabRobot code.

It ships in **simulation mode** by default. Every tool runs end to end against
PyLabRobot's chatterbox backends with no instruments attached, so you can try
the whole thing on a laptop. Point it at real hardware by setting one
environment variable (see below).

Verified against PyLabRobot 0.2.1.

## Why an MCP server (and not just tool-use)

Driving PyLabRobot from a Claude skill or direct tool-calls is tool-use inside
one agent. An MCP server is a standalone process that speaks the Model Context
Protocol over stdio, so *any* MCP client can discover and call these tools
without knowing anything about PyLabRobot. This repo is the server.

## Install

```bash
git clone https://github.com/di-omics/plr-mcp.git
cd plr-mcp
pip install -e .
```

This pulls in `mcp` and `pylabrobot`.

## Prove it works (no hardware)

```bash
python examples/smoke_test.py
```

It drives every tool through the chatterbox backends and prints `ALL OK` when
the run succeeds.

## Run the server

```bash
plr-mcp            # stdio transport, simulation mode
```

## Tools

| Tool            | What it does |
|-----------------|--------------|
| `setup_deck`    | Build the liquid handler on a Hamilton STARLet deck and place a tip rack and a 96-well plate. Call this first. |
| `deck_state`    | List the resources on the deck and the run mode. |
| `pick_up_tips`  | Pick up tips from the tip rack for a well range (for example `A1:H1`). |
| `drop_tips`     | Return tips to the rack. |
| `aspirate`      | Aspirate a volume from each plate well in a range. |
| `dispense`      | Dispense a volume into each plate well in a range. |
| `transfer`      | One head pass: pick up, aspirate, dispense, drop. |
| `read_plate`    | Read absorbance, fluorescence, or luminescence. |
| `thermocycler`  | Set block or lid temperature, open or close the lid, deactivate, status. |
| `heater_shaker` | Set temperature, shake, stop, deactivate, status. |

Well ranges use PyLabRobot syntax: a single well `A1`, a column `A1:H1`, or a
partial column `A1:D1`.

## Connect a client

### Claude Code

```bash
claude mcp add plr -- plr-mcp
```

### Claude Desktop

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "plr": {
      "command": "plr-mcp"
    }
  }
}
```

If `plr-mcp` is not on the client's PATH, use the absolute path to the console
script (`which plr-mcp`) or run it as `python -m plr_mcp.server`.

## Real hardware

Simulation is the default. To target real hardware:

```bash
PLR_MCP_SIMULATE=0 plr-mcp
```

With that set, `setup_deck` uses PyLabRobot's real Hamilton `STAR` backend. The
instrument backends (plate reader, thermocycler, heater-shaker) are left as
clearly marked extension points in `plr_mcp/lab.py`, because they are vendor
specific and cannot be exercised without the physical device. Wire in your own
(for example an Inheco ODTC thermocycler or a BioTek reader) at the marked
`_ensure_*` methods and validate on your deck before trusting a run.

## Layout

```
plr_mcp/
  lab.py       stateful PyLabRobot wrapper (all the real calls live here)
  server.py    FastMCP server, one thin tool per Lab method
examples/
  smoke_test.py end-to-end run with no hardware
```

## License

MIT
