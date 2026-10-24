#!/usr/bin/env python3
"""Build continuous solid 州 hex fills from Tan-262 purple boundaries.

Each sheet is georeferenced by its printed degree grid. Purple ink becomes
barriers in a shared 500×500 lon/lat hex grid. 州 seats flood through coast
land until purple barriers — one continuous map, no scan splat / white holes.

Does not copy JPEG scans into the git repo.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W = MAP_H = 500

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

SHEET_PRIMARY = {
    "03-jiaozhou.jpg": ("交州",),
    "04-yangzhou-wu.jpg": ("扬州",),
    "05-jingzhou-wu.jpg": ("荆州",),
    "06-yizhou-north.jpg": ("益州", "雍州"),
    "07-yizhou-south.jpg": ("益州",),
    "09-sizhou.jpg": ("司州",),
    "10-yan-yu-yang.jpg": ("兖州", "豫州"),
    "11-qing-xu.jpg": ("青州", "徐州"),
    "12-youzhou.jpg": ("幽州",),
    "13-liangzhou.jpg": ("凉州",),
    "14-jingzhou-wei.jpg": ("荆州", "豫州"),
    "15-yongzhou.jpg": ("雍州",),
    "16-ji-bing.jpg": ("冀州", "并州"),
}

SKIP = {"01-overview.jpg", "02-xiyu.jpg", "08-xianbei.jpg"}


def lonlat_to_hex(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return col, row


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def purple_mask(im: np.ndarray) -> np.ndarray:
    r = im[:, :, 0].astype(np.float32)
    g = im[:, :, 1].astype(np.float32)
    b = im[:, :, 2].astype(np.float32)
    purple = (r > 90) & (b > 90) & (g < r * 0.85) & (g < b * 0.9) & ((r + b) > 1.6 * g + 40)
    violet = (r > 70) & (b > 100) & (b > g + 25) & (r > g + 10) & (r + g + b < 520)
    return purple | violet


def mark_purple_barriers(path: Path, g: dict, barrier: np.ndarray, scale: float = 0.35) -> int:
    """Project sheet purple ink onto hex barrier grid. Returns #hexes marked."""
    im_full = Image.open(path).convert("RGB")
    im = im_full.resize(
        (max(1, int(im_full.width * scale)), max(1, int(im_full.height * scale))),
        Image.Resampling.BILINEAR,
    )
    arr = np.asarray(im)
    pur = purple_mask(arr)
    # thicken so hex barrier is continuous
    pur = ndimage.binary_dilation(pur, iterations=2)
    ys, xs = np.where(pur)
    n = 0
    # subsample for speed but keep density high enough for continuity
    step = 1
    for y, x in zip(ys[::step], xs[::step]):
        # map downscaled pixel → full sheet → lon/lat
        fx = x / scale
        fy = y / scale
        lon = g["lon_a"] * fx + g["lon_b"]
        lat = g["lat_c"] * fy + g["lat_d"]
        if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
            continue
        col, row = lonlat_to_hex(lon, lat)
        if 0 <= col < MAP_W and 0 <= row < MAP_H:
            if not barrier[row, col]:
                n += 1
            barrier[row, col] = True
            # also mark 4-neighbors so thin lines seal hex gaps
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc, nr = col + dx, row + dy
                if 0 <= nc < MAP_W and 0 <= nr < MAP_H:
                    barrier[nr, nc] = True
    return n


def flood_zhou(
    coast: np.ndarray,
    barrier: np.ndarray,
    seeds: list[tuple[int, int, int]],
) -> np.ndarray:
    """Flood 州 ids through coast & ~barrier from seat hexes."""
    out = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    dist = np.full((MAP_H, MAP_W), 10**9, dtype=np.int32)
    q: deque[tuple[int, int, int, int]] = deque()
    for col, row, zid in seeds:
        if not (0 <= col < MAP_W and 0 <= row < MAP_H):
            continue
        # nudge off barrier / off-coast onto nearby land
        if coast[row, col] == 0 or barrier[row, col]:
            found = False
            for rad in range(1, 20):
                for dy in range(-rad, rad + 1):
                    for dx in range(-rad, rad + 1):
                        nc, nr = col + dx, row + dy
                        if (
                            0 <= nc < MAP_W
                            and 0 <= nr < MAP_H
                            and coast[nr, nc]
                            and not barrier[nr, nc]
                        ):
                            col, row = nc, nr
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                continue
        out[row, col] = zid
        dist[row, col] = 0
        q.append((col, row, zid, 0))

    while q:
        col, row, zid, d = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = col + dx, row + dy
            if not (0 <= nc < MAP_W and 0 <= nr < MAP_H):
                continue
            if coast[nr, nc] == 0 or barrier[nr, nc]:
                continue
            nd = d + 1
            if nd >= dist[nr, nc]:
                continue
            # first-arrival owns the cell (nearest seat in hex steps)
            out[nr, nc] = zid
            dist[nr, nc] = nd
            q.append((nc, nr, zid, nd))
    return out


