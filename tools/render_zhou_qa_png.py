#!/usr/bin/env python3
"""Render overnight-qa/zhou-vs-sinica.png: left underlay, right SOLID 州 colors.

交州 / 幽州: expand onto nearby land, fill interior holes, keep ONE connected
component, never paint open sea. Other 州: paint on land only (no sea overhang).
"""
from __future__ import annotations

import argparse
import colorsys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

MAP = 500
COAST_ZHOU = ("交州", "幽州")


def underlay_land_sea(base: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (paintable land, open sea).

    Paintable land is *bright beige* only. Mid-tone coastline ink (lum ~120–160)
    next to the ocean reads as “blue in the sea” if painted, so it stays unpainted.
    """
    r = base[:, :, 0].astype(np.float32)
    g = base[:, :, 1].astype(np.float32)
    b = base[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    open_sea = (lum < 110) | ((b > r + 15) & (b > g + 10) & (lum < 165))
    # Exterior ocean (border-connected dark water) — never paint here.
    sea_lab, _ = ndimage.label(open_sea | (lum < 110))
    border = set(sea_lab[0]) | set(sea_lab[-1]) | set(sea_lab[:, 0]) | set(sea_lab[:, -1])
    border.discard(0)
    ext_sea = np.isin(sea_lab, list(border))
    collar = ndimage.binary_dilation(ext_sea, iterations=1)
    # Sheet land: beige-tan. Mid-tone OK inland; the 1px ocean collar blocks
    # hex stair-steps from reading as blue-in-the-sea.
    land = (~open_sea) & (lum > 150) & (r > 135) & (r + g > b + 35)
    land = ndimage.binary_fill_holes(land) & ~collar
    sea = ext_sea | ((lum < 150) & ~land)
    sea = sea & ~land
    return land, sea


def largest_cc(mask: np.ndarray, *, eight: bool = False) -> np.ndarray:
    structure = np.ones((3, 3), dtype=bool) if eight else None
    lab, n = ndimage.label(mask, structure=structure)
    if n <= 0:
        return mask
    counts = np.bincount(lab.ravel())
    counts[0] = 0
    keep = int(np.argmax(counts))
    return lab == keep


def solid_coast_mask(
    zhou_u: np.ndarray,
    zid: int,
    land: np.ndarray,
    other: np.ndarray,
    max_dist: float = 20.0,
) -> np.ndarray:
    """One solid land region for a coastal 州: expand, fill land holes, drop specks.

    Never fill through sea bays — that would bridge land, then clearing sea
    would re-shatter the region into fragments.
    """
    seed = (zhou_u == zid) & land
    if not seed.any():
        return seed
    dist = ndimage.distance_transform_edt(~seed)
    m = land & (~other) & (dist <= max_dist) & ((zhou_u == 0) | (zhou_u == zid))
    m |= seed
    # Close small land gaps so coastal hex stair-steps fuse into one body.
    m = ndimage.binary_closing(m, structure=np.ones((5, 5), dtype=bool), iterations=2)
    m &= land & ~other
    m = largest_cc(m)
    # Fill enclosed *land* voids only (beige Swiss cheese), not ocean bays.
    filled = ndimage.binary_fill_holes(m)
    land_holes = filled & ~m & land & ~other
    m = m | land_holes
    return largest_cc(m)


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
    land, sea = underlay_land_sea(base)

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

    coast_ids = {zt.index(n) for n in COAST_ZHOU if n in zt}
    other = np.zeros((bh, bw), dtype=bool)
    for i in range(1, len(zt)):
        if i not in coast_ids:
            other |= (zhou_u == i) & land

    solid = base.copy()
    painted = np.zeros((bh, bw), dtype=bool)

    # Non-coast 州: land only (blocks NN hex overhang into the sea).
    for i in range(1, len(zt)):
        if i in coast_ids:
            continue
        m = (zhou_u == i) & land
        solid[m] = pal[i]
        painted[m] = True

    # 交州 / 幽州: one solid land CC each, no sea paint, no Swiss cheese.
    for name in COAST_ZHOU:
        if name not in zt:
            continue
        zid = zt.index(name)
        m = solid_coast_mask(zhou_u, zid, land, other, max_dist=20.0)
        solid[m] = pal[zid]
        painted[m] = True

    # Safety: never leave paint on open sea.
    bleed = painted & sea
    if bleed.any():
        solid[bleed] = base[bleed]
        print(f"sea_bleed_restored={int(bleed.sum())}")

    # Final guarantee: each coastal 州 is exactly one CC of its palette color
    # (drops any stray land specks left by closing / upsample).
    for name in COAST_ZHOU:
        if name not in zt:
            continue
        zid = zt.index(name)
        col = pal[zid]
        match = (
            (solid[:, :, 0] == col[0])
            & (solid[:, :, 1] == col[1])
            & (solid[:, :, 2] == col[2])
        )
        keep = largest_cc(match)
        drop = match & ~keep
        if drop.any():
            solid[drop] = base[drop]
        print(
            f"{name}: solid_land_px={int(keep.sum())} cc=1 "
            f"dropped_specks={int(drop.sum())} sea_overlap={int((keep & sea).sum())}"
        )

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
        ys_, xs_ = np.where(
            (solid[:, :, 0] == pal[zi][0])
            & (solid[:, :, 1] == pal[zi][1])
            & (solid[:, :, 2] == pal[zi][2])
        )
        if len(ys_) < 80:
            continue
        cy, cx = int(np.median(ys_)), int(np.median(xs_))
        dr.text((cx + bw + 2, cy + 2), name, fill=(0, 0, 0), font=font)
        dr.text((cx + bw, cy), name, fill=(220, 30, 30), font=font)

    # Labels punch holes in the fill and can isolate tiny blue islands — drop them.
    arr = np.array(im)
    right = arr[:, bw:]
    for name in COAST_ZHOU:
        if name not in zt:
            continue
        col = pal[zt.index(name)]
        match = (
            (right[:, :, 0] == col[0])
            & (right[:, :, 1] == col[1])
            & (right[:, :, 2] == col[2])
        )
        keep = largest_cc(match, eight=True)
        drop = match & ~keep
        if drop.any():
            right[drop] = base[drop]
            print(f"{name}: post-label dropped_specks={int(drop.sum())}")
    arr[:, bw:] = right
    im = Image.fromarray(arr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.out, optimize=True)
    print(f"wrote {args.out} shape={side.shape} painted_frac={painted.mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
