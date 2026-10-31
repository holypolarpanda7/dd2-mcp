"""Load a shelved save scenario end to end: unshelve, launch, arm the save guard, pass the boot screens,
start the slot through app.MainFlowManager, and verify the loaded state (verified in game 2026-10-29).

The title menu's own load is a single requestStartType call (Latest -> Auto slot, Base -> Last Inn Rest
slot of the current character unit), so only the boot screens need key presses."""
import json
import subprocess
import time

from . import control, game, saves

PHASE_TITLE_MENU, PHASE_IN_GAME = 8, 27
START_TYPE = {0: 2, 1: 3}  # loadable slot types -> app.MainFlowManager.StartType the menu uses (Latest, Base)
ENTER, F_KEY = 0x1C, 0x21


def _eval(code: str, phase: str = "frame", timeout: float = 5):
    res = game.eval_lua(code, phase=phase, timeout=timeout)
    try:
        return json.loads(res["ret"][0]) if res.get("ok") and res.get("ret") else None
    except json.JSONDecodeError:  # "nil" and other non-JSON renderings
        return None


def _flow_phase():
    return _eval('local m = sdk.get_managed_singleton("app.MainFlowManager") return m and m:call("get_FlowPhase")')


def _wait(cond, limit: float, step: float = 1.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


def _kill_game() -> None:
    subprocess.run(["taskkill.exe", "/IM", "DD2.exe", "/F"], capture_output=True)
    _wait(lambda: not game.running(), 30)
    time.sleep(2)  # let Steam finish its exit sync before the slot file is replaced


def load(name: str, restart: bool = False) -> str:
    log = []
    meta_path = saves.SCENARIOS / name / "scenario.json"
    if not meta_path.exists():
        return f"no scenario {name!r}"
    meta = json.loads(meta_path.read_text())
    slot = meta["source_slot"]
    if slot % 5 not in START_TYPE:
        return f"slot {slot} is a {saves.SLOT_TYPES[slot % 5]} slot; only Auto and Last Inn Rest load automatically"

    if game.running():
        if not restart:
            return "refused: DD2 is running; pass restart=true to quit it (unsaved progress is lost)"
        _kill_game()
        log.append("quit DD2")
    log.append(saves.unshelve(name))
    # Saves made with a mod active may not load without it (a Wizard save crashed in calcMaxWeightWithBuff,
    # 2026-10-31), so the scenario names the projects to deploy before launch.
    for project in meta.get("deploy") or []:
        log.append(game.deploy(project))
    state = _boot_and_start(slot, log)
    if state is None:
        return "\n".join(log)
    t = state["play_time_s"]
    pos = [round(state["pos"][k], 1) for k in ("ux", "uy", "uz")]
    expected = meta.get("play_time_s")
    if expected is None:
        # Fingerprint from the first load; later loads must land on it (a few seconds pass before the read).
        meta["play_time_s"], meta["universal_pos"] = t, pos
        meta_path.write_text(json.dumps(meta, indent=1) + "\n")
        log.append("recorded play time and position as this scenario's fingerprint")
    else:
        dist = sum((a - b) ** 2 for a, b in zip(pos, meta.get("universal_pos", pos))) ** 0.5
        if abs(t - expected) > 60 or dist > 10:
            log.append(f"MISMATCH: expected play time ~{expected}s at {meta.get('universal_pos')}, got {t}s at {pos}; "
                       "the slot did not load this scenario")
        else:
            log.append(f"verified: play time {t - expected:+d}s and {dist:.1f} m from the scenario fingerprint")
    blocked = [e for e in state.get("guard") or [] if not e["allowed"]]
    if blocked:
        log.append(f"guard blocked {len(blocked)} save(s): " + ", ".join(f"slot {e['slot']}" for e in blocked))
    return "\n".join(log)


def _boot_and_start(slot: int, log: list) -> dict | None:
    """Launch DD2, arm the save guard (test units only, no autosaves), pass the boot screens and start `slot`
    from the title menu. Returns {play_time_s, pos, guard} once in game, or None (reason appended to log)."""
    game.launch()
    if not _wait(lambda: _eval('return sdk.get_managed_singleton("app.SaveDataManager") ~= nil'), 180, 2):
        log.append("bridge never answered after launch (dd2_log)")
        return None
    log.append("armed: " + game.format_result(game.eval_lua(
        'D.quiet.on = true return D.saveguard.arm({ units = { %s }, autosave = false })' % ", ".join(map(str, sorted(saves.TEST_UNITS))))))

    # Boot screens: "START GAME" takes Enter, the autosave notice takes F. Check before every press so
    # nothing reaches the title menu itself.
    end = time.monotonic() + 120
    presses = 0
    while (ph := _flow_phase()) != PHASE_TITLE_MENU:
        if time.monotonic() > end:
            log.append(f"title menu not reached (phase {ph}) after {presses} key presses")
            return None
        control.win("focusgame")
        control.key(ENTER if presses % 2 == 0 else F_KEY)
        presses += 1
        time.sleep(1.5)
    # The title phase starts on the "START GAME" splash; Enter opens the menu (and does nothing once it is open).
    control.win("focusgame")
    control.key(ENTER)
    time.sleep(2.5)
    log.append(f"title menu after {presses + 1} key presses")

    unit = _eval('return sdk.get_managed_singleton("app.GameSystem"):call("get_CachedSlotAuto") // 5')
    if unit != slot // 5:
        # Same effect as the title's Change Save Slot (verified 2026-10-29: unit 1 -> 0, auto slot 5 -> 0).
        unit = _eval('local g = sdk.get_managed_singleton("app.GameSystem") '
                     f'D.call(g, "setUnitNumber(System.Int32)", {slot // 5}) return g:call("get_CachedSlotAuto") // 5',
                     phase="update")
        if unit != slot // 5:
            log.append(f"could not switch the character unit to {slot // 5} (still {unit})")
            return None
        log.append(f"switched character unit to {unit}")
    # Calling MainFlowManager.requestStartType ourselves throws (it needs title-UI state) and crashed the game
    # once from the update phase; forcing Phase.RequestLoadData hangs at StartLoad. So point the unit's
    # "latest save" at the slot type and confirm the title's default first item, Load from Last Saved Data.
    latest = _eval('local g = sdk.get_managed_singleton("app.GameSystem") '
                   f'D.call(g, "setLatestSaveSlotNum(System.Int32)", {slot % 5}) '
                   f'return g:call("getLatestSaveSlotNum(System.Int32)", {slot // 5})')
    if latest != slot % 5:
        log.append(f"could not point the latest save at slot type {slot % 5} (got {latest})")
        return None
    control.win("focusgame")
    control.key(F_KEY)
    if not _wait(lambda: (_flow_phase() or 0) > PHASE_TITLE_MENU, 15, 0.5):
        log.append("title menu did not start a load after F (menu not on its first item?)")
        return None
    if not _wait(lambda: _flow_phase() == PHASE_IN_GAME and _eval("return D.player_pos() ~= nil"), 240, 2):
        log.append(f"not in game after 240 s (phase {_flow_phase()})")
        return None

    state = _eval('local t = math.floor(sdk.get_managed_singleton("app.TimeManager"):call("get_WholePlayTime")) '
                  'return { play_time_s = t, pos = D.player_pos(), guard = D.saveguard.log }')
    t = state["play_time_s"]
    log.append(f"in game: play time {t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}, "
               f"universal pos {[round(state['pos'][k], 1) for k in ('ux', 'uy', 'uz')]}")
    return state


def load_slot(slot: int, restart: bool = False) -> str:
    """Load a slot exactly as it is on disk (no unshelve), with the save guard armed for the test units only,
    so a slot outside them (the play-through) is read but can never be written. For checking a save."""
    if slot % 5 not in START_TYPE:
        return f"slot {slot} is a {saves.SLOT_TYPES[slot % 5]} slot; only Auto and Last Inn Rest load automatically"
    log = []
    if game.running():
        if not restart:
            return "refused: DD2 is running; pass restart=true to quit it (unsaved progress is lost)"
        _kill_game()
        log.append("quit DD2")
    state = _boot_and_start(slot, log)
    if state is None:
        return "\n".join(log)
    blocked = [e for e in state.get("guard") or [] if not e["allowed"]]
    if blocked:
        log.append(f"guard blocked {len(blocked)} save(s): " + ", ".join(f"slot {e['slot']}" for e in blocked))
    return "\n".join(log)
