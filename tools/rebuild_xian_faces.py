#!/usr/bin/env python3
"""Rebuild 县 hex faces = nearest Sinica 县治 inside existing parent 郡 masks.

Does not redo 州/郡 faces (reads zhou_jun_fills.bin as-is).

Usage:
  python3 tools/rebuild_xian_faces.py --points data/admin_points.json
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
    lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
    lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
    return lon, lat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--fills-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--fills-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--out-bin", type=Path, default=Path("data/xian_fills.bin"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/xian_fills.json"))
    ap.add_argument("--report", type=Path, default=Path("data/xian_fills_report.json"))
    args = ap.parse_args()

    pts = json.loads(args.points.read_text(encoding="utf-8"))
    meta = json.loads(args.fills_meta.read_text(encoding="utf-8"))
    jun_table = meta["jun_table"]
    raw = args.fills_bin.read_bytes()
    n = MAP_W * MAP_H
    cells = struct.unpack(f"<{n * 2}H", raw)
    coast = args.coast.read_bytes()

    seats_by_jun: dict[str, list] = defaultdict(list)
    for s in pts["xian"]:
        seats_by_jun[s["jun"]].append(s)

    xian_table = [""]
    xian_index: dict[str, int] = {}
    xian_cell = [0] * n
    xian_counts: Counter[str] = Counter()
    xian_filled = 0
    jun_filled = 0

    jun_hexes: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if coast[i] == 0:
                continue
            jid = cells[2 * i + 1]
            if jid:
                jun_filled += 1
                jun_hexes[jid].append((col, row, i))

    for jid, hexes in jun_hexes.items():
        jname = jun_table[jid] if jid < len(jun_table) else ""
        seats = seats_by_jun.get(jname, [])
        if not seats:
            continue
        seat_refs = []
        for s in seats:
            xid = s["xian_id"]
            if xid not in xian_index:
                xian_index[xid] = len(xian_table)
                xian_table.append(xid)
            seat_refs.append((xian_index[xid], s["lon"], s["lat"]))
        for col, row, i in hexes:
            lon, lat = offset_to_lonlat(col, row)
            best_id, best_d = 0, 1e18
            for xid, slon, slat in seat_refs:
                d = (lon - slon) ** 2 + (lat - slat) ** 2
                if d < best_d:
                    best_d = d
                    best_id = xid
            if best_id:
                xian_cell[i] = best_id
                xian_filled += 1
                xian_counts[xian_table[best_id]] += 1

    args.out_bin.write_bytes(struct.pack(f"<{n}H", *xian_cell))
    xian_meta = {
        "tier": "xian_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "method": "nearest Sinica 县治 inside existing Sinica-traced 郡 mask",
        "xian_table": xian_table,
        "bin": args.out_bin.name,
        "layout": "uint16 xian_idx LE per cell; 0=empty",
        "stats": {
            "xian_filled": xian_filled,
            "xian_units": len(xian_table) - 1,
            "jun_filled": jun_filled,
            "fill_rate_vs_jun": round(xian_filled / max(jun_filled, 1), 4),
        },
        "note": "Seats from Sinica ad0262 OCR only. 州/郡 faces unchanged this bake.",
    }
    args.out_meta.write_text(json.dumps(xian_meta, ensure_ascii=False, indent=2) + "\n")

    # Update parent meta xian section
    meta["xian"] = {
        "source": "nearest Sinica ad0262 县治 inside 郡",
        "bin": args.out_bin.name,
        "units": len(xian_table) - 1,
    }
    meta["stats"]["xian_filled"] = xian_filled
    meta["citation"] = "Academia Sinica CCTS ad0262 OCR labels (县); prior Sinica-traced 州/郡 faces"
    args.fills_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    report = {
        "xian_units": len(xian_table) - 1,
        "xian_filled": xian_filled,
        "jun_filled": jun_filled,
        "fill_rate_vs_jun": xian_meta["stats"]["fill_rate_vs_jun"],
        "top_xian": xian_counts.most_common(20),
        "juns_without_seats": sorted(
            jun_table[jid]
            for jid in jun_hexes
            if jid and jun_table[jid] and not seats_by_jun.get(jun_table[jid])
        ),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"DONE xian_units={len(xian_table)-1} filled={xian_filled}/{jun_filled} "
        f"juns_without_seats={len(report['juns_without_seats'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
