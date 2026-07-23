"""MCP server exposing a PyLabRobot lab as tools.

Runs over stdio by default (the transport Claude Desktop, Claude Code, and most
MCP clients use). Every tool is thin: it forwards to a single shared `Lab`
instance defined in `plr_mcp.lab`, which holds the live PyLabRobot objects.

Pick the liquid-handling backend with PLR_MCP_BACKEND (chatterbox, star, ot2,
evo); the default is chatterbox, which runs with no hardware. For ot2 set
PLR_MCP_OT2_HOST to the robot's IP. A tool call to setup_deck can override the
backend per session.

Tool naming: every tool is registered under a `plr_` prefix (`plr_aspirate`,
`plr_transfer`, ...) so it stays unambiguous when this server is loaded next to
others. The Python function keeps the short name.

Tool annotations: each tool carries MCP hints (readOnlyHint / destructiveHint /
idempotentHint / openWorldHint) so a client can tell a probe (connect_check,
deck_state, read_plate) apart from a tool that moves a physical arm or liquid
(setup_deck with home, the liquid-handling tools, run_targeted_pcr_round1). Treat every
`destructiveHint=True` tool as capable of real motion on non-chatterbox backends.

Error convention (uniform across tools):
  * Invalid arguments and unmet preconditions RAISE (FastMCP returns them as tool
    errors): an unknown backend, a bad well range, calling a liquid-handling tool
    before setup_deck, or moving before a real instrument is homed.
  * Expected operational states that a correct call can still hit are RETURNED as
    a normal result with `ok: False` and a `notes` list the agent can act on: a
    hardware backend that is not reachable from this host, a missing vendor
    extra, or a human-gated real run awaiting confirm=true.
Every successful result also carries `simulated`: True means a chatterbox stub
with no instrument attached; never read a `simulated: True` value as a real
measurement.
"""

from __future__ import annotations

import os
from typing import Annotated, Literal, Optional, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import schemas
from .analysis import generate_analysis
from .lab import Lab
from .protocols import run_targeted_pcr_round1 as _run_targeted_pcr_round1

mcp = FastMCP("pylabrobot")

# A PyLabRobot well range: a single well (A1), a full or partial column
# (A1:H1, A1:D1). Kept deliberately loose on plate size (rows A-Z, 1-2 digit
# columns) so it also fits 384-well labware; Lab still validates against the
# labware actually loaded.
_WELL_RANGE = r"^[A-Za-z]{1,2}\d{1,2}(:[A-Za-z]{1,2}\d{1,2})?$"

_backend = os.environ.get("PLR_MCP_BACKEND", "chatterbox").strip().lower()
# Back-compat: PLR_MCP_SIMULATE=0 used to mean "real hardware" (Hamilton STAR).
if os.environ.get("PLR_MCP_SIMULATE", "1").strip().lower() in ("0", "false", "no", "off"):
    _backend = "star"
_host = os.environ.get("PLR_MCP_OT2_HOST")
LAB = Lab(backend=_backend, host=_host)


