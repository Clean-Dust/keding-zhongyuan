#!/usr/bin/env python3
"""Bake 州 / 郡 hex fills from xusizhe01/sanguo-map GeoJSON (hex-center PIP).

Replaces Sinica raster-trace fills. 县 faces are NOT baked (seats stay CHGIS points).

  python3 tools/bake_sanguo_map_fills.py \\
    --states data/sanguo_map/states.geojson \\
    --commanderies data/sanguo_map/commanderies.geojson \\
    --coast data/coast_mask.bin \\
    --out-meta data/zhou_jun_fills.json \\
    --out-bin data/zhou_jun_fills.bin \\
    --report data/zhou_jun_fills_report.json

Attribution: see data/sanguo_map/ATTRIBUTION.md
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from shapely.validation import make_valid

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500

# Light name cleanup toward game roster style (still source-first).
JUN_ALIASES = {
    "琅琊国": "琅邪国",
    "琅邪": "琅邪国",
    "益郡": "益州郡",
    "越嶲": "越巂郡",
    "越巂": "越巂郡",
}


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def normalize_jun(raw: str) -> str:
    name = str(raw or "").strip()
    if not name:
        return ""
    name = JUN_ALIASES.get(name, name)
    if name.endswith(("郡", "国", "尹", "属国", "校尉")):
        return name
    return name + "郡"


def load_polys(path: Path, kind: str) -> list[tuple[object, str, dict]]:
    """Return (geom, display_name, props)."""
    fc = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[object, str, dict]] = []
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            continue
        g = make_valid(shape(geom))
        if g.is_empty:
            continue
        if kind == "zhou":
            name = str(props.get("name") or "").strip()
        else:
            name = normalize_jun(str(props.get("name") or ""))
        if not name:
            continue
        out.append((g, name, props))
    return out


def build_index(feats: list[tuple[object, str, dict]]):
    geoms = [g for g, _, _ in feats]
    tree = STRtree(geoms)
    # shapely 2: tree.query returns indices
    return tree, geoms, feats


def pip_name(tree: STRtree, geoms, feats, lon: float, lat: float) -> tuple[str, dict | None]:
    pt = Point(lon, lat)
    hits = tree.query(pt)
    # hits may be ndarray of indices
    best = None
    best_area = 1e99
    for idx in hits:
        g = geoms[int(idx)]
        if g.contains(pt) or g.covers(pt):
            area = g.area
            if area < best_area:
                best_area = area
                best = feats[int(idx)]
    if best is None:
        return "", None
    return best[1], best[2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", type=Path, default=Path("data/sanguo_map/states.geojson"))
    ap.add_argument(
        "--commanderies",
        type=Path,
        default=Path("data/sanguo_map/commanderies.geojson"),
    )
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    args = ap.parse_args()

    coast = args.coast.read_bytes()
    if len(coast) != MAP_W * MAP_H:
        raise SystemExit("coast size mismatch")

    zhou_feats = load_polys(args.states, "zhou")
    jun_feats = load_polys(args.commanderies, "jun")
    print(f"loaded zhou={len(zhou_feats)} jun={len(jun_feats)}")

    zhou_tree, zhou_geoms, zhou_feats = build_index(zhou_feats)
    jun_tree, jun_geoms, jun_feats = build_index(jun_feats)

    zhou_table = [""]
    jun_table = [""]
    zhou_index: dict[str, int] = {}
    jun_index: dict[str, int] = {}

    def zid_of(name: str) -> int:
        if not name:
            return 0
        if name not in zhou_index:
            zhou_index[name] = len(zhou_table)
            zhou_table.append(name)
        return zhou_index[name]

    def jid_of(name: str) -> int:
        if not name:
            return 0
        if name not in jun_index:
            jun_index[name] = len(jun_table)
            jun_table.append(name)
        return jun_index[name]

    n_cells = MAP_W * MAP_H
    zhou_cell = [0] * n_cells
    jun_cell = [0] * n_cells
    zhou_counts: Counter[str] = Counter()
    jun_counts: Counter[str] = Counter()
    land_n = water_n = zhou_filled = jun_filled = 0
    jun_backfill_zhou = 0

    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                water_n += 1
                continue
            land_n += 1
            lon, lat = offset_to_lonlat(col, row)
            zname, _ = pip_name(zhou_tree, zhou_geoms, zhou_feats, lon, lat)
            jname, jprops = pip_name(jun_tree, jun_geoms, jun_feats, lon, lat)
            if not zname and jprops:
                # Prefer commandery's parent state if state poly missed (edge)
                zname = str(jprops.get("state") or "").strip()
                if zname:
                    jun_backfill_zhou += 1
            if zname:
                zi = zid_of(zname)
                zhou_cell[i] = zi
                zhou_filled += 1
                zhou_counts[zname] += 1
            if jname:
                ji = jid_of(jname)
                jun_cell[i] = ji
                jun_filled += 1
                jun_counts[jname] += 1

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
            "source": "xusizhe01/sanguo-map states.geojson",
            "method": "hex_center_point_in_polygon",
            "feature_count": len(zhou_feats),
            "note": "12 州: 冀兖青徐扬荆豫益交幽司隶雍 (雍≈凉/并合区; not classical 十三州)",
            "url": "https://github.com/xusizhe01/sanguo-map",
        },
        "jun": {
            "source": "xusizhe01/sanguo-map commanderies.geojson",
            "method": "hex_center_point_in_polygon",
            "feature_count": len(jun_feats),
            "name_field": "properties.name (+ light suffix/alias normalize)",
            "url": "https://github.com/xusizhe01/sanguo-map",
        },
        "xian": {
            "note": "counties.geojson not used; 县 = CHGIS seat points only",
        },
        "citation": (
            "Admin faces from xusizhe01/sanguo-map (states + commanderies GeoJSON). "
            "See data/sanguo_map/ATTRIBUTION.md. Sinica CCTS ad0262 underlay is separate QA imagery."
        ),
        "fidelity_note": (
            "Hex-center PIP on 500×500 (~6.9 km). Not identical to Sinica raster ink. "
            "Source CRS is WGS84 lon/lat overlapping our Sinica bbox."
        ),
        "supersedes": "Sinica raster-trace fills (caa7e43 and later attempts)",
        "bin": str(args.out_bin.name),
        "layout": "per cell: uint16 zhou_idx, uint16 jun_idx (LE); 0 = empty",
        "zhou_table": zhou_table,
        "jun_table": jun_table,
        "stats": {
            "land": land_n,
            "water": water_n,
            "zhou_filled": zhou_filled,
            "jun_filled": jun_filled,
            "zhou_fill_rate_land": round(zhou_filled / max(land_n, 1), 4),
            "jun_fill_rate_land": round(jun_filled / max(land_n, 1), 4),
            "zhou_units": len(zhou_table) - 1,
            "jun_units": len(jun_table) - 1,
            "jun_backfill_zhou_cells": jun_backfill_zhou,
        },
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "stats": meta["stats"],
        "zhou_hex_counts": dict(zhou_counts.most_common()),
        "jun_hex_counts": dict(jun_counts.most_common()),
        "zhou_names": zhou_table[1:],
        "jun_names": jun_table[1:],
        "gaps": {
            "zhou_vs_classical_13": (
                "Source has 12 州 including 雍州; game roster uses 凉州+并州 (no 雍). "
                "Fills follow source names."
            ),
            "no_county_faces": "counties.geojson intentionally unused",
            "alignment": "Compare to Sinica underlay via 底图 toggle; expect small mismatch",
        },
        "source": {
            "repo": "https://github.com/xusizhe01/sanguo-map",
            "states": str(args.states),
            "commanderies": str(args.commanderies),
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"zhou_filled={zhou_filled}/{land_n} ({meta['stats']['zhou_fill_rate_land']:.1%}) "
        f"jun_filled={jun_filled} zhou_units={len(zhou_table)-1} jun_units={len(jun_table)-1}"
    )
    print("zhou:", dict(zhou_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
