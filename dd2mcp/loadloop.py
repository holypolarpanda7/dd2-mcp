"""Repeated boot + save-load cycles that keep what each crash leaves behind (crash RCA, 2026-11-12).

One run: (optionally) the project deployed, `load_slot` with a restart (quit, launch, guard, title menu, load), then
`watch` seconds alive check. A crash keeps the REFramework log and reframework_crash.dmp of that run; every run keeps
the lines of its log that match `log_filter` (a mod's own tag), so the last things a mod did before a crash are known
to the millisecond. Runs as a background process (a loop of 24 runs takes ~40 min):

    python -m dd2mcp.loadloop <out_dir> <slot> <runs> <max_crashes> <watch_s> <log_filter> [project]

The loop writes <out_dir>/status.json after every run; dd2_loadloop('status') reads it."""
import json
import pathlib
import re
import shutil
import sys
import time

from . import crash, game, harness


def _write(out: pathlib.Path, st: dict) -> None:
    (out / "status.json").write_text(json.dumps(st, indent=1))


def _log_lines(path: pathlib.Path, rx: re.Pattern | None, last: int = 12) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    keep = [l for l in lines if (rx.search(l) if rx else False) or "Exception occurred" in l]
    return keep[-last:]


def _alive(secs: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < secs:
        if not game.running():
            return False
        time.sleep(1)
    return bool(game.running())


def run(out: pathlib.Path, slot: int, runs: int, max_crashes: int, watch: float, log_filter: str, project: str = "") -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rx = re.compile(log_filter) if log_filter else None
    st = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "slot": slot, "runs_planned": runs, "max_crashes": max_crashes,
          "watch": watch, "project": project, "runs": [], "crashes": 0, "state": "running"}
    _write(out, st)
    if project:
        st["deploy"] = game.deploy(project)
    try:
        for i in range(1, runs + 1):
            if (out / "STOP").exists():
                st["state"] = "stopped"
                break
            t0 = time.strftime("%H:%M:%S")
            msg = harness.load_slot(slot, restart=True)
            crashed = "exited" in msg or "crash" in msg.lower()
            if not crashed:
                crashed = not _alive(watch)
            entry = {"run": i, "time": t0, "crashed": crashed, "load": msg.splitlines()[-1] if msg else ""}
            log_copy = out / f"run{i:02d}_log.txt"
            try:
                shutil.copyfile(game.LOG, log_copy)
            except OSError:
                pass
            entry["log_tail"] = _log_lines(log_copy, rx)
            if crashed:
                st["crashes"] += 1
                time.sleep(3)   # the dump is written while the process dies
                dump = out / f"run{i:02d}.dmp"
                try:
                    shutil.copyfile(crash.DUMP, dump)
                    entry["dump"] = str(dump)
                    entry["fault"] = next((l for l in crash.analyze(dump).splitlines() if l.startswith("exception")), "")
                except OSError as e:
                    entry["dump"] = f"none ({e})"
            elif not rx:
                log_copy.unlink(missing_ok=True)   # nothing to read from a clean run without a filter
            st["runs"].append(entry)
            _write(out, st)
            if st["crashes"] >= max_crashes:
                break
    finally:
        if game.running():
            harness._kill_game()
        if project:
            st["undeploy"] = game.undeploy(project)
        if st["state"] == "running":
            st["state"] = "done"
        st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(out, st)
    return st


def summary(st: dict, detail: bool = True) -> str:
    n = len(st.get("runs", []))
    head = (f"{st.get('state')}: {st.get('crashes', 0)} crash(es) in {n}/{st.get('runs_planned')} runs (slot {st.get('slot')}, "
            f"watch {st.get('watch')} s, started {st.get('started')})")
    lines = [head]
    for r in st.get("runs", []):
        if r.get("crashed") or detail:
            lines.append(f"  run {r['run']} {r['time']}: {'CRASH' if r.get('crashed') else 'ok'}"
                         + (f" | {r.get('fault', '')}" if r.get("crashed") else ""))
            if r.get("crashed"):
                lines += [f"     {l[:170]}" for l in r.get("log_tail", [])[-6:]]
                if r.get("dump"):
                    lines.append(f"     dump: {r['dump']} (dd2_crash dump=...)")
    return "\n".join(lines)


if __name__ == "__main__":
    a = sys.argv[1:]
    run(pathlib.Path(a[0]), int(a[1]), int(a[2]), int(a[3]), float(a[4]), a[5], a[6] if len(a) > 6 else "")