def fill_interior_holes(zhou: np.ndarray, coast: np.ndarray, barrier: np.ndarray) -> np.ndarray:
    """Fill empty coast pockets fully enclosed by a single 州 (no white holes)."""
    out = zhou.copy()
    empty = (out == 0) & (coast > 0) & (~barrier)
    labeled, n = ndimage.label(empty)
    for cid in range(1, n + 1):
        ys, xs = np.where(labeled == cid)
        if len(ys) == 0 or len(ys) > 8000:
            continue
        # neighbors of the pocket
        neigh: set[int] = set()
        touches_map_edge = False
        for y, x in zip(ys, xs):
            if y == 0 or x == 0 or y == MAP_H - 1 or x == MAP_W - 1:
                touches_map_edge = True
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if not (0 <= nx < MAP_W and 0 <= ny < MAP_H):
                    touches_map_edge = True
                    continue
                v = int(out[ny, nx])
                if v:
                    neigh.add(v)
        if touches_map_edge:
            continue
        if len(neigh) == 1:
            out[labeled == cid] = next(iter(neigh))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--georef", type=Path, default=Path("/tmp/tan262/georef/all_georef.json"))
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-bin", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.json"))
    ap.add_argument("--scale", type=float, default=0.4)
    args = ap.parse_args()

    georef = json.loads(args.georef.read_text())
    pts = json.loads(args.points.read_text())
    coast = np.frombuffer(args.coast.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)

    barrier = np.zeros((MAP_H, MAP_W), dtype=bool)
    for path in sorted(args.sheets.glob("*.jpg")):
        if path.name in SKIP:
            continue
        g = georef.get(path.name)
        if not g or not g.get("ok"):
            print(f"skip {path.name}: no georef", flush=True)
            continue
        n = mark_purple_barriers(path, g, barrier, scale=args.scale)
        print(f"barrier {path.name}: +{n} hex (total {int(barrier.sum())})", flush=True)

    # Soften: barrier hexes that are seats get cleared so flood can start
    zhou_names = [""] + [z["zhou"] for z in pts["zhou"] if z["zhou"] in CANON]
    zhou_index = {n: i for i, n in enumerate(zhou_names) if n}
    seeds: list[tuple[int, int, int]] = []
    for z in pts["zhou"]:
        zid = zhou_index.get(z["zhou"])
        if not zid:
            continue
        col, row = lonlat_to_hex(z["lon"], z["lat"])
        seeds.append((col, row, zid))
        if 0 <= col < MAP_W and 0 <= row < MAP_H:
            barrier[row, col] = False
    # also clear around 郡治 of each 州 (extra seeds for disconnected pockets)
    for j in pts["jun"]:
        zid = zhou_index.get(j.get("zhou", ""))
        if not zid:
            continue
        col, row = lonlat_to_hex(j["lon"], j["lat"])
        seeds.append((col, row, zid))
        if 0 <= col < MAP_W and 0 <= row < MAP_H:
            barrier[row, col] = False

    print(f"seeds={len(seeds)} barrier_hex={int(barrier.sum())}", flush=True)
    zhou = flood_zhou(coast, barrier, seeds)
    print(f"after flood filled={int((zhou > 0).sum())}", flush=True)
    zhou = fill_interior_holes(zhou, coast, barrier)
    print(f"after hole-fill filled={int((zhou > 0).sum())}", flush=True)

    # Second pass: empty coast near a 州 seat-ball, still blocked by barrier
    # (covers thin purple-miss regions without crossing purple)
    SEAT_R2 = 2.4**2
    seats_by_z: dict[int, list[tuple[float, float]]] = {i: [] for i in zhou_index.values()}
    for z in pts["zhou"]:
        zid = zhou_index.get(z["zhou"])
        if zid:
            seats_by_z[zid].append((z["lon"], z["lat"]))
    for j in pts["jun"]:
        zid = zhou_index.get(j.get("zhou", ""))
        if zid:
            seats_by_z[zid].append((j["lon"], j["lat"]))
    n_extra = 0
    for row in range(MAP_H):
        for col in range(MAP_W):
            if coast[row, col] == 0 or barrier[row, col] or zhou[row, col] != 0:
                continue
            lon, lat = offset_to_lonlat(col, row)
            best_z, best_d = 0, SEAT_R2
            for zid, seats in seats_by_z.items():
                for sl, st in seats:
                    d = (lon - sl) ** 2 + (lat - st) ** 2
                    if d < best_d:
                        best_d, best_z = d, zid
            if best_z:
                zhou[row, col] = best_z
                n_extra += 1
    print(f"seat-ball extra={n_extra} filled={int((zhou > 0).sum())}", flush=True)
    zhou = fill_interior_holes(zhou, coast, barrier)

    # Absorb barrier/empty coast into neighboring 州 so purple lines don't leave
    # white corridors — solid continuous fills.
    for rnd in range(50):
        ys, xs = np.where((zhou == 0) & (coast > 0))
        if len(ys) == 0:
            break
        updates: list[tuple[int, int, int]] = []
        for y, x in zip(ys, xs):
            votes: Counter[int] = Counter()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= nx < MAP_W and 0 <= ny < MAP_H and zhou[ny, nx] > 0:
                    votes[int(zhou[ny, nx])] += 1
            if votes:
                updates.append((y, x, votes.most_common(1)[0][0]))
        if not updates:
            break
        for y, x, v in updates:
            zhou[y, x] = v
    print(f"after absorb filled={int((zhou > 0).sum())} empty-coast={int(((zhou == 0) & (coast > 0)).sum())}", flush=True)
    zhou = fill_interior_holes(zhou, coast, np.zeros_like(barrier))

    counts = Counter(zhou_names[int(i)] for i in zhou.ravel() if i)
    args.out_bin.write_bytes(zhou.astype(np.uint8).tobytes())
    meta = {
        "zhou_names": zhou_names,
        "counts": dict(counts),
        "n_filled": int((zhou > 0).sum()),
        "n_barrier": int(barrier.sum()),
        "bin": str(args.out_bin),
        "layout": "uint8 zhou_idx row-major 500x500",
        "authority": "Tan-262 purple 州 boundaries (georef flood, solid hex)",
        "method": "purple→hex barriers; multi-source flood from 州/郡 seats; hole fill",
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"wrote {args.out_meta} filled={meta['n_filled']} counts={meta['counts']}")
    missing = [n for n in CANON if counts.get(n, 0) == 0]
    if missing:
        print("WARNING empty 州:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
