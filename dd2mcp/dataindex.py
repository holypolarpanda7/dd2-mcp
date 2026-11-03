"""Which data file holds which serialized class? Header-only index of the RSZ instance tables of the game's .user files
(gameplay tables, enemy/character params, quests, gimmicks, items, GUI data...), skipping the bulk generated/sound/AI-grid
folders. Built once in a background process (~7 min), cached at ~/.cache/dd2-mcp/dataindex.json keyed by pak mtimes.

Run directly:  python -m dd2mcp.dataindex build"""
import collections
import json
import pathlib
import re
import struct
import sys
import time

from . import game, pak, rszfile

CACHE = pathlib.Path.home() / ".cache" / "dd2-mcp" / "dataindex.json"
PROGRESS = pathlib.Path.home() / ".cache" / "dd2-mcp" / "dataindex.progress"
BULK = re.compile(r"gridattribute|manualset|/sound/|appdataback|/ai/(situation|actioninterface|decision)|lipsync|"
                  r"generatepackage|contextdata|hidegrassposition|domainquerygenerate|autosetpoint")


def _stamp() -> str:
    return ",".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in pak.game_paks())


def paths() -> list[str]:
    return [p for p in pak.search_list(r"\.user\.\d+$", 200000) if not BULK.search(p)]


def type_counts(data: bytes) -> dict:
    """{class name: instance count} over every RSZ block, from the instance-hash table only."""
    C = rszfile.candidates()
    cnt = collections.Counter()
    for off in rszfile.blocks(data):
        try:
            _m, _v, _nobj, ninst, _nud, _ = struct.unpack_from("<4sIiiii", data, off)
            inst_off = struct.unpack_from("<Q", data, off + 0x18)[0]
            for i in range(1, ninst):
                h = struct.unpack_from("<I", data, off + inst_off + 8 * i)[0]
                cnt[C[h][0]["name"] if h in C else f"?{h:08x}"] += 1
        except (struct.error, KeyError):
            continue
    return dict(cnt)


def build() -> None:
    todo = paths()
    index = {}
    t0 = time.time()
    for n, p in enumerate(todo):
        try:
            f = pak.find(p)
            if f:
                index[p] = type_counts(f[1])
        except Exception as e:  # keep going; record the failure
            index[p] = {"!error": 1}
        if n % 200 == 0:
            PROGRESS.write_text(f"{n}/{len(todo)} files, {time.time() - t0:.0f}s")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"stamp": _stamp(), "files": index}))
    PROGRESS.write_text(f"done: {len(index)} files in {time.time() - t0:.0f}s")


def load() -> dict | None:
    if not CACHE.exists():
        return None
    d = json.loads(CACHE.read_text())
    return d["files"] if d.get("stamp") == _stamp() else None


def search(type_rx: str = "", path_rx: str = "", limit: int = 40) -> str:
    idx = load()
    if idx is None:
        return "no current index: dd2_where action='build' (takes ~7 min in the background), then 'status'"
    tx, px = (re.compile(type_rx, re.I) if type_rx else None), (re.compile(path_rx, re.I) if path_rx else None)
    out = []
    for p, types in idx.items():
        if px and not px.search(p):
            continue
        hit = {t: c for t, c in types.items() if not tx or tx.search(t)}
        if hit:
            top = ", ".join(f"{t} x{c}" for t, c in sorted(hit.items(), key=lambda x: -x[1])[:4])
            out.append((sum(hit.values()), f"{p.split('natives/stm/')[-1]}: {top}"))
    out.sort(key=lambda x: -x[0])
    lines = [l for _, l in out[:limit]]
    return "\n".join(lines + ([f"... {len(out) - limit} more files"] if len(out) > limit else [])) or "no match"


if __name__ == "__main__" and sys.argv[1:] == ["build"]:
    build()
