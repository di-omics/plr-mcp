"""Tests for the PyLabRobot lab wrapper.

These run against PyLabRobot's chatterbox (simulation) backends, so they need no
hardware. Async methods are driven with asyncio.run to avoid a pytest-asyncio
dependency.
"""

import asyncio
import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from plr_mcp.lab import Lab, LabNotReady


def run(coro):
    return asyncio.run(coro)


def test_setup_deck_loads_labware():
    lab = Lab(backend="chatterbox")
    res = run(lab.setup_deck())
    assert res["ok"] is True
    assert res["backend"] == "chatterbox"
    assert res["connected"] is True
    assert res["labware"]["tip_rack"]["type"] == "hamilton_96_tiprack_1000uL_filter"
    assert res["labware"]["plate"]["type"] == "Cor_96_wellplate_360ul_Fb"


def test_full_liquid_handling_cycle():
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    assert run(lab.pick_up_tips("A1:C1"))["channels"] == 3
    assert run(lab.aspirate("A1:C1", 50))["volume_ul"] == 50
    assert run(lab.dispense("A4:C4", 50))["ok"] is True
    assert run(lab.drop_tips("A1:C1"))["ok"] is True
    assert run(lab.transfer("A1:H1", "A12:H12", 20))["channels"] == 8


def test_instruments_read_and_control():
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    assert run(lab.read_plate(mode="absorbance", wavelength=600))["ok"] is True
    assert run(lab.thermocycler("set_block", block_temp=95))["block_temperature_c"] == [95]
    assert run(lab.heater_shaker("set_temperature", temperature=37))["temperature_c"] == 37


def test_deck_state_lists_labware():
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    state = lab.deck_state()
    names = {r["name"] for r in state["resources"]}
    assert {"tips", "plate"} <= names


def test_tools_require_setup_first():
    lab = Lab(backend="chatterbox")
    with pytest.raises(LabNotReady):
        run(lab.aspirate("A1", 10))


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        Lab(backend="nope")


def test_ot2_without_host_errors():
    lab = Lab(backend="ot2")
    with pytest.raises(ValueError):
        run(lab.setup_deck())


def test_transfer_over_channel_count_rejected():
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    with pytest.raises(ValueError):
        run(lab.transfer("A1:H2", "A1:H2", 10))  # 16 wells > 8 channels


def test_chatterbox_is_always_homed_after_setup():
    lab = Lab(backend="chatterbox")
    res = run(lab.setup_deck())
    assert res["homed"] is True
    assert res["motion"] == "none"


def test_require_homed_blocks_unhomed_hardware():
    # A real backend that is connected but not homed must refuse to move.
    lab = Lab(backend="star")
    with pytest.raises(LabNotReady):
        lab._require_homed()
    lab._homed = True
    lab._require_homed()  # once homed, no raise


def test_require_homed_never_blocks_chatterbox():
    Lab(backend="chatterbox")._require_homed()  # must not raise


def test_connect_check_chatterbox_is_a_stub():
    res = run(Lab(backend="chatterbox").connect_check())
    assert res["ok"] is True
    assert res["simulated"] is True


def test_teardown_releases_link_and_resets_homed():
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    assert lab._homed is True and lab.lh is not None
    run(lab._teardown())
    assert lab._homed is False and lab.lh is None


def test_setup_deck_can_be_rerun_without_leaking():
    # A second setup_deck must tear the first link down first, not stack a
    # second driver. Both calls succeed and leave the lab ready.
    lab = Lab(backend="chatterbox")
    assert run(lab.setup_deck())["homed"] is True
    assert run(lab.setup_deck())["homed"] is True
    run(lab.pick_up_tips("A1:C1"))  # still usable after the re-setup


def test_star_connect_check_gets_past_deck_assert_and_reports_version():
    # No hardware here, so this returns ok=False. The point is HOW it fails:
    # it must reach the USB layer (a reach/cable error), NOT the old
    # 'Deck not set' AssertionError, and it must surface the library version.
    res = run(Lab(backend="star").connect_check())
    assert res["ok"] is False
    assert "pylabrobot_version" in res
    # the internal-error branch (which the deck bug would have triggered) says
    # "not a cable"; the fixed path reports a real reach problem instead.
    assert "not a cable" not in " ".join(res["notes"])


def test_evo_home_false_does_not_initialize():
    # A real backend with home=false must not run setup() (which homes).
    lab = Lab(backend="evo")
    res = run(lab.setup_deck(home=False))
    assert res["homed"] is False
    assert res["motion"] == "none"
    assert res["connected"] is False  # built but deliberately not initialized


