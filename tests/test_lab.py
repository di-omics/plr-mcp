"""Tests for the PyLabRobot lab wrapper.

These run against PyLabRobot's chatterbox (simulation) backends, so they need no
hardware. Async methods are driven with asyncio.run to avoid a pytest-asyncio
dependency.
"""

import asyncio

import pytest

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
    assert "not a cable" not in res["note"]


def test_evo_home_false_does_not_initialize():
    # A real backend with home=false must not run setup() (which homes).
    lab = Lab(backend="evo")
    res = run(lab.setup_deck(home=False))
    assert res["homed"] is False
    assert res["motion"] == "none"
    assert res["connected"] is False  # built but deliberately not initialized


def test_server_registers_core_tools():
    # Subset check, not exact match: the server may carry extra tools added out
    # of band, and those must not break this test.
    from plr_mcp.server import mcp

    tools = run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "connect_check",
        "setup_deck",
        "deck_state",
        "pick_up_tips",
        "drop_tips",
        "aspirate",
        "dispense",
        "transfer",
        "read_plate",
        "thermocycler",
        "heater_shaker",
    } <= names
