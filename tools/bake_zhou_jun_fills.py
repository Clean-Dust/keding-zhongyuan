#!/usr/bin/env python3
"""Bake 州 / 郡 hex membership from real CHGIS polygons (hex-center PIP).

Inventory (see also data + store notes):
  - 郡: CHGIS V6 `v6_time_pref_pgn_utf_wgs84` — time-series prefecture polygons.
    Eastern Han / TK coverage is sparse (≈15–30 郡, mostly SE). Year **140**.
  - 州: CHGIS V4 `v4_time_prov_pgn_utf` — time-series province polygons (Xian80 GK19 → WGS84).
    No polygons @140; mid-TK year **262** has 扬州 / 荆州 / 交州 only (not 十三州).
  - 县: CHGIS has no Han-era county faces — do not invent (no Thiessen here).

Outside polygons → empty. No forced assignment.

Usage:
  python3 tools/bake_zhou_jun_fills.py \\
    --pref-pgn /tmp/chgis/v6_time_pref_pgn_utf_wgs84.shp \\
    --prov-pgn /tmp/chgis/v4_time_prov_pgn_utf.shp \\
    --prov-prj /tmp/chgis/v4_time_prov_pgn_utf.prj \\
    --coast data/coast_mask.bin \\
    --out-meta data/zhou_jun_fills.json \\
    --out-bin data/zhou_jun_fills.bin \\
    --report data/zhou_jun_fills_report.json
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

import shapefile
from opencc import OpenCC
from pyproj import CRS, Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shp_transform
from shapely.strtree import STRtree
from shapely.validation import make_valid

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500
YEAR_JUN = 140
YEAR_ZHOU = 262
JUN_TYPES = {"郡", "国", "國", "尹", "属国", "屬國", "侯国", "侯國"}
# Skip non-admin faces (夷洲 / 朱崖洲 etc.)
SKIP_TYPES = {"非政区国土", "非政區國土"}

_cc = OpenCC("t2s")


def t2s(s: str) -> str:
    return _cc.convert(str(s or "").strip())


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def load_coast(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) != MAP_W * MAP_H:
        raise SystemExit(f"coast size {len(raw)} != {MAP_W * MAP_H}")
    return raw


def load_roster_jun_zhou(path: Path) -> dict[str, str]:
    """bare/full 郡名 → 州."""
    roster = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in roster:
        jun = t2s(row["jun"])
        zhou = t2s(row["zhou"])
        out[jun] = zhou
        for suf in ("属国", "屬國", "郡", "国", "國", "尹"):
            if jun.endswith(suf) and len(jun) > len(suf):
                out[jun[: -len(suf)]] = zhou
    return out


def _bbox_overlaps_map(bbox: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bbox
    return not (maxx < LON_MIN or minx > LON_MAX or maxy < LAT_MIN or miny > LAT_MAX)


def load_jun_polygons(shp: Path) -> list[tuple[object, str, str]]:
    """Return (geom, jun_name, type_ch) for YEAR_JUN prefecture faces."""
    sf = shapefile.Reader(str(shp))
    fields = [f[0] for f in sf.fields[1:]]
    out: list[tuple[object, str, str]] = []
    for sr in sf.iterShapeRecords():
        r = dict(zip(fields, sr.record))
        beg = int(float(r["BEG_YR"]))
        end = int(float(r["END_YR"]))
        if not (beg <= YEAR_JUN <= end):
            continue
        typ = t2s(str(r.get("TYPE_CH") or ""))
        if typ in SKIP_TYPES:
            continue
        if typ not in JUN_TYPES:
            continue
        name = t2s(str(r.get("NAME_CH") or ""))
        if not name:
            continue
        g = make_valid(shape(sr.shape.__geo_interface__))
        if g.is_empty or not _bbox_overlaps_map(g.bounds):
            continue
        out.append((g, name, typ))
    return out


def load_zhou_polygons(shp: Path, prj: Path) -> list[tuple[object, str]]:
    """Return (geom_wgs84, zhou_name) for YEAR_ZHOU province faces."""
    crs = CRS.from_wkt(prj.read_text(encoding="utf-8", errors="replace"))
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def _project(x, y, z=None):
        lon, lat = to_wgs.transform(x, y)
        return (lon, lat)

    sf = shapefile.Reader(str(shp), encoding="utf-8", encodingErrors="replace")
    fields = [f[0] for f in sf.fields[1:]]
    out: list[tuple[object, str]] = []
    for sr in sf.iterShapeRecords():
        r = dict(zip(fields, sr.record))
        beg = int(float(r["BEG_YR"]))
        end = int(float(r["END_YR"]))
        if not (beg <= YEAR_ZHOU <= end):
            continue
        typ = t2s(str(r.get("TYPE_CH") or ""))
        if typ != "州":
            continue
        name = t2s(str(r.get("NAME_CH") or ""))
        if not name:
            continue
        g0 = make_valid(shape(sr.shape.__geo_interface__))
        if g0.is_empty:
            continue
        g = make_valid(shp_transform(_project, g0))
        if g.is_empty or not _bbox_overlaps_map(g.bounds):
            continue
        out.append((g, name))
    return out


def table_index(table: list[str], index: dict[str, int], name: str) -> int:
    if name not in index:
        index[name] = len(table)
        table.append(name)
    return index[name]


def pip_assign(
    geoms: list,
    labels: list[str],
    coast: bytes,
) -> tuple[list[int], Counter[str], int]:
    """Assign each land hex center to first/smallest containing geom label index (1-based via caller tables)."""
    if not geoms:
        return [0] * (MAP_W * MAP_H), Counter(), 0
    tree = STRtree(geoms)
    indices = [0] * (MAP_W * MAP_H)
    counts: Counter[str] = Counter()
    ambiguous = 0
    label_to_idx = {lab: i for i, lab in enumerate(labels)}
    # We'll store 1-based indices into a table built by caller — here return 0-based into labels, +1 later
    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                continue
            lon, lat = offset_to_lonlat(col, row)
            pt = Point(lon, lat)
            hits = tree.query(pt)
            chosen = None
            for hi in hits:
                g = geoms[int(hi)]
                if g.contains(pt) or g.touches(pt):
                    if chosen is None:
                        chosen = int(hi)
                    else:
                        ambiguous += 1
                        if g.area < geoms[chosen].area:
                            chosen = int(hi)
            if chosen is None:
                continue
            lab = labels[chosen]
            indices[i] = label_to_idx[lab] + 1  # 1-based; 0=empty
            counts[lab] += 1
    return indices, counts, ambiguous


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref-pgn", type=Path, required=True, help="V6 time pref polygons WGS84")
    ap.add_argument("--prov-pgn", type=Path, required=True, help="V4 time prov polygons (Xian80)")
    ap.add_argument("--prov-prj", type=Path, required=True)
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--roster", type=Path, default=Path("data/counties_roster.json"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    args = ap.parse_args()

    coast = load_coast(args.coast)
    jun_to_zhou = load_roster_jun_zhou(args.roster)

    print(f"loading 郡 polygons @ {YEAR_JUN}…")
    jun_feats = load_jun_polygons(args.pref_pgn)
    print(f"  {len(jun_feats)} faces")
    print(f"loading 州 polygons @ {YEAR_ZHOU} (reproject)…")
    zhou_feats = load_zhou_polygons(args.prov_pgn, args.prov_prj)
    print(f"  {len(zhou_feats)} faces: {[n for _, n in zhou_feats]}")

    jun_geoms = [g for g, _, _ in jun_feats]
    jun_labels = [n for _, n, _ in jun_feats]
    zhou_geoms = [g for g, _ in zhou_feats]
    zhou_labels = [n for _, n in zhou_feats]

    print("PIP 郡…")
    jun_idx_raw, jun_counts, jun_amb = pip_assign(jun_geoms, jun_labels, coast)
    print("PIP 州…")
    zhou_idx_raw, zhou_counts, zhou_amb = pip_assign(zhou_geoms, zhou_labels, coast)

    # Build tables (index 0 = empty)
    zhou_table = [""]
    zhou_index: dict[str, int] = {"": 0}
    jun_table = [""]
    jun_index: dict[str, int] = {"": 0}

    n = MAP_W * MAP_H
    zhou_cell = [0] * n
    jun_cell = [0] * n
    land = water = jun_filled = zhou_filled = 0
    for i in range(n):
        if coast[i] == 0:
            water += 1
            continue
        land += 1
        if jun_idx_raw[i] > 0:
            jname = jun_labels[jun_idx_raw[i] - 1]
            jun_cell[i] = table_index(jun_table, jun_index, jname)
            jun_filled += 1
        if zhou_idx_raw[i] > 0:
            zname = zhou_labels[zhou_idx_raw[i] - 1]
            zhou_cell[i] = table_index(zhou_table, zhou_index, zname)
            zhou_filled += 1

    # Roster cross-check: 郡→州 for reporting (not forced into paint)
    jun_roster_zhou = {}
    for jname in jun_table[1:]:
        bare = jname
        for suf in ("属国", "郡", "国", "尹", "侯国"):
            if bare.endswith(suf) and len(bare) > len(suf):
                bare = bare[: -len(suf)]
                break
        z = jun_to_zhou.get(jname) or jun_to_zhou.get(bare)
        jun_roster_zhou[jname] = z or ""

    blob = b"".join(struct.pack("<HH", zhou_cell[i], jun_cell[i]) for i in range(n))
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
        "jun": {
            "layer": "CHGIS V6 v6_time_pref_pgn_utf_wgs84",
            "year": YEAR_JUN,
            "year_filter": f"beg_yr≤{YEAR_JUN}≤end_yr",
            "types": sorted(JUN_TYPES),
            "method": "hex_center_point_in_polygon",
            "citation": "CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016",
        },
        "zhou": {
            "layer": "CHGIS V4 v4_time_prov_pgn_utf (Xian80 GK Zone 19 → WGS84)",
            "year": YEAR_ZHOU,
            "year_filter": f"beg_yr≤{YEAR_ZHOU}≤end_yr; TYPE_CH=州",
            "method": "hex_center_point_in_polygon after reprojection",
            "citation": "CHGIS, Version 4 (c) Fairbank Center / Fudan CHGS",
            "note": "No province polygons at year 140; mid-TK 262 only has 扬州/荆州/交州 — not full 十三州.",
        },
        "xian": {
            "note": "No Han-era CHGIS county polygons; Thiessen/guessed 县 fills disabled.",
        },
        "fidelity_note": (
            "Not vector-identical — 500×500 hex (~6.9 km) assigned by whether its center "
            "lies inside a CHGIS polygon. Outside all polygons → empty."
        ),
        "bin": str(args.out_bin.name),
        "layout": "per cell: uint16 zhou_idx, uint16 jun_idx (LE); 0 = empty",
        "zhou_table": zhou_table,
        "jun_table": jun_table,
        "stats": {
            "land": land,
            "water": water,
            "zhou_filled": zhou_filled,
            "jun_filled": jun_filled,
            "zhou_fill_rate_land": round(zhou_filled / max(land, 1), 4),
            "jun_fill_rate_land": round(jun_filled / max(land, 1), 4),
            "zhou_faces": len(zhou_feats),
            "jun_faces": len(jun_feats),
            "zhou_units": len(zhou_table) - 1,
            "jun_units": len(jun_table) - 1,
            "zhou_ambiguous": zhou_amb,
            "jun_ambiguous": jun_amb,
        },
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "meta": {k: meta[k] for k in meta if k not in ("zhou_table", "jun_table")},
        "stats": meta["stats"],
        "zhou_hex_counts": dict(zhou_counts.most_common()),
        "jun_hex_counts": dict(jun_counts.most_common()),
        "jun_roster_zhou": jun_roster_zhou,
        "inventory": {
            "pref_pgn_year_140_faces": len(jun_feats),
            "prov_pgn_year_262_zhou_faces": len(zhou_feats),
            "prov_pgn_year_140_zhou_faces": 0,
            "county_pgn_han": "none in CHGIS V6 time series",
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"zhou_filled={zhou_filled}/{land} ({meta['stats']['zhou_fill_rate_land']:.1%}) "
        f"jun_filled={jun_filled}/{land} ({meta['stats']['jun_fill_rate_land']:.1%}) "
        f"→ {args.out_meta} + {args.out_bin}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
