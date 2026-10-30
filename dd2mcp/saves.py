"""DD2 save snapshots: back up the whole win64_save folder, list snapshots, restore one.

DD2 keeps every slot plus the system file in one Steam Cloud folder, so a snapshot is the whole
folder. Restore refuses while the game runs and always snapshots the current state first."""
import hashlib
import json
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


# --- Scenarios: single-slot shelves for the test harness (verified in game 2026-10-29) ---
# 15 slots = 3 character units x 5 types (slot = unit*5 + type). A slot file only loads from the
# slot it was written to, and the game sees only files Steam has indexed, so a scenario goes back
# into its own slot, while the game is closed (Steam rescans at launch; mid-run swaps read as empty).
# Deleting a file outside the game does not stick: Steam Cloud downloads it again.
SCENARIOS = BACKUPS / "scenarios"
SLOT_TYPES = ["Auto", "Manual (Last Inn Rest)", "Back (unmoored-world restart)", "Clear", "AddAuto"]
TEST_UNITS = {int(u) for u in os.environ.get("DD2_TEST_UNITS", "0").split(",")}


def slot_file(slot: int) -> str:
    return "data000.bin" if slot == 0 else f"data{slot:03d}Slot.bin"


def _check_test_slot(slot: int) -> str | None:
    if not 0 <= slot < 15:
        return f"slot {slot} out of range 0-14"
    if slot // 5 not in TEST_UNITS:
        return f"refused: slot {slot} belongs to unit {slot // 5}; test units are {sorted(TEST_UNITS)}"
    return None


def scenario_list() -> str:
    lines = [f"scenarios in {SCENARIOS} (test units {sorted(TEST_UNITS)}):"]
    for d in sorted(SCENARIOS.iterdir()) if SCENARIOS.exists() else []:
        meta = d / "scenario.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            slot = m.get("source_slot")
            lines.append(f"  {d.name}: slot {slot} ({SLOT_TYPES[slot % 5] if slot is not None else '?'}), "
                         f"{m.get('area', '?')} - {m.get('notes', '')}")
    return "\n".join(lines)


def shelve(name: str, slot: int, area: str = "", notes: str = "") -> str:
    """Archive the live file of one slot as a named scenario."""
    if err := _check_test_slot(slot):
        return err
    live = game.saves_dir()
    src = live / slot_file(slot) if live else None
    if not src or not src.exists():
        return f"slot {slot} has no file ({slot_file(slot)})"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    dst = SCENARIOS / safe
    if dst.exists():
        return f"scenario {safe!r} exists; pick another name"
    dst.mkdir(parents=True)
    shutil.copy2(src, dst / "slot.bin")
    meta = {"name": safe, "source_slot": slot, "area": area, "notes": notes,
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            "file_mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(src.stat().st_mtime)),
            "shelved": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (dst / "scenario.json").write_text(json.dumps(meta, indent=1) + "\n")
    return f"shelved slot {slot} as {safe}"


def unshelve(name: str) -> str:
    """Put a scenario back into its own slot. Game must be closed; the live folder is snapshotted first."""
    if game.running():
        return "refused: quit DD2 first (Steam only picks up a swapped slot at launch)"
    d = SCENARIOS / name
    meta = d / "scenario.json"
    if not meta.exists():
        return f"no scenario {name!r}"
    slot = json.loads(meta.read_text())["source_slot"]
    if err := _check_test_slot(slot):
        return err
    live = game.saves_dir()
    if not live:
        return "DD2 save folder not found"
    before = backup(f"pre-unshelve-{name}")
    shutil.copy2(d / "slot.bin", live / slot_file(slot))
    os.utime(live / slot_file(slot))  # newer than the cloud copy, so Steam uploads instead of restoring
    return (f"{before}\nunshelved {name} into slot {slot} ({slot_file(slot)}). The title menu's cards come "
            f"from cached system data and may show old details; the load itself uses this file.")
