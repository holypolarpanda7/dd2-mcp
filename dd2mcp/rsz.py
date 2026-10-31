"""Offline search of the RE Engine type dump (alphaZomega's RE_RSZ rszdd2.json): every serializable DD2 type
with its fields. Covers what the live bridge cannot (REFramework Lua has no type enumeration). The dump can lag
game patches: treat it as a map and confirm live with dd2_types."""
import json
import os
import pathlib
import pickle
import re
import subprocess

from . import game

DUMP = pathlib.Path(os.environ.get("DD2_RSZ", "/mnt/d/DD2Modding/research/rsz/rszdd2.json"))
URL = "https://raw.githubusercontent.com/alphazolam/RE_RSZ/master/rszdd2.json"
CACHE = game.SCRATCH / "rsz_index.pickle"


def download() -> str:
    DUMP.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSfL", "-o", str(DUMP), URL], capture_output=True, text=True, timeout=600)
    if r.returncode:
        return "download failed: " + r.stderr.strip()
    CACHE.unlink(missing_ok=True)
    from . import rszfile  # the newer layout template the RSZ decoder prefers (REE-Lib-Resources, MIT)
    r2 = subprocess.run(["curl", "-sSfL", "-o", str(rszfile.TEMPLATE), rszfile.TEMPLATE_URL], capture_output=True,
                        text=True, timeout=600)
    extra = f", template {rszfile.TEMPLATE.stat().st_size // 1_000_000} MB" if not r2.returncode else \
        ", template download failed: " + r2.stderr.strip()
    return f"downloaded {DUMP} ({DUMP.stat().st_size // 1_000_000} MB){extra}"


def _index() -> dict:
    """name -> [(field name, type), ...], cached next to the scratch data (rebuilt when the dump changes)."""
    if not DUMP.exists():
        raise FileNotFoundError(f"no dump at {DUMP}: run dd2_rsz action='download'")
    stamp = DUMP.stat().st_mtime
    if CACHE.exists():
        with open(CACHE, "rb") as f:
            c = pickle.load(f)
        if c.get("stamp") == stamp:
            return c["types"]
    types = {}
    for v in json.loads(DUMP.read_text()).values():
        if v.get("name"):
            types[v["name"]] = [(f.get("name"), (f.get("original_type") or f.get("type")) + ("[]" if f.get("array") and "[]" not in (f.get("original_type") or "") else ""))
                                for f in v.get("fields", [])]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump({"stamp": stamp, "types": types}, f)
    return types


def search(pattern: str, fields: bool = False, limit: int = 200, generic: bool = False) -> str:
    """Types whose full name matches the regex `pattern` (case-insensitive); fields=True also lists their
    fields, and matches field names/types too. Generic collection wrappers are hidden unless generic=True."""
    types = _index()
    rx = re.compile(pattern, re.I)
    out = []
    for name in sorted(types):
        if not generic and (name.startswith("System.") or "`" in name):
            continue
        hit_fields = [f for f in types[name] if fields and (rx.search(f[0] or "") or rx.search(f[1] or ""))]
        if rx.search(name) or hit_fields:
            out.append(name)
            if fields:
                for fn, ft in (types[name] if rx.search(name) else hit_fields):
                    out.append(f"    {fn} : {ft}")
        if len(out) >= limit:
            out.append(f"... (limit {limit})")
            break
    return f"{len(types)} types in dump; matches for /{pattern}/:\n" + "\n".join(out) if out else f"no match for /{pattern}/"
