# Brand assets

`uptimesphere-mark.svg` is the source of truth: a ring (the sphere) that a
heartbeat runs through (the uptime). Ring and pulse share one stroke weight and
the pulse terminates on the ring's centre line, so the mark stays a single
connected shape when it is scaled down — at 16 px a crossing line breaks the
ring into fragments instead.

Palette is UptimeSphere's own: `#0a0e1a` ground, `#22d3ee` mark.

The rasterised icons under `custom_components/uptimesphere/brand/` are what HACS
reads. To regenerate them from the SVG:

```bash
python -c "from svglib.svglib import svg2rlg; from reportlab.graphics import renderPDF; \
renderPDF.drawToFile(svg2rlg('brand/uptimesphere-mark.svg'), 'mark.pdf')"
sips -s format png -Z 256 mark.pdf --out custom_components/uptimesphere/brand/icon.png
sips -s format png -Z 512 mark.pdf --out 'custom_components/uptimesphere/brand/icon@2x.png'
rm mark.pdf
```
