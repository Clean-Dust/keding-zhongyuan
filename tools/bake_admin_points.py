#!/usr/bin/env python3
"""Bake admin POINT layers for Phase 1 (no faces).

  - 州治: 东汉十三州 — CHGIS pref 州 pts when available, else known 治所 seats
  - 郡治: CHGIS V6 pref_pts @140 matched to roster 郡 (zhou from roster; 江夏→荆州)
  - 县治: existing CHGIS-only county_seats.json (no invented Sinica matches)

Also writes empty zhou_jun_fills (drops sanguo-map color fills).

Usage:
  python3 tools/bake_admin_points.py
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from pathlib import Path

import shapefile
from opencc import OpenCC

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500

_cc = OpenCC("t2s")


def t2s(s: str) -> str:
    return _cc.convert(str(s or "").strip())


def lonlat_to_col_row(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return max(0, min(MAP_W - 1, col)), max(0, min(MAP_H - 1, row))


ZHOU_SEAT_PREFS: dict[str, list[str]] = {
    "司隶": ["雒阳"],
    "豫州": ["谯"],
    "冀州": ["邺"],
    "兖州": ["昌邑"],
    "徐州": ["郯", "下邳"],
    "青州": ["临菑", "临淄"],
    "荆州": ["汉寿", "襄阳"],
    "扬州": ["寿春"],
    "益州": ["成都"],
    "凉州": ["姑臧", "陇", "狄道"],
    "并州": ["晋阳", "太原"],
    "幽州": ["蓟", "涿"],
    "交州": ["龙编", "广信", "番禺"],
}

JUN_ALIASES = {
    "越嶲郡": "越巂郡",
    "牂柯郡": "牂牁郡",
    "广平郡": None,
    "砀郡": None,
}


def load_chgis_pref(path: Path, year: int = 140) -> list[dict]:
    r = shapefile.Reader(str(path), encoding="utf-8", encodingErrors="replace")
    fields = [f[0] for f in r.fields[1:]]
    out = []
    for rec, shp in zip(r.iterRecords(), r.iterShapes()):
        d = dict(zip(fields, rec))
        beg = int(float(d["BEG_YR"]))
        end = int(float(d["END_YR"]))
        if not (beg <= year <= end) or not shp.points:
            continue
        out.append(
            {
                "name": t2s(str(d.get("NAME_CH") or "")),
                "typ": t2s(str(d.get("TYPE_CH") or "")),
                "lon": float(shp.points[0][0]),
                "lat": float(shp.points[0][1]),
                "sys_id": d.get("SYS_ID"),
                "pres": str(d.get("PRES_LOC") or ""),
                "beg": beg,
                "end": end,
            }
        )
    return out


def load_chgis_zhou_pts(path: Path) -> dict[str, dict]:
    r = shapefile.Reader(str(path), encoding="utf-8", encodingErrors="replace")
    fields = [f[0] for f in r.fields[1:]]
    best: dict[str, dict] = {}
    for rec, shp in zip(r.iterRecords(), r.iterShapes()):
        d = dict(zip(fields, rec))
        if t2s(str(d.get("TYPE_CH") or "")) != "州":
            continue
        name = t2s(str(d.get("NAME_CH") or ""))
        if name not in ZHOU_SEAT_PREFS or not shp.points:
            continue
        beg = int(float(d["BEG_YR"]))
        end = int(float(d["END_YR"]))
        if end < 100 or beg > 280:
            continue
        score = 3 if beg <= 140 <= end else (2 if beg <= 220 <= end else 1)
        row = {
            "name": name,
            "lon": float(shp.points[0][0]),
            "lat": float(shp.points[0][1]),
            "sys_id": d.get("SYS_ID"),
            "pres": str(d.get("PRES_LOC") or ""),
            "beg": beg,
            "end": end,
            "score": score,
        }
        prev = best.get(name)
        if prev is None or row["score"] > prev["score"] or (
            row["score"] == prev["score"] and beg >= prev["beg"]
        ):
            best[name] = row
    return best


def match_roster_jun(name: str, jun_to_zhou: dict[str, str]) -> tuple[str, str] | None:
    if name in jun_to_zhou:
        return name, jun_to_zhou[name]
    bare = name
    for suf in ("属国", "郡", "国", "尹"):
        if bare.endswith(suf) and len(bare) > len(suf):
            bare = bare[: -len(suf)]
            break
    for j, z in jun_to_zhou.items():
        jb = j
        for suf in ("属国", "郡", "国", "尹"):
            if jb.endswith(suf) and len(jb) > len(suf):
                jb = jb[: -len(suf)]
                break
        if jb == bare or j == name:
            return j, z
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref", type=Path, default=Path("/tmp/chgis/v6_time_pref_pts_utf_wgs84.shp"))
    ap.add_argument("--roster", type=Path, default=Path("data/counties_roster.json"))
    ap.add_argument("--seats", type=Path, default=Path("data/county_seats.json"))
    ap.add_argument("--seats-report", type=Path, default=Path("data/county_seats_report.json"))
    ap.add_argument("--out-points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--out-fills-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--out-fills-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--out-fills-report", type=Path, default=Path("data/zhou_jun_fills_report.json"))
    args = ap.parse_args()

    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    seats = json.loads(args.seats.read_text(encoding="utf-8"))["seats"]
    seats_by_zhou: dict[str, list] = defaultdict(list)
    seats_by_jun: dict[tuple[str, str], list] = defaultdict(list)
    for s in seats:
        seats_by_zhou[s["zhou"]].append(s)
        seats_by_jun[(s["zhou"], s["jun"])].append(s)

    roster_juns: dict[str, set[str]] = defaultdict(set)
    jun_to_zhou: dict[str, str] = {}
    for row in roster:
        roster_juns[row["zhou"]].add(row["jun"])
        if row["jun"] not in jun_to_zhou:
            jun_to_zhou[row["jun"]] = row["zhou"]

    chgis_zhou = load_chgis_zhou_pts(args.pref) if args.pref.is_file() else {}
    zhou_points = []
    for zhou in ZHOU_SEAT_PREFS:
        lon = lat = None
        seat_name = None
        src = note = ""
        if zhou in chgis_zhou:
            cz = chgis_zhou[zhou]
            lon, lat = cz["lon"], cz["lat"]
            seat_name = zhou
            src = "CHGIS_V6_pref_pts_州"
            note = cz.get("pres") or ""
        else:
            found = None
            for want in ZHOU_SEAT_PREFS[zhou]:
                for s in seats_by_zhou.get(zhou, []):
                    if s["name"] == want:
                        found = s
                        break
                if found:
                    break
            if not found and seats_by_zhou.get(zhou):
                found = seats_by_zhou[zhou][0]
                note = "fallback_first_seat_in_zhou"
            if found:
                lon, lat = found["lon"], found["lat"]
                seat_name = found["name"]
                src = "CHGIS_V6_county_seat"
        if lon is None:
            print(f"WARN no 州治 for {zhou}")
            continue
        col, row = lonlat_to_col_row(lon, lat)
        zhou_points.append(
            {
                "level": "zhou",
                "zhou": zhou,
                "name": f"{zhou}治",
                "seat_name": seat_name,
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "col": col,
                "row": row,
                "source": src,
                "note": note,
            }
        )

    prefs = load_chgis_pref(args.pref, 140) if args.pref.is_file() else []
    jun_points = []
    matched_juns: set[str] = set()
    for p in prefs:
        if p["typ"] not in ("郡", "国", "尹", "侯国", "属国"):
            continue
        name = p["name"]
        if name in JUN_ALIASES:
            mapped = JUN_ALIASES[name]
            if mapped is None:
                continue
            name = mapped
        hit = match_roster_jun(name, jun_to_zhou)
        if hit is None:
            continue
        name, zhou = hit
        if name.startswith("江夏"):
            zhou = "荆州"
            name = "江夏郡"
        matched_juns.add(name)
        col, row = lonlat_to_col_row(p["lon"], p["lat"])
        jun_points.append(
            {
                "level": "jun",
                "zhou": zhou,
                "jun": name,
                "name": name,
                "lon": round(p["lon"], 6),
                "lat": round(p["lat"], 6),
                "col": col,
                "row": row,
                "source": "CHGIS_V6_pref_pts",
                "chgis_sys_id": p.get("sys_id"),
                "note": p.get("pres") or "",
            }
        )

    missing_jun = []
    for zhou, jset in roster_juns.items():
        for j in sorted(jset):
            if j in matched_juns:
                continue
            pts = seats_by_jun.get((zhou, j), [])
            if not pts:
                missing_jun.append({"zhou": zhou, "jun": j, "reason": "no_chgis_pref_no_seats"})
                continue
            lons = sorted(x["lon"] for x in pts)
            lats = sorted(x["lat"] for x in pts)
            mid = len(pts) // 2
            lon, lat = lons[mid], lats[mid]
            col, row = lonlat_to_col_row(lon, lat)
            jun_points.append(
                {
                    "level": "jun",
                    "zhou": zhou,
                    "jun": j,
                    "name": j,
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "col": col,
                    "row": row,
                    "source": "median_CHGIS_county_seats",
                    "n_seats": len(pts),
                    "note": "no CHGIS pref_pts@140; median of matched 县",
                }
            )
            matched_juns.add(j)

    xian_points = [
        {
            "level": "xian",
            "xian_id": s["xian_id"],
            "zhou": s["zhou"],
            "jun": s["jun"],
            "name": s["name"],
            "lon": s["lon"],
            "lat": s["lat"],
            "col": s["col"],
            "row": s["row"],
            "source": s.get("source", "CHGIS_V6"),
            "confidence": s.get("confidence", "high"),
        }
        for s in seats
    ]

    unmatched = []
    if args.seats_report.is_file():
        unmatched = json.loads(args.seats_report.read_text(encoding="utf-8")).get("unmatched") or []

    # Verify 江夏
    jx = [j for j in jun_points if j["jun"].startswith("江夏")]
    assert all(j["zhou"] == "荆州" for j in jx), jx

    doc = {
        "meta": {
            "phase": 1,
            "authority_visual": "Sinica CCTS ad0262 (underlay QA)",
            "coords": "CHGIS V6 when available; hierarchy from game roster",
            "jiangxia": "江夏郡 → 荆州 (roster; not 扬州)",
            "faces": "none — fills cleared empty",
            "xian_unmatched_policy": (
                "No invented Sinica OCR matches this bake; leave unmatched until "
                "high-confidence label read is available."
            ),
        },
        "stats": {
            "zhou_seats": len(zhou_points),
            "jun_seats": len(jun_points),
            "xian_seats_matched": len(xian_points),
            "xian_roster": len(roster),
            "xian_unmatched": len(unmatched),
            "jun_gaps": len(missing_jun),
        },
        "zhou": zhou_points,
        "jun": jun_points,
        "xian": xian_points,
        "xian_unmatched": unmatched,
        "jun_gaps": missing_jun,
    }
    args.out_points.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"points: 州治={len(zhou_points)} 郡治={len(jun_points)} "
        f"县治={len(xian_points)} unmatched_县={len(unmatched)}"
    )

    n = MAP_W * MAP_H
    args.out_fills_bin.write_bytes(b"".join(struct.pack("<HH", 0, 0) for _ in range(n)))
    fills_meta = {
        "tier": "zhou_jun_fills",
        "map_w": MAP_W,
        "map_h": MAP_H,
        "bbox": {
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
        },
        "zhou": {"source": "none", "note": "Phase 1 points only; sanguo-map fills removed"},
        "jun": {"source": "none", "note": "Phase 1 points only; sanguo-map fills removed"},
        "xian": {"note": "No faces; see admin_points.json"},
        "zhou_table": [""],
        "jun_table": [""],
        "bin": args.out_fills_bin.name,
        "layout": "per cell: uint16 zhou_idx, uint16 jun_idx (LE); 0 = empty",
        "stats": {
            "zhou_filled": 0,
            "jun_filled": 0,
            "zhou_units": 0,
            "jun_units": 0,
        },
    }
    args.out_fills_meta.write_text(json.dumps(fills_meta, ensure_ascii=False, indent=2) + "\n")
    args.out_fills_report.write_text(
        json.dumps({"stats": fills_meta["stats"], "note": "empty fills; phase 1 points"}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print("cleared zhou_jun_fills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
