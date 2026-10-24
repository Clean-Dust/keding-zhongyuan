#!/usr/bin/env python3
"""Render overnight-qa/zhou-vs-sinica.png: left underlay, right SOLID 州 colors.

Right panel is flat filled colors from hex ids — never sheet JPEG textures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
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

    meta = json.loads(args.fills.read_text())
    zt = meta["zhou_table"]
    raw = args.bin.read_bytes()
    zhou = np.frombuffer(raw, dtype="<u2").reshape(MAP, MAP, 2)[:, :, 0]

    src = args.underlay if args.underlay.is_file() else args.fallback_underlay
    base = np.array(Image.open(src).convert("RGB"))
    bh, bw = base.shape[:2]

    # upsample hex ids to underlay size (nearest)
    ys = (np.arange(bh) + 0.5) / bh * MAP
    xs = (np.arange(bw) + 0.5) / bw * MAP
    zhou_u = zhou[
        np.clip(ys.astype(int), 0, MAP - 1)[:, None],
        np.clip(xs.astype(int), 0, MAP - 1)[None, :],
    ]

    # Stable distinct solid palette (not pastel scan-like)
    rng = np.random.default_rng(7)
    pal = np.zeros((max(len(zt), 2), 3), dtype=np.uint8)
    pal[0] = (245, 245, 240)  # empty = light paper, not white holes in ocean
    for i in range(1, len(zt)):
        # saturated mid-tones
        h = rng.random()
        s = 0.55 + 0.25 * rng.random()
        v = 0.70 + 0.15 * rng.random()
        # HSV → RGB
        import colorsys

        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        pal[i] = (int(r * 255), int(g * 255), int(b * 255))

    # RIGHT: solid color plane only — no sheet JPEG, no border etch (avoids fake seams)
    lum = base.mean(axis=2)
    sea = lum < 90
    solid = pal[np.clip(zhou_u, 0, len(pal) - 1)].copy()
    solid[sea & (zhou_u == 0)] = (40, 70, 110)  # sea blue
    # empty land (unlabeled) → same sea/land treatment, never bright white
    empty_land = (~sea) & (zhou_u == 0)
    solid[empty_land] = (210, 205, 195)

    # LEFT: Sinica underlay unchanged
    side = np.concatenate([base, solid], axis=1)
    im = Image.fromarray(side)
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 26)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font

    for zi, name in enumerate(zt):
        if zi == 0:
            continue
        ys_, xs_ = np.where(zhou_u == zi)
        if len(ys_) < 80:
            continue
        cy, cx = int(np.median(ys_)), int(np.median(xs_))
        dr.text((cx + bw + 3, cy + 3), name, fill=(0, 0, 0), font=font)
        dr.text((cx + bw, cy), name, fill=(220, 30, 30), font=font)

    dr.text(
        (16, 12),
        "LEFT: Sinica ad0262 underlay",
        fill=(15, 15, 15),
        font=font_sm,
    )
    dr.text(
        (bw + 16, 12),
        "RIGHT: Tan-262 solid 州 hex (purple-boundary flood) — no sheet texture",
        fill=(15, 15, 15),
        font=font_sm,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.out, optimize=True)
    print(f"wrote {args.out} shape={side.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
