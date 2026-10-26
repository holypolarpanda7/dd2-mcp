"""Dragon's Dogma 2 install, process, log, screenshot and eval-bridge helpers."""
import glob
import json
import os
import pathlib
import shutil
import subprocess
import time

APP_ID = "2054970"
GAME = pathlib.Path(os.environ.get("DD2_GAME", "/mnt/d/SteamLibrary/steamapps/common/Dragons Dogma 2"))
REF = GAME / "reframework"
BRIDGE_DIR = REF / "data" / "dd2mcp"
LOG = GAME / "re2_framework_log.txt"
LUA_SRC = pathlib.Path(__file__).parent / "lua" / "dd2mcp_bridge.lua"
SCRATCH = pathlib.Path(os.environ.get("DD2_SCRATCH", pathlib.Path.home() / ".cache" / "dd2-mcp"))


def running() -> int | None:
    """PID of DD2.exe, or None."""
    try:
        out = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq DD2.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == "dd2.exe":
            return int(parts[1])
    return None


def refw_revision() -> str | None:
    p = GAME / "reframework_revision.txt"
    return p.read_text().strip() if p.exists() else None


def bridge_installed() -> bool:
    return (REF / "autorun" / "dd2mcp_bridge.lua").exists()


def install_bridge() -> str:
    if not (GAME / "dinput8.dll").exists():
        return "REFramework (dinput8.dll) is not installed in the game folder"
    (REF / "autorun").mkdir(parents=True, exist_ok=True)
    shutil.copy2(LUA_SRC, REF / "autorun" / "dd2mcp_bridge.lua")
    return "bridge copied to reframework/autorun; it loads on the next game start or REFramework script reset"


def eval_lua(code: str, phase: str = "frame", timeout: float = 15) -> dict:
    """Run a Lua chunk in the game through the bridge. phase='update' runs it inside UpdateBehavior."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    cid = str(time.time_ns())
    header = f"--id:{cid}\n" + ("--phase:update\n" if phase == "update" else "")
    (BRIDGE_DIR / "in.lua").write_text(header + code + "\n")
    res_path = BRIDGE_DIR / "out.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = json.loads(res_path.read_text())
            if res.get("id") == cid:
                return res
        except (OSError, json.JSONDecodeError):  # 9p mount: a read can race the game's write
            pass
        time.sleep(0.02)
    hint = "game not running" if not running() else (
        "bridge not loaded (dd2_install_bridge, then restart the game)" if not bridge_installed()
        else "no answer: game paused in the background for update-phase calls, loading, or the bridge errored (dd2_log)")
    return {"id": cid, "ok": False, "err": f"timeout after {timeout}s: {hint}"}


def format_result(res: dict) -> str:
    lines = list(res.get("out") or [])
    lines += [f"=> {r}" for r in res.get("ret") or []]
    if not res.get("ok"):
        lines.append(f"ERROR: {res.get('err')}")
    return "\n".join(lines) if lines else "(no output)"


def log_tail(pattern: str | None = None, lines: int = 60, since_start: bool = True) -> str:
    if not LOG.exists():
        return "no REFramework log"
    text = LOG.read_text(errors="replace").splitlines()
    if since_start:  # only the current game run: REFramework logs a fresh header on start
        for i in range(len(text) - 1, -1, -1):
            if "REFramework entry" in text[i] or "Logging initialized" in text[i]:
                text = text[i:]
                break
    if pattern:
        pat = pattern.lower()
        text = [t for t in text if pat in t.lower()]
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(LOG.stat().st_mtime))
    return f"log modified {mtime}\n" + "\n".join(text[-lines:])


def launch() -> str:
    if running():
        return f"already running (pid {running()})"
    subprocess.run(["cmd.exe", "/c", "start", "", f"steam://rungameid/{APP_ID}"], capture_output=True)
    return "launched via Steam; load a save, then dd2_status"


def screenshot() -> str:
    """Primary-screen PNG; returns its path (open it with an image-capable Read)."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = SCRATCH / f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    win = subprocess.run(["wslpath", "-w", str(out)], capture_output=True, text=True).stdout.strip()
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
          "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
          "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
          "$g=[System.Drawing.Graphics]::FromImage($bmp); "
          "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
          f"$bmp.Save('{win}')")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    return str(out) if out.exists() else "screenshot failed"


def deploy(project: str) -> str:
    """Copy <project>/src/reframework/** into the game's reframework folder (only files the project owns)."""
    src = pathlib.Path(project) / "src" / "reframework"
    if not src.is_dir():
        return f"no src/reframework in {project}"
    n = 0
    for f in src.rglob("*"):
        if f.is_file():
            dst = REF / f.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            n += 1
    return f"deployed {n} files from {src}"


def saves_dir() -> pathlib.Path | None:
    hits = glob.glob(f"/mnt/c/Program Files (x86)/Steam/userdata/*/{APP_ID}/remote/win64_save")
    return pathlib.Path(hits[0]) if hits else None
