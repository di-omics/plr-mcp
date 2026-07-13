"""Run validated starlab protocol scripts as MCP tools.

These wrappers do NOT reimplement any protocol. They import the operator's
existing, hardware-validated scripts (from the plr-tested starlab_live tree) and
call the script's own protocol functions under a selectable backend, preserving
its tuned geometry, volumes, tip logic, and safety behavior.

Point PLR_MCP_STARLAB_DIR at the starlab_live directory. On starpi that is the
on-Pi plr-tested checkout, where a 'star' backend drives the real instrument.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

# Default location of the validated scripts; override with PLR_MCP_STARLAB_DIR.
_DEFAULT_STARLAB_DIR = os.path.expanduser("~/Downloads/plr-tested/hamilton-star/starlab_live")

AMPSEQ_PCR1_SCRIPT = "01_ampseq_pcr1_mastermix_col1.py"


def starlab_dir() -> str:
    return os.environ.get("PLR_MCP_STARLAB_DIR", _DEFAULT_STARLAB_DIR)


def ampseq_pcr1_script_path() -> str:
    return os.path.join(starlab_dir(), AMPSEQ_PCR1_SCRIPT)


def _load_script(filename: str) -> Any:
    path = os.path.join(starlab_dir(), filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"validated script not found at {path}. Set PLR_MCP_STARLAB_DIR to your "
            "plr-tested starlab_live directory (on starpi, the on-Pi checkout)."
        )
    spec = importlib.util.spec_from_file_location("starlab_ampseq_pcr1", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load a module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run_ampseq_pcr1(
    backend: str = "chatterbox",
    mode: str = "deck",
    return_tips: bool = False,
    tip_col: int = 1,
    confirm: bool = False,
) -> dict:
    """Run the validated targeted PCR PCR1 master-mix script (starlab script 01).

    Imports the real script and calls its assign_deck / transfer_pcr1_master_mix
    functions unchanged, so the tuned geometry and volumes are exactly the bench
    values.

    backend: 'chatterbox' (dry-run, no hardware) or 'star' (real Hamilton STAR).
    mode: 'deck' (assign the deck only) or 'pcr1-mm' (the 22.5 uL x8 transfer).
    return_tips: True returns tips (observation only); False discards (production).
    confirm: must be True for the star backend, since a real run homes the arm
      and moves liquid (human-gated).
    """
    backend = backend.lower()
    if backend not in ("chatterbox", "star"):
        raise ValueError("backend must be 'chatterbox' or 'star'")
    if mode not in ("deck", "pcr1-mm"):
        raise ValueError("mode must be 'deck' or 'pcr1-mm'")
    if not (1 <= tip_col <= 12):
        raise ValueError("tip_col must be 1..12")
    if backend == "star" and not confirm:
        return {
            "ok": False,
            "backend": "star",
            "simulated": False,
            "notes": [
                "real STAR run is human-gated. Re-call with confirm=true, with a "
                "person watching the deck, after a clean chatterbox dry-run and a "
                "mode='deck' check. Note lh.setup homes the arm on connect."
            ],
        }

    mod = _load_script(AMPSEQ_PCR1_SCRIPT)

    from pylabrobot.liquid_handling import LiquidHandler
    from pylabrobot.resources.hamilton import STARDeck

    if backend == "chatterbox":
        from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend

        lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(num_channels=8), deck=STARDeck())
        await lh.setup()
    else:
        from pylabrobot.liquid_handling.backends import STARBackend

        lh = LiquidHandler(backend=STARBackend(), deck=STARDeck())
        await lh.setup(skip_autoload=True)  # homes the arm on the real instrument

    executed = []
    try:
        r = await mod.assign_deck(lh)
        executed.append("assign_deck")
        if mode == "pcr1-mm":
            await mod.transfer_pcr1_master_mix(lh, r, discard_tips=not return_tips, tip_col=tip_col)
            executed.append("transfer_pcr1_master_mix")
    finally:
        if backend == "star":
            try:
                await lh.backend.park_iswap()
            except Exception:
                pass
        try:
            await lh.stop()
        except Exception:
            pass

    return {
        "ok": True,
        "protocol": "ampseq_pcr1_mastermix_col1",
        "script": AMPSEQ_PCR1_SCRIPT,
        "backend": backend,
        "simulated": backend == "chatterbox",
        "mode": mode,
        "volume_ul": getattr(mod, "VOL_PCR1_MASTER_MIX", None),
        "tips": "returned" if return_tips else "discarded",
        "tip_col": tip_col,
        "executed": executed,
        "source": "rail35 pos1 col1",
        "destination": "rail35 pos0 col1",
    }
