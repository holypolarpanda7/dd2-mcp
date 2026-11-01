"""Run the headless REE-Lib helper (dotnet/reetool) with the private .NET SDK in <dd2-modding>/Tools.

The project is built on demand (and rebuilt when its sources are newer than the build). Everything it needs lives outside the
repo: Tools/dotnet (private .NET 10 SDK), Tools/ContentEditor (REE Content Editor DLLs). Paths are overridable via DD2_TOOLS."""
import os
import pathlib
import re
import subprocess

TOOLS = pathlib.Path(os.environ.get("DD2_TOOLS", "/mnt/d/DD2Modding/Tools"))
DOTNET = TOOLS / "dotnet" / "dotnet.exe"
PROJECT = pathlib.Path(__file__).resolve().parent.parent / "dotnet" / "reetool"
DLL = PROJECT / "bin" / "Release" / "net10.0" / "reetool.dll"
GAME_WIN = r"D:\SteamLibrary\steamapps\common\Dragons Dogma 2"


def winpath(p) -> str:
    p = str(p)
    if len(p) > 2 and p[1] == ":":
        return p
    return subprocess.run(["wslpath", "-w", p], capture_output=True, text=True).stdout.strip()


def _env() -> dict:
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1",
               REE_CE_DIR=winpath(TOOLS / "ContentEditor"), REE_GAME_DIR=GAME_WIN, REETOOL_TRACE=os.environ.get("REETOOL_TRACE", "0"), REETOOL_EFX_IDS=os.environ.get("REETOOL_EFX_IDS", "DD2"))
    env["WSLENV"] = ":".join(filter(None, [env.get("WSLENV", ""), "DOTNET_CLI_TELEMETRY_OPTOUT", "DOTNET_NOLOGO", "REE_CE_DIR", "REE_GAME_DIR", "REETOOL_TRACE", "REETOOL_EFX_IDS"]))
    return env


def ensure_built() -> str:
    if not DOTNET.exists():
        return f"private dotnet SDK missing: {DOTNET} (see Tools/README.md)"
    srcs = [PROJECT / "Program.cs", PROJECT / "reetool.csproj"]
    if DLL.exists() and DLL.stat().st_mtime >= max(s.stat().st_mtime for s in srcs):
        return ""
    r = subprocess.run([str(DOTNET), "build", "-c", "Release", winpath(PROJECT / "reetool.csproj")], capture_output=True, text=True,
                       env=_env(), timeout=600)
    errs = [l for l in r.stdout.splitlines() if " error " in l]
    return "" if r.returncode == 0 else "reetool build failed:\n" + "\n".join(sorted(set(errs))[:15])


def run(*args, timeout: int = 600) -> str:
    err = ensure_built()
    if err:
        return err
    cmd = [str(DOTNET), winpath(DLL)] + [winpath(a) if isinstance(a, pathlib.Path) or str(a).startswith("/") else str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout)
    out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
    # drop REE-Lib's chatty log lines
    out = "\n".join(l for l in out.splitlines() if "[I]" not in l)
    return out or f"(exit {r.returncode})"


RESEARCH = pathlib.Path(os.environ.get("DD2_RESEARCH", "/mnt/d/DD2Modding/research"))


def extract(regex: str, kind: str, limit: int = 20000) -> str:
    """Copy every game file whose community-list path matches `regex` out of the paks into research/<kind>/ (path under natives/stm/
    preserved, version suffix kept so the readers can tell the format version). Existing files are not rewritten."""
    from . import pak
    names = pak.search_list(regex, limit)
    base = RESEARCH / kind
    new = 0
    for p in names:
        dst = base / p.replace("natives/stm/", "")
        if dst.exists():
            continue
        found = pak.find(p)
        if not found:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(found[1])
        new += 1
    return f"{len(names)} matching paths, {new} newly written under {base}"


