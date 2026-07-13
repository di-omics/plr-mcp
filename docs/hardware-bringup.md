# Hardware bring-up: driving a real Hamilton STAR

This is the runbook for the first liquid-handling run on a real STAR through the
MCP server. Read it before you connect anything. The server ships in simulation;
the steps below are the deliberate ladder from "no motion" to "full cycle" so
nothing on the deck moves before you say so.

## The one rule that matters

`setup_deck` with `home=true` (or any full PyLabRobot setup on a STAR) HOMES the
channels and iSWAP. That is real motion. Everything below is built so you reach
that step on purpose, with a clear deck, and never by accident.

The server enforces this: with `home=false` (the default) the STAR backend does
a zero-motion connect and every liquid-handling tool stays blocked until you
home. You cannot aspirate on an un-homed instrument through this server.

## Before you touch it

1. **Run the server where the USB cable is.** The STAR talks over USB, so the
   MCP server has to run on the machine holding that cable, i.e. `starpi`, not a
   laptop across the network. Your MCP client connects to the server on the Pi.
2. **One PyLabRobot driver at a time.** Make sure no other PyLabRobot process,
   notebook, or script has the STAR open. Two drivers on one USB link is how you
   get garbage responses and unsafe state.
3. **Match the PyLabRobot version.** Confirm the PyLabRobot on `starpi` is the
   one you validated against. `connect_check` reads firmware identity, so a
   version or protocol mismatch shows up there before any motion.
4. **Start the server against the STAR backend:**

   ```bash
   PLR_MCP_BACKEND=star plr-mcp
   ```

   (or override per call with `setup_deck(backend="star")`).

## The ladder

### 1. Connect, zero motion

Call `connect_check`. It opens the USB link, reads machine configuration,
channel count, initialization status, and tip presence, then closes. It does
NOT move the arm.

Expect `ok: true`, `motion: "none"`, a `num_channels` that matches your
instrument, and `instrument_initialized` (whether the firmware reports itself
initialized). If this fails, stop: it is a link, version, or one-driver problem,
not something to push past.

**Check `tips_present`.** If any channel reports a tip (left over from an
aborted run), physically eject those tips from the channels before you home.
Homing with tips still mounted can drive a tipped channel into labware or the
deck. Re-run `connect_check` and confirm `tips_present` is all false before
moving on.

### 2. Build the deck, still zero motion

Call `setup_deck` with `home=false` (default) and the labware that is
**physically** on the deck:

```
setup_deck(
  home=false,
  tip_rack="hamilton_96_tiprack_1000uL_filter",  # the tips actually loaded
  plate="Cor_96_wellplate_360ul_Fb",             # the plate actually loaded
  tip_rail=1,                                      # the rail it sits on
  plate_rail=10,
)
```

Wrong labware or wrong rail here is what turns the first move into a crash. The
definition has to match the real tip type (Z height) and the real rail. The
response reports `homed: false` and reminds you the arm is not homed.

### 3. Clear the deck, then home

`home=true` MOVES the arm every time, whether or not `connect_check` reported
the instrument as initialized. Do not treat a reported-initialized instrument as
"already homed and safe" -- watch it on every home.

Physically confirm the deck is clear of anything the channels or iSWAP could hit,
and that no tips are left on the channels (step 1). Then, and only then:

```
setup_deck(home=true, tip_rack=..., plate=..., tip_rail=..., plate_rail=...)
```

This runs the full firmware init and homes the channels and iSWAP. Watch the
instrument. The response reports `homed: true` and
`motion: "homes channels and iSWAP"`. The `tip_rack` / `plate` / rail values are
software geometry describing what you will load; they are assigned after the
home motion, so homing itself does not touch deck positions.

After the home completes, physically place (or restore) your labware so it
matches the definitions and rails you passed. You do not need to call
`setup_deck` again; the session stays connected and homed. If you do re-run it,
the server first releases the open link so you never end up with two drivers on
the USB.

### 4. Full cycle

Now the liquid-handling tools unlock. Run the cycle one step at a time, watching
the deck on each:

```
pick_up_tips(wells="A1:H1")
aspirate(wells="A1:H1", volume=50)
dispense(wells="A1:H1", volume=50)
drop_tips(wells="A1:H1")
```

Start with a small volume and a single column. `deck_state` shows what the
server thinks is on the deck at any point.

## If a move looks wrong

Hit the physical e-stop / pause on the instrument. Do not try to "fix" a bad
move through the server. Once stopped, disconnect (stop the server process),
check the deck, and restart from step 1.

## Notes

- The zero-motion connect uses PyLabRobot's Hamilton base setup (the USB link
  plus the response reader) and deliberately skips the STAR firmware
  initialization that homes the arm. It is the same idea as calling
  `io.setup()` yourself.
- The instruments other than the liquid handler (plate reader, thermocycler,
  heater-shaker) run on simulation in this server. Wire real backends in
  `plr_mcp/lab.py` (the `_ensure_*` methods) before trusting them on hardware.
- Nothing here has been run on a physical STAR from this repo. Treat the first
  run as a supervised bring-up, not a walk-away.
