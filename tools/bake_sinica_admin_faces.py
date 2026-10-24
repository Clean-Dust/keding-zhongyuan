#!/usr/bin/env python3
"""Bake 州 → 郡 → 县 hex faces from Sinica ad0262 underlay + admin_points.

Quality goals (vs rejected caa7e43 / sanguo-map):
  - Seed 州 from admin_points 州治; multi-source BFS on brown ink barriers
  - 江夏 area must be 荆州 (seed/check)
  - 郡 seeds from 郡治, flood only inside parent 州 mask
  - 县: nearest 县治 inside each 郡 mask (no cross-郡 spill)

Usage:
  python3 tools/bake_sinica_admin_faces.py \\
    --underlay /tmp/sinica_ad0262_z9_eq.png \\
    --points data/admin_points.json
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500
WEST_CLIP = 97.8

# Self-check samples: (lon, lat, expected_zhou)
CHECKS = [
    (114.71, 31.02, "荆州"),  # 江夏
    (112.54, 33.00, "荆州"),  # 南阳
    (115.90, 28.67, "扬州"),  # 豫章 (must not be eaten by 荆州)
    (116.78, 32.58, "扬州"),  # 寿春
    (113.47, 34.16, "豫州"),  # 颍川
    (112.60, 34.73, "司隶"),  # 雒阳
    (104.08, 30.65, "益州"),  # 成都
    (118.37, 36.86, "青州"),  # 临淄
    (115.97, 39.49, "幽州"),  # 涿
]

# Hard lon/lat clips per 州 (prevent northern wrap / 羌胡). Not distance-cap diagonals.
# (lon_min, lon_max, lat_min, lat_max) — None = open side
ZHOU_BBOX: dict[str, tuple[float | None, float | None, float | None, float | None]] = {
    # Loose clips: stop catastrophic wrap (凉州/羌胡, northern sea). Real borders = thick ink.
    "凉州": (97.8, 110.5, 32.5, 42.8),
    "并州": (108.0, 116.0, 34.0, 42.0),
    "幽州": (112.0, 128.0, 37.0, 42.8),
    "冀州": (112.0, 119.0, 34.0, 40.5),
    "青州": (116.0, 123.5, 34.0, 39.5),
    "兖州": (114.0, 119.5, 33.0, 37.5),
    "徐州": (116.0, 122.0, 31.0, 35.5),
    "豫州": (111.5, 118.5, 31.5, 35.5),
    "司隶": (107.0, 115.5, 32.0, 37.5),
    # 荆州 lon_max kept west of 豫章(~115.9); 江夏(~114.7) stays inside.
    "荆州": (107.0, 115.35, 24.5, 33.8),
    "扬州": (114.8, 122.5, 23.5, 33.8),
    "益州": (97.8, 111.0, 21.0, 35.0),
    "交州": (103.0, 118.5, 15.5, 26.5),
}


def lonlat_to_xy(lon: float, lat: float, w: int, h: int) -> tuple[int, int]:
    x = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * w)
    y = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * h)
    return x, y


def xy_to_lon(x: int, w: int) -> float:
    return LON_MIN + (x + 0.5) / w * (LON_MAX - LON_MIN)


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def build_barriers(im: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (land_for_zhou, land_for_jun, sea).

    Thick ink → 州 barriers (follow solid 州 borders; avoid dashed 郡 noise).
    All ink (closed) → 郡 barriers.
    """
    r = im[:, :, 0].astype(np.float32)
    g = im[:, :, 1].astype(np.float32)
    b = im[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    sea = lum < 85
    paper = (r > 205) & (g > 190) & (b > 150) & (lum > 180)
    bluish = (b > r + 8) & (b > g + 2)
    ink = (
        (~sea)
        & (~paper)
        & (~bluish)
        & (lum < 175)
        & (lum > 40)
        & (r >= g - 10)
        & ((r - b) > 12)
    )
    dark = (~sea) & (~bluish) & (lum < 115) & (lum > 25) & (r >= g - 18)
    grad = ndimage.gaussian_gradient_magnitude(lum, 1.0)
    edge = (grad > 8) & (~sea) & (~bluish) & (lum < 180) & (r >= g - 5) & ((r - b) > 8)
    raw = ink | dark | edge
    # Thick 州 strokes: high local ink density
    dens = ndimage.uniform_filter(raw.astype(np.float32), size=7)
    thick = raw & (dens > 0.12)
    thick = ndimage.binary_closing(thick, iterations=2)
    thick = ndimage.binary_dilation(thick, iterations=2)
    # All lines for 郡 (close dashes)
    thin = ndimage.binary_closing(raw, iterations=2)
    thin = ndimage.binary_dilation(thin, iterations=2)
    zhou_barrier = sea | thick
    jun_barrier = sea | thin | thick
    land_zhou = (~zhou_barrier) & (lum > 100)
    land_jun = (~jun_barrier) & (lum > 100)
    return land_zhou.astype(bool), land_jun.astype(bool), sea.astype(bool)


def nudge(land: np.ndarray, x: int, y: int, max_r: int = 80) -> tuple[int, int] | None:
    h, w = land.shape
    if 0 <= x < w and 0 <= y < h and land[y, x]:
        return x, y
    best = None
    bestd = 10**18
    for rad in range(1, max_r + 1):
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                if abs(dx) + abs(dy) > int(rad * 1.5):
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and land[ny, nx]:
                    d = dx * dx + dy * dy
                    if d < bestd:
                        bestd, best = d, (nx, ny)
        if best:
            return best
    return None


def multi_source(
    land: np.ndarray,
    seeds: list[tuple[int, int, int]],
    w: int,
    h_img: int,
    allowed: np.ndarray | None = None,
    lid_names: dict[int, str] | None = None,
) -> np.ndarray:
    """Nearest-seed flood; barriers = ~land. Optional per-州 bbox clips (no geodesic caps)."""
    hh, ww = land.shape
    labels = np.zeros((hh, ww), dtype=np.int16)
    dist = np.full((hh, ww), np.float32(1e9))
    q: deque[tuple[int, int, int, float]] = deque()

    def in_clip(nx: int, ny: int, lid: int) -> bool:
        if xy_to_lon(nx, w) < WEST_CLIP:
            return False
        if not lid_names:
            return True
        name = lid_names.get(lid)
        if not name or name not in ZHOU_BBOX:
            return True
        lo0, lo1, la0, la1 = ZHOU_BBOX[name]
        lon = LON_MIN + (nx + 0.5) / w * (LON_MAX - LON_MIN)
        lat = LAT_MAX - (ny + 0.5) / h_img * (LAT_MAX - LAT_MIN)
        if lo0 is not None and lon < lo0:
            return False
        if lo1 is not None and lon > lo1:
            return False
        if la0 is not None and lat < la0:
            return False
        if la1 is not None and lat > la1:
            return False
        return True

    for x, y, lid in seeds:
        if not (0 <= x < ww and 0 <= y < hh):
            continue
        if not land[y, x]:
            continue
        if allowed is not None and not allowed[y, x]:
            continue
        if not in_clip(x, y, lid):
            continue
        labels[y, x] = lid
        dist[y, x] = 0.0
        q.append((x, y, lid, 0.0))
    while q:
        x, y, lid, d = q.popleft()
        if d > dist[y, x] + 1e-3:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= ww or ny >= hh:
                continue
            if not land[ny, nx]:
                continue
            if allowed is not None and not allowed[ny, nx]:
                continue
            if not in_clip(nx, ny, lid):
                continue
            nd = d + 1.0
            if labels[ny, nx] == 0:
                labels[ny, nx] = lid
                dist[ny, nx] = nd
                q.append((nx, ny, lid, nd))
            elif labels[ny, nx] == lid and nd < dist[ny, nx]:
                dist[ny, nx] = nd
                q.append((nx, ny, lid, nd))
            elif labels[ny, nx] != lid and nd + 1.5 < dist[ny, nx]:
                labels[ny, nx] = lid
                dist[ny, nx] = nd
                q.append((nx, ny, lid, nd))
    return labels


def fill_small_holes(labels: np.ndarray, land: np.ndarray, max_hole: int = 400) -> np.ndarray:
    """Assign unlabeled land holes fully surrounded by one label."""
    out = labels.copy()
    unlabeled = land & (out == 0)
    structure = np.ones((3, 3), dtype=bool)
    labeled, n = ndimage.label(unlabeled, structure=structure)
    for i in range(1, n + 1):
        ys, xs = np.where(labeled == i)
        if len(ys) == 0 or len(ys) > max_hole:
            continue
        # neighbors
        neigh = set()
        for y, x in zip(ys, xs):
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < out.shape[0] and 0 <= nx < out.shape[1] and out[ny, nx] > 0:
                    neigh.add(int(out[ny, nx]))
        if len(neigh) == 1:
            out[labeled == i] = next(iter(neigh))
    return out


def sample_label(labels: np.ndarray, lon: float, lat: float, w: int, h: int) -> int:
    x, y = lonlat_to_xy(lon, lat, w, h)
    if not (0 <= x < w and 0 <= y < h):
        return 0
    lid = int(labels[y, x])
    if lid:
        return lid
    for rad in range(1, 6):
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and labels[ny, nx] > 0:
                    return int(labels[ny, nx])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlay", type=Path, default=Path("/tmp/sinica_ad0262_z8.jpg"))
    ap.add_argument("--fallback", type=Path, default=Path("data/sinica_ad0262_underlay.jpg"))
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    ap.add_argument("--out-xian-bin", type=Path, default=Path("data/xian_fills.bin"))
    ap.add_argument("--out-xian-meta", type=Path, default=Path("data/xian_fills.json"))
    ap.add_argument("--debug", type=Path, default=Path("/tmp/sinica_faces_dbg.jpg"))
    args = ap.parse_args()

    src = args.underlay if args.underlay.is_file() else args.fallback
    if not src.is_file():
        raise SystemExit(f"underlay missing: {src}")
    print(f"underlay {src}")
    im = np.array(Image.open(src).convert("RGB"))
    h, w, _ = im.shape
    land_zhou, land_jun, _sea = build_barriers(im)
    print(
        f"  size={w}x{h} land_zhou={land_zhou.mean():.3f} land_jun={land_jun.mean():.3f}"
    )

    pts = json.loads(args.points.read_text(encoding="utf-8"))
    zhou_pts = pts["zhou"]
    jun_pts = pts["jun"]
    xian_pts = pts["xian"]

    # --- 州 flood (thick barriers + bbox clips; no geodesic distance caps) ---
    zhou_table = [""]
    zhou_index: dict[str, int] = {}
    zhou_seeds: list[tuple[int, int, int]] = []
    for zp in zhou_pts:
        name = zp["zhou"]
        xy = lonlat_to_xy(zp["lon"], zp["lat"], w, h)
        n = nudge(land_zhou, *xy)
        if not n:
            print(f"  FAIL 州 seed {name}")
            continue
        if name not in zhou_index:
            zhou_index[name] = len(zhou_table)
            zhou_table.append(name)
        zid = zhou_index[name]
        zhou_seeds.append((n[0], n[1], zid))
        print(f"  州 seed {name} @ {n}")

    # Extra 荆州 seeds: 江夏 corridor + 南阳 (north of thick ink that blocks 江陵 seed).
    if "荆州" in zhou_index:
        zid = zhou_index["荆州"]
        for lon, lat in (
            (114.5, 30.5),
            (114.7, 31.0),
            (113.2, 30.2),
            (112.54, 33.00),  # 南阳
            (112.2, 32.4),
            (113.0, 32.2),
        ):
            xy = lonlat_to_xy(lon, lat, w, h)
            n = nudge(land_zhou, *xy, 100)
            if n:
                zhou_seeds.append((n[0], n[1], zid))
                print(f"  州 extra 荆州 seed @ {n} near ({lon},{lat})")

    # Extra 扬州 seeds so 豫章/庐江 are not eaten by 荆州 eastward flood.
    if "扬州" in zhou_index:
        zid = zhou_index["扬州"]
        for lon, lat in ((115.90, 28.67), (116.2, 29.5), (116.5, 31.0)):
            xy = lonlat_to_xy(lon, lat, w, h)
            n = nudge(land_zhou, *xy, 100)
            if n:
                zhou_seeds.append((n[0], n[1], zid))
                print(f"  州 extra 扬州 seed @ {n} near ({lon},{lat})")

    # 颍川 is 豫州; 司隶 flood otherwise claims it.
    if "豫州" in zhou_index:
        zid = zhou_index["豫州"]
        for lon, lat in ((113.47, 34.16), (114.0, 33.8)):
            xy = lonlat_to_xy(lon, lat, w, h)
            n = nudge(land_zhou, *xy, 100)
            if n:
                zhou_seeds.append((n[0], n[1], zid))
                print(f"  州 extra 豫州 seed @ {n} near ({lon},{lat})")

    lid_names = {i: zhou_table[i] for i in range(1, len(zhou_table))}
    zhou_labels = multi_source(land_zhou, zhou_seeds, w, h, lid_names=lid_names)
    zhou_labels = fill_small_holes(zhou_labels, land_zhou, max_hole=800)
    print(
        f"  州 labeled frac={(zhou_labels > 0).sum() / max(land_zhou.sum(), 1):.3f} "
        f"units={len(zhou_table)-1}"
    )

    fails = []
    for lon, lat, expect in CHECKS:
        lid = sample_label(zhou_labels, lon, lat, w, h)
        got = zhou_table[lid] if lid < len(zhou_table) else "?"
        ok = got == expect
        print(f"  check {expect} @({lon},{lat}) → {got} {'OK' if ok else 'FAIL'}")
        if not ok:
            fails.append((expect, got, lon, lat))

    # --- 郡 flood inside each 州 (thin+thick barriers) ---
    jun_table = [""]
    jun_index: dict[str, int] = {}
    jun_to_zhou_idx: dict[int, int] = {}
    jun_labels = np.zeros((h, w), dtype=np.int16)

    by_zhou_jun: dict[str, list] = defaultdict(list)
    for jp in jun_pts:
        by_zhou_jun[jp["zhou"]].append(jp)

    for zhou_name, jlist in by_zhou_jun.items():
        zid = zhou_index.get(zhou_name)
        if not zid:
            print(f"  skip 郡 in unknown 州 {zhou_name}")
            continue
        mask = zhou_labels == zid
        seeds = []
        for jp in jlist:
            jname = jp["jun"]
            if jname not in jun_index:
                jun_index[jname] = len(jun_table)
                jun_table.append(jname)
            jid = jun_index[jname]
            jun_to_zhou_idx[jid] = zid
            xy = lonlat_to_xy(jp["lon"], jp["lat"], w, h)
            grow_land = land_jun & mask if mask.any() else land_jun
            n = nudge(grow_land, *xy, 60)
            if not n:
                n = nudge(land_jun, *xy, 80)
            if not n:
                continue
            seeds.append((n[0], n[1], jid))
        if not seeds:
            continue
        allowed = mask.copy()
        for x, y, _jid in seeds:
            allowed[y, x] = True
        part = multi_source(land_jun, seeds, w, h, allowed=allowed)
        sel = (part > 0) & mask
        jun_labels[sel] = part[sel]
        print(f"  郡 in {zhou_name}: seeds={len(seeds)} labeled={(part > 0).sum()}")
    print(
        f"  jun units={len(jun_table)-1} "
        f"labeled frac={(jun_labels > 0).sum() / max(land_jun.sum(), 1):.3f}"
    )

    # Consistency: where jun labeled, zhou should match parent; fill zhou holes from jun
    for y in range(h):
        # vectorized chunk
        pass
    has_jun = jun_labels > 0
    for jid in range(1, len(jun_table)):
        zid = jun_to_zhou_idx.get(jid, 0)
        if zid:
            jun_labels_mask = jun_labels == jid
            zhou_labels[jun_labels_mask] = zid

    # --- Hex assign 州/郡 ---
    coast = args.coast.read_bytes()
    n_cells = MAP_W * MAP_H
    zhou_cell = [0] * n_cells
    jun_cell = [0] * n_cells
    zhou_counts: Counter[str] = Counter()
    jun_counts: Counter[str] = Counter()
    land_n = zhou_filled = jun_filled = 0

    # Precompute hex→image for 县 later
    hex_zhou = np.zeros((MAP_H, MAP_W), dtype=np.int16)
    hex_jun = np.zeros((MAP_H, MAP_W), dtype=np.int16)

    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                continue
            land_n += 1
            lon, lat = offset_to_lonlat(col, row)
            zid = sample_label(zhou_labels, lon, lat, w, h)
            jid = sample_label(jun_labels, lon, lat, w, h)
            if jid and jid in jun_to_zhou_idx:
                zid = jun_to_zhou_idx[jid]
            if zid:
                zhou_cell[i] = zid
                hex_zhou[row, col] = zid
                zhou_filled += 1
                zhou_counts[zhou_table[zid]] += 1
            if jid:
                jun_cell[i] = jid
                hex_jun[row, col] = jid
                jun_filled += 1
                jun_counts[jun_table[jid]] += 1

    # Hex-level self-check
    hex_fails = []
    for lon, lat, expect in CHECKS:
        col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
        row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
        i = row * MAP_W + col
        zid = zhou_cell[i]
        got = zhou_table[zid] if zid < len(zhou_table) else ""
        print(f"  hex-check {expect} → {got}")
        if got != expect:
            hex_fails.append((expect, got))

    args.out_bin.write_bytes(
        b"".join(struct.pack("<HH", zhou_cell[i], jun_cell[i]) for i in range(n_cells))
    )

    # --- 县: nearest seat inside 郡 ---
    xian_table = [""]
    xian_index: dict[str, int] = {}
    # seats by jun name
    seats_by_jun: dict[str, list] = defaultdict(list)
    for s in xian_pts:
        seats_by_jun[s["jun"]].append(s)

    xian_cell = [0] * n_cells
    xian_counts: Counter[str] = Counter()
    xian_filled = 0

    # For each jun, collect hex indices and assign nearest seat
    jun_hexes: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # jid -> (col,row,i)
    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            jid = jun_cell[i]
            if jid:
                jun_hexes[jid].append((col, row, i))

    for jid, cells in jun_hexes.items():
        jname = jun_table[jid]
        seats = seats_by_jun.get(jname, [])
        if not seats:
            continue
        # Ensure xian ids in table
        seat_refs = []
        for s in seats:
            xid = s["xian_id"]
            if xid not in xian_index:
                xian_index[xid] = len(xian_table)
                xian_table.append(xid)
            seat_refs.append((xian_index[xid], s["lon"], s["lat"], s["name"]))
        for col, row, i in cells:
            lon, lat = offset_to_lonlat(col, row)
            best_id = 0
            best_d = 1e18
            for xid, slon, slat, _nm in seat_refs:
                d = (lon - slon) ** 2 + (lat - slat) ** 2
                if d < best_d:
                    best_d = d
                    best_id = xid
            if best_id:
                xian_cell[i] = best_id
                xian_filled += 1
                xian_counts[xian_table[best_id]] += 1

    # xian bin: uint16 per cell
    args.out_xian_bin.write_bytes(struct.pack(f"<{n_cells}H", *xian_cell))
    xian_meta = {
        "tier": "xian_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "method": "nearest_县治 inside Sinica-traced 郡 mask",
        "xian_table": xian_table,
        "bin": args.out_xian_bin.name,
        "layout": "uint16 xian_idx LE per cell; 0=empty",
        "stats": {
            "xian_filled": xian_filled,
            "xian_units": len(xian_table) - 1,
            "fill_rate_vs_jun": round(xian_filled / max(jun_filled, 1), 4),
        },
        "note": "No cross-郡 spill; seats from CHGIS. Not whole-map Thiessen.",
    }
    args.out_xian_meta.write_text(json.dumps(xian_meta, ensure_ascii=False, indent=2) + "\n")

    meta = {
        "tier": "zhou_jun_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "bbox": {
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
        },
        "zhou": {
            "source": "Sinica CCTS ad0262 digitize",
            "method": "multi_source_BFS from 州治; thick ink barriers + ZHOU_BBOX clips",
            "underlay": str(src),
            "units": len(zhou_table) - 1,
        },
        "jun": {
            "source": "Sinica CCTS ad0262 digitize",
            "method": "multi_source_BFS from 郡治 constrained to parent 州 mask",
            "units": len(jun_table) - 1,
        },
        "xian": {
            "source": "nearest CHGIS 县治 inside 郡",
            "bin": args.out_xian_bin.name,
            "units": len(xian_table) - 1,
        },
        "citation": "Academia Sinica CCTS ad0262; CHGIS seats/points for seeds",
        "fidelity_note": "Hex-center sample ~6.9km; raster ink digitize not cadastral",
        "supersedes": ["caa7e43 sinica-trace", "30edcd4 sanguo-map"],
        "zhou_table": zhou_table,
        "jun_table": jun_table,
        "bin": args.out_bin.name,
        "layout": "uint16 zhou_idx, uint16 jun_idx LE",
        "stats": {
            "land": land_n,
            "zhou_filled": zhou_filled,
            "jun_filled": jun_filled,
            "xian_filled": xian_filled,
            "zhou_fill_rate_land": round(zhou_filled / max(land_n, 1), 4),
            "jun_fill_rate_land": round(jun_filled / max(land_n, 1), 4),
            "zhou_units": len(zhou_table) - 1,
            "jun_units": len(jun_table) - 1,
        },
        "self_check": {
            "pixel_fails": [{"expect": a, "got": b} for a, b, *_ in fails],
            "hex_fails": [{"expect": a, "got": b} for a, b in hex_fails],
        },
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    report = {
        "stats": meta["stats"],
        "zhou_hex_counts": dict(zhou_counts.most_common()),
        "jun_hex_counts": dict(jun_counts.most_common(30)),
        "xian_units": len(xian_table) - 1,
        "self_check": meta["self_check"],
        "missing_zhou": [z for z in ZHOU_BBOX if zhou_counts.get(z, 0) == 0],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    # Debug
    dbg = im.copy()
    rng = np.random.default_rng(7)
    pal = rng.integers(50, 230, size=(max(len(zhou_table), 4), 3), dtype=np.int32)
    ys, xs = np.where(zhou_labels > 0)
    for y, x in zip(ys[::4], xs[::4]):
        c = pal[int(zhou_labels[y, x]) % len(pal)]
        dbg[y, x] = (
            (int(dbg[y, x, 0]) + int(c[0])) // 2,
            (int(dbg[y, x, 1]) + int(c[1])) // 2,
            (int(dbg[y, x, 2]) + int(c[2])) // 2,
        )
    Image.fromarray(dbg).resize((1400, 1080)).save(args.debug, quality=88)

    print(
        f"DONE zhou={zhou_filled} jun={jun_filled} xian={xian_filled} "
        f"zhou_u={len(zhou_table)-1} jun_u={len(jun_table)-1} xian_u={len(xian_table)-1}"
    )
    print("zhou counts", dict(zhou_counts))
    if len(zhou_table) - 1 < 13:
        print("FAIL: not all 十三州")
        return 1
    if hex_fails:
        print("FAIL: hex self-check", hex_fails)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
