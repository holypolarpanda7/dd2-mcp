"""Reader for the in-game watcher (dd2mcp/lua/dd2mcp_watch.lua): sessions, tails and summaries of its JSON-lines streams.

Files: <game>/reframework/data/dd2mcp/watch_<session>_<stream>.jsonl. Streams: meta state node snap key damage skill gui item inventory hook errors.
"""
from __future__ import annotations

import collections
import json
import pathlib
import shutil

from . import game

LUA = pathlib.Path(__file__).parent / "lua" / "dd2mcp_watch.lua"
STREAMS = ("meta", "state", "node", "snap", "key", "damage", "skill", "gui", "item", "inventory", "hook", "errors")


def install() -> str:
    dst = game.REF / "autorun" / "dd2mcp_watch.lua"
    shutil.copyfile(LUA, dst)
    hooks = game.BRIDGE_DIR / "watch_hooks.txt"
    if not hooks.exists():
        hooks.write_text("# Extra hooks (type::method signature), re-read every 5 s while the game runs\n")
    return f"installed {dst} (loads at the next game start; read-only observation)"


def uninstall() -> str:
    dst = game.REF / "autorun" / "dd2mcp_watch.lua"
    if dst.exists():
        dst.unlink()
        return f"removed {dst}"
    return "not installed"


def _files(session: str | None = None):
    d = game.BRIDGE_DIR
    out = collections.defaultdict(dict)
    for f in d.glob("watch_*_*.jsonl"):
        parts = f.stem.split("_", 3)          # watch, YYYYMMDD, HHMMSS, stream
        if len(parts) == 4:
            out[parts[1] + "_" + parts[2]][parts[3]] = f
    return out if session is None else {session: out.get(session, {})}


def sessions() -> str:
    rows = []
    for sess, streams in sorted(_files().items()):
        size = sum(p.stat().st_size for p in streams.values())
        rows.append(f"{sess}: " + ", ".join(f"{s}={sum(1 for _ in p.open('rb'))}" for s, p in sorted(streams.items())) + f"  ({size / 1e6:.1f} MB)")
    return "\n".join(rows) or "no watcher sessions yet (start the game once with the watcher installed)"


def latest() -> str | None:
    f = _files()
    return max(f) if f else None


def read(session: str, stream: str):
    p = _files(session).get(session, {}).get(stream)
    if not p:
        return []
    out = []
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def tail(session: str, stream: str, n: int = 30) -> str:
    rows = read(session, stream)[-n:]
    return "\n".join(json.dumps(r, separators=(",", ":")) for r in rows) or f"no rows in {stream}"


def summary(session: str) -> str:
    lines = [f"session {session}"]
    st = read(session, "state")
    if st:
        lines.append(f"  play {st[0].get('play')}..{st[-1].get('play')} s, {len(st)} heartbeats, jobs seen {sorted({r.get('job') for r in st if r.get('job') is not None})}")
    node = read(session, "node")
    by = collections.defaultdict(collections.Counter)
    for r in node:
        if r.get("ev") == "node":
            by[r.get("kind", "?") + ":" + str(r.get("who", "?")).rsplit("_", 1)[0]][str(r.get("n0")) + "|" + str(r.get("n1"))] += 1
    lines.append("  node changes by actor kind (top 4 nodes each):")
    for who, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))[:14]:
        lines.append(f"    {who}: {sum(c.values())} changes; " + "; ".join(f"{k} x{v}" for k, v in c.most_common(4)))
    sk = read(session, "skill")
    c = collections.Counter((r.get("who"), r.get("action")) for r in sk if r.get("ev") == "request")
    lines.append(f"  action requests: {sum(c.values())}; top: " + "; ".join(f"{w}:{a} x{n}" for (w, a), n in c.most_common(12)))
    cs = {(r.get("job"), r.get("skill")): r.get("action") for r in sk if r.get("ev") == "custom"}
    if cs:
        lines.append("  custom skill id -> action: " + "; ".join(f"j{j}/s{i}={a}" for (j, i), a in sorted(cs.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))[:30]))
    dm = read(session, "damage")
    if dm:
        tot = collections.defaultdict(lambda: [0, 0.0])
        for r in dm:
            v = str(r.get("victim", "?")).rsplit("_", 1)[0]
            tot[v][0] += 1; tot[v][1] += float(r.get("Damage") or 0)
        lines.append(f"  damage events: {len(dm)}; by victim: " + "; ".join(f"{v} {n}x {d:.0f}" for v, (n, d) in sorted(tot.items(), key=lambda kv: -kv[1][0])[:10]))
    ky = collections.Counter(r.get("k") for r in read(session, "key") if r.get("down"))
    lines.append("  key presses: " + ", ".join(f"{k} {n}" for k, n in ky.most_common(14)))
    gu = collections.Counter(n for r in read(session, "gui") for n in r.get("opened", []))
    lines.append("  GUI windows opened (top): " + ", ".join(f"{n} x{k}" for n, k in gu.most_common(14)))
    it = read(session, "item")
    lines.append(f"  item events: {len(it)}; " + ", ".join(f"{r.get('name')} {r.get('was')}->{r.get('num')}" for r in it[:8]))
    er = read(session, "errors")
    if er:
        lines.append(f"  ERRORS ({len(er)} rows): " + "; ".join(f"{r.get('where')}: {r.get('msg')}" for r in er[:5]))
    return "\n".join(lines)
