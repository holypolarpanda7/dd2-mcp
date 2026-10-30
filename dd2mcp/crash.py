"""Native crash analysis: REFramework's reframework_crash.dmp (minidump) read without Windows tools, code
addresses mapped back to managed methods through a per-build method map exported from the live game, and
disassembly straight from DD2.exe on disk (its code is not packed).

Workflow (2026-10-29, job 11 spike): dd2_methodmap once per game build while the game runs, then dd2_crash
after every crash. Addresses are RVAs (offsets from the DD2.exe load address), so a map stays valid across
runs and reboots of the same build."""
import bisect
import json
import os
import pathlib
import re
import struct

from . import game

DUMP = game.GAME / "reframework_crash.dmp"
EXE = game.GAME / "DD2.exe"
MAPS = pathlib.Path(os.environ.get("DD2_RESEARCH", "/mnt/d/DD2Modding/research"))


def build_id() -> str:
    """PE TimeDateStamp of DD2.exe on disk: changes with every game patch."""
    with open(EXE, "rb") as f:
        h = f.read(0x400)
    pe = struct.unpack_from("<I", h, 0x3C)[0]
    return f"{struct.unpack_from('<I', h, pe + 8)[0]:08x}"


def map_path() -> pathlib.Path:
    return MAPS / f"methodmap-{build_id()}.tsv"


def write_typenames() -> int:
    """Type names for the in-game export: every non-generic type in the RSZ dump (REFramework Lua cannot
    enumerate types itself)."""
    from . import rsz
    names = sorted(n for n in rsz._index() if n and "[]" not in n and "`" not in n and "<" not in n)
    game.BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    (game.BRIDGE_DIR / "typenames.txt").write_text("\n".join(names))
    return len(names)


def export_methodmap() -> str:
    n = write_typenames()
    res = game.eval_lua("return D.export_methodmap()", timeout=120)
    if not res.get("ok"):
        return game.format_result(res)
    out = map_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((game.BRIDGE_DIR / "methodmap.tsv").read_bytes())
    _MAP.clear()
    return f"{n} type names sent; {game.format_result(res)}\nmap: {out}"


_MAP: dict = {}


def _map():
    if not _MAP:
        p = map_path()
        if not p.exists():
            return None
        rows = sorted((int(a, 16), name) for a, name in (l.rstrip("\n").split("\t", 1) for l in open(p) if "\t" in l))
        _MAP.update(keys=[r[0] for r in rows], rows=rows)
    return _MAP


def method_at(rva: int, slack: int = 0x4000) -> str | None:
    """'Type.method(params)+0xNN' for the method whose code starts closest below rva (within slack bytes).
    Only types in the RSZ dump are mapped: compiler-generated and some native types come back as None."""
    m = _map()
    if not m:
        return None
    i = bisect.bisect_right(m["keys"], rva) - 1
    if i < 0 or rva - m["keys"][i] > slack:
        return None
    return f"{m['rows'][i][1]}+0x{rva - m['keys'][i]:x}"


def method_start(rva: int, slack: int = 0x4000) -> int | None:
    m = _map()
    if not m:
        return None
    i = bisect.bisect_right(m["keys"], rva) - 1
    return m["keys"][i] if i >= 0 and rva - m["keys"][i] <= slack else None


def find_method(pattern: str, limit: int = 20) -> list[tuple[int, str]]:
    m = _map() or {"rows": []}
    rx = re.compile(pattern, re.I)
    return [r for r in m["rows"] if rx.search(r[1])][:limit]


def _section(rva: int):
    with open(EXE, "rb") as f:
        h = f.read(0x1000)
    pe = struct.unpack_from("<I", h, 0x3C)[0]
    nsec = struct.unpack_from("<H", h, pe + 6)[0]
    so = pe + 24 + struct.unpack_from("<H", h, pe + 20)[0]
    for i in range(nsec):
        _, vsize, va, _, raw = struct.unpack_from("<8sIIII", h, so + i * 40)
        if va <= rva < va + vsize:
            return raw + rva - va
    return None


_TEXT = None


def _text():
    """(rva, bytes as a numpy uint8 array) of DD2.exe's first executable section, cached per process."""
    global _TEXT
    if _TEXT is None:
        import numpy as np
        with open(EXE, "rb") as f:
            h = f.read(0x1000)
            pe = struct.unpack_from("<I", h, 0x3C)[0]
            nsec = struct.unpack_from("<H", h, pe + 6)[0]
            so = pe + 24 + struct.unpack_from("<H", h, pe + 20)[0]
            for i in range(nsec):
                _, vsize, va, rawsize, raw = struct.unpack_from("<8sIIII", h, so + i * 40)
                if struct.unpack_from("<I", h, so + i * 40 + 36)[0] & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                    # local copy per build: reading ~150 MB from the Windows drive is the slow part
                    cache = game.SCRATCH / f"text-{build_id()}.bin"
                    if not cache.exists():
                        f.seek(raw)
                        cache.write_bytes(f.read(min(vsize, rawsize)))
                    _TEXT = (va, np.fromfile(cache, dtype=np.uint8))
                    break
    return _TEXT


