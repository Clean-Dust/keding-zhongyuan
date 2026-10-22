#!/usr/bin/env python3
"""Bake dual_scale coast mask + Eastern Han admin polygons (州/郡/县).

Sources (offline bake; runtime never downloads):
  - Natural Earth 50m land (public domain)
  - CHGIS V6 county + prefecture seats, year ≈ 140 CE (Eastern Han)
    Citation: CHGIS, Version 6. Fairbank Center (Harvard) & Fudan CHGS, 2016.
    Academic research license — derived simplified rings only; gaps = omit.

Usage (from repo root, after downloading NE + CHGIS into /tmp/dual_scale_bake):
  python3 tools/bake_coast_admin.py \\
    --ne /tmp/dual_scale_bake/ne_50m_land.shp \\
    --cnty /tmp/dual_scale_bake/chgis_cnty/v6_time_cnty_pts_utf_wgs84.shp \\
    --pref /tmp/dual_scale_bake/chgis_pref_pts/v6_time_pref_pts_utf_wgs84.shp \\
    --out data
"""

from __future__ import annotations

import argparse
import json
import math
import zlib
from pathlib import Path

import numpy as np
import shapefile
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import clip_by_rect, unary_union, voronoi_diagram
from shapely.validation import make_valid

# Match Hex.gd geographic constants.
LON_MIN, LON_MAX = 94.662, 125.69
LAT_MIN, LAT_MAX = 15.55, 41.272
MAP_W, MAP_H = 160, 160
YEAR = 140
PAD = 0.5

