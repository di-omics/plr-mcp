"""MCP server exposing a PyLabRobot lab as tools.

Runs over stdio by default (the transport Claude Desktop, Claude Code, and most
MCP clients use). Every tool is thin: it forwards to a single shared `Lab`
instance defined in `plr_mcp.lab`, which holds the live PyLabRobot objects.

Set PLR_MCP_SIMULATE=0 to target real hardware (Hamilton STAR liquid handler);
the default is simulation via PyLabRobot's chatterbox backends.
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .lab import Lab

mcp = FastMCP("pylabrobot")

_simulate = os.environ.get("PLR_MCP_SIMULATE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
LAB = Lab(simulate=_simulate)


@mcp.tool()
async def setup_deck(tip_rail: int = 1, plate_rail: int = 10) -> dict:
    """Initialize the liquid handler on a Hamilton STARLet deck and place a
    1000 uL tip rack and a Corning 96-well plate. Call this before any liquid
    handling tool."""
    return await LAB.setup_deck(tip_rail=tip_rail, plate_rail=plate_rail)


@mcp.tool()
async def deck_state() -> dict:
    """List the resources currently assigned to the deck and the run mode."""
    return LAB.deck_state()


@mcp.tool()
async def pick_up_tips(wells: str = "A1:H1") -> dict:
    """Pick up tips from the tip rack. `wells` is a PyLabRobot range such as
    'A1', 'A1:H1' (a full column), or 'A1:D1'."""
    return await LAB.pick_up_tips(wells)


@mcp.tool()
async def drop_tips(wells: str = "A1:H1") -> dict:
    """Return tips to the tip rack at the given range."""
    return await LAB.drop_tips(wells)


@mcp.tool()
async def aspirate(wells: str, volume: float) -> dict:
    """Aspirate `volume` microliters from each plate well in `wells`."""
    return await LAB.aspirate(wells, volume)


@mcp.tool()
async def dispense(wells: str, volume: float) -> dict:
    """Dispense `volume` microliters into each plate well in `wells`."""
    return await LAB.dispense(wells, volume)


@mcp.tool()
async def transfer(
    source: str, dest: str, volume: float, tips: Optional[str] = None
) -> dict:
    """Transfer `volume` microliters from `source` wells to `dest` wells in one
    head pass (pick up tips, aspirate, dispense, drop tips). Source and dest
    ranges must have the same well count, at most one column."""
    return await LAB.transfer(source, dest, volume, tips=tips)


@mcp.tool()
async def read_plate(
    mode: str = "absorbance",
    wavelength: int = 600,
    excitation: int = 485,
    emission: int = 520,
    focal_height: float = 7.5,
) -> dict:
    """Read the plate in the reader. `mode` is 'absorbance' (uses wavelength),
    'fluorescence' (uses excitation/emission/focal_height), or 'luminescence'
    (uses focal_height)."""
    return await LAB.read_plate(
        mode=mode,
        wavelength=wavelength,
        excitation=excitation,
        emission=emission,
        focal_height=focal_height,
    )


@mcp.tool()
async def thermocycler(
    action: str,
    block_temp: Optional[float] = None,
    lid_temp: Optional[float] = None,
) -> dict:
    """Control the thermocycler. `action` is one of: set_block (needs
    block_temp), set_lid (needs lid_temp), open_lid, close_lid, deactivate,
    status. Temperatures are in Celsius."""
    return await LAB.thermocycler(action, block_temp=block_temp, lid_temp=lid_temp)


@mcp.tool()
async def heater_shaker(
    action: str,
    temperature: Optional[float] = None,
    speed: Optional[float] = None,
    duration: Optional[float] = None,
) -> dict:
    """Control the heater-shaker. `action` is one of: set_temperature (needs
    temperature in Celsius), shake (needs speed in rpm, optional duration in
    seconds), stop, deactivate, status."""
    return await LAB.heater_shaker(
        action, temperature=temperature, speed=speed, duration=duration
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
