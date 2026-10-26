"""Hand the screen between the player's terminal and the game.

take_control remembers the window the player is looking at (their terminal), brings DD2 to the front
and closes the focus-loss pause menu; hand_back brings that window back. DD2 opens its pause menu
(PauseType InGame) whenever it loses focus, so the game pauses itself on hand-back."""
import json
import subprocess
import time

from . import game

PS = game.pathlib.Path(__file__).parent / "ps" / "window.ps1"
STATE = game.SCRATCH / "control.json"
ESC = 0x01
IN_GAME = 5  # app.PauseManager.PauseType.InGame: the pause menu


def _ps(*args: str) -> str:
    win = subprocess.run(["wslpath", "-w", str(PS)], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win, *args],
                       capture_output=True, text=True, timeout=30)
    return (r.stdout or r.stderr).strip()


def pause_state() -> dict | None:
    """{'paused': bool, 'types': [PauseType ints]} from app.PauseManager, or None if the bridge can't answer."""
    res = game.eval_lua(
        "local pm = sdk.get_managed_singleton('app.PauseManager'); local l = pm:call('getCurrentPausedTypeList'); local t = {}\n"
        "for i = 0, l:call('get_Count') - 1 do t[#t + 1] = l:call('get_Item', i) end\n"
        "return json.dump_string({paused = pm:call('isPausedAny'), types = t})", timeout=5)
    if not res.get("ok") or not res.get("ret"):
        return None
    st = json.loads(res["ret"][0])
    st["types"] = st.get("types") or []  # an empty Lua table serialises as null
    return st


def key(scan: int) -> str:
    return _ps("-Action", "key", "-Scan", str(scan))


def take_control(unpause: bool = True) -> str:
    if not game.running():
        return "game not running"
    prev = _ps("-Action", "foreground")
    game.SCRATCH.mkdir(parents=True, exist_ok=True)
    if prev.lstrip("-").isdigit():
        STATE.write_text(json.dumps({"cli_hwnd": int(prev), "at": time.time()}))
    out = [_ps("-Action", "focusgame")]
    if not out[0].startswith("foreground"):
        return f"could not focus the game: {out[0]}"
    if unpause:
        time.sleep(0.8)  # the focus-gain transition settles before the menu takes input
        for _ in range(3):
            st = pause_state()
            if st is None:
                out.append("pause state unknown (bridge silent)")
                break
            if IN_GAME not in st["types"]:
                out.append("unpaused" if not st["paused"] else f"running UI paused only by types {st['types']}")
                break
            key(ESC)
            time.sleep(0.8)
        else:
            out.append(f"still paused after 3 Esc presses: {pause_state()}")
    return "; ".join(out)


def hand_back() -> str:
    try:
        hwnd = json.loads(STATE.read_text())["cli_hwnd"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return "no saved terminal window (take_control was not used); switch back manually"
    r = _ps("-Action", "focus", "-Hwnd", str(hwnd))
    time.sleep(1.2)  # the focus-loss pause menu opens within about a second
    st = pause_state()
    tail = "" if st is None else (" game paused" if st["paused"] else " game still running")
    return r + ";" + tail
