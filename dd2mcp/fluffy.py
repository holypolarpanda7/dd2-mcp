"""Drive Fluffy Mod Manager (Modmanager.exe in the game folder) for release-install tests.

Fluffy has no CLI, so this clicks its window. Layout measured on 3.084 at its default 1280x750 window
(2026-10-29): the first mod row's enable box sits at (+678, +223) from the window rect's top-left, rows are 37 px
apart in the default list view. A click toggles the mod: Fluffy installs (scripts to <game>/reframework, .pak to
<game>/pak_mods as x0000-<name>.pak with REFramework's pak loader) or uninstalls immediately.
Verify with installed_files() rather than trusting the click."""
import subprocess
import time

from . import control, game

EXE = "Modmanager.exe"
PROC = "Modmanager"
FIRST_ROW = (678, 223)
ROW_STEP = 37


def running() -> bool:
    out = subprocess.run(["tasklist.exe", "/FI", f"IMAGENAME eq {EXE}", "/NH"], capture_output=True, text=True).stdout
    return EXE.lower() in out.lower()


def launch(wait: float = 30) -> str:
    if not (game.GAME / EXE).exists():
        return f"Fluffy is not installed in {game.GAME}"
    if not running():
        win = subprocess.run(["wslpath", "-w", str(game.GAME)], capture_output=True, text=True).stdout.strip()
        subprocess.Popen(["cmd.exe", "/c", "start", "", "/D", win, EXE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if control.win(f"focusproc {PROC}").startswith("foreground"):
            time.sleep(2)  # mod list loads after the window appears
            return "Fluffy open"
        time.sleep(1)
    return "Fluffy window did not appear"


def close() -> str:
    subprocess.run(["taskkill.exe", "/IM", EXE], capture_output=True)
    for _ in range(20):
        if not running():
            return "Fluffy closed"
        time.sleep(0.5)
    subprocess.run(["taskkill.exe", "/IM", EXE, "/F"], capture_output=True)
    return "Fluffy killed"


def toggle(row: int = 0) -> str:
    """Click the enable box of mod row `row` (0 = first) in Fluffy's mod list."""
    r = control.win(f"focusproc {PROC}")
    if not r.startswith("foreground"):
        return f"Fluffy not focused: {r}"
    rect = control.win(f"rect {PROC}").split()
    if len(rect) != 4:
        return f"no Fluffy window rect: {rect}"
    left, top = int(rect[0]), int(rect[1])
    x, y = left + FIRST_ROW[0], top + FIRST_ROW[1] + row * ROW_STEP
    control.win(f"click {x} {y}")
    time.sleep(3)
    return f"clicked row {row} at {x},{y}"


def installed_files(name_part: str) -> dict:
    """Game files a mod install would leave: pak_mods entries and reframework scripts matching name_part."""
    paks = sorted(p.name for p in (game.GAME / "pak_mods").glob("*.pak") if name_part.lower() in p.name.lower()) \
        if (game.GAME / "pak_mods").is_dir() else []
    scripts = sorted(str(p.relative_to(game.GAME)) for p in (game.REF / "autorun").rglob("*")
                     if p.is_file() and name_part.lower() in str(p).lower())
    return {"pak_mods": paks, "scripts": scripts}
