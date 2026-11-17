"""Screen burst capture with input driven during it: what the player SAW, frame by frame (camera feel, flicker, snaps).
Game Bar records the focused window (it recorded the terminal twice, 2026-11-16); this records the screen."""
import pathlib
import re
import shutil
import subprocess
import threading
import time

from . import game

PS = pathlib.Path(__file__).parent / "ps" / "burst_capture.ps1"


def _winpath(p) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True).stdout.strip()


def run_actions(actions: str):
    """'down:l wait:1.2 up:l press:45 pan:200,0' -> control calls (non-blocking down/up, so they can overlap)."""
    from . import control
    for tok in actions.split():
        op, _, arg = tok.partition(":")
        if op == "wait":
            time.sleep(float(arg))
        elif op == "down":
            control.down(arg)
        elif op == "up":
            control.up(arg)
        elif op == "press":
            control.down(arg); time.sleep(0.3); control.up(arg)
        elif op == "pan":
            dx, _, dy = arg.partition(",")
            control.pan(int(dx), int(dy or 0))
        else:
            raise ValueError(f"unknown action {tok!r} (down:, up:, press:, wait:, pan:)")


def capture(seconds: float = 6.0, actions: str = "", lead: float = 0.5, keep_frames: bool = False, sheet_every: int = 3) -> str:
    import numpy as np
    from PIL import Image, ImageDraw
    out = game.SCRATCH / f"capture_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    if actions:
        from . import control
        control.take_control()
        time.sleep(0.3)
    p = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _winpath(PS),
                          "-secs", str(seconds), "-out", _winpath(out)], stdout=subprocess.PIPE, text=True)
    p.stdout.readline()                      # "ready": the capture loop starts now (powershell start-up takes ~1 s)
    t0 = time.time()
    err = []
    if actions:
        def go():
            try:
                time.sleep(lead)
                run_actions(actions)
            except Exception as e:           # noqa: BLE001 - reported below
                err.append(str(e))
        th = threading.Thread(target=go)
        th.start()
    p.communicate(timeout=seconds + 120)
    if actions:
        th.join()
    fs = sorted(out.glob("f*.png"))
    if len(fs) < 2:
        return f"capture failed ({len(fs)} frames) in {out}"
    ts = [int(re.search(r"_(\d+)\.png$", f.name).group(1)) for f in fs]
    # motion of the BACKGROUND (top strip + right quarter): a camera move is a bell over many frames, a snap one spike
    diffs, prev = [], None
    for f in fs:
        a = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        bg = np.concatenate([a[: a.shape[0] // 10, :].ravel(), a[:, int(a.shape[1] * 0.75):].ravel()])
        diffs.append(0.0 if prev is None else float(np.abs(bg - prev).mean()))
        prev = bg
    med = float(np.median(diffs[1:])) or 0.1
    lines = [f"{len(fs)} frames in {ts[-1] / 1000:.1f} s ({len(fs) / (ts[-1] / 1000):.0f} fps); actions started at "
             f"{lead * 1000:.0f} ms. Background motion per frame (median {med:.1f}); frames above 2x median:"]
    for i, d in enumerate(diffs):
        if d > 2 * med:
            spike = " SPIKE" if d > 2.5 * max(diffs[i - 1] if i else 0, diffs[i + 1] if i + 1 < len(diffs) else 0, med) else ""
            lines.append(f"  f{i:04d} {ts[i]:6d} ms {d:5.1f} {'#' * min(int(d * 2), 60)}{spike}")
    # contact sheet
    sel = list(range(0, len(fs), max(1, sheet_every)))[:48]
    w, h = Image.open(fs[0]).size
    w2, h2 = w // 2, h // 2
    cols = 6
    sheet = Image.new("RGB", (cols * w2, ((len(sel) + cols - 1) // cols) * h2))
    for k, i in enumerate(sel):
        im = Image.open(fs[i]).resize((w2, h2))
        ImageDraw.Draw(im).text((4, 4), f"f{i:04d} {ts[i]}ms", fill="yellow")
        sheet.paste(im, ((k % cols) * w2, (k // cols) * h2))
    sp = game.SCRATCH / f"{out.name}_sheet.png"
    sheet.save(sp)
    if keep_frames:
        lines.append(f"frames kept: {out} (they show the whole desktop: delete when done)")
    else:
        shutil.rmtree(out, ignore_errors=True)
    if err:
        lines.append("action error: " + "; ".join(err))
    lines.append(f"contact sheet (every {sheet_every} frames): {sp} (whole desktop: delete when done)")
    return "\n".join(lines)
