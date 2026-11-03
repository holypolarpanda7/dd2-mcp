"""Tabular view of RSZ data files (.user/.scn/.pfb): rows = instances of one type, columns = flattened fields.

Object references are inlined as dotted columns (`_Param._Hp`), GUIDs that name a game text are replaced with the English
string, and rows can be filtered / sorted / column-selected so a data table reads like a spreadsheet."""
import collections
import operator
import pathlib
import re

from . import msg, pak, rszfile
from .rszfile import Ref


def load_blocks(path: str):
    p = pathlib.Path(path)
    if p.exists():
        data = p.read_bytes()
    else:
        found = pak.find(path)
        if not found:
            raise FileNotFoundError(f"{path}: not a local file and in no pak")
        data = found[1]
    offs = rszfile.blocks(data)
    return [rszfile.Block(data, o, offs[k + 1] if k + 1 < len(offs) else None) for k, o in enumerate(offs)]


_ftypes = None


def field_types() -> dict:
    """(type name, field name) -> original C# type, from the layout sources."""
    global _ftypes
    if _ftypes is None:
        _ftypes = {}
        for lst in rszfile.candidates().values():
            for lay in lst:
                for f in lay["fields"]:
                    _ftypes.setdefault((lay["name"], f["name"]), f.get("original_type"))
    return _ftypes


def flatten(block, i: int, depth: int, prefix: str = "", meta: dict | None = None) -> dict:
    """Instance i as {dotted column: value}; Ref fields are inlined `depth` levels, lists of refs become [n] columns.
    meta, if given, receives {column: original C# type}."""
    name, fields = block.instances[i]
    row = {}
    ft = field_types() if meta is not None else None
    for k, v in fields.items():
        key = prefix + k
        if meta is not None:
            meta[key] = ft.get((name, k))
        if isinstance(v, Ref):
            if depth > 0 and 0 < v < len(block.instances):
                row.update(flatten(block, v, depth - 1, key + ".", meta))
            else:
                row[key] = f"@{int(v)}" if v else None
        elif isinstance(v, list):
            if v and all(isinstance(x, Ref) for x in v):
                if depth > 0:
                    for n, r in enumerate(v):
                        if 0 < r < len(block.instances):
                            row.update(flatten(block, r, depth - 1, f"{key}[{n}].", meta))
                else:
                    row[key] = f"{len(v)} refs"
            else:
                row[key] = v
        else:
            row[key] = v
    return row


_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_texts = None


def guid_text() -> dict:
    global _texts
    if _texts is None:
        _texts = {r[2]: r[3] for r in msg.index()}
    return _texts


_enums = None
_names = None


def enums() -> dict:
    global _enums
    if _enums is None:
        import json
        d = json.loads((pathlib.Path(rszfile.__file__).parents[2] / "research" / "rsz" / "il2cpp_cache_dd2.json").read_text())
        _enums = {}
        for t, e in d["Enums"].items():
            _enums[t] = ({it["value"]: it["name"] for it in e["Items"]}, e["Items"])
    return _enums


def enum_name(t, v):
    e = enums().get(t) if t else None
    if e is None or not isinstance(v, int):
        return None
    byval, items = e
    if v in byval:
        return byval[v]
    bits = [it["name"] for it in items if it["value"] and it["value"] & (it["value"] - 1) == 0 and v & it["value"]]
    return "|".join(bits) if bits and sum(1 for it in items if it["value"] & (it["value"] - 1) == 0) > 2 else None


def name_text() -> dict:
    global _names
    if _names is None:
        _names = {}
        for f, n, g, t in msg.index():  # base game file wins over contents_00xx variants
            if n not in _names or "extracontents" not in f:
                _names[n] = t
    return _names


def _fmt(v, names: bool, width: int) -> str:
    if isinstance(v, str) and names and _GUID.match(v):
        t = guid_text().get(v)
        if t is not None:
            v = t.replace("\r\n", " ").replace("\n", " ")
    if isinstance(v, float):
        v = round(v, 4)
    s = "" if v is None else str(v)
    return s if len(s) <= width else s[:width - 1] + "…"


_OPS = [(">=", operator.ge), ("<=", operator.le), ("!=", operator.ne), ("~", None), ("=", operator.eq),
        (">", operator.gt), ("<", operator.lt)]