@mcp.tool(
    name="plr_connect_check",
    annotations=ToolAnnotations(
        title="Connect check (zero motion)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def connect_check() -> schemas.ConnectCheckResult:
    """Zero-motion hardware pre-flight. Opens the link to the real instrument,
    reads its identity (channel count, whether it is already initialized, tip
    presence), and closes. Does NOT move the arm and does not build a deck. Use
    this first at the instrument to prove the server can talk to the STAR before
    anything moves. On the chatterbox backend it returns a simulation stub."""
    return cast(schemas.ConnectCheckResult, await LAB.connect_check())


@mcp.tool(
    name="plr_setup_deck",
    annotations=ToolAnnotations(
        title="Set up deck",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def setup_deck(
    backend: Annotated[
        Optional[Literal["chatterbox", "star", "ot2", "evo"]],
        Field(description="Liquid-handling backend; None uses the server's configured backend."),
    ] = None,
    host: Annotated[
        Optional[str], Field(description="OT-2 robot IP address (only used when backend='ot2').")
    ] = None,
    home: Annotated[
        bool,
        Field(
            description="Physical-motion gate for real hardware. False = connect without moving "
            "(for star, a zero-motion connect + identify); liquid-handling tools stay blocked "
            "until homed. True runs the full init that HOMES the channels and iSWAP, so the deck "
            "must be physically clear. chatterbox ignores this."
        ),
    ] = False,
    tip_rail: Annotated[int, Field(ge=1, description="Deck rail position for the tip rack.")] = 1,
    plate_rail: Annotated[int, Field(ge=1, description="Deck rail position for the plate.")] = 10,
    tip_rack: Annotated[
        Optional[str],
        Field(
            description="PyLabRobot tip-rack definition name; must match the deck. None keeps "
            "the default hamilton_96_tiprack_1000uL_filter."
        ),
    ] = None,
    plate: Annotated[
        Optional[str],
        Field(
            description="PyLabRobot plate definition name; must match the deck. None keeps the "
            "default Cor_96_wellplate_360ul_Fb."
        ),
    ] = None,
) -> schemas.SetupDeckResult:
    """Initialize the liquid handler and place labware. Call this before any
    liquid handling tool.

    backend: 'chatterbox' (simulation, no hardware), 'star' (Hamilton STAR),
    'ot2' (Opentrons OT-2, needs host), or 'evo' (Tecan Freedom EVO). Defaults
    to the server's configured backend.

    home: physical-motion gate for real hardware. Default False = connect
    without moving (for star, a zero-motion connect + identify); liquid-handling
    tools stay blocked until homed. home=True runs the full init that HOMES the
    channels and iSWAP, so the deck must be physically clear. chatterbox ignores
    home.

    tip_rail / plate_rail: deck rail positions. tip_rack / plate: PyLabRobot
    labware definition names, so they match what is physically on the deck.
    host: OT-2 robot IP address (only used when backend='ot2')."""
    global LAB
    if backend is not None or host is not None:
        LAB = Lab(backend=backend or LAB.backend, host=host or LAB.host)
    return cast(
        schemas.SetupDeckResult,
        await LAB.setup_deck(
            tip_rail=tip_rail,
            plate_rail=plate_rail,
            home=home,
            tip_rack=tip_rack,
            plate=plate,
        ),
    )


@mcp.tool(
    name="plr_deck_state",
    annotations=ToolAnnotations(
        title="Deck state",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def deck_state() -> schemas.DeckStateResult:
    """List the resources currently assigned to the deck and the run mode."""
    return cast(schemas.DeckStateResult, LAB.deck_state())


@mcp.tool(
    name="plr_pick_up_tips",
    annotations=ToolAnnotations(
        title="Pick up tips",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def pick_up_tips(
    wells: Annotated[
        str,
        Field(
            pattern=_WELL_RANGE,
            description="PyLabRobot range: 'A1', 'A1:H1' (a full column), or 'A1:D1'.",
        ),
    ] = "A1:H1",
) -> schemas.LiquidHandlingResult:
    """Pick up tips from the tip rack. `wells` is a PyLabRobot range such as
    'A1', 'A1:H1' (a full column), or 'A1:D1'."""
    return cast(schemas.LiquidHandlingResult, await LAB.pick_up_tips(wells))


@mcp.tool(
    name="plr_drop_tips",
    annotations=ToolAnnotations(
        title="Drop tips",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def drop_tips(
    wells: Annotated[
        str,
        Field(pattern=_WELL_RANGE, description="PyLabRobot range to return tips to, e.g. 'A1:H1'."),
    ] = "A1:H1",
) -> schemas.LiquidHandlingResult:
    """Return tips to the tip rack at the given range."""
    return cast(schemas.LiquidHandlingResult, await LAB.drop_tips(wells))


@mcp.tool(
    name="plr_aspirate",
    annotations=ToolAnnotations(
        title="Aspirate",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def aspirate(
    wells: Annotated[
        str, Field(pattern=_WELL_RANGE, description="Plate wells to aspirate from, e.g. 'A1:H1'.")
    ],
    volume: Annotated[
        float,
        Field(
            gt=0,
            le=1000,
            description="Microliters per well. Upper bound is the 1000 uL default tip capacity.",
        ),
    ],
) -> schemas.LiquidHandlingResult:
    """Aspirate `volume` microliters from each plate well in `wells`."""
    return cast(schemas.LiquidHandlingResult, await LAB.aspirate(wells, volume))


@mcp.tool(
    name="plr_dispense",
    annotations=ToolAnnotations(
        title="Dispense",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def dispense(
    wells: Annotated[
        str, Field(pattern=_WELL_RANGE, description="Plate wells to dispense into, e.g. 'A1:H1'.")
    ],
    volume: Annotated[
        float,
        Field(
            gt=0,
            le=1000,
            description="Microliters per well. Upper bound is the 1000 uL default tip capacity.",
        ),
    ],
) -> schemas.LiquidHandlingResult:
    """Dispense `volume` microliters into each plate well in `wells`."""
    return cast(schemas.LiquidHandlingResult, await LAB.dispense(wells, volume))


@mcp.tool(
    name="plr_transfer",
    annotations=ToolAnnotations(
        title="Transfer",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def transfer(
    source: Annotated[
        str,
        Field(pattern=_WELL_RANGE, description="Source wells, at most one column, e.g. 'A1:H1'."),
    ],
    dest: Annotated[
        str,
        Field(
            pattern=_WELL_RANGE,
            description="Destination wells; same count as source, e.g. 'A12:H12'.",
        ),
    ],
    volume: Annotated[
        float,
        Field(
            gt=0,
            le=1000,
            description="Microliters per well. Upper bound is the 1000 uL default tip capacity.",
        ),
    ],
    tips: Annotated[
        Optional[str],
        Field(pattern=_WELL_RANGE, description="Tip range; defaults to A1..(n)1 for n wells."),
    ] = None,
) -> schemas.TransferResult:
    """Transfer `volume` microliters from `source` wells to `dest` wells in one
    head pass (pick up tips, aspirate, dispense, drop tips). Source and dest
    ranges must have the same well count, at most one column."""
    return cast(schemas.TransferResult, await LAB.transfer(source, dest, volume, tips=tips))


@mcp.tool(
    name="plr_read_plate",
    annotations=ToolAnnotations(
        title="Read plate",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def read_plate(
    mode: Annotated[
        Literal["absorbance", "fluorescence", "luminescence"],
        Field(
            description="absorbance uses wavelength; fluorescence uses excitation/emission/"
            "focal_height; luminescence uses focal_height."
        ),
    ] = "absorbance",
    wavelength: Annotated[int, Field(ge=200, le=1000, description="nm, absorbance mode.")] = 600,
    excitation: Annotated[int, Field(ge=200, le=1000, description="nm, fluorescence mode.")] = 485,
    emission: Annotated[int, Field(ge=200, le=1000, description="nm, fluorescence mode.")] = 520,
    focal_height: Annotated[
        float, Field(ge=0, le=25, description="mm, fluorescence/luminescence modes.")
    ] = 7.5,
) -> schemas.ReadPlateResult:
    """Read the plate in the reader. `mode` is 'absorbance' (uses wavelength),
    'fluorescence' (uses excitation/emission/focal_height), or 'luminescence'
    (uses focal_height)."""
    return cast(
        schemas.ReadPlateResult,
        await LAB.read_plate(
            mode=mode,
            wavelength=wavelength,
            excitation=excitation,
            emission=emission,
            focal_height=focal_height,
        ),
    )


@mcp.tool(
    name="plr_thermocycler",
    annotations=ToolAnnotations(
        title="Thermocycler control",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def thermocycler(
    action: Annotated[
        Literal["set_block", "set_lid", "open_lid", "close_lid", "deactivate", "status"],
        Field(description="set_block needs block_temp; set_lid needs lid_temp."),
    ],
    block_temp: Annotated[
        Optional[float], Field(ge=0, le=110, description="Block temperature in Celsius.")
    ] = None,
    lid_temp: Annotated[
        Optional[float], Field(ge=0, le=120, description="Lid temperature in Celsius.")
    ] = None,
) -> schemas.ThermocyclerResult:
    """Control the thermocycler. `action` is one of: set_block (needs
    block_temp), set_lid (needs lid_temp), open_lid, close_lid, deactivate,
    status. Temperatures are in Celsius."""
    return cast(
        schemas.ThermocyclerResult,
        await LAB.thermocycler(action, block_temp=block_temp, lid_temp=lid_temp),
    )


@mcp.tool(
    name="plr_heater_shaker",
    annotations=ToolAnnotations(
        title="Heater-shaker control",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def heater_shaker(
    action: Annotated[
        Literal["set_temperature", "shake", "stop", "deactivate", "status"],
        Field(description="set_temperature needs temperature; shake needs speed."),
    ],
    temperature: Annotated[
        Optional[float], Field(ge=0, le=120, description="Celsius, for set_temperature.")
    ] = None,
    speed: Annotated[Optional[float], Field(gt=0, le=3000, description="rpm, for shake.")] = None,
    duration: Annotated[
        Optional[float], Field(gt=0, description="Seconds, optional, for shake.")
    ] = None,
) -> schemas.HeaterShakerResult:
    """Control the heater-shaker. `action` is one of: set_temperature (needs
    temperature in Celsius), shake (needs speed in rpm, optional duration in
    seconds), stop, deactivate, status."""
    return cast(
        schemas.HeaterShakerResult,
        await LAB.heater_shaker(action, temperature=temperature, speed=speed, duration=duration),
    )


@mcp.tool(
    name="plr_generate_analysis_pipeline",
    annotations=ToolAnnotations(
        title="Generate analysis pipeline",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def generate_analysis_pipeline(
    out_dir: Annotated[
        str, Field(description="Directory to write the two scripts into (created if missing).")
    ] = "analysis_out",
    read_length: Annotated[
        int, Field(ge=1, le=1000, description="Sets STAR sjdbOverhang (read_length - 1).")
    ] = 100,
    strand: Annotated[
        int, Field(ge=0, le=2, description="featureCounts -s for the UMI reads (protocol uses 1).")
    ] = 1,
    leiden_resolution: Annotated[
        float, Field(gt=0, description="scanpy Leiden clustering resolution.")
    ] = 1.0,
) -> schemas.AnalysisPipelineResult:
    """Generate the fastq-to-analysis pipeline for FLASH-seq UMI single-cell RNA-seq
    (protocol section 12). Writes two files into out_dir: flashseq_pipeline.sh (bcl to
    counts: bcl2fastq, umi_tools extract with the CTAAC spacer and 8 bp UMI, STAR,
    samtools -F 260, featureCounts, umi_tools count) and flashseq_analysis.py (counts to
    clusters with scanpy: QC, normalize, HVG, PCA, Leiden, UMAP, marker genes). The
    external tools (bcl2fastq, umi_tools, STAR, samtools, featureCounts, scanpy) are not
    bundled; the shell pipeline preflights for them. RESEARCH USE ONLY."""
    return cast(
        schemas.AnalysisPipelineResult,
        generate_analysis(
            out_dir,
            read_length=read_length,
            strand=strand,
            leiden_resolution=leiden_resolution,
        ),
    )


@mcp.tool(
    name="plr_run_targeted_pcr_round1",
    annotations=ToolAnnotations(
        title="Run targeted PCR round 1 (validated)",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def run_targeted_pcr_round1(
    backend: Annotated[
        Literal["chatterbox", "star"],
        Field(description="chatterbox dry-runs with no hardware; star drives the real STAR."),
    ] = "chatterbox",
    mode: Annotated[
        Literal["deck", "pcr1-mm"],
        Field(description="deck assigns the deck only; pcr1-mm runs the 22.5 uL x8 transfer."),
    ] = "deck",
    return_tips: Annotated[
        bool,
        Field(description="True returns tips (observation only); False discards (production)."),
    ] = False,
    tip_col: Annotated[int, Field(ge=1, le=12, description="Tip column to use.")] = 1,
    confirm: Annotated[
        bool,
        Field(
            description="Required True for the star backend: a real run homes the arm and moves "
            "liquid (human-gated)."
        ),
    ] = False,
) -> schemas.TargetedPcrRound1Result:
    """Run the operator's validated targeted PCR round 1 master-mix protocol (starlab
    script 01). This does NOT reimplement the protocol; it imports the real
    script and runs its own functions, so the tuned geometry and volumes are the
    bench values.

    backend: 'chatterbox' dry-runs with no hardware; 'star' drives the real
    Hamilton STAR and requires confirm=True (human-gated: it homes the arm and
    moves liquid). mode: 'deck' assigns the deck only; 'pcr1-mm' runs the
    22.5 uL x8 master-mix transfer. return_tips True is observation only; False
    discards (production). Set PLR_MCP_STARLAB_DIR to the starlab_live checkout
    (on starpi, the on-Pi path)."""
    return cast(
        schemas.TargetedPcrRound1Result,
        await _run_targeted_pcr_round1(
            backend=backend,
            mode=mode,
            return_tips=return_tips,
            tip_col=tip_col,
            confirm=confirm,
        ),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