def xrefs(target: int, limit: int = 40) -> list[tuple[int, str]]:
    """Code that references rva `target`: rel32 call/jmp (E8/E9) to it, or a rip-relative operand pointing at it
    (globals, vtables, method-info slots). Returns (rva of the instruction, kind) pairs; a numpy scan, ~1-2 s."""
    import numpy as np
    va, b = _text()
    n = len(b) - 5

    def rel_at(i):  # signed int32 at byte offsets i
        v = (b[i].astype(np.int64) | (b[i + 1].astype(np.int64) << 8) | (b[i + 2].astype(np.int64) << 16)
             | (b[i + 3].astype(np.int64) << 24))
        return np.where(v >= 1 << 31, v - (1 << 32), v)
    out = []
    head = b[:n]
    i = np.nonzero((head == 0xE8) | (head == 0xE9))[0]
    for k in i[(i + va + 5 + rel_at(i + 1)) == target][:limit]:
        out.append((int(k) + va, "call" if b[k] == 0xE8 else "jmp"))
    # rip-relative: ModRM mod=00 rm=101, disp32 next; it ends the instruction or an imm8/imm32 follows.
    i = np.nonzero((head & 0xC7) == 0x05)[0]
    end = i + va + 5 + rel_at(i + 1)
    for imm in (0, 1, 4):
        for k in i[end + imm == target][:limit]:
            out.append((int(k) + va, f"rip-rel (imm{imm * 8})" if imm else "rip-rel"))
    return sorted(set(out))[:limit]


def disasm(rva: int, length: int = 0x80, mark: int | None = None) -> str:
    """Disassemble DD2.exe code at rva; rip-relative operands get their target RVA annotated."""
    import capstone
    off = _section(rva)
    if off is None:
        return f"rva {rva:x} is outside every section"
    with open(EXE, "rb") as f:
        f.seek(off)
        code = f.read(length)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    lines = []
    for ins in md.disasm(code, rva):
        if ins.mnemonic == "int3":
            break
        note = ""
        r = re.search(r"\[rip ([+-]) 0x([0-9a-f]+)\]", ins.op_str)
        if r:
            tgt = ins.address + ins.size + (1 if r.group(1) == "+" else -1) * int(r.group(2), 16)
            note = f"   ; rva {tgt:x}"
        elif ins.mnemonic == "call" and ins.op_str.startswith("0x"):
            name = method_at(int(ins.op_str, 16), slack=0)
            note = f"   ; {name}" if name else ""
        lines.append(f"{'>>' if ins.address == mark else '  '} {ins.address:x}  {ins.mnemonic} {ins.op_str}{note}")
    return "\n".join(lines)


def windows_events(minutes: int = 180, limit: int = 5) -> list[dict]:
    """DD2 crashes recorded by Windows (Application log, 'Application Error' 1000): time, module, code, offset.
    REFramework's dump is not rewritten for every crash (2026-10-29: two crashes left the old dump), Windows always logs."""
    import subprocess
    ps = ("Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'; "
          f"StartTime=(Get-Date).AddMinutes(-{minutes})}} -ErrorAction SilentlyContinue | "
          "Where-Object { $_.Message -match 'DD2.exe' } | Select-Object -First %d | ForEach-Object { "
          "$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + '|' + ($_.Properties[3].Value) + '|' + "
          "('{0:x}' -f $_.Properties[6].Value) + '|' + ('{0:x}' -f $_.Properties[7].Value) }" % limit)
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4:
            rows.append({"time": parts[0], "module": parts[1], "code": parts[2], "offset": int(parts[3], 16)})
    return rows