def _parse_where(expr: str):
    """'col~regex' | 'col=val' | 'col>=n' ... joined by ' & '. Returns a predicate over a row dict."""
    conds = []
    for part in [x.strip() for x in expr.split(" & ") if x.strip()]:
        for sym, fn in _OPS:
            if sym in part:
                col, val = (x.strip() for x in part.split(sym, 1))
                conds.append((col, sym, fn, val))
                break
        else:
            raise ValueError(f"bad condition {part!r} (use col~regex, col=val, col>n, col!=val)")

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    def pred(row, textrow):
        for col, sym, fn, val in conds:
            cell = row.get(col)
            shown = textrow.get(col, "")
            if sym == "~":
                if not re.search(val, str(cell) + " " + shown, re.I):
                    return False
            elif num(val) is not None and num(cell) is not None:
                if not fn(num(cell), num(val)):
                    return False
            elif not fn(str(cell), val) and not fn(shown, val):
                return False
        return True
    return pred


def pick_type(block, want: str) -> str:
    counts = collections.Counter(x[0] for x in block.instances[1:] if x)
    if want:
        rx = re.compile(want, re.I)
        m = [t for t in counts if rx.search(t)]
        if not m:
            raise ValueError(f"no type matches {want!r}; types: " + ", ".join(f"{t} x{c}" for t, c in counts.most_common(12)))
        return max(m, key=lambda t: counts[t])
    # default: the most frequent type that isn't a bare prefab/userdata reference
    for t, _ in counts.most_common():
        if t != "via.Prefab":
            return t
    return counts.most_common(1)[0][0]


def render(path: str, type_: str = "", cols: str = "", where: str = "", sort: str = "", limit: int = 30,
           depth: int = 2, names: bool = True, width: int = 28, listcols: bool = False) -> str:
    blocks = load_blocks(path)
    out = []
    for bi, b in enumerate(blocks):
        if len(b.instances) <= 1:
            continue
        t = pick_type(b, type_)
        idx = [i for i in range(1, len(b.instances)) if b.instances[i] and b.instances[i][0] == t]
        meta = {}
        rows = [(i, flatten(b, i, depth, "", meta)) for i in idx]
        if names:
            for _, r in rows:
                for c, v in list(r.items()):
                    en = enum_name(meta.get(c), v)
                    if en is not None:
                        m = re.fullmatch(r"It0*(\d+)", en)
                        nm = name_text().get(f"item_name_{m.group(1)}") if m else None
                        r[c] = f"{v}:{nm}" if nm else f"{v}:{en}"
                if isinstance(r.get("_Id"), int) and "item_name_%d" % r["_Id"] in name_text() and t.startswith("app.Item"):
                    r["Name"] = name_text()["item_name_%d" % r["_Id"]]
        allcols = list(dict.fromkeys(c for _, r in rows for c in r))
        if "Name" in allcols:  # put the readable name first
            allcols.remove("Name")
            allcols.insert(0, "Name")
        if listcols:
            out.append(f"block {bi} type {t}: {len(rows)} rows, columns:\n  " + "\n  ".join(allcols))
            continue
        shown = [(i, r, {c: _fmt(v, names, 10_000) for c, v in r.items()}) for i, r in rows]
        if where:
            pred = _parse_where(where)
            shown = [x for x in shown if pred(x[1], x[2])]
        if sort:
            desc = sort.startswith("-")
            key = sort.lstrip("-")

            def sk(x):
                v = x[1].get(key)
                return (0, v) if isinstance(v, (int, float)) else (1, str(x[2].get(key, "")))
            shown.sort(key=sk, reverse=desc)
        if cols:
            sel = []
            for c in [c.strip() for c in cols.split(",") if c.strip()]:
                sel += [a for a in allcols if a == c] or [a for a in allcols if re.search(c, a, re.I)]
            sel = list(dict.fromkeys(sel))
        else:  # no selection: first scalar columns that fit a reasonable line
            sel = [c for c in allcols if not c.startswith("_Param.") or True][:10]
        total = len(shown)
        lines = ["#\t" + "\t".join(sel)]
        for i, r, tr in shown[:limit]:
            lines.append(f"{i}\t" + "\t".join(_fmt(r.get(c), names, width) for c in sel))
        more = f"\n... {total - limit} more rows" if total > limit else ""
        hidden = f"\n({len(allcols) - len(sel)} more columns; cols=... or listcols=True)" if len(allcols) > len(sel) else ""
        out.append(f"block {bi}: {t}, {total}/{len(rows)} rows\n" + "\n".join(lines) + more + hidden)
    return "\n\n".join(out) or "no RSZ data"
