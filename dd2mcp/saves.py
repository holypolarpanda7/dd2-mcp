"""DD2 save snapshots: back up the whole win64_save folder, list snapshots, restore one.

DD2 keeps every slot plus the system file in one Steam Cloud folder, so a snapshot is the whole
folder. Restore refuses while the game runs and always snapshots the current state first."""
import hashlib
import os
import pathlib
import shutil
import time

from . import game

BACKUPS = pathlib.Path(os.environ.get("DD2_SAVE_BACKUPS", "/mnt/d/DD2Modding/SaveBackups"))


def _files(d: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix == ".bin")


def listing() -> str:
    src = game.saves_dir()
    lines = [f"live: {src}"]
    if src:
        for f in _files(src):
            lines.append(f"  {f.name:18} {f.stat().st_size:>9}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))}")
    lines.append(f"snapshots in {BACKUPS}:")
    if BACKUPS.exists():
        for d in sorted(BACKUPS.iterdir(), reverse=True):
            if d.is_dir():
                lines.append(f"  {d.name}  ({len(_files(d))} files)")
    return "\n".join(lines)


def backup(label: str = "manual") -> str:
    src = game.saves_dir()
    if not src:
        return "DD2 save folder not found"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
    dst = BACKUPS / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{safe}"
    dst.mkdir(parents=True)
    sums = []
    for f in _files(src):
        shutil.copy2(f, dst / f.name)
        sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (dst / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return f"snapshot {dst.name}: {len(sums)} files"


def restore(name: str) -> str:
    if game.running():
        return "refused: quit DD2 first (the game rewrites saves on exit and autosave)"
    src = BACKUPS / name
    live = game.saves_dir()
    if not src.is_dir() or not live:
        return f"no snapshot {name!r}" if live else "DD2 save folder not found"
    before = backup(f"pre-restore-of-{name}")
    for f in _files(live):
        f.unlink()
    for f in _files(src):
        shutil.copy2(f, live / f.name)
    return (f"{before}\nrestored {name}. Steam Cloud may offer a conflict on next launch: "
            "pick the LOCAL copy, or the cloud copy will overwrite this restore.")
