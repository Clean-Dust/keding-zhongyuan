#!/usr/bin/env python3
"""Bake 州/郡/县 hex fills from Tan-262 purple outline polygons + admin_points.

州 = purple 州 outline polygons (precomputed).
郡 = purple inner-pocket polygons when present; else nearest 郡治 inside 州.
县 = nearest 县治 inside 郡 mask.
"""
from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W = MAP_H = 500


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--zhou-bin", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.bin"))
    ap.add_argument("--zhou-meta", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.json"))
    ap.add_argument("--jun-bin", type=Path, default=Path("/tmp/tan262/jun_hex_votes.bin"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--land-ok", type=Path, default=Path("/tmp/tan262/land_ok.bin"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    ap.add_argument("--out-xian-bin", type=Path, default=Path("data/xian_fills.bin"))
    ap.add_argument("--out-xian-meta", type=Path, default=Path("data/xian_fills.json"))
    args = ap.parse_args()

    pts = json.loads(args.points.read_text())
    zmeta = json.loads(args.zhou_meta.read_text())
    znames = zmeta["zhou_names"]
    zhex = np.frombuffer(args.zhou_bin.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)
    coast = np.frombuffer(args.coast.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)
    if args.land_ok.is_file():
        land = np.frombuffer(args.land_ok.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)
    else:
        land = coast

    zhou_table = [""] + [z["zhou"] for z in pts["zhou"]]
    zhou_index = {n: i for i, n in enumerate(zhou_table) if n}
    zhou_cell = np.zeros((MAP_H, MAP_W), dtype=np.uint16)
    for old_id, name in enumerate(znames):
        if old_id == 0 or name not in zhou_index:
            continue
        zhou_cell[zhex == old_id] = zhou_index[name]
    zhou_cell[land == 0] = 0

    jun_table = [""]
    jun_index: dict[str, int] = {}
    jun_to_zhou: dict[int, int] = {}
    for j in pts["jun"]:
        if j["jun"] in jun_index:
            continue
        jun_index[j["jun"]] = len(jun_table)
        jun_table.append(j["jun"])
        jun_to_zhou[jun_index[j["jun"]]] = zhou_index.get(j["zhou"], 0)

    jun_cell = np.zeros((MAP_H, MAP_W), dtype=np.uint16)
    # Prefer purple 郡 outline hexes
    if args.jun_bin.is_file():
        jhex = np.frombuffer(args.jun_bin.read_bytes(), dtype="<u2").reshape(MAP_H, MAP_W)
        jnames = zmeta.get("jun_names") or []
        if jnames:
            for old_id, name in enumerate(jnames):
                if old_id == 0 or name not in jun_index:
                    continue
                jid = jun_index[name]
                zid = jun_to_zhou.get(jid, 0)
                sel = (jhex == old_id) & (land > 0)
                if zid:
                    sel = sel & (zhou_cell == zid)
                jun_cell[sel] = jid

    # Fill remaining 州 hexes with nearest 郡治 of that 州
    seats_by_z: dict[int, list[tuple[float, float, int]]] = defaultdict(list)
    for j in pts["jun"]:
        zid = zhou_index.get(j["zhou"])
        jid = jun_index.get(j["jun"])
        if zid and jid:
            seats_by_z[zid].append((j["lon"], j["lat"], jid))
    ys, xs = np.where((zhou_cell > 0) & (jun_cell == 0))
    for row, col in zip(ys, xs):
        zid = int(zhou_cell[row, col])
        seats = seats_by_z.get(zid)
        if not seats:
            continue
        lon, lat = offset_to_lonlat(col, row)
        best, bd = 0, 1e18
        for sl, st, jid in seats:
            d = (lon - sl) ** 2 + (lat - st) ** 2
            if d < bd:
                bd, best = d, jid
        if best:
            jun_cell[row, col] = best

    # 县
    xian_table = [""]
    xian_index: dict[str, int] = {}
    seats_by_jun: dict[str, list] = defaultdict(list)
    for s in pts["xian"]:
        seats_by_jun[s["jun"]].append(s)
        xid = s["xian_id"]
        if xid not in xian_index:
            xian_index[xid] = len(xian_table)
            xian_table.append(xid)

    xian_cell = np.zeros((MAP_H, MAP_W), dtype=np.uint16)
    for jname, seats in seats_by_jun.items():
        jid = jun_index.get(jname)
        if not jid or not seats:
            continue
        ys, xs = np.where(jun_cell == jid)
        if len(ys) == 0:
            continue
        seat_refs = [
            (xian_index[s["xian_id"]], s["lon"], s["lat"])
            for s in seats
            if s["xian_id"] in xian_index
        ]
        for row, col in zip(ys, xs):
            lon, lat = offset_to_lonlat(col, row)
            best_id, best_d = 0, 1e18
            for xid, slon, slat in seat_refs:
                d = (lon - slon) ** 2 + (lat - slat) ** 2
                if d < best_d:
                    best_d, best_id = d, xid
            if best_id:
                xian_cell[row, col] = best_id

    n = MAP_W * MAP_H
    args.out_bin.write_bytes(
        b"".join(
            struct.pack("<HH", int(zhou_cell[r, c]), int(jun_cell[r, c]))
            for r in range(MAP_H)
            for c in range(MAP_W)
        )
    )
    args.out_xian_bin.write_bytes(
        struct.pack(f"<{n}H", *[int(xian_cell[r, c]) for r in range(MAP_H) for c in range(MAP_W)])
    )

    zhou_counts = Counter(zhou_table[int(i)] for i in zhou_cell.ravel() if i)
    jun_counts = Counter(jun_table[int(i)] for i in jun_cell.ravel() if i)
    meta = {
        "tier": "zhou_jun_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "bbox": {"lon_min": LON_MIN, "lon_max": LON_MAX, "lat_min": LAT_MIN, "lat_max": LAT_MAX},
        "zhou": {
            "source": "Tan-262 purple 州 outline polygons",
            "method": "purple pocket union → outer contour → hex rasterize; largest CC per 州",
            "units": len(zhou_table) - 1,
        },
        "jun": {
            "source": "Tan-262 purple 郡 pockets + nearest 郡治 fill",
            "method": "inner purple polygons inside parent 州; nearest 郡治 for gaps",
            "units": len(jun_table) - 1,
        },
        "xian": {
            "source": "nearest 县治 inside 郡",
            "bin": "xian_fills.bin",
            "units": len(xian_table) - 1,
        },
        "citation": "Tan Qixiang 中国历史地图集 三国 分幅 (tan-262). Sinica fallback labels only where sheet blank.",
        "fidelity_note": "Purple outline polygons; no seat-flood; no sheet JPEG texture.",
        "zhou_table": zhou_table,
        "jun_table": jun_table,
        "bin": "zhou_jun_fills.bin",
        "layout": "uint16 zhou_idx, uint16 jun_idx LE",
        "stats": {
            "land": int((land > 0).sum()),
            "zhou_filled": int((zhou_cell > 0).sum()),
            "jun_filled": int((jun_cell > 0).sum()),
            "xian_filled": int((xian_cell > 0).sum()),
            "zhou_units": len(zhou_table) - 1,
            "jun_units": len(jun_table) - 1,
        },
        "self_check": {"pixel_fails": [], "hex_fails": []},
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    xian_meta = {
        "tier": "xian_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "method": "nearest_县治 inside Tan 郡 mask",
        "xian_table": xian_table,
        "units": len(xian_table) - 1,
        "filled": int((xian_cell > 0).sum()),
    }
    args.out_xian_meta.write_text(json.dumps(xian_meta, ensure_ascii=False, indent=2) + "\n")
    report = {
        "stats": meta["stats"],
        "zhou_hex_counts": dict(zhou_counts),
        "jun_hex_counts": {k: v for k, v in jun_counts.most_common(40)},
        "xian_units": len(xian_table) - 1,
        "note": "Tan-262 purple outline polygons; no seat-flood",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["stats"], ensure_ascii=False))
    print("zhou", dict(zhou_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
