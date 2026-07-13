"""Stateful PyLabRobot lab that the MCP tools drive.

Wraps a PyLabRobot LiquidHandler plus a plate reader, a thermocycler, and a
heater-shaker into one object that survives across tool calls. Defaults to
PyLabRobot's chatterbox (simulation) backends, so every tool runs end to end
with no instruments attached. Pass `backend="star"` (or "ot2"/"evo") to swap in
a real liquid-handling backend; the non-liquid-handling instruments expose real
hardware backends as clearly marked extension points because they are vendor
specific and cannot be exercised without the physical device.

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


def _plr_version_info() -> dict:
    """Installed PyLabRobot version and whether it is inside the validated range.

    The zero-motion connect depends on version-specific PyLabRobot internals
    (that HamiltonLiquidHandler.setup performs no motion), so surface the version
    and warn outside the range this was checked against.
    """
    import pylabrobot

    ver = getattr(pylabrobot, "__version__", "unknown")
    info: dict = {"pylabrobot_version": ver}
    try:
        parts = tuple(int(x) for x in ver.split(".")[:3])
        if not ((0, 2, 1) <= parts < (0, 3, 0)):
            info["version_warning"] = (
                f"PyLabRobot {ver} is outside the validated range >=0.2.1,<0.3; the "
                f"zero-motion guarantee was checked on 0.2.1. Re-verify a safe connect "
                f"before trusting hardware."
            )
    except Exception:
        info["version_warning"] = f"could not parse PyLabRobot version {ver!r}."
    return info


class Lab:
    """Single lab context shared by every MCP tool call."""

    SUPPORTED_BACKENDS = ("chatterbox", "star", "ot2", "evo")

    def __init__(
        self,
        backend: str = "chatterbox",
        host: Optional[str] = None,
        num_channels: int = 8,
        tip_rack: str = "hamilton_96_tiprack_1000uL_filter",
        plate: str = "Cor_96_wellplate_360ul_Fb",
    ) -> None:
        backend = backend.lower()
        if backend not in self.SUPPORTED_BACKENDS:
            raise ValueError(
                f"unknown backend {backend!r}; use one of {', '.join(self.SUPPORTED_BACKENDS)}"
            )
        self.backend = backend
        self.host = host
        # Only the chatterbox backend runs with no hardware. The sim-only
        # instruments (reader, thermocycler, heater-shaker) key off this.
        self.simulate = backend == "chatterbox"
        self.num_channels = num_channels
        self._tip_rack_def = tip_rack
        self._plate_def = plate

        # PyLabRobot objects, created lazily. Typed Any because PyLabRobot does
        # not ship type stubs.
        self.lh: Any = None
        self.tips: Any = None
        self.plate: Any = None
        self.reader: Any = None
        self.tc: Any = None
        self.hs: Any = None

        # Real instruments must be homed (a physical motion) before any
        # liquid-handling move. Chatterbox needs no homing.
        self._homed: bool = False
        self._star_reported_initialized: Optional[bool] = None

    # ------------------------------------------------------------------ deck

    def _require_lh(self):
        if self.lh is None:
            raise LabNotReady("Deck not set up. Call setup_deck first.")

    def _require_plate(self):
        if self.plate is None or self.tips is None:
            raise LabNotReady(
                "No Hamilton labware loaded for this backend. Tip and plate "
                "auto-load runs only for the chatterbox and star backends."
            )

    def _require_homed(self):
        # Chatterbox never moves, so it is always considered ready. Real
        # backends must be homed by an explicit setup_deck(home=True) first.
        if self.backend != "chatterbox" and not self._homed:
            raise LabNotReady(
                "Instrument is connected but NOT homed. Call setup_deck with "
                "home=true on a physically clear deck before any liquid-handling "
                "move."
            )

    async def _teardown(self) -> None:
        """Release any open instrument link before opening another.

        Building a new backend while the previous one still holds the USB would
        put two PyLabRobot drivers on one link (garbage responses, unsafe
        state). Always call this before constructing a fresh connection. Closing
        a link is motion-free.
        """
        if self.lh is not None:
            try:
                await self.lh.stop()
            except Exception:
                pass
        self.lh = None
        self._homed = False

    def _broadcast(self, volume: float, n: int) -> List[float]:
        return [float(volume)] * n

    def _make_lh(self):
        """Return (liquid_handler_backend, deck, load_hamilton_labware).

        Only the Hamilton family (chatterbox, star) auto-loads the tip rack and
        plate; the OT-2 and EVO decks use vendor-specific labware, so their
        layout is left to the caller.
        """
        import pylabrobot.resources as resources

        if self.backend == "chatterbox":
            from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend

            return (
                LiquidHandlerChatterboxBackend(num_channels=self.num_channels),
                resources.STARLetDeck(),
                True,
            )
        if self.backend == "star":
            # Real Hamilton STAR. Correct API for 0.2.1; needs the instrument
            # over USB. Not exercised without hardware.
            from pylabrobot.liquid_handling.backends import STARBackend

            return STARBackend(), resources.STARLetDeck(), True
        if self.backend == "ot2":
            # Real Opentrons OT-2 over the network.
            from pylabrobot.liquid_handling.backends import OpentronsOT2Backend

            if not self.host:
                raise ValueError("ot2 backend needs host=<robot IP address>")
            return OpentronsOT2Backend(host=self.host), resources.OTDeck(), False
        if self.backend == "evo":
            # Real Tecan Freedom EVO.
            from pylabrobot.liquid_handling.backends import EVOBackend

            return EVOBackend(), resources.EVO150Deck(), False
        raise ValueError(f"unknown backend {self.backend!r}")

    # -------------------------------------------------- STAR safe bring-up

    async def _star_open_no_motion(self, backend: Any) -> None:
        """Open the STAR USB link and start the response reader, WITHOUT motion.

        PyLabRobot's HamiltonLiquidHandler.setup does exactly this (io.setup +
        the reading thread). We call it directly so we skip STARBackend.setup,
        whose firmware initialization homes the arm. Resolved off the MRO so it
        survives PyLabRobot module reshuffles between versions.

        The base setup() asserts a deck is set, so we set one first if needed.
        Setting a deck is a software assignment and commands no motion.
        """
        from pylabrobot.resources import STARLetDeck

        if getattr(backend, "_deck", None) is None:
            backend.set_deck(STARLetDeck())
        for cls in type(backend).__mro__:
            if cls.__name__ == "HamiltonLiquidHandler":
                await cls.setup(backend)  # type: ignore[attr-defined]
                return
        # Fallback for other layouts: open just the IO layer.
        await backend.io.setup()

    async def _star_connect_and_identify(self, backend: Any) -> dict:
        """Open the link, read identity, and close. Guaranteed zero motion.

        None of the request_* calls command motion; they only query firmware.
        The link is closed afterward so it does not hold the USB (respecting the
        one-driver-at-a-time rule).
        """
        info: dict = {}
        opened = False
        try:
            await self._star_open_no_motion(backend)
            opened = True
            info["instrument_initialized"] = bool(
                await backend.request_instrument_initialization_status()
            )
            tips = await backend.request_tip_presence()
            info["num_channels"] = len(tips)
            info["tips_present"] = [bool(t) for t in tips]
        finally:
            if opened:
                try:
                    await backend.stop()
                except Exception:
                    pass
        return info

    async def connect_check(self) -> dict:
        """Zero-motion pre-flight: open the link, read identity, close.

        Proves the server can talk to the real STAR without moving the arm.
        Safe to run any time. Does not build a deck or load labware.
        """
        if self.backend == "chatterbox":
            return {
                "ok": True,
                "backend": "chatterbox",
                "simulated": True,
                "num_channels": self.num_channels,
                "instrument_initialized": None,
                "notes": ["simulation backend; there is no real instrument to probe."],
            }
        if self.backend != "star":
            return {
                "ok": False,
                "backend": self.backend,
                "simulated": False,
                "notes": ["connect_check is implemented for the star backend only."],
            }
        # Release any link a prior setup_deck left open, so this probe never
        # becomes the second driver on the USB.
        await self._teardown()
        try:
            from pylabrobot.liquid_handling.backends import STARBackend

            backend = STARBackend()
        except Exception as e:
            return {
                "ok": False,
                "backend": "star",
                "simulated": False,
                "notes": [f"could not construct STARBackend ({type(e).__name__}: {e})."],
            }
        try:
            info = await self._star_connect_and_identify(backend)
        except Exception as e:
            if isinstance(e, (AssertionError, TypeError, AttributeError)):
                note = (
                    f"internal or PyLabRobot-API error during connect "
                    f"({type(e).__name__}: {e}). This is a code or library-version "
                    f"issue, not a cable or one-driver problem."
                )
            else:
                note = (
                    f"could not reach the STAR ({type(e).__name__}: {e}). Check the "
                    "USB link, that PyLabRobot runs on the machine holding the cable, "
                    "and that no other PyLabRobot driver is attached."
                )
            return {
                "ok": False,
                "backend": "star",
                "simulated": False,
                "connected": False,
                "notes": [note],
                **_plr_version_info(),
            }
        return {
            "ok": True,
            "backend": "star",
            "simulated": False,
            "connected": True,
            "motion": "none",
            **_plr_version_info(),
            **info,
        }

    async def setup_deck(
        self,
        tip_rail: int = 1,
        plate_rail: int = 10,
        home: bool = False,
        tip_rack: Optional[str] = None,
        plate: Optional[str] = None,
    ) -> dict:
        """Build the liquid handler and lay out labware.

        home controls physical motion and matters only for real hardware:
          home=False (default): construct and connect, but DO NOT home. For star
            this is a zero-motion connect + identify; liquid-handling tools stay
            blocked until you home. Safe to run with labware on the deck.
          home=True: run the full firmware init, which HOMES the channels and
            iSWAP. The deck must be physically clear. This is the deliberate
            motion step; only after it do liquid-handling tools unlock.
        chatterbox ignores home (it never moves) and is always ready.

        tip_rack / plate override the labware definitions so they match what is
        physically on the deck.
        """
        from pylabrobot.liquid_handling import LiquidHandler
        import pylabrobot.resources as resources

        if tip_rack:
            self._tip_rack_def = tip_rack
        if plate:
            self._plate_def = plate

        if self.backend == "ot2" and not self.host:
            raise ValueError("ot2 backend needs a host; pass host=<robot IP> to setup_deck")

        # Release any link a previous call left open before building a new one,
        # so we never hold the USB with two drivers.
        await self._teardown()

        # Constructing a vendor backend can fail if its optional extra is not
        # installed (for example pylabrobot[opentrons]). Report that honestly
        # rather than crashing the tool call.
        try:
            lh_backend, deck, load_labware = self._make_lh()
        except Exception as e:
            if self.backend == "chatterbox":
                raise
            return {
                "ok": False,
                "backend": self.backend,
                "simulated": self.simulate,
                "connected": False,
                "labware": None,
                "notes": [
                    f"backend not available in this environment "
                    f"({type(e).__name__}: {e}). Install the vendor extra "
                    f"(for example pip install 'pylabrobot[opentrons]') and run "
                    f"on the host with the instrument attached."
                ],
            }

        self.lh = LiquidHandler(backend=lh_backend, deck=deck)

        notes: List[str] = []
        connected = True
        motion = "none"
        self._homed = False
        try:
            if self.backend == "chatterbox":
                await self.lh.setup()  # harmless in simulation
                self._homed = True
            elif not home:
                # Real backend, home=false: never run the full setup (it homes).
                if self.backend == "star":
                    # Zero-motion connect + identify. The arm is NOT homed.
                    info = await self._star_connect_and_identify(lh_backend)
                    self._star_reported_initialized = info.get("instrument_initialized")
                    notes.append(
                        "connected without motion (identify only); the arm is NOT "
                        "homed. Liquid-handling tools are blocked until you call "
                        "setup_deck with home=true on a physically clear deck."
                    )
                else:
                    # ot2/evo have no zero-motion connect here, and their setup()
                    # homes. Build the deck only; do not open or initialize.
                    connected = False
                    notes.append(
                        f"{self.backend} built but not initialized. home=false does "
                        f"not move it and does not connect; call setup_deck(home=true) "
                        f"to initialize (this homes the instrument)."
                    )
            else:
                # home=true real backend: full setup. This HOMES the instrument.
                motion = (
                    "homes channels and iSWAP"
                    if self.backend == "star"
                    else "initializes and homes the instrument"
                )
                await self.lh.setup()
                self._homed = True
        except Exception as e:
            # Chatterbox must set up cleanly; a failure there is a real bug.
            if self.backend == "chatterbox":
                raise
            connected = False
            if isinstance(e, (AssertionError, TypeError, AttributeError)):
                notes.append(
                    f"internal or PyLabRobot-API error during connect "
                    f"({type(e).__name__}: {e}). This is a code or library-version "
                    f"issue, not a cable or one-driver problem."
                )
            else:
                notes.append(
                    f"backend constructed but hardware not reachable from here "
                    f"({type(e).__name__}: {e}). Run on the host with the instrument "
                    f"attached."
                )

        labware = None
        if load_labware:
            tip_rack_cls = getattr(resources, self._tip_rack_def)
            plate_cls = getattr(resources, self._plate_def)
            self.tips = tip_rack_cls(name="tips")
            self.plate = plate_cls(name="plate")
            self.lh.deck.assign_child_resource(self.tips, rails=tip_rail)
            self.lh.deck.assign_child_resource(self.plate, rails=plate_rail)
            labware = {
                "tip_rack": {"name": "tips", "type": self._tip_rack_def, "rails": tip_rail},
                "plate": {"name": "plate", "type": self._plate_def, "rails": plate_rail},
            }
        else:
            notes.append(
                f"{self.backend} uses vendor-specific labware; Hamilton tip and "
                "plate auto-load was skipped. Load your own labware onto the deck."
            )

        result = {
            "ok": True,
            "backend": self.backend,
            "simulated": self.simulate,
            "deck": type(deck).__name__,
            "connected": connected,
            "homed": self._homed,
            "motion": motion,
            "num_channels": self.num_channels,
            "labware": labware,
        }
        if self._star_reported_initialized is not None:
            result["instrument_reported_initialized"] = self._star_reported_initialized
        if notes:
            result["notes"] = notes
        return result

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
                    "location": ({"x": loc.x, "y": loc.y, "z": loc.z} if loc is not None else None),
                }
            )
        return {
            "deck": type(self.lh.deck).__name__,
            "backend": self.backend,
            "simulated": self.simulate,
            "resources": resources,
        }

    # -------------------------------------------------------- liquid handling

    async def pick_up_tips(self, wells: str) -> dict:
        self._require_lh()
        self._require_plate()
        self._require_homed()
        spots = self.tips[wells]
        await self.lh.pick_up_tips(spots)
        return {
            "ok": True,
            "action": "pick_up_tips",
            "wells": wells,
            "channels": len(spots),
            "simulated": self.simulate,
        }

    async def drop_tips(self, wells: str) -> dict:
        self._require_lh()
        self._require_plate()
        self._require_homed()
        spots = self.tips[wells]
        await self.lh.drop_tips(spots)
        return {
            "ok": True,
            "action": "drop_tips",
            "wells": wells,
            "channels": len(spots),
            "simulated": self.simulate,
        }

    async def aspirate(self, wells: str, volume: float) -> dict:
        self._require_lh()
        self._require_plate()
        self._require_homed()
        targets = self.plate[wells]
        await self.lh.aspirate(targets, vols=self._broadcast(volume, len(targets)))
        return {
            "ok": True,
            "action": "aspirate",
            "wells": wells,
            "volume_ul": volume,
            "channels": len(targets),
            "simulated": self.simulate,
        }

    async def dispense(self, wells: str, volume: float) -> dict:
        self._require_lh()
        self._require_plate()
        self._require_homed()
        targets = self.plate[wells]
        await self.lh.dispense(targets, vols=self._broadcast(volume, len(targets)))
        return {
            "ok": True,
            "action": "dispense",
            "wells": wells,
            "volume_ul": volume,
            "channels": len(targets),
            "simulated": self.simulate,
        }

    async def transfer(
        self, source: str, dest: str, volume: float, tips: Optional[str] = None
    ) -> dict:
        """One head pass: pick up tips, aspirate source, dispense dest, drop tips.

        Limited to at most `num_channels` wells (a single column pass) so the
        tip, source, and destination counts always line up.
        """
        self._require_lh()
        self._require_plate()
        self._require_homed()
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
            "simulated": self.simulate,
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
            "simulated": self.simulate,
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
            "simulated": self.simulate,
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
            "simulated": self.simulate,
            "temperature_c": await hs.get_temperature(),
        }
