# plr-mcp

[![CI](https://github.com/di-omics/plr-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/di-omics/plr-mcp/actions/workflows/ci.yml)

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
plr-mcp                          # stdio transport, chatterbox simulation
PLR_MCP_BACKEND=star plr-mcp     # target a real Hamilton STAR instead
```

## Tools

Every tool is registered under a `plr_` prefix so it stays unambiguous when this
server is loaded next to others.

| Tool            | What it does |
|-----------------|--------------|
| `plr_connect_check` | Zero-motion hardware pre-flight: open the link to a real instrument, read its identity, close. Does not move the arm. See the [hardware bring-up guide](docs/hardware-bringup.md). |
| `plr_setup_deck`    | Build the liquid handler for the chosen backend and, for the Hamilton family, place a tip rack and a 96-well plate. Call this first. `home=true` homes a real STAR (motion; deck must be clear). |
| `plr_deck_state`    | List the resources on the deck and the run mode. |
| `plr_pick_up_tips`  | Pick up tips from the tip rack for a well range (for example `A1:H1`). |
| `plr_drop_tips`     | Return tips to the rack. |
| `plr_aspirate`      | Aspirate a volume from each plate well in a range. |
| `plr_dispense`      | Dispense a volume into each plate well in a range. |
| `plr_transfer`      | One head pass: pick up, aspirate, dispense, drop. |
| `plr_read_plate`    | Read absorbance, fluorescence, or luminescence. |
| `plr_thermocycler`  | Set block or lid temperature, open or close the lid, deactivate, status. |
| `plr_heater_shaker` | Set temperature, shake, stop, deactivate, status. |
| `plr_generate_analysis_pipeline` | Generate the fastq-to-analysis pipeline for FLASH-seq UMI scRNA-seq: a shell pipeline from bcl to a UMI count matrix (bcl2fastq, umi_tools, STAR, samtools, featureCounts), plus a scanpy script from counts to clusters. External tools are not bundled. |
| `plr_run_targeted_pcr_round1` | Run a validated targeted PCR round 1 master-mix protocol by importing and executing the operator's existing starlab script (not a reimplementation). `chatterbox` dry-runs; `star` requires `confirm=true` (human-gated). See below. |

Well ranges use PyLabRobot syntax: a single well `A1`, a column `A1:H1`, or a
partial column `A1:D1`.

### Tool semantics

Beyond names, the tools carry machine-readable metadata so an agent can use them
safely:

- **Annotations.** Each tool advertises MCP hints (`readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint`). Probes like
  `plr_connect_check`, `plr_deck_state`, and `plr_read_plate` are read-only;
  `plr_setup_deck` (with `home`), the liquid-handling tools, `plr_thermocycler`,
  `plr_heater_shaker`, and `plr_run_targeted_pcr_round1` are marked destructive, so a
  client can warn before anything moves on real hardware.
- **`simulated` flag.** Every result includes `simulated`. `true` means the
  numbers came from a chatterbox backend with no instrument attached; never read
  a `simulated: true` value as a real measurement.
- **Structured output.** Tools declare an output schema and return
  `structuredContent`, so clients parse results against a named shape instead of
  an opaque object (requires `mcp>=1.9`).
- **Errors.** Invalid arguments and unmet preconditions (unknown backend, bad
  well range, moving before `setup_deck`/home) are raised as tool errors.
  Expected operational states a correct call can still hit (hardware unreachable
  from this host, missing vendor extra, a human-gated real run awaiting
  `confirm=true`) come back as a normal result with `ok: false` and a `notes`
  list to act on.

## Connect a client

### Claude Code

This repo ships a project-scoped [`.mcp.json`](.mcp.json), so just open the repo
in Claude Code and approve the **`plr`** server when prompted (check `/mcp` or
`claude mcp list`). It starts on the `chatterbox` backend, and Claude Code loads
[`CLAUDE.md`](CLAUDE.md) for the tool catalog and safety rules. Tools appear
prefixed `plr_` (for example `plr_aspirate`).

Prefer to register it yourself instead:

```bash
claude mcp add --transport stdio plr -- plr-mcp
```

Either way, `plr-mcp` must be on PATH (`pip install -e .`); otherwise use the
absolute path from `which plr-mcp`, or `-- python -m plr_mcp.server`.

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

## Backends

Pick the liquid-handling backend with `PLR_MCP_BACKEND`, or override it per
session in a `setup_deck` call (`backend="star"`, etc.).

| Backend      | PyLabRobot backend       | Deck        | Runs with no hardware |
|--------------|--------------------------|-------------|-----------------------|
| `chatterbox` | `LiquidHandlerChatterboxBackend` | STARLet | yes (default) |
| `star`       | `STARBackend` (Hamilton STAR)    | STARLet | no |
| `ot2`        | `OpentronsOT2Backend` (needs `host`) | OTDeck | no |
| `evo`        | `EVOBackend` (Tecan Freedom EVO) | EVO150  | no |

Only `chatterbox` runs with no instrument. The other three construct the real
PyLabRobot backend (correct API for 0.2.1) and attempt to connect; if no
instrument is reachable, or a vendor extra such as `pylabrobot[opentrons]` is
not installed, `setup_deck` reports that in `notes` instead of crashing. The
Hamilton tip and plate auto-load only for `chatterbox` and `star`; `ot2` and
`evo` use vendor-specific labware, so load your own.

**Driving a real STAR moves a physical arm.** `setup_deck(home=true)` homes the
channels and iSWAP. The `star` backend defaults to a zero-motion connect and
blocks every liquid-handling tool until you home on a clear deck. Follow the
[hardware bring-up guide](docs/hardware-bringup.md) for the first run.

For `ot2`, pass the robot IP:

```bash
PLR_MCP_BACKEND=ot2 PLR_MCP_OT2_HOST=169.254.1.1 plr-mcp
```

The non-liquid-handling instruments (plate reader, thermocycler, heater-shaker)
run on chatterbox simulation and expose real hardware backends as clearly
marked extension points in `plr_mcp/lab.py` (the `_ensure_*` methods). Wire in
your own (for example an Inheco ODTC thermocycler or a BioTek reader) and
validate on your deck before trusting a run.

## Running a validated protocol

`run_targeted_pcr_round1` does not reimplement a protocol. It imports an existing,
hardware-validated starlab script and calls its own functions, so the tuned
geometry, volumes, and tip logic are exactly the bench values. Point it at the
scripts:

```bash
export PLR_MCP_STARLAB_DIR=/path/to/plr-tested/hamilton-star/starlab_live
```

On a real run, follow the same ladder the scripts require: a clean
`chatterbox` dry-run, then `mode='deck'` on the instrument (assignment only),
then the transfer with a person watching. The `star` backend refuses to run
without `confirm=true`, because a real run homes the arm and moves liquid.

## Layout

```
.mcp.json      Claude Code project-scoped registration (starts on chatterbox)
CLAUDE.md      guide Claude Code auto-loads (tools + safety rules)
plr_mcp/
  lab.py       stateful PyLabRobot wrapper (all the real calls live here)
  server.py    FastMCP server, one thin tool per Lab method
  schemas.py   TypedDict result shapes (the tools' output schemas)
  protocols.py validated starlab protocol wrappers (run_targeted_pcr_round1)
  analysis.py  FLASH-seq UMI pipeline generator
tests/
  test_lab.py  pytest suite, runs on chatterbox (no hardware)
examples/
  smoke_test.py end-to-end run with no hardware
evals/
  plr_mcp_eval.xml  agent-usability questions answerable on chatterbox
```

## Development

```bash
pip install -e '.[dev]'
ruff check plr_mcp tests        # lint
ruff format --check plr_mcp tests
mypy plr_mcp --check-untyped-defs
pytest -q
```

CI runs all four on Python 3.10 through 3.13 for every push and pull request.

## License

MIT
