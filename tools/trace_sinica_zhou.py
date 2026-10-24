#!/usr/bin/env python3
"""Digitize 十三州 hex fills from Sinica CCTS ad0262 underlay (raster).

Method:
  1. Classify thick brown 州 borders + dark sea as barriers on the underlay.
  2. Seed each Eastern Han / Three Kingdoms 州 from a lon/lat inside the painted
     region (CHGIS seat-based, nudged onto land).
  3. Multi-source BFS flood with per-州 geodesic distance caps so one province
     cannot wrap around the map; 凉州 also clipped west of ~98°E (羌胡).
  4. Assign each land hex by sampling the label at the hex center.

郡: dotted lines are too dense/faint for reliable auto-extract → jun_id stays 0.
县: no fake faces; seats remain CHGIS-only.

Attribution: Academia Sinica CCTS — 三國歷史地圖(Sanguo) ad0262 (raster).
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500

# name → (lon, lat, max_radius_deg)
ZHOU_SEEDS: dict[str, tuple[float, float, float]] = {
    "司隶": (112.45, 34.60, 5.0),
    "豫州": (115.50, 33.50, 4.5),
    "冀州": (115.00, 37.50, 4.5),
    "兖州": (116.50, 35.40, 4.0),
    "徐州": (117.50, 34.00, 4.5),
    "青州": (118.70, 36.50, 4.5),
    "荆州": (112.00, 30.00, 7.0),
    "扬州": (119.00, 30.20, 7.5),
    "益州": (104.10, 30.65, 9.5),
    "凉州": (103.50, 37.50, 8.5),
    "并州": (112.40, 37.70, 5.0),
    "幽州": (116.80, 40.00, 6.5),
    "交州": (106.00, 21.10, 8.0),  # 龙编 / 交趾 inland
}

# Extra hard clips: west of these lons treated as 羌胡/发羌 (empty).
LIANG_WEST_LON = 98.0
YI_WEST_LON = 98.5


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


def build_land(im: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = im[:, :, 0].astype(np.float32)
    g = im[:, :, 1].astype(np.float32)
    b = im[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    sea = lum < 85
    paper = (r > 205) & (g > 190) & (b > 150) & (lum > 180)
    brown = (
        (r > g)
        & (g >= b - 8)
        & ((r - b) > 25)
        & (lum < 165)
        & (lum > 70)
        & (~paper)
        & (~sea)
    )
    brown = ndimage.binary_opening(brown, iterations=1)
    brown = ndimage.binary_dilation(brown, iterations=2)
    barrier = sea | ndimage.binary_closing(brown, iterations=1)
    land = (~barrier) & (lum > 110)
    return land, barrier


def nudge_seed(land: np.ndarray, x: int, y: int, max_r: int = 80) -> tuple[int, int] | None:
    h, w = land.shape
    if 0 <= x < w and 0 <= y < h and land[y, x]:
        return x, y
    best = None
    bestd = 10**18
    for rad in range(1, max_r + 1):
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                if abs(dx) + abs(dy) > rad * 1.5:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and land[ny, nx]:
                    d = dx * dx + dy * dy
                    if d < bestd:
                        bestd = d
                        best = (nx, ny)
        if best is not None:
            return best
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--underlay", type=Path, default=Path("/tmp/sinica_ad0262_z8.jpg"))
    ap.add_argument(
        "--fallback-underlay",
        type=Path,
        default=Path("data/sinica_ad0262_underlay.jpg"),
    )
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    ap.add_argument("--debug", type=Path, default=Path("/tmp/sinica_zhou_trace_dbg.jpg"))
    args = ap.parse_args()

    src = args.underlay if args.underlay.is_file() else args.fallback_underlay
    print(f"underlay {src}")
    im = np.array(Image.open(src).convert("RGB"))
    h, w, _ = im.shape
    land, barrier = build_land(im)
    print(f"  size={w}x{h} land={land.mean():.3f} barrier={barrier.mean():.3f}")

    deg_per_px = ((LON_MAX - LON_MIN) / w + (LAT_MAX - LAT_MIN) / h) / 2.0
    zhou_table = [""]
    labels = np.zeros((h, w), dtype=np.int16)
    distm = np.full((h, w), 1e9, dtype=np.float32)
    max_dist_px: dict[int, float] = {}
    q: deque[tuple[int, int, int, float]] = deque()
    seed_info = []
    liang_id = -1
    yi_id = -1

    for name, (lon, lat, rdeg) in ZHOU_SEEDS.items():
        xy = lonlat_to_xy(lon, lat, w, h)
        nudged = nudge_seed(land, *xy)
        if nudged is None:
            print(f"  FAIL seed {name}")
            seed_info.append({"zhou": name, "ok": False})
            continue
        nx, ny = nudged
        zid = len(zhou_table)
        zhou_table.append(name)
        if name == "凉州":
            liang_id = zid
        if name == "益州":
            yi_id = zid
        max_dist_px[zid] = rdeg / deg_per_px
        labels[ny, nx] = zid
        distm[ny, nx] = 0.0
        q.append((nx, ny, zid, 0.0))
        seed_info.append(
            {
                "zhou": name,
                "ok": True,
                "lon": lon,
                "lat": lat,
                "seed_xy": [nx, ny],
                "rdeg": rdeg,
            }
        )
        print(f"  {name}: seed=({nx},{ny}) rdeg={rdeg}")

    while q:
        x, y, zid, d = q.popleft()
        if d > distm[y, x] + 1e-3:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            if not land[ny, nx]:
                continue
            plon = xy_to_lon(nx, w)
            if zid == liang_id and plon < LIANG_WEST_LON:
                continue
            if zid == yi_id and plon < YI_WEST_LON:
                continue
            nd = d + 1.0
            if nd > max_dist_px[zid]:
                continue
            if labels[ny, nx] == 0:
                labels[ny, nx] = zid
                distm[ny, nx] = nd
                q.append((nx, ny, zid, nd))
            elif labels[ny, nx] == zid and nd < distm[ny, nx]:
                distm[ny, nx] = nd
                q.append((nx, ny, zid, nd))

    px_counts = Counter(
        {zhou_table[i]: int((labels == i).sum()) for i in range(1, len(zhou_table))}
    )
    print(f"  pixel counts: {dict(px_counts)}")

    # Debug overlay
    dbg = im.copy()
    palette = [
        (220, 60, 60),
        (60, 140, 220),
        (60, 180, 80),
        (200, 160, 40),
        (160, 80, 200),
        (40, 180, 180),
        (220, 100, 40),
        (100, 100, 220),
        (180, 60, 120),
        (80, 160, 100),
        (200, 80, 80),
        (100, 180, 40),
        (60, 100, 180),
    ]
    ys, xs = np.where(labels > 0)
    for y, x in zip(ys[::3], xs[::3]):
        c = palette[(int(labels[y, x]) - 1) % len(palette)]
        dbg[y, x] = (
            (int(dbg[y, x, 0]) + c[0]) // 2,
            (int(dbg[y, x, 1]) + c[1]) // 2,
            (int(dbg[y, x, 2]) + c[2]) // 2,
        )
    Image.fromarray(dbg).resize((1280, 990)).save(args.debug, quality=88)
    print(f"debug → {args.debug}")

    coast = args.coast.read_bytes()
    if len(coast) != MAP_W * MAP_H:
        raise SystemExit("coast size mismatch")

    n_cells = MAP_W * MAP_H
    zhou_cell = [0] * n_cells
    jun_cell = [0] * n_cells
    counts: Counter[str] = Counter()
    land_n = water_n = filled = 0
    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                water_n += 1
                continue
            land_n += 1
            lon, lat = offset_to_lonlat(col, row)
            x, y = lonlat_to_xy(lon, lat, w, h)
            lid = 0
            if 0 <= x < w and 0 <= y < h:
                lid = int(labels[y, x])
                if lid == 0:
                    for rad in range(1, 5):
                        for dy in range(-rad, rad + 1):
                            for dx in range(-rad, rad + 1):
                                nx, ny = x + dx, y + dy
                                if 0 <= nx < w and 0 <= ny < h and labels[ny, nx] > 0:
                                    lid = int(labels[ny, nx])
                                    break
                            if lid:
                                break
                        if lid:
                            break
            if lid <= 0:
                continue
            zhou_cell[i] = lid
            filled += 1
            counts[zhou_table[lid]] += 1

    blob = b"".join(struct.pack("<HH", zhou_cell[i], jun_cell[i]) for i in range(n_cells))
    args.out_bin.write_bytes(blob)

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
            "source": "Sinica CCTS WMTS ad0262 raster underlay (digitized)",
            "method": (
                "multi_source_BFS from 13 州 seeds; barriers≈thick brown 州 borders+sea; "
                "per-州 geodesic distance caps; 凉州/益州 west clips (羌胡/发羌 empty); "
                "hex_center sample"
            ),
            "underlay_trace": str(src),
            "underlay_display": "data/sinica_ad0262_underlay.jpg",
            "zhou_count_expected": 13,
            "citation": "Academia Sinica CCTS — 三國歷史地圖(Sanguo) ad0262 (raster)",
            "confidence": (
                "medium — all 十三州 seeded and filled; borders jagged vs ink; "
                "not cadastral; peripheral 羌胡/鲜卑 beyond caps/clip stay empty"
            ),
        },
        "jun": {
            "source": "none (auto)",
            "note": (
                "郡 dotted borders too dense/faint for reliable auto-extract on this "
                "raster; jun_id left empty. Prefer 州 mode + underlay for 郡 detail."
            ),
        },
        "xian": {"note": "No Han county faces; seats remain CHGIS-only markers."},
        "fidelity_note": (
            "Hex-center sampling of flood regions on 500×500 (~6.9 km). Not "
            "vector-identical to the painted map. Not CHGIS cadastral polygons."
        ),
        "bin": str(args.out_bin.name),
        "layout": "per cell: uint16 zhou_idx, uint16 jun_idx (LE); 0 = empty",
        "zhou_table": zhou_table,
        "jun_table": [""],
        "stats": {
            "land": land_n,
            "water": water_n,
            "zhou_filled": filled,
            "jun_filled": 0,
            "zhou_fill_rate_land": round(filled / max(land_n, 1), 4),
            "zhou_units": len(zhou_table) - 1,
            "jun_units": 0,
        },
        "seeds": seed_info,
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "stats": meta["stats"],
        "zhou_hex_counts": dict(counts.most_common()),
        "pixel_counts": dict(px_counts),
        "missing_zhou": [z for z in ZHOU_SEEDS if counts.get(z, 0) == 0],
        "inventory": {
            "zhou_painted_on_sinica": list(ZHOU_SEEDS.keys()),
            "zhou_extracted": [z for z, n in counts.items() if n > 0],
            "jun_extracted": [],
        },
        "gaps": (
            "jun not extracted (dotted borders too dense). "
            "Land outside painted admin / beyond distance caps stays empty."
        ),
        "seeds": seed_info,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"zhou_filled={filled}/{land_n} ({meta['stats']['zhou_fill_rate_land']:.1%}) "
        f"units={len(zhou_table)-1} counts={dict(counts)}"
    )
    if report["missing_zhou"]:
        print("WARNING missing:", report["missing_zhou"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
