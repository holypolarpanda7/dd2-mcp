"""Offline catalog of the game's systems, built from data we already have (no game needed):
  * research/methodmap-<build>.tsv   every managed method (dd2_methodmap export)
  * research/rsz/il2cpp_cache_dd2.json  6.7k enums with values + the subclass (inheritance) tree
  * research/rsz/rszdd2.json         serializable classes with fields (dd2mcp.rsz)
Use it to answer "what does the game have for X?" before probing live: which manager owns a feature, which enum
values exist (damage types, actions, layers), who derives from what, what fields a data class serializes."""
import collections
import json
import pathlib
import pickle
import re

from . import crash, game, rsz

CACHE_JSON = pathlib.Path(rsz.DUMP).parent / "il2cpp_cache_dd2.json"
CACHE = game.SCRATCH / "catalog_index.pickle"

# subsystem rules, first match wins (regex on the full type name)
DOMAINS = [
    ("combat/damage", r"^app\.(HitController|Damage|Attack|AttackUserData|HitDataBase|DamageInfo|BattleManager|ShellManager|Shell|Catch|Hold|Stagger|Blow|Guard|Weak)|\.Dmg|Hit(Data|Info)"),
    ("AI/behavior", r"^app\.(AI|Ai|Decision|Behavior|BehaviorTree|MotionFsm|Fsm)|^via\.behaviortree|^AISituation|AIKeyLocation|Navigation|^via\.navigation"),
    ("pawns", r"^app\.(Pawn|StrayPawn|Rental|Spiritveil|Comrade)"),
    ("enemies/monsters", r"^app\.(Enemy|Monster|Ch\d|HumanEnemy|Boss|Dragon|Ogre)"),
    ("npc/talk/crime", r"^app\.(NPC|Talk|Crime|Village|Facility|Brothel|BarRoom|Spa|Oxcart|Camp|Quest|SuddenQuest|Event|Guild)"),
    ("character/player", r"^app\.(Character|Player|Human|Job|Skill|Ability|Augment|Vocation|Weapon|Equip|Stamina|Status|Buff|Parameter)"),
    ("items/inventory", r"^app\.(Item|Inventory|Storage|Shop|Recipe|Craft|Gimmick|RimStone)"),
    ("gui/ui", r"^app\.(Gui|UI|Ui|Option|Menu|Telop|Navi)|^via\.gui"),
    ("sound/music", r"^soundlib|^app\.Sound|^via\.wwise|^via\.audio"),
    ("effects/render", r"^via\.(effect|render|landscape|motion|dynamics|mesh)|^app\.(Effect|VFX|Rendering|Weather|Wind|Environment|Time|Light|Material)"),
    ("physics/collision", r"^via\.(physics|collision)|^app\.(Collision|Physics|Rigid)"),
    ("world/streaming", r"^app\.(Stage|Area|Map|Load|Streaming|BackStage|World|Field|Terrain)|^via\.(streaming|scene)"),
    ("save/flow/system", r"^app\.(Save|MainFlow|Boot|Global|UserGlobal|UserData|Network|UserService|UserInput|UserPad|Camera|Flow)|^via\.(Application|hid|os|sound|network)"),
]
_RX = [(n, re.compile(r)) for n, r in DOMAINS]


def domain(t: str) -> str:
    for n, rx in _RX:
        if rx.search(t):
            return n
    return "other/app" if t.startswith("app.") else ("engine/via" if t.startswith("via.") else "other")


def _build() -> dict:
    mm = crash.map_path()
    if not mm.exists():
        raise FileNotFoundError(f"no method map at {mm}: run dd2_methodmap action='export' with the game running")
    stamp = (mm.stat().st_mtime, CACHE_JSON.stat().st_mtime if CACHE_JSON.exists() else 0)
    if CACHE.exists():
        c = pickle.load(open(CACHE, "rb"))
        if c.get("stamp") == stamp:
            return c
    methods = collections.defaultdict(set)
    for l in open(mm):
        if "\t" not in l:
            continue
        sig = l.rstrip("\n").split("\t", 1)[1]
        head = sig.split("(")[0]
        t, _, m = head.rpartition(".")
        methods[t].add(sig[len(t) + 1:])
    enums, subs, parent = {}, {}, {}
    if CACHE_JSON.exists():
        d = json.loads(CACHE_JSON.read_text())
        enums = {k: [(i["name"], i["value"]) for i in v["Items"]] for k, v in d["Enums"].items()}
        subs = d["Subclasses"]
        for p, kids in subs.items():
            for k in kids:
                parent.setdefault(k, p)
    c = {"stamp": stamp, "methods": {k: sorted(v) for k, v in methods.items()}, "enums": enums, "subs": subs, "parent": parent}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump(c, open(CACHE, "wb"))
    return c


def overview() -> str:
    c = _build()
    by = collections.defaultdict(list)
    for t in c["methods"]:
        by[domain(t)].append(t)
    out = [f"{len(c['methods'])} types with methods, {len(c['enums'])} enums, {len(c['parent'])} types with a known parent\n"]
    for d, ts in sorted(by.items(), key=lambda kv: -sum(len(c['methods'][t]) for t in kv[1])):
        mg = sorted((t for t in ts if re.search(r"Manager$", t)), key=lambda t: -len(c["methods"][t]))[:6]
        out.append(f"{d}: {len(ts)} types, {sum(len(c['methods'][t]) for t in ts)} methods; managers: " +
                   (", ".join(f"{m.split('.')[-1]}({len(c['methods'][m])})" for m in mg) or "-"))
    return "\n".join(out)


