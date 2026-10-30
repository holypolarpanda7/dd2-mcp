"""Vortex profiles for DD2 mod projects, through the Vortex bridge extension that bg3-data-mcp ships
(bg3-data-mcp-bridge, 127.0.0.1 + token in its bridge.json). One Vortex manages BG3 and DD2; every call here
names the DD2 game so a profile switch never lands on BG3.

A mod project names its profiles in <project>/dd2mod.json:
  {"vortex": {"dev_profile": "Bastion-Dev", "release_profile": "Bastion-Release", "requires": ["REFramework"]}}
Defaults: <ProjectDir>-Dev, <ProjectDir>-Release, requires ["REFramework"]."""
import glob
import json
import os
import pathlib
import subprocess
import time

from . import game

GAME_ID = "dragonsdogma2"
VORTEX_EXE = r"C:\Program Files\Black Tree Gaming Ltd\Vortex\Vortex.exe"
STATE = game.SCRATCH / "vortex.json"  # the profile to return to with action 'play'


def _bridge_cfg() -> dict | None:
    hits = glob.glob(os.environ.get("DD2_VORTEX_BRIDGE",
                                    "/mnt/c/Users/*/AppData/Roaming/Vortex/plugins/bg3-data-mcp-bridge/bridge.json"))
    return json.loads(pathlib.Path(hits[0]).read_text()) if hits else None


def call(method: str, endpoint: str, body: dict | None = None, timeout: int = 200) -> dict:
    """One bridge request. WSL2's 127.0.0.1 is not Windows' localhost, so PowerShell makes it."""
    cfg = _bridge_cfg()
    if not cfg:
        return {"error": "Vortex bridge not installed (bg3-data-mcp: bg3_vortex_bridge_install, then restart Vortex)"}
    url = f"http://127.0.0.1:{cfg.get('port', 17846)}{endpoint}"
    body_arg = ""
    if method != "GET":
        game.SCRATCH.mkdir(parents=True, exist_ok=True)
        bodyfile = game.SCRATCH / "vortex_body.json"
        bodyfile.write_text(json.dumps(body or {}), encoding="utf-8")
        win = subprocess.run(["wslpath", "-w", str(bodyfile)], capture_output=True, text=True).stdout.strip()
        body_arg = f" -ContentType 'application/json; charset=utf-8' -Body ([IO.File]::ReadAllText('{win}'))"
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; try { $r = Invoke-RestMethod "
          f"-Uri '{url}' -Method {method} -TimeoutSec {timeout} -Headers @{{'x-bridge-token'='{cfg['token']}'}}{body_arg}; "
          "$r | ConvertTo-Json -Depth 8 -Compress } catch { $m = $_.ErrorDetails.Message; "
          "if (-not $m -and $_.Exception.Response) { $m = (New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd() }; "
          "if (-not $m) { $m = (@{error=$_.Exception.Message} | ConvertTo-Json -Compress) }; $m }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout + 20)
    try:
        return json.loads(r.stdout.strip() or "{}")
    except ValueError:
        return {"error": (r.stdout + r.stderr).strip()[:500]}


def ensure_running(wait: float = 90) -> str | None:
    """Start Vortex if its bridge does not answer; None when ready, else the reason."""
    if "error" not in call("GET", "/status", timeout=10):
        return None
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{VORTEX_EXE}'"], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(3)
        if "error" not in call("GET", "/status", timeout=10):
            return None
    return f"Vortex bridge did not answer within {wait:.0f} s"


def project_cfg(project: str) -> dict:
    p = pathlib.Path(project)
    v = {}
    if (p / "dd2mod.json").exists():
        v = json.loads((p / "dd2mod.json").read_text()).get("vortex", {})
    return {"dev_profile": v.get("dev_profile", f"{p.name}-Dev"),
            "release_profile": v.get("release_profile", f"{p.name}-Release"),
            "requires": v.get("requires", ["REFramework"])}


def _summary(st: dict) -> str:
    if "error" in st:
        return "ERROR: " + st["error"]
    on = [m["name"] for m in st.get("mods", []) if m.get("enabled")]
    return (f"game {st.get('gameId')}, profile {st.get('profile')!r}, needToDeploy {st.get('needToDeploy')}\n"
            f"enabled ({len(on)}): " + (", ".join(on) or "none"))


def status() -> str:
    return _summary(call("GET", "/status", timeout=20))


def profiles() -> str:
    r = call("POST", "/profiles", {"game": GAME_ID}, timeout=20)
    if "error" in r:
        return "ERROR: " + r["error"]
    return "\n".join(f"{'*' if p['id'] == r.get('active') else ' '} {p['name']} (id {p['id']}, {p['enabled']} enabled)"
                     for p in r.get("profiles", []))


def switch(profile: str, requires: list[str] | None = None, create: bool = False, remember: bool = True) -> str:
    """Switch Vortex to a DD2 profile (creating it if asked), enable the required mods, deploy.
    Refuses while DD2 runs: a switch purges and redeploys files the game has loaded (dinput8.dll)."""
    if game.running():
        return "DD2 is running: quit it before switching Vortex profiles"
    if err := ensure_running():
        return err
    before = call("GET", "/status", timeout=20)
    r = call("POST", "/profile", {"game": GAME_ID, "profile": profile, "create": create})
    if "error" in r:
        return "ERROR: " + r["error"]
    lines = [f"{'switched' if r.get('switched') else 'already on'} {profile!r}" + (" (created)" if r.get("created") else "")]
    if remember and r.get("switched") and before.get("gameId") and before.get("profile"):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"game": before["gameId"], "profile": before["profile"]}))
        lines.append(f"previous: {before['gameId']} / {before['profile']!r} (action 'play' returns there)")
    mods = r.get("mods", [])
    enable, missing = [], []
    for q in requires or []:
        hit = [m for m in mods if q.lower() in m["name"].lower()]
        if not hit:
            missing.append(q)
        elif not any(m.get("enabled") for m in hit):
            enable.append(max(hit, key=lambda m: m.get("version") or "")["id"])
    if missing:
        lines.append("NOT INSTALLED in Vortex: " + ", ".join(missing))
    st = r
    if enable:
        st = call("POST", "/modstate", {"enable": enable})
        lines.append("enabled: " + ", ".join(enable))
    if st.get("needToDeploy") or enable:
        st = call("POST", "/deploy")
        lines.append("deployed")
    lines.append(_summary(st))
    return "\n".join(lines)


