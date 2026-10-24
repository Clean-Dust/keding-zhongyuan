#!/usr/bin/env python3
"""Render overnight-qa/zhou-vs-sinica.png: left underlay, right SOLID 州 colors.

Right panel paints only hexes with a 州 id (no sea, no 羌胡/empty west).
No debug captions.
"""
from __future__ import annotations

import argparse
import colorsys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MAP = 500


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fills", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--underlay", type=Path, default=Path("/tmp/sinica_ad0262_z8.jpg"))
    ap.add_argument(
        "--fallback-underlay",
        type=Path,
        default=Path("data/sinica_ad0262_underlay.jpg"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(
            "/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/overnight-qa/zhou-vs-sinica.png"
        ),
    )
    args = ap.parse_args()

    import json

    meta = json.loads(args.fills.read_text())
    zt = meta["zhou_table"]
    zhou = np.frombuffer(args.bin.read_bytes(), dtype="<u2").reshape(MAP, MAP, 2)[:, :, 0]

    src = args.underlay if args.underlay.is_file() else args.fallback_underlay
    base = np.array(Image.open(src).convert("RGB"))
    bh, bw = base.shape[:2]

    ys = (np.arange(bh) + 0.5) / bh * MAP
    xs = (np.arange(bw) + 0.5) / bw * MAP
    zhou_u = zhou[
        np.clip(ys.astype(int), 0, MAP - 1)[:, None],
        np.clip(xs.astype(int), 0, MAP - 1)[None, :],
    ]

    rng = np.random.default_rng(7)
    pal = np.zeros((max(len(zt), 2), 3), dtype=np.uint8)
    for i in range(1, len(zt)):
        h = rng.random()
        s = 0.55 + 0.25 * rng.random()
        v = 0.70 + 0.15 * rng.random()
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        pal[i] = (int(r * 255), int(g * 255), int(b * 255))

    # RIGHT: solid-fill every hex that has a 州 id. Do not require underlay beige —
    # rivers/ink/grid inside a 州 were leaving white holes. Still skip dark sea.
    solid = base.copy()
    r = base[:, :, 0].astype(np.float32)
    g = base[:, :, 1].astype(np.float32)
    b = base[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    # Dark open water only — light blue grid/shelf on land must not punch holes.
    sea = (lum < 95) | ((b > r + 20) & (b > g + 15) & (lum < 140))
    painted = (zhou_u > 0) & (~sea)
    solid[painted] = pal[zhou_u[painted]]

    side = np.concatenate([base, solid], axis=1)
    im = Image.fromarray(side)
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 26)
    except Exception:
        font = ImageFont.load_default()

    for zi, name in enumerate(zt):
        if zi == 0:
            continue
        ys_, xs_ = np.where(zhou_u == zi)
        if len(ys_) < 80:
            continue
        cy, cx = int(np.median(ys_)), int(np.median(xs_))
        dr.text((cx + bw + 2, cy + 2), name, fill=(0, 0, 0), font=font)
        dr.text((cx + bw, cy), name, fill=(220, 30, 30), font=font)

    # No debug captions.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.out, optimize=True)
    print(f"wrote {args.out} shape={side.shape} painted_frac={painted.mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