def scan_efx(directory, filt: str = "") -> str:
    """efx-scan over a folder, restarting after any file that kills the process (REE-Lib can stack-overflow on a malformed attribute)."""
    err = ensure_built()
    if err:
        return err
    start, crashed, matches, failures, scanned = 0, [], [], [], 0
    while True:
        cmd = [str(DOTNET), winpath(DLL), "efx-scan", winpath(directory), filt, "--from", str(start)]
        r = subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=3600)
        last, block = None, []
        for line in r.stdout.splitlines():
            if line.startswith("#"):
                if block and block[0].startswith("!"):
                    failures.append(block[0])
                elif block:
                    matches.extend(block)
                idx, _, path = line[1:].partition(" ")
                last, block = (int(idx), path), []
            elif line.startswith("scanned "):
                scanned = max(scanned, int(line.split()[1]))
            else:
                block.append(line)
        if block and block[0].startswith("!"):
            failures.append(block[0])
        elif block and not any(l.startswith("scanned") for l in block):
            matches.extend(block)
        if r.returncode == 0:
            break
        if last is None:
            return "reetool crashed before scanning:\n" + r.stderr[:800]
        crashed.append(last[1])
        start = last[0] + 1
    out = matches[:]
    out.append(f"crashed the parser: {len(crashed)} files; parse failures: {len(failures)}; matches listed above for '{filt}'")
    return "\n".join(out)


def efx_patch_mesh(src, dst, mesh_path: str, mdf_path: str = "", type_id: int = 31) -> str:
    """Copy an efx and swap the mesh (and optionally material) path of its mesh element(s) at byte level (no REE-Lib parsing of the
    attributes, so it works despite the unsupported struct changes). Only plain files are handled (no actions / field parameters / bones).
    A mesh element stores [int byteSize][UTF-16 + NUL] for the mesh path, then the mirror mesh path, then the material path."""
    import struct
    d = bytearray(pathlib.Path(src).read_bytes())
    if d[:4] != b"efxr":
        return "not an efx"
    dim, ent, strlen, act, fparam, eparam, grp, grplen, bones, battr, pbind = struct.unpack_from("<11i", d, 4)
    if act or fparam or bones:
        return f"unsupported efx layout (actions={act}, fieldParams={fparam}, bones={bones})"
    pos = 48 + strlen + 24 * eparam + 2 * battr

    def wstr(s: str) -> bytes:
        return struct.pack("<i", (len(s) + 1) * 2) + s.encode("utf-16le") + b"\0\0"

    out = bytearray(d[:pos])
    patched = 0
    for e in range(ent):
        head = d[pos:pos + 20]
        out += head
        cnt = struct.unpack_from("<i", head, 16)[0]
        pos += 20
        for a in range(cnt):
            tid, size, seq = struct.unpack_from("<iii", d, pos)
            blob = bytes(d[pos + 12: pos + 8 + size])
            if tid == type_id:
                # locate the three strings after the fixed head: scan for the first "[int n][utf16 .mesh]"
                m = re.search(rb"(?:[\x20-\x7e]\x00){4,}", blob)
                # the int before a path is its byte size; find the .mesh path start by looking for the length prefix
                start = None
                for mm in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", blob):
                    off = mm.start()
                    # strings can begin with the printable low byte of the length int (e.g. 76 = 'L'); back up to the int
                    for back in (4, 3, 2, 1, 0):
                        o2 = off - back
                        if o2 >= 0 and o2 + 4 <= len(blob):
                            n = struct.unpack_from("<i", blob, o2)[0]
                            if 0 < n < 600 and n % 2 == 0 and o2 + 4 + n <= len(blob) and blob[o2 + 4 + n - 2: o2 + 4 + n] == b"\0\0":
                                try:
                                    txt = blob[o2 + 4: o2 + 4 + n - 2].decode("utf-16le")
                                except UnicodeDecodeError:
                                    continue
                                if txt.lower().endswith(".mesh"):
                                    start = o2
                                    break
                    if start is not None:
                        break
                if start is None:
                    return f"entry {e}: mesh attribute without a recognizable .mesh path"
                n_mesh = struct.unpack_from("<i", blob, start)[0]
                p1 = start + 4 + n_mesh                   # mirror mesh path
                n_mir = struct.unpack_from("<i", blob, p1)[0]
                p2 = p1 + 4 + n_mir                       # material path
                n_mat = struct.unpack_from("<i", blob, p2)[0]
                p3 = p2 + 4 + n_mat
                new = blob[:start] + wstr(mesh_path) + blob[p1:p2] + (wstr(mdf_path) if mdf_path else blob[p2:p3]) + blob[p3:]
                size += len(new) - len(blob)
                blob = new
                patched += 1
            out += struct.pack("<iii", tid, size, seq) + blob
            pos += 8 + (struct.unpack_from("<i", d, pos + 4)[0])
    out += d[pos:]
    pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(dst).write_bytes(bytes(out))
    return f"patched {patched} mesh element(s); {len(d)} -> {len(out)} bytes -> {dst}"
