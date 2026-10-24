#!/usr/bin/env python3
"""Extract 州 faces from purple boundary ink on Tan-262 分幅 sheets.

Detects purple/magenta boundary pixels, seals them, flood-fills land pockets,
labels each pocket by nearest Tan 州 seat label, then splatters onto the
common 500×500 lon/lat hex grid used by zhou_jun_fills.bin.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W = MAP_H = 500


def pix_to_lonlat(g: dict, x: float, y: float) -> tuple[float, float]:
    return float(g["lon_a"] * x + g["lon_b"]), float(g["lat_c"] * y + g["lat_d"])


def lonlat_to_hex(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return col, row


def purple_mask(im: np.ndarray) -> np.ndarray:
    """Purple/magenta 州 boundaries on Tan plates (not brown 郡 lines)."""
    r = im[:, :, 0].astype(np.float32)
    g = im[:, :, 1].astype(np.float32)
    b = im[:, :, 2].astype(np.float32)
    # purple: R and B high relative to G; not near-black, not paper
    purple = (r > 90) & (b > 90) & (g < r * 0.85) & (g < b * 0.9) & ((r + b) > 1.6 * g + 40)
    # also catch darker violet lines
    violet = (r > 70) & (b > 100) & (b > g + 25) & (r > g + 10) & (r + g + b < 520)
    return purple | violet


SHEET_PRIMARY = {
    "03-jiaozhou.jpg": ("交州",),
    "04-yangzhou-wu.jpg": ("扬州",),
    "05-jingzhou-wu.jpg": ("荆州",),
    "06-yizhou-north.jpg": ("益州", "雍州"),
    "07-yizhou-south.jpg": ("益州",),
    "09-sizhou.jpg": ("司州",),
    "10-yan-yu-yang.jpg": ("兖州", "豫州", "扬州"),
    "11-qing-xu.jpg": ("青州", "徐州"),
    "12-youzhou.jpg": ("幽州",),
    "13-liangzhou.jpg": ("凉州",),
    "14-jingzhou-wei.jpg": ("荆州", "豫州"),
    "15-yongzhou.jpg": ("雍州",),
    "16-ji-bing.jpg": ("冀州", "并州"),
}


def sheet_zhou_raster(
    path: Path,
    g: dict,
    zhou_seeds: list[tuple[str, float, float]],
    scale: float = 0.5,
) -> dict[str, np.ndarray]:
    """Return dict zhou_name -> boolean mask on sheet (downscaled)."""
    im_full = Image.open(path).convert("RGB")
    if scale != 1.0:
        im = im_full.resize(
            (int(im_full.width * scale), int(im_full.height * scale)),
            Image.Resampling.BILINEAR,
        )
    else:
        im = im_full
    arr = np.asarray(im)
    h, w, _ = arr.shape
    pur = purple_mask(arr)
    wall = ndimage.binary_dilation(pur, iterations=2)
    wall = ndimage.binary_closing(wall, iterations=2)
    # land ≈ not bright paper / not deep sea-blue
    lum = arr.mean(axis=2)
    sea = (arr[:, :, 2] > arr[:, :, 0] + 15) & (arr[:, :, 2] > arr[:, :, 1] + 10) & (lum < 200)
    paper = lum > 235
    land = (~wall) & (~sea) & (~paper)
    labeled, n = ndimage.label(land)
    primary = SHEET_PRIMARY.get(path.name, ())
    # map each component to nearest 州 seed (in sheet pixels)
    seed_xy: list[tuple[str, float, float]] = []
    for name, lon, lat in zhou_seeds:
        # Prefer seeds for this sheet's primary 州 (and any seed on-sheet)
        x = (lon - g["lon_b"]) / g["lon_a"]
        y = (lat - g["lat_d"]) / g["lat_c"]
        x *= scale
        y *= scale
        if 0 <= x < w and 0 <= y < h:
            seed_xy.append((name, x, y))
    # If a primary 州 has no on-sheet seed, plant one at sheet center
    have = {n for n, _, _ in seed_xy}
    for pname in primary:
        if pname not in have:
            seed_xy.append((pname, w / 2.0, h / 2.0))
    comp_zhou: dict[int, str] = {}
    for cid in range(1, n + 1):
        ys, xs = np.where(labeled == cid)
        if len(ys) < 80:
            continue
        cy, cx = float(ys.mean()), float(xs.mean())
        best, bestd = "", 1e18
        for name, sx, sy in seed_xy:
            d = (cx - sx) ** 2 + (cy - sy) ** 2
            # Strong bias: primary 州 of this sheet win ties / near ties
            if primary and name in primary:
                d *= 0.15
            if d < bestd:
                bestd, best = d, name
        if best:
            comp_zhou[cid] = best
    masks: dict[str, np.ndarray] = defaultdict(lambda: np.zeros((h, w), dtype=bool))
    for cid, name in comp_zhou.items():
        masks[name][labeled == cid] = True
    return {k: v for k, v in masks.items()}


def splat_to_hex(masks: dict[str, np.ndarray], g: dict, scale: float, votes: dict[str, np.ndarray]):
    for name, mask in masks.items():
        ys, xs = np.where(mask)
        # subsample for speed
        for y, x in zip(ys[::2], xs[::2]):
            lon, lat = pix_to_lonlat(g, x / scale, y / scale)
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue
            col, row = lonlat_to_hex(lon, lat)
            if 0 <= col < MAP_W and 0 <= row < MAP_H:
                # encode name via side channel — caller uses index
                votes[name][row, col] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--georef", type=Path, default=Path("/tmp/tan262/georef/all_georef.json"))
    ap.add_argument("--labels", type=Path, default=Path("/tmp/tan262/labels_all.json"))
    ap.add_argument("--out-hex", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.json"))
    ap.add_argument("--scale", type=float, default=0.45)
    # Skip overview / xianbei / non-州 sheets for face build
    ap.add_argument(
        "--skip",
        type=str,
        default="01-overview.jpg,02-xiyu.jpg,08-xianbei.jpg",
    )
    args = ap.parse_args()

    georef = json.loads(args.georef.read_text())
    labels = json.loads(args.labels.read_text())["labels"]
    CANON = {
        "交州",
        "扬州",
        "荆州",
        "益州",
        "司州",
        "兖州",
        "豫州",
        "青州",
        "徐州",
        "幽州",
        "凉州",
        "雍州",
        "冀州",
        "并州",
    }
    ALIAS = {
        "司隶": "司州",
        "蜀郡益州": "益州",
        "燕国幽州": "幽州",
        "扶柳冀州": "冀州",
        "南郡荆州": "荆州",
        "湖北荆州": "荆州",
        "丹阳郡扬州": "扬州",
        "下邳郡徐州": "徐州",
        "齐国州": "青州",
    }
    zhou_seeds = []
    for l in labels:
        if l["kind"] != "zhou" or l["conf"] < 0.7:
            continue
        raw = l["text"]
        name = ALIAS.get(raw, raw)
        # glued …州 → last canon 州
        for c in CANON:
            if raw.endswith(c) or c in raw:
                name = c
                break
        if name == "司隶":
            name = "司州"
        if name in CANON:
            zhou_seeds.append((name, l["lon"], l["lat"]))
    print(f"州 seeds from labels: {len(zhou_seeds)} canon-only", flush=True)

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    votes: dict[str, np.ndarray] = defaultdict(lambda: np.zeros((MAP_H, MAP_W), dtype=np.uint16))

    for path in sorted(args.sheets.glob("*.jpg")):
        if path.name in skip:
            continue
        g = georef.get(path.name)
        if not g or not g.get("ok"):
            print(f"skip {path.name}: no georef", flush=True)
            continue
        print(f"purple {path.name}", flush=True)
        masks = sheet_zhou_raster(path, g, zhou_seeds, scale=args.scale)
        print(f"  pockets: { {k: int(v.sum()) for k,v in masks.items()} }", flush=True)
        splat_to_hex(masks, g, args.scale, votes)

    # winner-take-all hex
    names = sorted(votes.keys())
    hex_zhou = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    name_to_id = {n: i + 1 for i, n in enumerate(names)}
    id_to_name = {i: n for n, i in name_to_id.items()}
    stack = np.stack([votes[n] for n in names], axis=0) if names else np.zeros((0, MAP_H, MAP_W))
    if len(names):
        best = stack.argmax(axis=0)
        mx = stack.max(axis=0)
        hex_zhou = np.where(mx > 0, best + 1, 0).astype(np.uint8)

    counts = Counter(id_to_name[int(i)] for i in hex_zhou.ravel() if i)
    payload = {
        "zhou_names": [""] + names,
        "counts": dict(counts),
        "n_filled": int((hex_zhou > 0).sum()),
        "hex": hex_zhou.tolist(),  # heavy but ok for /tmp
    }
    # save compact bin + meta instead of giant json
    bin_path = args.out_hex.with_suffix(".bin")
    bin_path.write_bytes(hex_zhou.astype(np.uint8).tobytes())
    meta = {
        "zhou_names": [""] + names,
        "counts": dict(counts),
        "n_filled": int((hex_zhou > 0).sum()),
        "bin": str(bin_path),
        "layout": "uint8 zhou_idx row-major 500x500",
        "authority": "Tan-262 purple 州 boundaries",
    }
    args.out_hex.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"wrote {args.out_hex} filled={meta['n_filled']} counts={meta['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
