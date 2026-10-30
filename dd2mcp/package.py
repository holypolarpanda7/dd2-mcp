"""Release packaging for DD2 mod projects: one Fluffy-format zip that both Fluffy Mod Manager and Vortex install.

Both routes end in Fluffy (checked 2026-10-29): Vortex's DD2 extension (1.0.2) re-zips any mod archive that is not
REFramework or Fluffy itself into <game>/Games/DragonsDogma2/Mods, where Fluffy enables it. Fluffy reads modinfo.ini at
the mod root and copies everything else relative to the game folder; .pak files at the root are installed as pak mods
(DD2: into pak_mods, which REFramework's pak loader injects).

Zip layout:  modinfo.ini, [screenshot.png], <Name>.pak (from build/), reframework/** (from src/reframework/, minus
package.exclude). Metadata comes from <project>/dd2mod.json "package"."""
import json
import pathlib
import subprocess
import zipfile

from . import game

DD2MCP_ENV = {"UV_PROJECT_ENVIRONMENT": str(game.SCRATCH / "venv")}


def config(project: pathlib.Path) -> dict:
    cfg = json.loads((project / "dd2mod.json").read_text()).get("package")
    if not cfg:
        raise ValueError(f"{project}/dd2mod.json has no 'package' section")
    for key in ("name", "version", "author"):
        if not cfg.get(key):
            raise ValueError(f"dd2mod.json package.{key} is missing")
    return cfg


def modinfo(cfg: dict) -> str:
    desc = cfg.get("description", "")
    if cfg.get("requires"):
        desc += " Requires: " + ", ".join(cfg["requires"]) + "."
    lines = [f"name={cfg['name']}", f"version={cfg['version']}", f"author={cfg['author']}", f"description={desc}"]
    if cfg.get("category"):
        lines.append(f"category={cfg['category']}")
    if cfg.get("screenshot"):
        lines.append(f"screenshot={pathlib.Path(cfg['screenshot']).name}")
    return "\r\n".join(lines) + "\r\n"


def build(project: str, run_build: bool = True) -> str:
    root = pathlib.Path(project).resolve()
    cfg = config(root)
    log = []
    if run_build and cfg.get("build"):
        import os
        r = subprocess.run(["uv", "run", "-q", "--directory", str(pathlib.Path(__file__).resolve().parents[1]),
                            "python", str(root / cfg["build"])], capture_output=True, text=True, timeout=600,
                           env={**os.environ, **DD2MCP_ENV})
        if r.returncode:
            return f"build step failed ({cfg['build']}):\n{r.stdout}{r.stderr}"
        log.append(f"built: {r.stdout.strip()}")
    exclude = {e.replace("\\", "/").lower() for e in cfg.get("exclude", [])}
    src = root / "src" / "reframework"
    entries: dict[str, pathlib.Path] = {}
    for f in sorted(src.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            rel = ("reframework/" + f.relative_to(src).as_posix())
            if rel.lower() not in exclude:
                entries[rel] = f
    for p in sorted((root / "build").glob("*.pak")):
        entries[p.name] = p
    if cfg.get("screenshot"):
        shot = root / cfg["screenshot"]
        entries[shot.name] = shot
    problems = check(entries, cfg)
    if problems:
        return "\n".join(log + ["refused:"] + problems)
    out = root / "build" / "release" / f"{cfg.get('file', root.name)}-{cfg['version']}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("modinfo.ini", modinfo(cfg))
        for rel, f in entries.items():
            z.write(f, rel)
    log.append(f"{out} ({out.stat().st_size // 1024} KB, {len(entries) + 1} files):")
    log += ["  modinfo.ini"] + [f"  {rel}" for rel in entries]
    return "\n".join(log)


def check(entries: dict, cfg: dict) -> list[str]:
    """Release sanity: no dev/test-only files, scripts present, every pak readable."""
    from . import pak
    problems = []
    if not any(r.startswith("reframework/autorun/") and r.endswith(".lua") for r in entries):
        problems.append("no reframework/autorun/*.lua in the package")
    for rel in entries:
        low = rel.lower()
        if "/dev." in low or low.endswith(".bak") or "/test" in low:
            problems.append(f"dev/test file in the package: {rel} (add it to package.exclude)")
    for rel, f in entries.items():
        if rel.endswith(".pak"):
            try:
                n = len(pak.Pak(f).entries)
                if not n:
                    problems.append(f"{rel} holds no files")
            except Exception as e:  # noqa: BLE001 - report any unreadable pak
                problems.append(f"{rel} is not a readable pak: {e}")
    return problems


def release(project: str, apply: bool = False) -> str:
    """Publish step: a DRAFT GitHub release v<version> on the project's repo with the release zip attached.
    Refuses unless the project is on main, clean and pushed, and the tag is new (mods release from main after the
    dev -> main PR and the user's go-ahead). apply=False only reports what it would do. A draft is private to the
    repo's owners until published on GitHub; Nexus Mods has no upload API, so Nexus uploads stay manual."""
    root = pathlib.Path(project).resolve()
    cfg = config(root)
    tag = f"v{cfg['version']}"

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    problems = []
    if branch != "main":
        problems.append(f"on branch {branch!r}, releases come from main (merge the dev -> main PR first)")
    if git("status", "--porcelain").stdout.strip():
        problems.append("working tree has uncommitted changes")
    git("fetch", "-q", "origin")
    if git("rev-list", "--count", "@{u}..HEAD").stdout.strip() not in ("", "0"):
        problems.append("main has unpushed commits")
    if git("tag", "-l", tag).stdout.strip() or git("ls-remote", "--tags", "origin", tag).stdout.strip():
        problems.append(f"tag {tag} already exists: bump package.version in dd2mod.json")
    built = build(project)
    if "refused" in built or "failed" in built:
        problems.append(built)
    zip_path = root / "build" / "release" / f"{cfg.get('file', root.name)}-{cfg['version']}.zip"
    notes = f"{cfg['name']} {cfg['version']}\n\n{cfg.get('description', '')}\n\nInstall with Fluffy Mod Manager or Vortex " \
            f"(Vortex hands it to Fluffy). Requires: {', '.join(cfg.get('requires', [])) or 'REFramework'}."
    plan = f"gh release create {tag} {zip_path.name} --draft --target main --title '{cfg['name']} {cfg['version']}'"
    if problems:
        return "not releasing:\n- " + "\n- ".join(problems) + f"\n(would run: {plan})"
    if not apply:
        return f"ready. apply=True would run:\n{plan}\nnotes:\n{notes}"
    r = subprocess.run(["gh", "release", "create", tag, str(zip_path), "--draft", "--target", "main",
                        "--title", f"{cfg['name']} {cfg['version']}", "--notes", notes],
                       cwd=root, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip() or "created"
