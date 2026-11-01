"""Run the headless REE-Lib helper (dotnet/reetool) with the private .NET SDK in <dd2-modding>/Tools.

The project is built on demand (and rebuilt when its sources are newer than the build). Everything it needs lives outside the
repo: Tools/dotnet (private .NET 10 SDK), Tools/ContentEditor (REE Content Editor DLLs). Paths are overridable via DD2_TOOLS."""
import os
import pathlib
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
