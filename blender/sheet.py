"""Assemble render_poses.py stills into one sheet per action: rows = views, columns = frames. python sheet.py <dir>"""
import sys, glob, os, re
from PIL import Image, ImageDraw
d = sys.argv[1]
groups = {}
for f in glob.glob(os.path.join(d, "_*.png")):
    m = re.match(r"_(.+)_(front|side|three_q)_(\d+)\.png$", os.path.basename(f))
    if m: groups.setdefault(m.group(1), {}).setdefault(m.group(2), []).append(f)
for name, views in groups.items():
    rows = [sorted(views[v]) for v in ("front", "side", "three_q") if v in views]
    w, h = Image.open(rows[0][0]).size
    cols = max(len(r) for r in rows)
    out = Image.new("RGB", (cols * w, len(rows) * h + 24), (20, 20, 24))
    ImageDraw.Draw(out).text((6, 6), name, fill="yellow")
    for ri, r in enumerate(rows):
        for ci, f in enumerate(r):
            out.paste(Image.open(f), (ci * w, 24 + ri * h))
    out.save(os.path.join(d, name + ".png"))
    for r in rows:
        for f in r: os.remove(f)
    print(os.path.join(d, name + ".png"))
