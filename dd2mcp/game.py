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


def reload_bridge() -> str:
    """Copy the bridge into the game and re-run it in place (no restart). Its callbacks are registered once
    per game run and call through D, so a reload swaps the logic; the running version must be 0.5.0+
    (older ones forget the last command id and would re-run the reload)."""
    msg = install_bridge()
    if not running():
        return msg
    win = subprocess.run(["wslpath", "-w", str(REF / "autorun" / "dd2mcp_bridge.lua")], capture_output=True, text=True).stdout.strip()
    res = eval_lua(f"local f = assert(loadfile({json.dumps(win)})); f(); return D.version", timeout=10)
    return msg + "\nreloaded: " + format_result(res)


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


def crash_info() -> str | None:
    """The crash record of the current/last game run from the REFramework log, if any."""
    if not LOG.exists():
        return None
    text = LOG.read_text(errors="replace").splitlines()
    for i in range(len(text) - 1, -1, -1):
        if "REFramework entry" in text[i] or "Logging initialized" in text[i]:
            text = text[i:]
            break
    for i, line in enumerate(text):
        if "Exception occurred" in line:
            return "\n".join(text[i:i + 6])
    return None


def window_titles_ps(proc: str) -> str:
    """PowerShell that prints the title of every top-level window owned by the named process."""
    return ("$p = Get-Process %s -ErrorAction SilentlyContinue; if (-not $p) { exit }; "
            "Add-Type -Namespace W -Name U -MemberDefinition '"
            "public delegate bool EP(System.IntPtr h, System.IntPtr l);"
            "[System.Runtime.InteropServices.DllImport(\"user32.dll\")] public static extern bool EnumWindows(EP f, System.IntPtr l);"
            "[System.Runtime.InteropServices.DllImport(\"user32.dll\")] public static extern int GetWindowThreadProcessId(System.IntPtr h, out int pid);"
            "[System.Runtime.InteropServices.DllImport(\"user32.dll\", CharSet=System.Runtime.InteropServices.CharSet.Unicode)]"
            " public static extern int GetWindowText(System.IntPtr h, System.Text.StringBuilder s, int n);'; "
            "$ids = @($p | ForEach-Object { $_.Id }); $t = New-Object System.Collections.ArrayList; "
            "[void][W.U]::EnumWindows({ param($h, $l) $o = 0; [void][W.U]::GetWindowThreadProcessId($h, [ref]$o); "
            "if ($ids -contains $o) { $sb = New-Object System.Text.StringBuilder 256; [void][W.U]::GetWindowText($h, $sb, 256); "
            "if ($sb.Length) { [void]$t.Add($sb.ToString()) } }; $true }, [System.IntPtr]::Zero); $t") % proc


def crash_dialog() -> str | None:
    """Title of DD2's crash dialog ("Dragon's Dogma 2 - Crash") if one is open, else None. A crashed game stays in the
    process list until the dialog is closed, so running() alone can't tell (2026-11-06: a session hung on it)."""
    # every top-level window of DD2.exe: the dialog is not the process's MainWindowTitle
    ps = window_titles_ps("DD2")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if "crash" in line.lower():
            return line.strip()
    return None


def launch(wait: float = 0) -> str:
    """Start DD2 through Steam. wait > 0: wait up to that many seconds until the Lua bridge answers (the title
    screen), returning early with CRASHED if the crash dialog opens or the game exits."""
    if running():
        return f"already running (pid {running()})"
    subprocess.run(["cmd.exe", "/c", "start", "", f"steam://rungameid/{APP_ID}"], capture_output=True)
    if wait <= 0:
        return "launched via Steam; load a save, then dd2_status"
    t0 = time.time()
    seen = False
    while time.time() - t0 < wait:
        time.sleep(5)
        pid = running()
        seen = seen or bool(pid)
        title = crash_dialog() if pid else None
        if title:
            return f"CRASHED during boot after {time.time() - t0:.0f}s ({title}); dd2_quit closes it"
        if seen and not pid:
            return f"CRASHED: the game exited after {time.time() - t0:.0f}s"
        if pid and eval_lua("return 1", timeout=3).get("ok"):
            return f"up: bridge answers after {time.time() - t0:.0f}s (pid {pid}); load a save next"
    return f"timeout after {wait:.0f}s: game {'running, bridge silent' if running() else 'not running'}"


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
    """Copy <project>/src/reframework/** into the game's reframework folder (only files the project owns), and
    <project>/build/*.pak into <game>/pak_mods (REFramework's IntegrityCheckBypass_LoadPakDirectory injects paks
    from there; a loose re_chunk_000.pak.patch_NNN.pak is ignored, 2026-10-29)."""
    root = pathlib.Path(project)
    src = root / "src" / "reframework"
    if not src.is_dir():
        return f"no src/reframework in {project}"
    n = 0
    for f in src.rglob("*"):
        if f.is_file() and not f.name.startswith("."):  # .gitkeep and friends stay in the repo
            dst = REF / f.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            n += 1
    out = [f"deployed {n} files from {src}"]
    paks = sorted((root / "build").glob("*.pak")) if (root / "build").is_dir() else []
    if paks:
        (GAME / "pak_mods").mkdir(exist_ok=True)
        copied, locked = [], []
        for p in paks:
            try:
                shutil.copy2(p, GAME / "pak_mods" / p.name)
                copied.append(p.name)
            except PermissionError:  # the running game holds its paks open (2026-10-30)
                locked.append(p.name)
        if copied:
            out.append("paks -> pak_mods: " + ", ".join(copied) + " (loaded at the next game start)")
        if locked:
            out.append("pak in use by the running game, not replaced: " + ", ".join(locked))
    return "\n".join(out)


def undeploy(project: str) -> str:
    """Move a project's deployed files (its src/reframework/** and build/*.pak counterparts in the game) out of the
    game into SaveBackups/undeployed-<project>-<timestamp>, so dev builds never run on the player's own saves. The game
    must be closed (it holds paks open)."""
    import time
    from .saves import BACKUPS
    if running():
        return "close the game first (dd2_quit): it holds the paks open"
    root = pathlib.Path(project)
    src = root / "src" / "reframework"
    park = BACKUPS / f"undeployed-{root.name}-{time.strftime('%Y-%m-%d_%H%M%S')}"
    moved = []
    targets = [(REF / f.relative_to(src), pathlib.Path("reframework") / f.relative_to(src))
               for f in src.rglob("*") if f.is_file() and not f.name.startswith(".")] if src.is_dir() else []
    if (root / "build").is_dir():
        targets += [(GAME / "pak_mods" / p.name, pathlib.Path("pak_mods") / p.name) for p in (root / "build").glob("*.pak")]
    for live, rel in targets:
        if live.exists():
            dst = park / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(live), dst)
            moved.append(str(rel))
    for d in sorted({live.parent for live, _ in targets}, key=lambda x: -len(x.parts)):
        # only the mod's own folders (reframework/autorun/wizard), never autorun, data or pak_mods themselves
        if d.is_relative_to(REF) and len(d.relative_to(REF).parts) >= 2 and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return f"moved {len(moved)} files to {park}" + "".join(f"\n  {m}" for m in moved) if moved else "nothing deployed"


def saves_dir() -> pathlib.Path | None:
    hits = glob.glob(f"/mnt/c/Program Files (x86)/Steam/userdata/*/{APP_ID}/remote/win64_save")
    return pathlib.Path(hits[0]) if hits else None
