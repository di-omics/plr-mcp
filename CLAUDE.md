# plr-mcp — guide for Claude Code

An MCP server that exposes PyLabRobot (liquid handler, plate reader,
thermocycler, heater-shaker) as tools. It ships in **simulation** by default and
can drive real instruments. Some tools **move a physical arm and liquid**, so
read the safety rules before doing anything on a non-`chatterbox` backend.

## Safety rules (read first)

1. **Simulation is the default and the safe place to work.** The `chatterbox`
   backend performs no motion. Do everything you can there first.
2. **`simulated: true` is NOT a measurement.** Every tool result carries a
   `simulated` flag. If it is true, the numbers came from a stub with no
   instrument attached — never report them as real data.
3. **Never call a destructive tool on real hardware until it is homed.** On
   `star`/`ot2`/`evo`, liquid-handling tools stay blocked until
   `plr_setup_deck(home=true)` runs on a **physically clear deck**. Homing moves
   the arm; a human must confirm the deck is clear first.
4. **Real protocol runs are human-gated.** `plr_run_ampseq_pcr1` on `star`
   refuses to run without `confirm=true`. Do not set `confirm=true` yourself —
   ask the operator to, with a person watching the deck.
5. **One driver on the USB at a time.** The server tears down a link before
   opening another. Don't spawn a second process against the same instrument.
6. Tool annotations encode this: `readOnlyHint` tools are safe probes;
   `destructiveHint` tools can move hardware. Respect them.

## How Claude Code connects

This repo ships `.mcp.json`, so opening it in Claude Code prompts you to enable
the **`plr`** server (approve once; check `/mcp` or `claude mcp list`). Tools
then appear prefixed **`plr_`** (e.g. `plr_aspirate`). `.mcp.json` pins
`PLR_MCP_BACKEND=chatterbox`, so the server starts in simulation. Requires
`pip install -e .` so the `plr-mcp` console script is on PATH.

## Tools

Probes — read-only, safe to repeat:

- `plr_connect_check` — zero-motion pre-flight against a real instrument.
- `plr_deck_state` — what's on the deck.
- `plr_read_plate` — absorbance / fluorescence / luminescence.

Motion — destructive on real hardware:

- `plr_setup_deck` — build the handler, place labware. `home=true` homes a real
  STAR. **Call this first.**
- `plr_pick_up_tips`, `plr_drop_tips`, `plr_aspirate`, `plr_dispense`,
  `plr_transfer` — liquid handling (well ranges like `A1`, `A1:H1`, `A1:D1`).
- `plr_thermocycler`, `plr_heater_shaker` — block/lid and temp/shake control.
- `plr_run_ampseq_pcr1` — runs the operator's **validated** starlab PCR1 script
  (not a reimplementation); `star` needs `confirm=true`.

Codegen — writes files, no hardware:

- `plr_generate_analysis_pipeline` — FLASH-seq UMI fastq→counts + scanpy scripts.

## Real-hardware bring-up ladder

Follow in order; don't skip: (1) clean `chatterbox` dry-run of the steps; (2)
`plr_connect_check` on the target backend to prove the link with no motion; (3)
`plr_setup_deck(home=true)` on a **clear** deck (human-confirmed); (4) the
liquid-handling steps with a person watching. For PCR1: `mode='deck'`
(assignment only) → then `confirm=true` for the real transfer. See
`docs/hardware-bringup.md`.

## Architecture (where to change things)

- `plr_mcp/lab.py` — the stateful PyLabRobot wrapper. **All real instrument
  calls live here.** Safety logic (zero-motion connect, home gating, teardown)
  is here; preserve it.
- `plr_mcp/server.py` — thin FastMCP layer: one tool per `Lab` method, with
  name, annotations, input constraints, and a typed return. No hardware logic.
- `plr_mcp/schemas.py` — `TypedDict` output shapes (the tools' `outputSchema`).
- `plr_mcp/protocols.py` — wrappers that import & run validated starlab scripts.
- `plr_mcp/analysis.py` — pipeline-script generator (pure string templating).

## Conventions

- **Error model:** raise for invalid arguments / unmet preconditions (unknown
  backend, bad well range, moving before setup/home). Return `ok:false` + a
  `notes` list for expected operational states (hardware unreachable, missing
  vendor extra, human-gated run). Don't mix the two.
- **Every result includes `simulated`.** New tools must too.
- **Tools are `plr_`-prefixed**, added via `@mcp.tool(name="plr_...",
  annotations=ToolAnnotations(...))`.
- **Adding a tool = four edits:** a `Lab` method (real work) → a thin
  `server.py` tool (name + annotations + typed return) → a `schemas.py` result
  TypedDict → a `tests/test_lab.py` test on `chatterbox`.
- Keep tool descriptions and `Field(description=...)` accurate; agents rely on
  them.

## Dev commands

```bash
pip install -e '.[dev]'
ruff format plr_mcp tests
ruff check plr_mcp tests
mypy plr_mcp --check-untyped-defs
pytest -q
python examples/smoke_test.py     # end-to-end on chatterbox, prints ALL OK
```

CI runs ruff + mypy + pytest on Python 3.10–3.13.

## Don't

- Don't reimplement a validated protocol — import and call the operator's script
  (see `protocols.py`).
- Don't widen the `mcp` (`>=1.9,<2`) or `pylabrobot` (`>=0.2.1,<0.3`) pins
  without re-validating; the zero-motion connect depends on PyLabRobot internals.
- Don't call real-hardware backends from tests or CI.
- Don't set `confirm=true` or `home=true` on the operator's behalf.
