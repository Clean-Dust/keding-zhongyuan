#!/usr/bin/env python3
"""Reparent Sinica 州/郡/县 points from baked face under each seat."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500


def lonlat_to_col_row(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return max(0, min(MAP_W - 1, col)), max(0, min(MAP_H - 1, row))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--fills-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--fills-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    args = ap.parse_args()

    pts = json.loads(args.points.read_text(encoding="utf-8"))
    meta = json.loads(args.fills_meta.read_text(encoding="utf-8"))
    zt = meta["zhou_table"]
    jt = meta["jun_table"]
    raw = args.fills_bin.read_bytes()
    n = MAP_W * MAP_H
    cells = struct.unpack(f"<{n * 2}H", raw)

    def face_at(lon: float, lat: float) -> tuple[str, str]:
        col, row = lonlat_to_col_row(lon, lat)
        for rad in range(0, 4):
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    c, r = col + dx, row + dy
                    if not (0 <= c < MAP_W and 0 <= r < MAP_H):
                        continue
                    i = r * MAP_W + c
                    zid, jid = cells[2 * i], cells[2 * i + 1]
                    zn = zt[zid] if zid and zid < len(zt) else ""
                    jn = jt[jid] if jid and jid < len(jt) else ""
                    if zn or jn:
                        return zn, jn
        return "", ""

    # Keep plate-read parents when face under seat is empty or conflicts with known plate.
    SINICA_JUN_PARENT = {
        "江夏郡": "荆州",
        "颍川郡": "豫州",
        "金城郡": "凉州",
        "魏郡": "冀州",
        "南安郡": "雍州",
        "阴平郡": "益州",
        "范阳郡": "幽州",
        "苍梧郡": "交州",
        "武陆郡": "荆州",
    }
    for j in pts["jun"]:
        zn, jn = face_at(j["lon"], j["lat"])
        if j["jun"] in SINICA_JUN_PARENT:
            j["zhou"] = SINICA_JUN_PARENT[j["jun"]]
        elif zn:
            j["zhou"] = zn
        if jn and jn != j["jun"]:
            # seat may sit on ink; keep own name
            pass
        j.pop("tk262_parent", None)
        if j.get("source", "").startswith("sinica_ad0262+tk262"):
            j["source"] = "sinica_ad0262"
        note = str(j.get("note") or "")
        if "ThreeKingdoms262" in note or "tk262" in note.lower():
            j["note"] = "Sinica 郡/国 label; parent 州 from Sinica face under seat"

    jun_parent = {j["jun"]: j["zhou"] for j in pts["jun"]}
    xian_out = []
    dropped = []
    for s in pts["xian"]:
        zn, jn = face_at(s["lon"], s["lat"])
        s = dict(s)
        if jn:
            s["jun"] = jn
        parent_z = jun_parent.get(s.get("jun") or "")
        locked_z = SINICA_JUN_PARENT.get(s.get("jun") or "")
        # 州 follows locked/plate 郡 parent when face 州 conflicts (阳翟→颍川→豫州)
        if locked_z:
            s["zhou"] = locked_z
        elif parent_z:
            s["zhou"] = parent_z
        elif zn:
            s["zhou"] = zn
        s["xian_id"] = f"xian:{s['zhou']}/{s['jun']}/{s['name']}"
        s.pop("tk262_parent", None)
        xian_out.append(s)

    # dedupe by id (keep highest OCR conf)
    by = {}
    for s in sorted(xian_out, key=lambda z: -float(z.get("sinica_ocr_conf") or 0)):
        by.setdefault(s["xian_id"], s)
    # drop obvious multi-ghost OCR junk name 蓓 if >2 copies under different 郡
    name_counts = {}
    for s in by.values():
        name_counts[s["name"]] = name_counts.get(s["name"], 0) + 1
    cleaned = []
    for s in by.values():
        if s["name"] == "蓓" and name_counts.get("蓓", 0) >= 3:
            dropped.append(s["xian_id"])
            continue
        cleaned.append(s)

    pts["xian"] = sorted(cleaned, key=lambda z: (z["zhou"], z["jun"], z["name"]))
    pts["stats"]["zhou_seats"] = len(pts["zhou"])
    pts["stats"]["jun_seats"] = len(pts["jun"])
    pts["stats"]["xian_seats_matched"] = len(pts["xian"])
    pts["stats"]["zhou_names"] = [z["zhou"] for z in pts["zhou"]]
    pts["meta"]["authority"] = "Sinica CCTS ad0262 OCR labels only"
    pts["meta"].pop("tk262_atlas", None)
    # strip tk262 stats noise
    for k in list(pts["stats"]):
        if "tk262" in k:
            pts["stats"].pop(k)

    args.points.write_text(json.dumps(pts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"reparented jun={len(pts['jun'])} xian={len(pts['xian'])} "
        f"dropped_junk={len(dropped)}"
    )
    if dropped:
        print("dropped", dropped[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
