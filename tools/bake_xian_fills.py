#!/usr/bin/env python3
"""Bake per-hex 县 fills from CHGIS V6 county seats @ year 140.

CHGIS V6 does **not** ship time-series *county polygons* for Eastern Han
(only county *points*; prefecture polygons exist but are nearly empty @ 140).
This bake builds Thiessen (Voronoi) cells of those CHGIS county points, clips
them to Natural Earth land ∩ Sinica bbox, then paints each **land hex** whose
**center** falls in a cell that matched our roster (`county_seats.json`).

Not vector-identical to continuous CHGIS geometry — hex-center sampling on the
500×500 grid (~6.9 km). Not a free-hand Voronoi over empty land: cells come
only from CHGIS seats, clipped to land; unmatched CHGIS counties and hexes
outside all matched cells stay empty (no forced 州).

郡/州 paint at runtime = unions of filled 县 via counties_roster parents.
Does NOT write legacy admin_* ring files.

Citation: CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016.

Usage (from repo root):
  python3 tools/bake_xian_fills.py \\
    --cnty /tmp/chgis/v6_time_cnty_pts_utf_wgs84.shp \\
    --ne /tmp/ne/ne_50m_land.shp \\
    --seats data/county_seats.json \\
    --coast data/coast_mask.bin \\
    --out-meta data/xian_fills.json \\
    --out-bin data/xian_fills.bin \\
    --report data/xian_fills_report.json
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path

import shapefile
from shapely.geometry import MultiPoint, Point, box
from shapely.ops import unary_union, voronoi_diagram
from shapely.strtree import STRtree
from shapely.validation import make_valid

# Match Hex.gd (Sinica ad0262 extent, 500²)
LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500
YEAR = 140
PAD = 0.5
COUNTY_TYPES = {"县", "邑", "道", "侯国", "侯國", "国", "國"}


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def load_land(ne_path: Path):
    from shapely.geometry import shape

    r = shapefile.Reader(str(ne_path))
    geoms = []
    for sr in r.iterShapeRecords():
        g = shape(sr.shape.__geo_interface__)
        if g.is_empty:
            continue
        geoms.append(make_valid(g))
    land = unary_union(geoms)
    bbox = box(LON_MIN - PAD, LAT_MIN - PAD, LON_MAX + PAD, LAT_MAX + PAD)
    return make_valid(land.intersection(bbox))


def load_coast(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) != MAP_W * MAP_H:
        raise SystemExit(f"coast_mask.bin size {len(raw)} != {MAP_W * MAP_H}")
    return raw


def active_county_points(shp_path: Path) -> list[dict]:
    r = shapefile.Reader(str(shp_path))
    names = [f[0] for f in r.fields[1:]]
    idx = {n: i for i, n in enumerate(names)}
    out: list[dict] = []
    for rec in r.iterRecords():
        beg, end = int(rec[idx["BEG_YR"]]), int(rec[idx["END_YR"]])
        if not (beg <= YEAR <= end):
            continue
        t = str(rec[idx["TYPE_CH"]]).strip()
        if t not in COUNTY_TYPES:
            continue
        x, y = float(rec[idx["X_COOR"]]), float(rec[idx["Y_COOR"]])
        if x == 0.0 and y == 0.0:
            continue
        if not (LON_MIN - PAD <= x <= LON_MAX + PAD and LAT_MIN - PAD <= y <= LAT_MAX + PAD):
            continue
        out.append(
            {
                "name": str(rec[idx["NAME_CH"]]).strip(),
                "type": t,
                "lon": x,
                "lat": y,
                "sys_id": int(rec[idx["SYS_ID"]]),
            }
        )
    return out


def voronoi_cells(points: list[dict], clip_geom) -> list[tuple[object, dict]]:
    """Return list of (polygon, point_dict) Thiessen cells clipped to land."""
    if len(points) < 2:
        return []
    coords = [(p["lon"], p["lat"]) for p in points]
    mp = MultiPoint(coords)
    env = clip_geom.envelope.buffer(0.3)
    vd = voronoi_diagram(mp, envelope=env, edges=False)
    cells: list[tuple[object, dict]] = []
    for p in points:
        seed = Point(p["lon"], p["lat"])
        cell = None
        for g in vd.geoms:
            if g.contains(seed) or g.touches(seed):
                cell = g
                break
        if cell is None:
            best, best_d = None, 1e18
            for g in vd.geoms:
                d = g.centroid.distance(seed)
                if d < best_d:
                    best, best_d = g, d
            cell = best
        if cell is None:
            continue
        clipped = make_valid(cell.intersection(clip_geom))
        if clipped.is_empty:
            continue
        # Keep multipolygons as-is for PIP.
        cells.append((clipped, p))
    return cells


def build_sys_to_xian(seats_path: Path) -> dict[int, str]:
    doc = json.loads(seats_path.read_text(encoding="utf-8"))
    seats = doc.get("seats", doc if isinstance(doc, list) else [])
    out: dict[int, str] = {}
    for s in seats:
        sid = s.get("chgis_sys_id")
        xid = s.get("xian_id")
        if sid is None or not xid:
            continue
        out[int(sid)] = str(xid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake CHGIS@140 → hex xian fills (center PIP).")
    ap.add_argument("--cnty", type=Path, required=True)
    ap.add_argument("--ne", type=Path, required=True)
    ap.add_argument("--seats", type=Path, default=Path("data/county_seats.json"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/xian_fills.json"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/xian_fills.bin"))
    ap.add_argument("--report", type=Path, default=Path("data/xian_fills_report.json"))
    args = ap.parse_args()

    print("loading land / coast / CHGIS points / seats…")
    land = load_land(args.ne)
    coast = load_coast(args.coast)
    points = active_county_points(args.cnty)
    sys_to_xian = build_sys_to_xian(args.seats)
    print(
        f"  chgis@{YEAR}={len(points)} seats_mapped={len(sys_to_xian)} "
        f"land_area≈{land.area:.1f}°²"
    )

    print("building Thiessen cells (CHGIS seats → clipped to NE land)…")
    cells = voronoi_cells(points, land)
    # Only keep cells that matched roster via CHGIS sys_id.
    matched_geoms = []
    matched_xian: list[str] = []
    unmatched_chgis = 0
    for geom, p in cells:
        xid = sys_to_xian.get(int(p["sys_id"]))
        if not xid:
            unmatched_chgis += 1
            continue
        matched_geoms.append(geom)
        matched_xian.append(xid)
    print(
        f"  cells={len(cells)} roster_matched_cells={len(matched_geoms)} "
        f"chgis_unmatched_cells={unmatched_chgis}"
    )

    # xian_table[0] = empty
    xian_table: list[str] = [""]
    xian_index: dict[str, int] = {"": 0}
    for xid in matched_xian:
        if xid not in xian_index:
            xian_index[xid] = len(xian_table)
            xian_table.append(xid)

    tree = STRtree(matched_geoms)
    # STRtree.query returns positions into matched_geoms

    print(f"PIP {MAP_W}×{MAP_H} land hex centers…")
    n_cells = MAP_W * MAP_H
    indices = [0] * n_cells
    land_n = water_n = filled = 0
    by_xian: Counter[str] = Counter()
    ambiguous = 0

    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                water_n += 1
                continue
            land_n += 1
            lon, lat = offset_to_lonlat(col, row)
            pt = Point(lon, lat)
            hits = tree.query(pt)
            chosen = None
            for hi in hits:
                g = matched_geoms[int(hi)]
                if g.contains(pt) or g.touches(pt):
                    if chosen is None:
                        chosen = int(hi)
                    else:
                        # Prefer smaller cell on boundary overlaps.
                        ambiguous += 1
                        if g.area < matched_geoms[chosen].area:
                            chosen = int(hi)
            if chosen is None:
                continue
            xid = matched_xian[chosen]
            indices[i] = xian_index[xid]
            filled += 1
            by_xian[xid] += 1

    # uint16 LE binary
    if len(xian_table) > 65535:
        raise SystemExit("xian_table too large for uint16")
    blob = struct.pack(f"<{n_cells}H", *indices)
    args.out_bin.write_bytes(blob)

    meta = {
        "tier": "xian_fills",
        "year": YEAR,
        "map_w": MAP_W,
        "map_h": MAP_H,
        "bbox": {
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
        },
        "layer": "CHGIS V6 v6_time_cnty_pts_utf_wgs84 (county points)",
        "year_filter": f"beg_yr≤{YEAR}≤end_yr",
        "method": (
            "thiessen_of_chgis_county_points_clipped_to_ne_land; "
            "hex_center_point_in_polygon; roster xian_id via county_seats.chgis_sys_id"
        ),
        "fidelity_note": (
            "Not vector-identical to continuous polygons — each 500×500 hex (~6.9 km) "
            "is wholly assigned by whether its center lies inside a CHGIS-derived cell. "
            "CHGIS V6 has no Han-era county polygon layer; cells are Thiessen extents of "
            "CHGIS county seats (schematic neighbor extents, not cadastral)."
        ),
        "citation": "CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016",
        "bin": str(args.out_bin.name),
        "xian_table": xian_table,
        "stats": {
            "land": land_n,
            "water": water_n,
            "filled": filled,
            "empty_land": land_n - filled,
            "fill_rate_land": round(filled / max(land_n, 1), 4),
            "chgis_points_in_bbox": len(points),
            "thiessen_cells": len(cells),
            "roster_matched_cells": len(matched_geoms),
            "chgis_cells_without_roster_seat": unmatched_chgis,
            "distinct_xian_painted": len(xian_table) - 1,
            "boundary_ambiguous_picks": ambiguous,
            "seats_sys_id_mapped": len(sys_to_xian),
        },
        "zhou_jun": "runtime unions via counties_roster parents of xian_id — no separate bake",
    }
    args.out_meta.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Coverage vs CHGIS: how many matched seats got ≥1 hex?
    painted_ids = set(by_xian)
    seat_ids = set(sys_to_xian.values())
    report = {
        "meta": {k: meta[k] for k in meta if k != "xian_table"},
        "stats": meta["stats"],
        "seats_with_zero_hexes": sorted(seat_ids - painted_ids),
        "hexes_per_xian_top": by_xian.most_common(20),
        "hexes_per_xian_bottom": sorted(
            ((xid, n) for xid, n in by_xian.items()), key=lambda t: t[1]
        )[:20],
    }
    # by 州 coverage
    roster = json.loads(Path("data/counties_roster.json").read_text(encoding="utf-8"))
    id_to_zhou = {r["id"]: r["zhou"] for r in roster}
    by_zhou_hex: Counter[str] = Counter()
    by_zhou_xian: defaultdict[str, set] = defaultdict(set)
    for xid, n in by_xian.items():
        z = id_to_zhou.get(xid, "?")
        by_zhou_hex[z] += n
        by_zhou_xian[z].add(xid)
    report["filled_hexes_by_zhou"] = dict(sorted(by_zhou_hex.items()))
    report["painted_xian_count_by_zhou"] = {z: len(s) for z, s in sorted(by_zhou_xian.items())}
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"filled={filled}/{land_n} land ({meta['stats']['fill_rate_land']:.1%}) "
        f"xian={len(xian_table)-1} → {args.out_meta} + {args.out_bin}"
    )
    print(f"report → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