def subsystem(name: str, limit: int = 60) -> str:
    c = _build()
    ts = sorted((t for t in c["methods"] if name.lower() in domain(t).lower()), key=lambda t: -len(c["methods"][t]))
    return "\n".join(f"{len(c['methods'][t]):5d}  {t}" for t in ts[:limit]) + (f"\n... {len(ts)} types" if len(ts) > limit else "") if ts else \
        "no such subsystem; names: " + ", ".join(n for n, _ in DOMAINS)


def find(pattern: str, limit: int = 60) -> str:
    """Types, methods and enum values matching the regex (case-insensitive)."""
    c = _build()
    rx = re.compile(pattern, re.I)
    tys = [t for t in c["methods"] if rx.search(t)]
    meths = [f"{t}.{m}" for t, ms in c["methods"].items() for m in ms if rx.search(m.split("(")[0])]
    enms = [f"{e}.{n}={v}" for e, items in c["enums"].items() for n, v in items if rx.search(n)]
    enm_t = [e for e in c["enums"] if rx.search(e)]
    sec = lambda title, rows: (f"== {title} ({len(rows)})\n" + "\n".join(rows[:limit]) + (f"\n... +{len(rows) - limit}" if len(rows) > limit else "")) if rows else ""
    return "\n".join(s for s in (sec("types", sorted(tys, key=len)), sec("enum types", sorted(enm_t, key=len)),
                                 sec("enum values", enms), sec("methods", meths)) if s) or f"no match for /{pattern}/"


def type_info(name: str, limit: int = 80) -> str:
    c = _build()
    exact = name if name in c["methods"] or name in c["enums"] or name in c["subs"] else None
    if not exact:
        cand = [t for t in set(c["methods"]) | set(c["enums"]) if t.endswith("." + name) or t == name]
        if len(cand) != 1:
            return "ambiguous or unknown: " + ", ".join(sorted(cand)[:15]) if cand else f"no type {name}"
        exact = cand[0]
    out = [f"{exact}   [{domain(exact)}]"]
    # the IL2CPP subclass tree lists some types as their own parent (via.behaviortree.Action, via.Behavior, ...),
    # so walk with a seen set: an unguarded loop grows `chain` until the machine runs out of memory
    chain, p, seen = [], exact, {exact}
    while (p := c["parent"].get(p)) is not None and p not in seen:
        seen.add(p)
        chain.append(p)
    if chain:
        out.append("parents: " + " > ".join(chain))
    kids = c["subs"].get(exact, [])
    if kids:
        out.append(f"derived ({len(kids)}): " + ", ".join(k.split('.')[-1] for k in kids[:25]) + (" ..." if len(kids) > 25 else ""))
    if exact in c["enums"]:
        out.append("enum: " + ", ".join(f"{n}={v}" for n, v in c["enums"][exact][:limit]))
    nested = sorted(e for e in c["enums"] if e.startswith(exact + ".") and e != exact)
    if nested:
        out.append("nested enums: " + ", ".join(e[len(exact) + 1:] for e in nested[:30]))
    try:
        f = rsz._index().get(exact)
    except FileNotFoundError:
        f = None
    if f:
        out.append("serialized fields: " + ", ".join(f"{a}:{b}" for a, b in f[:limit]))
    ms = c["methods"].get(exact, [])
    if ms:
        out.append(f"methods ({len(ms)}):\n  " + "\n  ".join(ms[:limit]) + (f"\n  ... +{len(ms) - limit}" if len(ms) > limit else ""))
    return "\n".join(out)


def export_md(path: str) -> str:
    """Write a markdown map of the systems (overview, managers per subsystem, biggest types) for humans and for me."""
    c = _build()
    by = collections.defaultdict(list)
    for t in c["methods"]:
        by[domain(t)].append(t)
    out = ["# DD2 systems catalog (generated by dd2_catalog export)\n", "```", overview(), "```\n"]
    for d, ts in sorted(by.items()):
        ts = sorted(ts, key=lambda t: -len(c["methods"][t]))
        out.append(f"## {d}\n")
        out.append("Managers: " + (", ".join(f"`{t}` ({len(c['methods'][t])})" for t in ts if t.endswith("Manager"))[:1500] or "-") + "\n")
        out.append("Largest types: " + ", ".join(f"`{t}` ({len(c['methods'][t])})" for t in ts[:25]) + "\n")
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text("\n".join(out))
    return f"wrote {path} ({len(out)} blocks)"


def manager_candidates() -> list[str]:
    """Type names that could be managed singletons (Manager/Director/Database/Service/System/... suffix), for live probing."""
    rx = re.compile(r"(Manager|Director|Database|Service|System|Catalog|Hub|Registry|Server)$")
    return [t for t in _build()["methods"] if rx.search(t) and ("app." in t or t.startswith("via.") or "." not in t)]