def back_to_play() -> str:
    if not STATE.exists():
        return "no previous profile recorded; switch with action 'switch' and profile=<name>"
    prev = json.loads(STATE.read_text())
    if game.running():
        return "DD2 is running: quit it before switching Vortex profiles"
    if err := ensure_running():
        return err
    r = call("POST", "/profile", {"game": prev["game"], "profile": prev["profile"]})
    if "error" in r:
        return "ERROR: " + r["error"]
    STATE.unlink()
    return f"back on {prev['game']} / {prev['profile']!r}\n" + _summary(r)


def install_release(project: str) -> str:
    """Package the project (package.build), import the zip into Vortex (bridge /import), install it in the release
    profile, enable it plus Fluffy, deploy. Vortex's DD2 extension re-zips it into Fluffy's Mods folder; enabling it
    in Fluffy (fluffy.toggle) is what writes it to the game (verified 2026-10-29)."""
    from . import package
    log = [package.build(project)]
    if "refused" in log[0] or "failed" in log[0]:
        return "\n".join(log)
    cfg = project_cfg(project)
    pkg = package.config(pathlib.Path(project))
    log.append(switch(cfg["release_profile"], cfg["requires"], create=True))
    zip_path = pathlib.Path(project) / "build" / "release" / f"{pkg.get('file', pathlib.Path(project).name)}-{pkg['version']}.zip"
    win = subprocess.run(["wslpath", "-w", str(zip_path)], capture_output=True, text=True).stdout.strip()
    mod_id = zip_path.stem
    st = call("GET", "/status", timeout=20)
    if any(m.get("id") == mod_id for m in st.get("mods", [])):
        call("POST", "/remove", {"mod": mod_id}, timeout=120)
        log.append(f"removed the old {mod_id}")
    imp = call("POST", "/import", {"paths": [win]}, timeout=60)
    if not imp.get("imported"):
        return "\n".join(log + [f"import failed (bridge 0.7.0+ needed): {imp}"])
    inst = call("POST", "/install", {"download": imp["imported"][0]["id"]}, timeout=180)
    mod = inst.get("installed")
    fluffy_mod = next((m["id"] for m in inst.get("mods", []) if m["id"].lower().startswith("fluffy mod manager")), None)
    call("POST", "/modstate", {"enable": [x for x in (mod, fluffy_mod) if x]}, timeout=60)
    dep = call("POST", "/deploy", timeout=180)
    log.append(f"installed {mod} in Vortex (+ {fluffy_mod or 'no Fluffy mod found'}), deployed: needToDeploy {dep.get('needToDeploy')}")
    log.append("next: dd2_fluffy action='enable' writes it to the game")
    return "\n".join(log)
