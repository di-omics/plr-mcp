"""End-to-end smoke test that drives every Lab method with no hardware.

Exercises the same code paths the MCP tools call, using PyLabRobot's chatterbox
(simulation) backends. Run it with:

    python examples/smoke_test.py

If it prints "ALL OK" and exits 0, the server's tools work against the
installed PyLabRobot.
"""
import asyncio

from plr_mcp.lab import Lab


async def main() -> None:
    lab = Lab(backend="chatterbox")

    print(await lab.setup_deck())
    print(await lab.pick_up_tips("A1:C1"))
    print(await lab.aspirate("A1:C1", 50))
    print(await lab.dispense("A4:C4", 50))
    print(await lab.drop_tips("A1:C1"))
    print(await lab.transfer("A1:H1", "A12:H12", 20))
    print({k: v for k, v in (await lab.read_plate(mode="absorbance", wavelength=600)).items() if k != "data"})
    print(await lab.thermocycler("set_lid", lid_temp=105))
    print(await lab.thermocycler("close_lid"))
    print(await lab.thermocycler("set_block", block_temp=95))
    print(await lab.heater_shaker("set_temperature", temperature=37))
    print(await lab.heater_shaker("shake", speed=1000, duration=5))
    print(await lab.heater_shaker("stop"))
    print(lab.deck_state())

    print("\nALL OK")


if __name__ == "__main__":
    asyncio.run(main())