# 东汉十三州 — commandery/princedom seat name → 州 (representative late-Han roster).
# Names match CHGIS NAME_CH where possible; aliases normalized in lookup.
JUN_TO_ZHOU: dict[str, str] = {
    # 司隶
    "河南尹": "司隶", "河内郡": "司隶", "河東郡": "司隶", "河东郡": "司隶",
    "弘农郡": "司隶", "弘農郡": "司隶", "京兆尹": "司隶",
    "左冯翊": "司隶", "左馮翊": "司隶", "右扶风": "司隶", "右扶風": "司隶",
    # 豫州
    "颍川郡": "豫州", "潁川郡": "豫州", "汝南郡": "豫州", "梁国": "豫州", "梁國": "豫州",
    "沛国": "豫州", "沛國": "豫州", "陈国": "豫州", "陳國": "豫州", "鲁国": "豫州", "魯國": "豫州",
    # 冀州
    "魏郡": "冀州", "巨鹿郡": "冀州", "鉅鹿郡": "冀州", "常山国": "冀州", "常山國": "冀州",
    "中山国": "冀州", "中山國": "冀州", "中山郡": "冀州", "安平国": "冀州", "安平國": "冀州",
    "河间国": "冀州", "河間國": "冀州", "清河国": "冀州", "清河國": "冀州", "甘陵国": "冀州", "甘陵國": "冀州",
    "赵国": "冀州", "趙國": "冀州", "勃海郡": "冀州", "渤海郡": "冀州", "广平郡": "冀州", "廣平郡": "冀州",
    "长乐郡": "冀州", "長樂郡": "冀州", "博陵郡": "冀州",
    # 兖州
    "陈留郡": "兖州", "陳留郡": "兖州", "东郡": "兖州", "東郡": "兖州",
    "东平国": "兖州", "東平國": "兖州", "任城国": "兖州", "任城國": "兖州",
    "泰山郡": "兖州", "济北国": "兖州", "濟北國": "兖州", "山阳郡": "兖州", "山陽郡": "兖州",
    "济阴郡": "兖州", "濟陰郡": "兖州",
    # 徐州
    "东海郡": "徐州", "東海郡": "徐州", "东海国": "徐州", "東海國": "徐州",
    "琅邪国": "徐州", "琅琊国": "徐州", "琅邪郡": "徐州", "琅玡国": "徐州", "琅玡國": "徐州",
    "彭城国": "徐州", "彭城國": "徐州", "广陵郡": "徐州", "廣陵郡": "徐州",
    "下邳国": "徐州", "下邳國": "徐州",
    # 青州
    "济南国": "青州", "濟南國": "青州", "平原郡": "青州", "平原国": "青州", "平原國": "青州",
    "乐安国": "青州", "樂安國": "青州", "北海国": "青州", "北海國": "青州", "北海郡": "青州",
    "东莱郡": "青州", "東萊郡": "青州", "齐国": "青州", "齊國": "青州",
    # 荆州
    "南阳郡": "荆州", "南陽郡": "荆州", "南郡": "荆州", "江夏郡": "荆州",
    "零陵郡": "荆州", "桂阳郡": "荆州", "桂陽郡": "荆州", "武陵郡": "荆州", "长沙郡": "荆州", "長沙郡": "荆州",
    # 扬州
    "九江郡": "扬州", "九江國": "扬州", "丹阳郡": "扬州", "丹陽郡": "扬州",
    "庐江郡": "扬州", "廬江郡": "扬州", "会稽郡": "扬州", "會稽郡": "扬州",
    "吴郡": "扬州", "吳郡": "扬州", "豫章郡": "扬州",
    # 益州
    "汉中郡": "益州", "漢中郡": "益州", "巴郡": "益州", "广汉郡": "益州", "廣漢郡": "益州",
    "蜀郡": "益州", "犍为郡": "益州", "犍為郡": "益州", "越巂郡": "益州", "益州郡": "益州",
    "牂柯郡": "益州", "牂牁郡": "益州", "永昌郡": "益州",
    # 凉州
    "陇西郡": "凉州", "隴西郡": "凉州", "汉阳郡": "凉州", "漢陽郡": "凉州", "武都郡": "凉州",
    "金城郡": "凉州", "安定郡": "凉州", "北地郡": "凉州", "武威郡": "凉州",
    "张掖郡": "凉州", "張掖郡": "凉州", "酒泉郡": "凉州", "敦煌郡": "凉州",
    "张掖属国": "凉州", "張掖屬國": "凉州", "居延属国": "凉州", "居延屬國": "凉州",
    # 并州
    "上党郡": "并州", "上黨郡": "并州", "太原郡": "并州", "上郡": "并州",
    "西河郡": "并州", "五原郡": "并州", "云中郡": "并州", "雲中郡": "并州",
    "定襄郡": "并州", "雁门郡": "并州", "雁門郡": "并州", "朔方郡": "并州",
    # 幽州
    "涿郡": "幽州", "广阳郡": "幽州", "廣陽郡": "幽州", "代郡": "幽州", "上谷郡": "幽州",
    "渔阳郡": "幽州", "漁陽郡": "幽州", "右北平郡": "幽州", "辽西郡": "幽州", "遼西郡": "幽州",
    "辽东郡": "幽州", "遼東郡": "幽州", "玄菟郡": "幽州", "乐浪郡": "幽州", "樂浪郡": "幽州",
    "辽东属国": "幽州", "遼東屬國": "幽州",
    # 交州
    "南海郡": "交州", "苍梧郡": "交州", "蒼梧郡": "交州", "郁林郡": "交州", "鬱林郡": "交州",
    "合浦郡": "交州", "交趾郡": "交州", "交阯郡": "交州", "九真郡": "交州", "日南郡": "交州",
}

ZHOU_ORDER = [
    "司隶", "豫州", "冀州", "兖州", "徐州", "青州",
    "荆州", "扬州", "益州", "凉州", "并州", "幽州", "交州",
]


