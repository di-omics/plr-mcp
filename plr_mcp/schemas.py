"""Typed result shapes for the MCP tools.

Each tool in ``server.py`` annotates its return with one of these ``TypedDict``s
so the FastMCP layer emits a JSON ``outputSchema`` and returns
``structuredContent`` (both require mcp>=1.9). Clients can then parse tool
results against a named schema instead of an opaque object.

Two rules keep structured output valid, and BOTH matter:

1. Every field is declared on a ``total=False`` TypedDict, because real hardware
   runs and simulation runs return different key sets (an unreachable instrument
   has no ``num_channels``, a chatterbox connect has no ``pylabrobot_version``).

2. Every field type is ``Optional`` (nullable). FastMCP fills absent
   ``total=False`` fields with ``null`` in the emitted ``structuredContent``, and
   the CLIENT validates that payload against this ``outputSchema``. A field typed
   ``str`` (not ``Optional[str]``) would advertise a non-nullable type, so the
   null FastMCP sends for an absent field fails client-side validation with
   "None is not of type 'string'". Keep every field Optional; do not "tidy" the
   ``Optional`` away or every tool call that omits a key breaks.

Values that come straight off PyLabRobot (a plate-reader payload, a
block-temperature reading) are typed ``Any`` because their shape is
device-specific and already normalised by ``lab._jsonify``; ``Any`` already
admits null.

Every result carries ``simulated``: True means the numbers came from a
chatterbox backend with no instrument attached, False means real hardware. Never
treat a ``simulated: True`` reading as a measurement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Pydantic (via FastMCP's outputSchema generation) requires
# typing_extensions.TypedDict, not typing.TypedDict, on Python < 3.12; the
# stdlib one raises PydanticUserError there. Import it from typing_extensions so
# the schemas build on every supported Python.
from typing_extensions import TypedDict


class LiquidHandlingResult(TypedDict, total=False):
    """pick_up_tips, drop_tips, aspirate, dispense."""

    ok: Optional[bool]
    action: Optional[str]
    wells: Optional[str]
    volume_ul: Optional[float]
    channels: Optional[int]
    simulated: Optional[bool]


class TransferResult(TypedDict, total=False):
    """transfer: one head pass (pick up, aspirate, dispense, drop)."""

    ok: Optional[bool]
    action: Optional[str]
    source: Optional[str]
    dest: Optional[str]
    volume_ul: Optional[float]
    channels: Optional[int]
    simulated: Optional[bool]


class LabwareSlot(TypedDict, total=False):
    name: Optional[str]
    type: Optional[str]
    rails: Optional[int]


class LabwareInfo(TypedDict, total=False):
    tip_rack: Optional[LabwareSlot]
    plate: Optional[LabwareSlot]


class SetupDeckResult(TypedDict, total=False):
    """setup_deck: build the handler and (for Hamilton) place labware."""

    ok: Optional[bool]
    backend: Optional[str]
    simulated: Optional[bool]
    deck: Optional[str]
    connected: Optional[bool]
    homed: Optional[bool]
    motion: Optional[str]
    num_channels: Optional[int]
    labware: Optional[LabwareInfo]
    instrument_reported_initialized: Optional[bool]
    notes: Optional[List[str]]


class ConnectCheckResult(TypedDict, total=False):
    """connect_check: zero-motion pre-flight against a real instrument."""

    ok: Optional[bool]
    backend: Optional[str]
    simulated: Optional[bool]
    connected: Optional[bool]
    motion: Optional[str]
    num_channels: Optional[int]
    instrument_initialized: Optional[bool]
    tips_present: Optional[List[bool]]
    pylabrobot_version: Optional[str]
    version_warning: Optional[str]
    notes: Optional[List[str]]


class DeckResource(TypedDict, total=False):
    name: Optional[str]
    type: Optional[str]
    location: Optional[Dict[str, float]]


class DeckStateResult(TypedDict, total=False):
    """deck_state: what is currently assigned to the deck."""

    deck: Optional[str]
    backend: Optional[str]
    simulated: Optional[bool]
    resources: Optional[List[DeckResource]]


class ReadPlateResult(TypedDict, total=False):
    """read_plate: absorbance / fluorescence / luminescence."""

    ok: Optional[bool]
    mode: Optional[str]
    simulated: Optional[bool]
    n_reads: Optional[int]
    data: Any


class ThermocyclerResult(TypedDict, total=False):
    """thermocycler: block/lid control and status."""

    ok: Optional[bool]
    action: Optional[str]
    simulated: Optional[bool]
    block_temperature_c: Any


class HeaterShakerResult(TypedDict, total=False):
    """heater_shaker: temperature/shake control and status."""

    ok: Optional[bool]
    action: Optional[str]
    simulated: Optional[bool]
    temperature_c: Any


class AnalysisPipelineResult(TypedDict, total=False):
    """generate_analysis_pipeline: paths written plus provenance."""

    pipeline_sh: Optional[str]
    analysis_py: Optional[str]
    method: Optional[str]
    steps: Optional[List[str]]
    external_tools: Optional[List[str]]
    note: Optional[str]


class TargetedPcrRound1Result(TypedDict, total=False):
    """run_targeted_pcr_round1: validated starlab PCR1 master-mix run."""

    ok: Optional[bool]
    protocol: Optional[str]
    script: Optional[str]
    backend: Optional[str]
    simulated: Optional[bool]
    mode: Optional[str]
    volume_ul: Optional[float]
    tips: Optional[str]
    tip_col: Optional[int]
    executed: Optional[List[str]]
    source: Optional[str]
    destination: Optional[str]
    notes: Optional[List[str]]