def test_targeted_pcr_round1_rejects_bad_args():
    from plr_mcp.protocols import run_targeted_pcr_round1

    for bad in (
        dict(backend="nope"),
        dict(mode="nope"),
        dict(tip_col=0),
        dict(tip_col=13),
    ):
        with pytest.raises(ValueError):
            run(run_targeted_pcr_round1(**bad))


def test_targeted_pcr_round1_star_is_human_gated():
    from plr_mcp.protocols import run_targeted_pcr_round1

    # Real backend without confirm must refuse and never touch a script or device.
    res = run(run_targeted_pcr_round1(backend="star", mode="pcr1-mm"))
    assert res["ok"] is False
    assert "confirm" in " ".join(res["notes"])


def test_targeted_pcr_round1_missing_script_is_reported():
    from plr_mcp.protocols import run_targeted_pcr_round1

    os.environ["PLR_MCP_STARLAB_DIR"] = "/nonexistent/starlab"
    try:
        with pytest.raises(FileNotFoundError):
            run(run_targeted_pcr_round1(backend="chatterbox", mode="deck"))
    finally:
        del os.environ["PLR_MCP_STARLAB_DIR"]


def test_targeted_pcr_round1_dry_run_when_script_present():
    # Runs the real validated script under chatterbox if it is checked out here;
    # skips in CI where plr-tested is absent.
    from plr_mcp.protocols import targeted_pcr_round1_script_path, run_targeted_pcr_round1

    if not os.path.exists(targeted_pcr_round1_script_path()):
        pytest.skip("starlab targeted PCR round 1 script not present in this environment")
    res = run(run_targeted_pcr_round1(backend="chatterbox", mode="pcr1-mm", return_tips=True))
    assert res["ok"] is True
    assert res["volume_ul"] == 22.5
    assert res["executed"] == ["assign_deck", "transfer_pcr1_master_mix"]


def test_server_registers_core_tools():
    # Subset check, not exact match: the server may carry extra tools added out
    # of band, and those must not break this test. Tools are registered under a
    # plr_ prefix so they stay unambiguous alongside other MCP servers.
    from plr_mcp.server import mcp

    tools = run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "plr_connect_check",
        "plr_setup_deck",
        "plr_deck_state",
        "plr_pick_up_tips",
        "plr_drop_tips",
        "plr_aspirate",
        "plr_dispense",
        "plr_transfer",
        "plr_read_plate",
        "plr_thermocycler",
        "plr_heater_shaker",
    } <= names


def test_server_stdio_contract_runs_through_a_real_mcp_client():
    """Exercise the shipped stdio transport, not only the in-process FastMCP object."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "plr_mcp.server"],
            env={**os.environ, "PLR_MCP_BACKEND": "chatterbox"},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("plr_setup_deck", {})

        assert "plr_setup_deck" in {tool.name for tool in tools.tools}
        assert result.isError is False
        assert result.structuredContent["simulated"] is True
        assert result.structuredContent["homed"] is True

    run(go())


def test_results_flag_simulation():
    # Every result must say whether it is a chatterbox stub or real hardware, so
    # a simulated reading is never mistaken for a measurement.
    lab = Lab(backend="chatterbox")
    run(lab.setup_deck())
    run(lab.pick_up_tips("A1:C1"))  # aspirate requires tips on the channels
    assert run(lab.aspirate("A1:C1", 10))["simulated"] is True
    assert run(lab.read_plate(mode="absorbance", wavelength=600))["simulated"] is True
    assert run(lab.thermocycler("status"))["simulated"] is True
    assert run(lab.heater_shaker("status"))["simulated"] is True
    assert lab.deck_state()["simulated"] is True


def test_every_tool_is_prefixed_annotated_and_schematized():
    # Requires mcp>=1.9 for outputSchema. Each tool must carry MCP annotations
    # and a structured output schema so clients can reason about it.
    from plr_mcp.server import mcp

    tools = run(mcp.list_tools())
    assert tools, "no tools registered"
    for t in tools:
        assert t.name.startswith("plr_"), f"{t.name} is not prefixed"
        assert t.annotations is not None, f"{t.name} has no annotations"
        assert t.outputSchema is not None, f"{t.name} has no output schema"


def test_annotations_separate_probes_from_motion():
    from plr_mcp.server import mcp

    tools = {t.name: t for t in run(mcp.list_tools())}
    # Probes: no motion, safe to repeat.
    assert tools["plr_connect_check"].annotations.readOnlyHint is True
    assert tools["plr_deck_state"].annotations.readOnlyHint is True
    # Motion: moves an arm or liquid on real hardware.
    for name in ("plr_aspirate", "plr_dispense", "plr_transfer", "plr_run_targeted_pcr_round1"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is True
