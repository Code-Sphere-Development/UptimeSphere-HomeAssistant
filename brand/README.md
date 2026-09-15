# Brand assets

`uptimesphere-logo.png` is the master artwork (1254x1254, transparent): a globe
inside a green-to-cyan ring, crossed by a pulse line.

The icons HACS reads live in `custom_components/uptimesphere/brand/` and are
scaled from the master. To regenerate them:

```bash
python - <<'PY'
from PIL import Image
src = Image.open("brand/uptimesphere-logo.png").convert("RGBA")

# Trim to the artwork, re-centre on a square canvas with a 4% margin, so the
# crop is defined rather than inherited from the export.
bbox = src.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
art = src.crop(bbox)
side = max(art.size)
pad = round(side * 0.04)
canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
canvas.paste(art, (pad + (side - art.size[0]) // 2,
                   pad + (side - art.size[1]) // 2), art)

for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
    canvas.resize((size, size), Image.LANCZOS).save(
        f"custom_components/uptimesphere/brand/{name}")
PY
```

Plain LANCZOS, no sharpening: an unsharp pass shifts the green towards violet
and leaves halos around the ring.

The artwork is raster only. Below roughly 32 px the globe's meridians blur
together, so the browser-tab favicon is the weakest size; a simplified
small-size variant would need a vector master.
