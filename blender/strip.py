"""Join labelled images in a row: python strip.py out.png label=file ..."""
import sys
from PIL import Image, ImageDraw
items = [a.split("=", 1) for a in sys.argv[2:]]
ims = [Image.open(f) for _, f in items]
w, h = ims[0].size
out = Image.new("RGB", (w * len(ims), h + 20), (20, 20, 24))
for i, ((lab, _), im) in enumerate(zip(items, ims)):
    out.paste(im, (i * w, 20)); ImageDraw.Draw(out).text((i * w + 4, 4), lab, fill="yellow")
out.save(sys.argv[1])
