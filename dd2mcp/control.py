"""Hand the screen between the player's terminal and the game.

take_control remembers the window the player is looking at (their terminal), brings DD2 to the front
and waits for the focus-loss pause menu to close; hand_back brings that window back. DD2 opens its pause
menu (PauseType InGame) whenever it loses focus and closes it on focus gain, so both directions are
"switch, then poll PauseManager until it agrees" - no fixed sleeps."""
import json
import subprocess
import threading
import time

from . import game

PS = game.pathlib.Path(__file__).parent / "ps" / "windowd.ps1"
STATE = game.SCRATCH / "control.json"
ESC = 0x01
IN_GAME = 5  # app.PauseManager.PauseType.InGame: the pause menu

_proc = None
_lock = threading.Lock()


def win(cmd: str, timeout: float = 10) -> str:
    """One request to the persistent PowerShell window helper (started on first use, restarted if it died)."""
    global _proc
    with _lock:
        if _proc is None or _proc.poll() is not None:
            path = subprocess.run(["wslpath", "-w", str(PS)], capture_output=True, text=True).stdout.strip()
            _proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
            if _proc.stdout.readline().strip() != "ready":
                _proc = None
                return "window helper failed to start"
        _proc.stdin.write(cmd + "\n")
        _proc.stdin.flush()
        return _proc.stdout.readline().strip()


def pause_state() -> dict | None:
    """{'paused': bool, 'types': [PauseType ints]} from app.PauseManager, or None if the bridge can't answer."""
    res = game.eval_lua(
        "local pm = sdk.get_managed_singleton('app.PauseManager'); local l = pm:call('getCurrentPausedTypeList'); local t = {}\n"
        "for i = 0, l:call('get_Count') - 1 do t[#t + 1] = l:call('get_Item', i) end\n"
        "return json.dump_string({paused = pm:call('isPausedAny'), types = t})", timeout=3)
    if not res.get("ok") or not res.get("ret"):
        return None
    st = json.loads(res["ret"][0])
    st["types"] = st.get("types") or []  # an empty Lua table serialises as null
    return st


def _wait(cond, limit: float):
    """Poll pause_state until cond(state) holds or `limit` seconds pass; returns (state, held)."""
    end = time.monotonic() + limit
    while True:
        st = pause_state()
        if st is not None and cond(st):
            return st, True
        if time.monotonic() >= end:
            return st, False


def key(scan: int) -> str:
    return win(f"key {scan}")


def take_control(unpause: bool = True) -> str:
    if not game.running():
        return "game not running"
    t0 = time.monotonic()
    prev = win("foreground")
    game.SCRATCH.mkdir(parents=True, exist_ok=True)
    if prev.lstrip("-").isdigit():
        STATE.write_text(json.dumps({"cli_hwnd": int(prev), "at": time.time()}))
    out = [win("focusgame")]
    if not out[0].startswith("foreground"):
        return f"could not focus the game: {out[0]}"
    if unpause:
        # Focus gain does not close the pause menu, and in exclusive fullscreen the game stalls ~1.2 s on the
        # device reset; wait until the bridge answers (the game is live again), then Esc only if the menu is up.
        st, ok = _wait(lambda s: True, 4.0)
        for _ in range(3):
            if st is None or IN_GAME not in st["types"]:
                break
            key(ESC)
            st, ok = _wait(lambda s: IN_GAME not in s["types"], 0.6)
        if st is None:
            out.append("pause state unknown (bridge silent)")
        elif IN_GAME not in st["types"]:
            out.append("unpaused" if not st["paused"] else f"menu closed, still paused by types {st['types']}")
        else:
            out.append(f"still paused: {st}")
    return "; ".join(out) + f" ({time.monotonic() - t0:.2f}s)"


def hand_back() -> str:
    try:
        hwnd = json.loads(STATE.read_text())["cli_hwnd"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return "no saved terminal window (take_control was not used); switch back manually"
    t0 = time.monotonic()
    r = win(f"focus {hwnd}")
    st, ok = _wait(lambda s: s["paused"], 1.5)
    tail = "game paused" if ok else ("pause state unknown" if st is None else "game still running")
    return f"{r}; {tail} ({time.monotonic() - t0:.2f}s)"