def analyze(path: pathlib.Path = DUMP, stack_bytes: int = 0x800) -> str:
    """Exception, registers, faulting code and stack return-address candidates of a minidump."""
    if not path.exists():
        return f"no dump at {path}"
    data = path.read_bytes()
    _, _, nstreams, diroff = struct.unpack_from("<4sIII", data, 0)
    streams = {}
    for i in range(nstreams):
        t, size, rva = struct.unpack_from("<III", data, diroff + i * 12)
        streams.setdefault(t, (size, rva))

    def wstr(rva):
        n, = struct.unpack_from("<I", data, rva)
        return data[rva + 4: rva + 4 + n].decode("utf-16le")

    mods = []
    _, rva = streams.get(4, (0, 0))
    if rva:
        n, = struct.unpack_from("<I", data, rva)
        for i in range(n):
            base, size, _, _, name_rva = struct.unpack_from("<QIIII", data, rva + 4 + i * 108)
            mods.append((base, size, wstr(name_rva).split("\\")[-1]))
    exe_base = next((b for b, _, n in mods if n.lower() == "dd2.exe"), None)

    def where(a):
        for b, s, n in mods:
            if b <= a < b + s:
                if b == exe_base:
                    return f"DD2.exe+0x{a - b:x}" + (f" {method_at(a - b)}" if method_at(a - b) else "")
                return f"{n}+0x{a - b:x}"
        return None

    mem = []
    for t in (5, 9):
        if t not in streams:
            continue
        _, rva = streams[t]
        if t == 5:
            n, = struct.unpack_from("<I", data, rva)
            for i in range(n):
                start, dsize, drva = struct.unpack_from("<QII", data, rva + 4 + i * 16)
                mem.append((start, data[drva:drva + dsize]))
        else:
            n, off = struct.unpack_from("<QQ", data, rva)
            for i in range(n):
                start, dsize = struct.unpack_from("<QQ", data, rva + 16 + i * 16)
                mem.append((start, data[off:off + dsize]))
                off += dsize

    def read(a, n):
        for s, b in mem:
            if s <= a and a + n <= s + len(b):
                return b[a - s:a - s + n]

    head = []
    events = windows_events()
    if events:
        head.append("Windows crash log (newest first):")
        for e in events:
            loc = f"DD2.exe+0x{e['offset']:x}" + (f" {method_at(e['offset'])}" if e["module"].lower() == "dd2.exe" and method_at(e["offset"]) else "")
            head.append(f"  {e['time']}  {e['code']} in {e['module'] if e['module'].lower() != 'dd2.exe' else loc}"
                        + (f"+0x{e['offset']:x}" if e["module"].lower() != "dd2.exe" else ""))
        import datetime
        newest = datetime.datetime.strptime(events[0]["time"], "%Y-%m-%d %H:%M:%S").timestamp()
        if path.exists() and newest - path.stat().st_mtime > 30:
            head.append("  NOTE: the REFramework dump below is OLDER than the newest crash; only the line above is current.")
    out = head + [f"dump {path.name} ({os.path.getsize(path)} bytes, {_mtime(path)}); DD2.exe base "
           f"{exe_base:x}" if exe_base else "DD2.exe not in module list"]
    if not _map():
        out.append(f"(no method map for build {build_id()}: run dd2_methodmap with the game running)")
    if 6 not in streams:
        out.append("no exception stream")
        return "\n".join(out)
    _, rva = streams[6]
    tid, = struct.unpack_from("<I", data, rva)
    code, _, _, addr, nparams = struct.unpack_from("<IIQQI", data, rva + 8)
    params = struct.unpack_from("<15Q", data, rva + 40)[:nparams]
    access = ""
    if code == 0xC0000005 and len(params) >= 2:
        access = f" ({['read', 'write', '?', '?', '?', '?', '?', '?', 'execute'][min(params[0], 8)]} of 0x{params[1]:x})"
    out.append(f"exception {code:08x}{access} in thread {tid} at {where(addr) or hex(addr)}")
    csize, crva = struct.unpack_from("<II", data, rva + 8 + 152)
    ctx = data[crva:crva + csize]
    regs = struct.unpack_from("<16Q", ctx, 0x78)
    names = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi"] + [f"r{i}" for i in range(8, 16)]
    out.append("registers: " + "  ".join(f"{n}={v:x}" + (f"({where(v)})" if where(v) else "") for n, v in zip(names, regs)))
    if exe_base and exe_base <= addr < exe_base + 0x20000000:
        fault = addr - exe_base
        start = method_start(fault, slack=0x400) or fault - 0x30
        out.append("code (>> = faulting instruction):")
        out.append(disasm(start, fault - start + 0x30, mark=fault))
    stack = read(regs[4], stack_bytes) or b""
    out.append("stack return-address candidates (DD2.exe only):")
    for i in range(0, len(stack) - 7, 8):
        v, = struct.unpack_from("<Q", stack, i)
        if exe_base and exe_base <= v < exe_base + 0x20000000:
            w = where(v)
            if w and " " in w:
                out.append(f"  rsp+{i:x}: {w}")
    return "\n".join(out)


def _mtime(p: pathlib.Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
