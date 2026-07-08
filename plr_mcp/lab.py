"""Stateful PyLabRobot lab that the MCP tools drive.

Wraps a PyLabRobot LiquidHandler plus a plate reader, a thermocycler, and a
heater-shaker into one object that survives across tool calls. Defaults to
PyLabRobot's chatterbox (simulation) backends, so every tool runs end to end
with no instruments attached. Flip `simulate=False` to swap in the real
Hamilton STAR backend for liquid handling; instrument hardware backends are
left as clearly marked extension points because they are vendor specific and
cannot be exercised without the physical device.

Verified against PyLabRobot 0.2.1.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pylabrobot.resources import Coordinate


class LabNotReady(RuntimeError):
    """Raised when a tool needs the deck but setup_deck has not run yet."""


def _row_letter(n: int) -> str:
    return chr(ord("A") + n - 1)


def _jsonify(obj: Any) -> Any:
    """Best effort conversion of PyLabRobot return values into JSON safe data."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    for attr in ("serialize", "as_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return _jsonify(fn())
            except Exception:
                pass
    return repr(obj)


class Lab:
    """Single lab context shared by every MCP tool call."""

    def __init__(
        self,
        simulate: bool = True,
        num_channels: int = 8,
        tip_rack: str = "hamilton_96_tiprack_1000uL_filter",
        plate: str = "Cor_96_wellplate_360ul_Fb",
    ) -> None:
        self.simulate = simulate
        self.num_channels = num_channels
        self._tip_rack_def = tip_rack
        self._plate_def = plate

        self.lh = None
        self.tips = None
        self.plate = None
        self.reader = None
        self.tc = None
        self.hs = None

    # ------------------------------------------------------------------ deck

    def _require_lh(self):
        if self.lh is None:
            raise LabNotReady("Deck not set up. Call setup_deck first.")

    def _broadcast(self, volume: float, n: int) -> List[float]:
        return [float(volume)] * n

    async def setup_deck(self, tip_rail: int = 1, plate_rail: int = 10) -> dict:
        """Build the liquid handler on a Hamilton STARLet deck and lay out a tip
        rack and a 96-well plate."""
        from pylabrobot.liquid_handling import LiquidHandler
        from pylabrobot.resources import STARLetDeck
        import pylabrobot.resources as resources

        if self.simulate:
            from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend

            backend = LiquidHandlerChatterboxBackend(num_channels=self.num_channels)
        else:
            # Real Hamilton STAR. Needs the instrument attached over USB. Not
            # exercised by this repo's tests; validate on your own deck first.
            from pylabrobot.liquid_handling.backends import STAR

            backend = STAR()

        self.lh = LiquidHandler(backend=backend, deck=STARLetDeck())
        await self.lh.setup()

        tip_rack_cls = getattr(resources, self._tip_rack_def)
        plate_cls = getattr(resources, self._plate_def)
        self.tips = tip_rack_cls(name="tips")
        self.plate = plate_cls(name="plate")
        self.lh.deck.assign_child_resource(self.tips, rails=tip_rail)
        self.lh.deck.assign_child_resource(self.plate, rails=plate_rail)

        return {
            "ok": True,
            "mode": "simulation" if self.simulate else "hardware",
            "num_channels": self.num_channels,
            "tip_rack": {"name": "tips", "type": self._tip_rack_def, "rails": tip_rail},
            "plate": {"name": "plate", "type": self._plate_def, "rails": plate_rail},
        }

    def deck_state(self) -> dict:
        """Summarize what is currently on the deck."""
        self._require_lh()
        resources = []
        for child in self.lh.deck.children:
            loc = child.location
            resources.append(
                {
                    "name": child.name,
                    "type": type(child).__name__,
                    "location": (
                        {"x": loc.x, "y": loc.y, "z": loc.z} if loc is not None else None
                    ),
                }
            )
        return {
            "deck": type(self.lh.deck).__name__,
            "mode": "simulation" if self.simulate else "hardware",
            "resources": resources,
        }

    # -------------------------------------------------------- liquid handling

    async def pick_up_tips(self, wells: str) -> dict:
        self._require_lh()
        spots = self.tips[wells]
        await self.lh.pick_up_tips(spots)
        return {"ok": True, "action": "pick_up_tips", "wells": wells, "channels": len(spots)}

    async def drop_tips(self, wells: str) -> dict:
        self._require_lh()
        spots = self.tips[wells]
        await self.lh.drop_tips(spots)
        return {"ok": True, "action": "drop_tips", "wells": wells, "channels": len(spots)}

    async def aspirate(self, wells: str, volume: float) -> dict:
        self._require_lh()
        targets = self.plate[wells]
        await self.lh.aspirate(targets, vols=self._broadcast(volume, len(targets)))
        return {
            "ok": True,
            "action": "aspirate",
            "wells": wells,
            "volume_ul": volume,
            "channels": len(targets),
        }

    async def dispense(self, wells: str, volume: float) -> dict:
        self._require_lh()
        targets = self.plate[wells]
        await self.lh.dispense(targets, vols=self._broadcast(volume, len(targets)))
        return {
            "ok": True,
            "action": "dispense",
            "wells": wells,
            "volume_ul": volume,
            "channels": len(targets),
        }

    async def transfer(
        self, source: str, dest: str, volume: float, tips: Optional[str] = None
    ) -> dict:
        """One head pass: pick up tips, aspirate source, dispense dest, drop tips.

        Limited to at most `num_channels` wells (a single column pass) so the
        tip, source, and destination counts always line up.
        """
        self._require_lh()
        src = self.plate[source]
        dst = self.plate[dest]
        if len(src) != len(dst):
            raise ValueError(
                f"source has {len(src)} wells but dest has {len(dst)}; counts must match"
            )
        n = len(src)
        if n > self.num_channels:
            raise ValueError(
                f"transfer is one head pass of at most {self.num_channels} wells; got {n}"
            )
        tip_spec = tips or f"A1:{_row_letter(n)}1"
        tip_spots = self.tips[tip_spec]
        vols = self._broadcast(volume, n)

        await self.lh.pick_up_tips(tip_spots)
        await self.lh.aspirate(src, vols=vols)
        await self.lh.dispense(dst, vols=vols)
        await self.lh.drop_tips(tip_spots)
        return {
            "ok": True,
            "action": "transfer",
            "source": source,
            "dest": dest,
            "volume_ul": volume,
            "channels": n,
        }

    # ------------------------------------------------------------ plate reader

    async def _ensure_reader(self):
        if self.reader is not None:
            return self.reader
        from pylabrobot.plate_reading import PlateReader
        from pylabrobot.resources import Cor_96_wellplate_360ul_Fb

        if self.simulate:
            from pylabrobot.plate_reading.chatterbox import PlateReaderChatterboxBackend

            backend = PlateReaderChatterboxBackend()
        else:
            raise LabNotReady(
                "Hardware plate reader backend not configured. Wire your reader "
                "(for example a BioTek Cytation or Synergy) into Lab._ensure_reader."
            )
        self.reader = PlateReader(
            name="reader", size_x=127.76, size_y=85.48, size_z=45, backend=backend
        )
        await self.reader.setup()
        # A plate must be present for a read; place one in the reader.
        reader_plate = Cor_96_wellplate_360ul_Fb(name="reader_plate")
        self.reader.assign_child_resource(reader_plate, location=Coordinate(0, 0, 0))
        return self.reader

    async def read_plate(
        self,
        mode: str = "absorbance",
        wavelength: int = 600,
        excitation: int = 485,
        emission: int = 520,
        focal_height: float = 7.5,
    ) -> dict:
        reader = await self._ensure_reader()
        if mode == "absorbance":
            res = await reader.read_absorbance(wavelength=wavelength)
        elif mode == "fluorescence":
            res = await reader.read_fluorescence(
                excitation_wavelength=excitation,
                emission_wavelength=emission,
                focal_height=focal_height,
            )
        elif mode == "luminescence":
            res = await reader.read_luminescence(focal_height=focal_height)
        else:
            raise ValueError(
                f"unknown mode {mode!r}; use absorbance, fluorescence, or luminescence"
            )
        return {
            "ok": True,
            "mode": mode,
            "n_reads": len(res) if hasattr(res, "__len__") else None,
            "data": _jsonify(res),
        }

    # ------------------------------------------------------------ thermocycler

    async def _ensure_thermocycler(self):
        if self.tc is not None:
            return self.tc
        from pylabrobot.thermocycling import Thermocycler

        if self.simulate:
            from pylabrobot.thermocycling.chatterbox import ThermocyclerChatterboxBackend

            backend = ThermocyclerChatterboxBackend(num_zones=1)
        else:
            raise LabNotReady(
                "Hardware thermocycler backend not configured. Wire your cycler "
                "(for example an Inheco ODTC) into Lab._ensure_thermocycler."
            )
        self.tc = Thermocycler(
            name="thermocycler",
            size_x=127.76,
            size_y=85.48,
            size_z=90,
            backend=backend,
            child_location=Coordinate(0, 0, 0),
        )
        await self.tc.setup()
        return self.tc

    async def thermocycler(
        self,
        action: str,
        block_temp: Optional[float] = None,
        lid_temp: Optional[float] = None,
    ) -> dict:
        tc = await self._ensure_thermocycler()
        if action == "set_block":
            if block_temp is None:
                raise ValueError("set_block needs block_temp")
            await tc.set_block_temperature([float(block_temp)])
        elif action == "set_lid":
            if lid_temp is None:
                raise ValueError("set_lid needs lid_temp")
            await tc.set_lid_temperature([float(lid_temp)])
        elif action == "open_lid":
            await tc.open_lid()
        elif action == "close_lid":
            await tc.close_lid()
        elif action == "deactivate":
            await tc.deactivate_block()
            await tc.deactivate_lid()
        elif action == "status":
            pass
        else:
            raise ValueError(
                f"unknown action {action!r}; use set_block, set_lid, open_lid, "
                "close_lid, deactivate, or status"
            )
        return {
            "ok": True,
            "action": action,
            "block_temperature_c": _jsonify(await tc.get_block_current_temperature()),
        }

    # ------------------------------------------------------------ heater shaker

    async def _ensure_heater_shaker(self):
        if self.hs is not None:
            return self.hs
        from pylabrobot.heating_shaking import HeaterShaker

        if self.simulate:
            from pylabrobot.heating_shaking.chatterbox import HeaterShakerChatterboxBackend

            backend = HeaterShakerChatterboxBackend()
        else:
            raise LabNotReady(
                "Hardware heater-shaker backend not configured. Wire your device "
                "(for example a Hamilton Heater Shaker) into Lab._ensure_heater_shaker."
            )
        self.hs = HeaterShaker(
            name="heater_shaker",
            size_x=127.76,
            size_y=85.48,
            size_z=80,
            backend=backend,
            child_location=Coordinate(0, 0, 0),
        )
        await self.hs.setup()
        return self.hs

    async def heater_shaker(
        self,
        action: str,
        temperature: Optional[float] = None,
        speed: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> dict:
        hs = await self._ensure_heater_shaker()
        if action == "set_temperature":
            if temperature is None:
                raise ValueError("set_temperature needs temperature")
            await hs.set_temperature(float(temperature))
        elif action == "shake":
            if speed is None:
                raise ValueError("shake needs speed")
            await hs.shake(speed=float(speed), duration=duration)
        elif action == "stop":
            await hs.stop_shaking()
        elif action == "deactivate":
            await hs.deactivate()
        elif action == "status":
            pass
        else:
            raise ValueError(
                f"unknown action {action!r}; use set_temperature, shake, stop, "
                "deactivate, or status"
            )
        return {
            "ok": True,
            "action": action,
            "temperature_c": await hs.get_temperature(),
        }