def lonlat_of_offset(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def load_land(ne_path: Path):
    r = shapefile.Reader(str(ne_path))
    geoms = []
    for sr in r.iterShapeRecords():
        g = shape(sr.shape.__geo_interface__)
        if g.is_empty:
            continue
        g = make_valid(g)
        geoms.append(g)
    land = unary_union(geoms)
    bbox = box(LON_MIN - PAD, LAT_MIN - PAD, LON_MAX + PAD, LAT_MAX + PAD)
    return make_valid(land.intersection(bbox))


def bake_coast(land) -> dict:
    cells = []
    land_n = water_n = 0
    for row in range(MAP_H):
        for col in range(MAP_W):
            lon, lat = lonlat_of_offset(col, row)
            is_land = land.contains(Point(lon, lat)) or land.touches(Point(lon, lat))
            cells.append(1 if is_land else 0)
            if is_land:
                land_n += 1
            else:
                water_n += 1
    raw = bytes(cells)
    return {
        "format": "zlib_bytes_0water_1land",
        "width": MAP_W,
        "height": MAP_H,
        "lon_min": LON_MIN,
        "lon_max": LON_MAX,
        "lat_min": LAT_MIN,
        "lat_max": LAT_MAX,
        "source": "Natural Earth 50m land (public domain)",
        "land_count": land_n,
        "water_count": water_n,
        "payload_b64": __import__("base64").b64encode(zlib.compress(raw, 9)).decode("ascii"),
    }, land_n, water_n


def active_points(shp_path: Path, year: int, type_ok: set[str] | None = None):
    r = shapefile.Reader(str(shp_path))
    names = [f[0] for f in r.fields[1:]]
    idx = {n: i for i, n in enumerate(names)}
    out = []
    for rec in r.iterRecords():
        beg, end = int(rec[idx["BEG_YR"]]), int(rec[idx["END_YR"]])
        if not (beg <= year <= end):
            continue
        t = str(rec[idx["TYPE_CH"]]).strip()
        if type_ok is not None and t not in type_ok:
            continue
        x, y = float(rec[idx["X_COOR"]]), float(rec[idx["Y_COOR"]])
        if not (LON_MIN - PAD <= x <= LON_MAX + PAD and LAT_MIN - PAD <= y <= LAT_MAX + PAD):
            continue
        if x == 0.0 and y == 0.0:
            continue
        name = str(rec[idx["NAME_CH"]]).strip()
        out.append({"name": name, "type": t, "lon": x, "lat": y, "sys_id": int(rec[idx["SYS_ID"]])})
    return out


def simplify_ring(coords, tol=0.08):
    if len(coords) < 4:
        return None
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.is_empty:
        return None
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(tol, preserve_topology=True)
    if poly.is_empty or poly.area < 1e-6:
        return None
    ring = list(poly.exterior.coords)
    # Drop closing duplicate for JSON size? Keep closed for Godot.
    if len(ring) < 4:
        return None
    return [[round(c[0], 4), round(c[1], 4)] for c in ring]


def geom_to_rings(geom, tol=0.08):
    rings = []
    if geom is None or geom.is_empty:
        return rings
    geom = make_valid(geom)
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for g in geoms:
        if g.geom_type != "Polygon" or g.is_empty:
            continue
        g = g.simplify(tol, preserve_topology=True)
        if g.is_empty or g.area < 1e-5:
            continue
        ring = [[round(c[0], 4), round(c[1], 4)] for c in g.exterior.coords]
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def voronoi_features(points: list[dict], clip_geom, tol: float, id_prefix: str):
    """Build Voronoi cells clipped to land; document as schematic neighbor extents."""
    if len(points) < 2:
        return []
    coords = [(p["lon"], p["lat"]) for p in points]
    mp = MultiPoint(coords)
    # Envelope slightly larger than clip so edge cells still form.
    env = clip_geom.envelope.buffer(0.3)
    vd = voronoi_diagram(mp, envelope=env, edges=False)
    features = []
    # Match cells to seeds by containment of seed point.
    for i, p in enumerate(points):
        seed = Point(p["lon"], p["lat"])
        cell = None
        for g in vd.geoms:
            if g.contains(seed) or g.touches(seed):
                cell = g
                break
        if cell is None:
            # Fallback: nearest polygon centroid.
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
        rings = geom_to_rings(clipped, tol=tol)
        if not rings:
            continue
        features.append({
            "id": f"{id_prefix}_{p['sys_id']}",
            "name": p["name"],
            "type": p.get("type", ""),
            "owner": None,
            "rings": rings,
            "centroid": [round(p["lon"], 4), round(p["lat"], 4)],
        })
    return features


def dissolve_by_zhou(jun_feats: list[dict], land) -> list[dict]:
    buckets: dict[str, list] = {z: [] for z in ZHOU_ORDER}
    missing = []
    for f in jun_feats:
        z = JUN_TO_ZHOU.get(f["name"])
        if z is None:
            # Strip suffix variants
            for k, v in JUN_TO_ZHOU.items():
                if f["name"].startswith(k[:2]) and (k in f["name"] or f["name"] in k):
                    z = v
                    break
        if z is None:
            missing.append(f["name"])
            continue
        for ring in f["rings"]:
            try:
                poly = Polygon(ring)
                if poly.is_valid and not poly.is_empty:
                    buckets[z].append(poly)
            except Exception:
                pass
    out = []
    for z in ZHOU_ORDER:
        geoms = buckets[z]
        if not geoms:
            out.append({
                "id": f"zhou_{z}",
                "name": z,
                "owner": None,
                "rings": [],
                "note": "资料缺失",
                "centroid": None,
            })
            continue
        merged = make_valid(unary_union(geoms).intersection(land))
        rings = geom_to_rings(merged, tol=0.12)
        c = merged.centroid if not merged.is_empty else None
        out.append({
            "id": f"zhou_{z}",
            "name": z,
            "owner": None,
            "rings": rings,
            "centroid": [round(c.x, 4), round(c.y, 4)] if c and not c.is_empty else None,
        })
    return out, missing


def write_json(path: Path, obj: dict):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne", required=True)
    ap.add_argument("--cnty", required=True)
    ap.add_argument("--pref", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("loading land…")
    land = load_land(Path(args.ne))
    print("baking coast…")
    coast, land_n, water_n = bake_coast(land)
    write_json(out / "coast_mask.json", coast)
    # Also write compact raw for debugging / alternate loaders.
    raw = zlib.decompress(__import__("base64").b64decode(coast["payload_b64"]))
    (out / "coast_mask.bin").write_bytes(raw)
    print(f"coast land={land_n} water={water_n} total={land_n + water_n}")

    print("county points…")
    cnty = active_points(
        Path(args.cnty), YEAR,
        type_ok={"县", "侯国", "国", "道", "邑"},
    )
    print(f"  {len(cnty)} seats in bbox @ {YEAR}")
    xian = voronoi_features(cnty, land, tol=0.06, id_prefix="xian")
    write_json(out / "admin_xian.json", {
        "tier": "xian",
        "year": YEAR,
        "source": "CHGIS V6 time-series county points → Voronoi clipped to NE land (schematic; not cadastral)",
        "citation": "CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016. Academic research use.",
        "method": "voronoi_neighbor_extent",
        "feature_count": len(xian),
        "features": xian,
    })

    print("prefecture points…")
    pref = active_points(
        Path(args.pref), YEAR,
        type_ok={"郡", "国", "尹", "侯国", "属国"},
    )
    print(f"  {len(pref)} seats in bbox @ {YEAR}")
    jun = voronoi_features(pref, land, tol=0.1, id_prefix="jun")
    write_json(out / "admin_jun.json", {
        "tier": "jun",
        "year": YEAR,
        "source": "CHGIS V6 time-series prefecture points → Voronoi clipped to NE land (schematic)",
        "citation": "CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016. Academic research use.",
        "method": "voronoi_neighbor_extent",
        "feature_count": len(jun),
        "features": jun,
    })

    print("dissolve 州…")
    zhou, missing = dissolve_by_zhou(jun, land)
    with_rings = sum(1 for z in zhou if z["rings"])
    write_json(out / "admin_zhou.json", {
        "tier": "zhou",
        "year": YEAR,
        "system": "东汉十三州 (representative; whole Three Kingdoms era uses this tier)",
        "source": "CHGIS V6 prefecture seats @140 → Voronoi → dissolve by Eastern Han 郡→州 roster",
        "citation": "CHGIS V6 + 后汉书/续汉书郡国志 roster",
        "feature_count": len(zhou),
        "with_geometry": with_rings,
        "unmapped_jun_names": missing,
        "features": zhou,
    })
    print(f"州 with geometry: {with_rings}/13; unmapped jun: {missing}")


if __name__ == "__main__":
    main()
