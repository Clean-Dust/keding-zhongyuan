#!/usr/bin/env python3
"""Match counties_roster.json to CHGIS V6 county seat points; project to hex.

Does NOT invent coordinates. Unmatched / far-offshore / out-of-bbox stay unmatched.
Does NOT write xian_id hex fills or Voronoi — seats only.

Citation: CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016.
Academic research use; derived seat points only (no redistribution of raw shapefile).

Usage (from repo root):
  python3 tools/match_county_seats.py \\
    --cnty /tmp/chgis/v6_time_cnty_pts_utf_wgs84.shp \\
    --pref /tmp/chgis/v6_time_pref_pts_utf_wgs84.shp \\
    --coast data/coast_mask.bin \\
    --roster data/counties_roster.json \\
    --out data/county_seats.json \\
    --report data/county_seats_report.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

try:
    import shapefile
except ImportError as e:
    raise SystemExit("pip install pyshp") from e

try:
    from opencc import OpenCC
except ImportError as e:
    raise SystemExit("pip install opencc-python-reimplemented") from e

# Match Hex.gd (Sinica ad0262 screenshot extent)
LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 240, 240
YEAR_PRIMARY = 140
YEAR_LO, YEAR_HI = 100, 280
# Snap seat from water to land within this hex distance; beyond → unmatched.
SNAP_MAX = 3
# Reject if raw (unclamped) offset is this many cells outside the map.
OOB_MARGIN = 2

COUNTY_TYPES = {"县", "邑", "道", "侯国", "侯國"}
JUN_TYPES = {"郡", "国", "國", "尹", "属国", "屬國", "州", "侯国", "侯國"}

# Orthographic / 续汉书↔CHGIS aliases (roster bare name → CHGIS bare name).
NAME_ALIASES: dict[str, str] = {
    "菀陵": "苑陵",
    "䜌": "栾",  # rare: if roster name is just this char (unlikely)
    "南䜌": "南栾",
    "隐强": "隐强",  # placeholder; CHGIS may use 隱強 → t2s
    "灈阳": "濯阳",
    "鲖阳": "鮦阳",
    "襃信": "褒信",
    "驺": "邹",
    "絺": "希",  # uncommon; leave if unused
    "杨": "杨",  # 河东杨县 often 杨氏 in later sources — handled below
}

# Roster name → preferred CHGIS NAME_CH (with suffix) when bare match fails.
FULL_NAME_ALIASES: dict[str, str] = {
    "司隶/河东郡/杨": "杨县",  # often listed 杨; CHGIS EH may use 杨氏 — try below
    "豫州/鲁国/驺": "驺县",
}

# Extra bare-name aliases after t2s.
EXTRA_ALIASES: dict[str, list[str]] = {
    "菀陵": ["苑陵"],
    "南䜌": ["南栾", "南䜌"],
    "隐强": ["隐强", "㴶强"],
    "灈阳": ["濯阳", "灈阳"],
    "鲖阳": ["鮦阳", "鲖阳"],
    "襃信": ["褒信"],
    "驺": ["邹", "驺"],
    "杨": ["杨氏", "杨"],
    "绛": ["绛邑", "绛"],
    "襄": ["襄城"],  # 颍川襄 — often 襄城; only use with jun proximity
    "平": ["平阴"],  # weak; jun-gated
    "梁": ["梁"],
    "巩": ["巩"],
    "新城": ["新城", "北新城"],
    "甘陵": ["甘陵"],
    # 续汉书异体 ↔ Kongming/CHGIS simplified forms
    "东昬": ["东昏"],
    "无塩": ["无盐"],
    "富成": ["富城"],
    "乗氏": ["乘氏"],
    "胊": ["朐"],
    "赣楡": ["赣榆"],
    "塩渎": ["盐渎"],
    "盱台": ["盱眙"],
    "湿阴": ["漯阴"],
    "千乗": ["千乘"],
    "営陵": ["营陵"],
    "临菑": ["临淄"],
    "临胊": ["临朐"],
    "穣": ["穰"],
    "很山": ["佷山"],
    "営道": ["营道"],
    "営浦": ["营浦"],
    "荼陵": ["茶陵"],
    "锺离": ["钟离"],
    "江乗": ["江乘"],
    "晥": ["皖"],
    "居巣": ["居巢"],
    "海塩": ["海盐"],
    "祋祤": ["祋祤", "祋祡"],
    "拒挺": ["拒县"],
}

# Kongming AD262 map: roster 东汉 州 ↔ map Prov_CHS (never 西域 / peripheral).
KONGMING_SEAT_TYPES = {"County Seat", "Commandery Seat", "Provincial Seat", "Capital"}
JUN_NAME_ALIASES: dict[str, list[str]] = {
    "广阳": ["燕", "广阳"],
    "左冯翊": ["冯翊", "左冯翊"],
    "右扶风": ["扶风", "右扶风"],
    "东": ["东"],
    "陈留": ["陈留"],
    "河南": ["河南"],
    "京兆": ["京兆"],
    "辽东": ["辽东"],
    "乐浪": ["乐浪"],
}


_cc = OpenCC("t2s")
# Strip only clear admin suffixes. Never strip 邑/道 — they are often part of the
# place name itself (安邑、高邑、夷道、狄道). Roster names are already bare.
_CHGIS_SUFFIXES = ("侯国", "侯國", "属国", "屬國", "县", "郡", "尹", "州", "国", "國")
_JUN_SUFFIXES = ("属国", "屬國", "侯国", "侯國", "郡", "国", "國", "尹", "州")


def t2s(s: str) -> str:
    return _cc.convert(str(s or "").strip())


def bare_from_chgis(name: str) -> str:
    s = t2s(name)
    for suf in _CHGIS_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def norm_jun(s: str) -> str:
    """Normalize 郡/国/尹/属国 label for comparison."""
    s = t2s(s)
    for suf in _JUN_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def roster_bare(name: str) -> str:
    """Roster names are already bare historical names — t2s only."""
    return t2s(name)


def lonlat_to_offset_raw(lon: float, lat: float) -> tuple[float, float]:
    col_f = (lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W - 0.5
    row_f = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H - 0.5
    return col_f, row_f


def offset_to_axial(col: int, row: int) -> tuple[int, int]:
    q = col - (row + (row & 1)) // 2
    return q, row


def axial_to_offset(q: int, r: int) -> tuple[int, int]:
    col = q + (r + (r & 1)) // 2
    return col, r


def axial_neighbors(q: int, r: int) -> list[tuple[int, int]]:
    return [
        (q + 1, r),
        (q + 1, r - 1),
        (q, r - 1),
        (q - 1, r),
        (q - 1, r + 1),
        (q, r + 1),
    ]


def load_coast(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) != MAP_W * MAP_H:
        print(f"warn: coast size {len(raw)} != {MAP_W * MAP_H}")
        return None
    return raw


def is_land(coast: bytes | None, col: int, row: int) -> bool:
    if coast is None:
        return True
    if not (0 <= col < MAP_W and 0 <= row < MAP_H):
        return False
    return coast[row * MAP_W + col] != 0


def snap_to_land(
    col: int, row: int, coast: bytes | None, max_dist: int = SNAP_MAX
) -> tuple[int, int, int, str]:
    """Return (col, row, snap_dist, status). status: land|snapped|water_far|oob."""
    if not (0 <= col < MAP_W and 0 <= row < MAP_H):
        return col, row, -1, "oob"
    if is_land(coast, col, row):
        return col, row, 0, "land"
    q0, r0 = offset_to_axial(col, row)
    seen = {(q0, r0)}
    frontier = [(q0, r0, 0)]
    while frontier:
        q, r, d = frontier.pop(0)
        if d >= max_dist:
            continue
        for nq, nr in axial_neighbors(q, r):
            if (nq, nr) in seen:
                continue
            seen.add((nq, nr))
            c, rw = axial_to_offset(nq, nr)
            if not (0 <= c < MAP_W and 0 <= rw < MAP_H):
                continue
            if is_land(coast, c, rw):
                return c, rw, d + 1, "snapped"
            frontier.append((nq, nr, d + 1))
    return col, row, -1, "water_far"


def read_pts(shp: Path, year: int, types: set[str]) -> list[dict]:
    r = shapefile.Reader(str(shp))
    fields = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        d = dict(zip(fields, sr.record))
        if not (d["BEG_YR"] <= year <= d["END_YR"]):
            continue
        if d["TYPE_CH"] not in types:
            continue
        lon, lat = float(d["X_COOR"]), float(d["Y_COOR"])
        out.append(
            {
                "sys_id": int(d["SYS_ID"]),
                "name_ch": str(d["NAME_CH"]),
                "name_ft": str(d["NAME_FT"]),
                "type_ch": str(d["TYPE_CH"]),
                "lon": lon,
                "lat": lat,
                "beg": int(d["BEG_YR"]),
                "end": int(d["END_YR"]),
                "pres_loc": str(d.get("PRES_LOC") or ""),
                "bare": bare_from_chgis(d["NAME_CH"]),
                "bare_ft": bare_from_chgis(d["NAME_FT"]),
            }
        )
    return out


def read_pts_range(shp: Path, y0: int, y1: int, types: set[str]) -> list[dict]:
    """Points whose validity overlaps [y0, y1]. Prefer row covering YEAR_PRIMARY."""
    r = shapefile.Reader(str(shp))
    fields = [f[0] for f in r.fields[1:]]
    best: dict[int, dict] = {}
    for sr in r.iterShapeRecords():
        d = dict(zip(fields, sr.record))
        if d["END_YR"] < y0 or d["BEG_YR"] > y1:
            continue
        if d["TYPE_CH"] not in types:
            continue
        sid = int(d["SYS_ID"])
        lon, lat = float(d["X_COOR"]), float(d["Y_COOR"])
        rec = {
            "sys_id": sid,
            "name_ch": str(d["NAME_CH"]),
            "name_ft": str(d["NAME_FT"]),
            "type_ch": str(d["TYPE_CH"]),
            "lon": lon,
            "lat": lat,
            "beg": int(d["BEG_YR"]),
            "end": int(d["END_YR"]),
            "pres_loc": str(d.get("PRES_LOC") or ""),
            "bare": bare_from_chgis(d["NAME_CH"]),
            "bare_ft": bare_from_chgis(d["NAME_FT"]),
        }
        prev = best.get(sid)
        if prev is None:
            best[sid] = rec
            continue
        prev_cov = prev["beg"] <= YEAR_PRIMARY <= prev["end"]
        new_cov = rec["beg"] <= YEAR_PRIMARY <= rec["end"]
        if new_cov and not prev_cov:
            best[sid] = rec
    return list(best.values())


def index_by_bare(pts: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for p in pts:
        keys = {p["bare"], p["bare_ft"]}
        for k in keys:
            if not k:
                continue
            # dedupe same sys_id under same key
            if any(x["sys_id"] == p["sys_id"] for x in by[k]):
                continue
            by[k].append(p)
    return by


def index_jun_seats(prefs: list[dict]) -> dict[str, dict]:
    """bare jun name → nearest pref seat dict."""
    by: dict[str, list[dict]] = defaultdict(list)
    for p in prefs:
        by[p["bare"]].append(p)
        by[p["bare_ft"]].append(p)
    out = {}
    for k, lst in by.items():
        # unique by sys_id
        uniq = {}
        for p in lst:
            uniq[p["sys_id"]] = p
        vals = list(uniq.values())
        if len(vals) == 1:
            out[k] = vals[0]
        elif vals:
            # pick one (first); rare
            out[k] = vals[0]
    return out


def dist2(a: dict, b: dict) -> float:
    dx = a["lon"] - b["lon"]
    dy = a["lat"] - b["lat"]
    return dx * dx + dy * dy


def candidate_names(roster_name: str, zhou: str, jun: str) -> list[str]:
    bare = roster_bare(roster_name)
    names = [bare]
    if bare in EXTRA_ALIASES:
        names.extend(EXTRA_ALIASES[bare])
    if bare in NAME_ALIASES:
        names.append(NAME_ALIASES[bare])
    key = f"{zhou}/{jun}/{roster_name}"
    if key in FULL_NAME_ALIASES:
        names.append(bare_from_chgis(FULL_NAME_ALIASES[key]))
    # de-dupe preserve order
    seen = set()
    out = []
    for n in names:
        n = roster_bare(n)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def dedupe_cands(cands: list[dict]) -> list[dict]:
    """Collapse duplicate CHGIS rows that share the same seat coordinates."""
    by_xy: dict[tuple[float, float], dict] = {}
    for p in cands:
        key = (round(p["lon"], 4), round(p["lat"], 4))
        prev = by_xy.get(key)
        if prev is None:
            by_xy[key] = p
            continue
        # Prefer row covering year 140.
        prev_cov = prev["beg"] <= YEAR_PRIMARY <= prev["end"]
        new_cov = p["beg"] <= YEAR_PRIMARY <= p["end"]
        if new_cov and not prev_cov:
            by_xy[key] = p
    return list(by_xy.values())


def pick_candidate(
    cands: list[dict], jun_bare: str, jun_seat: dict | None
) -> tuple[dict | None, str]:
    cands = dedupe_cands(cands)
    if not cands:
        return None, ""
    if len(cands) == 1:
        return cands[0], "unique_name"
    if jun_seat is not None:
        best = min(cands, key=lambda p: dist2(p, jun_seat))
        # Require clearly nearer than alternatives (or sole nearest).
        dists = sorted(dist2(p, jun_seat) for p in cands)
        if len(dists) == 1 or dists[0] < dists[1] * 0.85 or dists[0] < 2.0:
            return best, "name_jun_nearest"
        return best, "name_jun_nearest_weak"
    # No jun seat: refuse ambiguous
    return None, "ambiguous_no_jun"


def project_seat(
    lon: float, lat: float, coast: bytes | None
) -> dict:
    col_f, row_f = lonlat_to_offset_raw(lon, lat)
    # outside bbox?
    if (
        col_f < -OOB_MARGIN
        or col_f > MAP_W - 1 + OOB_MARGIN
        or row_f < -OOB_MARGIN
        or row_f > MAP_H - 1 + OOB_MARGIN
    ):
        return {"status": "out_of_bounds", "lon": lon, "lat": lat}
    col = int(round(col_f))
    row = int(round(row_f))
    col = max(0, min(MAP_W - 1, col))
    row = max(0, min(MAP_H - 1, row))
    scol, srow, snap_d, st = snap_to_land(col, row, coast, SNAP_MAX)
    if st == "water_far":
        return {
            "status": "water_far",
            "lon": lon,
            "lat": lat,
            "col_raw": col,
            "row_raw": row,
        }
    if st == "oob":
        return {"status": "out_of_bounds", "lon": lon, "lat": lat}
    q, r = offset_to_axial(scol, srow)
    return {
        "status": "ok",
        "lon": lon,
        "lat": lat,
        "col": scol,
        "row": srow,
        "q": q,
        "r": r,
        "snap_dist": snap_d,
        "snap": st,
    }


def strip_paren(s: str) -> str:
    s = t2s(s)
    for mark in ("（", "("):
        if mark in s:
            s = s[: s.index(mark)]
    return s.strip()


def load_kongming_towns(path: Path) -> dict[str, list[dict]]:
    """Index Kongming/Zhou Dadudu All_Towns.json by bare Town_CHS name.

    Source: https://github.com/Heliog3nesis/Threekingdoms_Archive (maps on kongming.net).
    Skips 西域 and non-seat types. Does not invent coords.
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("All_Towns_Details", data if isinstance(data, list) else [])
    by: dict[str, list[dict]] = defaultdict(list)
    for t in rows:
        if str(t.get("Type", "")) not in KONGMING_SEAT_TYPES:
            continue
        prov = strip_paren(str(t.get("Prov_CHS") or t.get("Prov_CH") or ""))
        if prov == "西域":
            continue  # outside Sinica 十三州 crop / never force into roster 州
        lon = float(t.get("Longitude", 0) or 0)
        lat = float(t.get("Latitude", 0) or 0)
        if lon == 0 and lat == 0:
            continue
        bare_names = {
            roster_bare(str(t.get("Town_CHS") or "")),
            bare_from_chgis(str(t.get("Town_CHS") or "")),
            roster_bare(str(t.get("Town_CH") or "")),
            bare_from_chgis(str(t.get("Town_CH") or "")),
        }
        rec = {
            "name": t2s(str(t.get("Town_CHS") or t.get("Town_CH") or "")),
            "lon": lon,
            "lat": lat,
            "type": str(t.get("Type", "")),
            "prov": prov,
            "comm": roster_bare(str(t.get("Comm_CHS") or t.get("Comm_CH") or "")),
            "comm_raw": t2s(str(t.get("Comm_CHS") or t.get("Comm_CH") or "")),
            "modern": t2s(str(t.get("Modern_CHS") or "")),
        }
        for b in bare_names:
            if not b:
                continue
            # dedupe same lon/lat under key
            if any(
                abs(x["lon"] - lon) < 1e-5 and abs(x["lat"] - lat) < 1e-5 for x in by[b]
            ):
                continue
            by[b].append(rec)
    return by


def jun_aliases(jun_bare: str) -> list[str]:
    b = roster_bare(jun_bare)
    out = [b]
    for k, alts in JUN_NAME_ALIASES.items():
        if b == k or b in alts:
            out.extend(alts)
            out.append(k)
    # de-dupe
    seen = set()
    res = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res


def zhou_compatible(roster_zhou: str, km_prov: str, roster_jun: str) -> bool:
    """Strict-ish 州 gate: never jam into a wrong 州; allow TK renames 司隶↔雍州 for 三辅."""
    rz = t2s(roster_zhou)
    kp = strip_paren(km_prov)
    if not kp or kp == "西域":
        return False
    if kp == rz:
        return True
    if kp.startswith(rz):  # 扬州（吴） already stripped, but belt-and-suspenders
        return True
    jb = norm_jun(roster_jun)
    # 东汉 司隶 三辅 ↔ 三国图 雍州
    if rz == "司隶" and kp == "雍州" and jb in ("河南", "河内", "河东", "弘农", "京兆", "左冯翊", "右扶风"):
        return True
    # 凉州郡 sometimes drawn under 雍州 on AD262 maps
    if rz == "凉州" and kp == "雍州":
        return True
    return False


def pick_kongming(
    cands: list[dict], roster_zhou: str, roster_jun: str
) -> tuple[dict | None, str]:
    """Require 州 compatibility; prefer 郡 name match. Refuse ambiguous cross-郡."""
    if not cands:
        return None, ""
    zhou_ok = [c for c in cands if zhou_compatible(roster_zhou, c["prov"], roster_jun)]
    if not zhou_ok:
        return None, "kongming_zhou_mismatch"
    jun_alts = set(jun_aliases(norm_jun(roster_jun)))
    jun_ok = [
        c
        for c in zhou_ok
        if c["comm"] in jun_alts or any(a and a in c["comm_raw"] for a in jun_alts)
    ]
    pool = jun_ok if jun_ok else zhou_ok
    # dedupe by coords
    uniq: dict[tuple[float, float], dict] = {}
    for c in pool:
        uniq[(round(c["lon"], 4), round(c["lat"], 4))] = c
    pool = list(uniq.values())
    if len(pool) == 1:
        how = "kongming_name_jun" if jun_ok else "kongming_name_zhou"
        return pool[0], how
    if jun_ok and len(uniq) == 1:
        return pool[0], "kongming_name_jun"
    # Multiple distinct seats — refuse (do not invent / jam)
    return None, "kongming_ambiguous"


def try_kongming(
    row: dict, by_km: dict[str, list[dict]], coast: bytes | None, base: dict, names: list[str]
) -> dict | None:
    if not by_km:
        return None
    cands: list[dict] = []
    for n in names:
        for t in by_km.get(n, []):
            if not any(
                abs(t["lon"] - c["lon"]) < 1e-5 and abs(t["lat"] - c["lat"]) < 1e-5
                for c in cands
            ):
                cands.append(t)
    pick, how = pick_kongming(cands, row["zhou"], row["jun"])
    if pick is None:
        return None
    proj = project_seat(pick["lon"], pick["lat"], coast)
    if proj["status"] != "ok":
        return {
            **base,
            "matched": False,
            "reason": proj["status"],
            "confidence": "unmatched",
            "lon": pick["lon"],
            "lat": pick["lat"],
            "source": "kongming_All_Towns",
            "match_how": how,
            "n_candidates": len(cands),
        }
    conf = "high" if how == "kongming_name_jun" else "medium"
    return {
        **base,
        "matched": True,
        "lon": round(pick["lon"], 6),
        "lat": round(pick["lat"], 6),
        "col": proj["col"],
        "row": proj["row"],
        "q": proj["q"],
        "r": proj["r"],
        "snap_dist": proj["snap_dist"],
        "snap": proj["snap"],
        "confidence": conf,
        "source": "kongming_All_Towns",
        "citation": "Zhou Dadudu All_Towns.json via Kongming’s Archives / Threekingdoms_Archive (AD262 map)",
        "chgis_sys_id": None,
        "chgis_name": pick["name"],
        "chgis_type": pick["type"],
        "chgis_beg": None,
        "chgis_end": None,
        "chgis_pres_loc": pick.get("modern", ""),
        "year_filter": "ad262_map",
        "match_how": how,
        "match_name_key": roster_bare(row["name"]),
    }


# Project-approved lon/lat already used as map anchors (scripts/map_factory.gd).
# Only applied when CHGIS has no name match for the same roster bare name.
ANCHOR_SEATS: dict[str, dict] = {
    "敦煌": {"lon": 94.662, "lat": 40.142, "note": "map_factory anchor 敦煌"},
    "永昌": {"lon": 99.258, "lat": 25.155, "note": "map_factory anchor 永昌"},
    "交趾": {"lon": 106.05, "lat": 21.05, "note": "map_factory anchor 交趾"},
    "日南": {"lon": 106.68, "lat": 16.60, "note": "map_factory anchor 日南"},
    "象林": {"lon": 108.05, "lat": 15.55, "note": "map_factory anchor 象林"},
    "东冶": {"lon": 119.299, "lat": 26.096, "note": "map_factory anchor 东冶"},
    "襄平": {"lon": 123.173, "lat": 41.272, "note": "map_factory anchor 襄平"},
    "乐浪": {"lon": 125.69, "lat": 38.96, "note": "map_factory anchor 乐浪"},
    "金城": {"lon": 103.295, "lat": 36.090, "note": "map_factory anchor 金城"},
    "蓟": {"lon": 116.407, "lat": 39.904, "note": "map_factory anchor 蓟"},
    "番禺": {"lon": 113.264, "lat": 23.129, "note": "map_factory anchor 番禺"},
    "会稽": {"lon": 120.582, "lat": 30.002, "note": "map_factory anchor 会稽"},
}


def match_one(
    row: dict,
    by_primary: dict[str, list[dict]],
    by_range: dict[str, list[dict]],
    jun_seats: dict[str, dict],
    coast: bytes | None,
    by_km: dict[str, list[dict]] | None = None,
) -> dict:
    xid = row["id"]
    name = row["name"]
    zhou = row["zhou"]
    jun = row["jun"]
    jun_bare = norm_jun(jun)
    jun_seat = jun_seats.get(jun_bare)
    base = {
        "xian_id": xid,
        "name": name,
        "zhou": zhou,
        "jun": jun,
    }
    names = candidate_names(name, zhou, jun)
    # Primary year first
    cands: list[dict] = []
    used_year = YEAR_PRIMARY
    for n in names:
        for p in by_primary.get(n, []):
            if not any(c["sys_id"] == p["sys_id"] for c in cands):
                cands.append(p)
    conf_prefix = "y140"
    if not cands:
        used_year = -1
        for n in names:
            for p in by_range.get(n, []):
                if not any(c["sys_id"] == p["sys_id"] for c in cands):
                    cands.append(p)
        conf_prefix = f"y{YEAR_LO}-{YEAR_HI}"

    pick, how = pick_candidate(cands, jun_bare, jun_seat)
    if pick is None:
        # Kongming / Zhou Dadudu All_Towns (strict 州/郡 gate — no peripheral jam).
        km = try_kongming(row, by_km or {}, coast, base, names)
        if km is not None and km.get("matched"):
            return km
        # Fallback: reuse project map-anchor lon/lat when the roster name matches.
        bare_r = roster_bare(name)
        if bare_r in ANCHOR_SEATS and not cands:
            a = ANCHOR_SEATS[bare_r]
            proj = project_seat(a["lon"], a["lat"], coast)
            if proj["status"] == "ok":
                return {
                    **base,
                    "matched": True,
                    "lon": round(a["lon"], 6),
                    "lat": round(a["lat"], 6),
                    "col": proj["col"],
                    "row": proj["row"],
                    "q": proj["q"],
                    "r": proj["r"],
                    "snap_dist": proj["snap_dist"],
                    "snap": proj["snap"],
                    "confidence": "medium",
                    "source": "map_factory_anchor",
                    "citation": "keding-zhongyuan MapFactory.anchor_specs (edge/helper anchors)",
                    "chgis_sys_id": None,
                    "chgis_name": bare_r,
                    "chgis_type": "anchor",
                    "chgis_beg": None,
                    "chgis_end": None,
                    "chgis_pres_loc": a["note"],
                    "year_filter": "anchor",
                    "match_how": "anchor_fallback",
                    "match_name_key": bare_r,
                }
        reason = how or "no_chgis_name"
        if not cands:
            reason = "no_chgis_name"
        if km is not None and not km.get("matched"):
            reason = str(km.get("reason") or reason)
        return {
            **base,
            "matched": False,
            "reason": reason,
            "confidence": "unmatched",
            "candidate_names": names,
            "n_candidates": len(cands),
        }

    # Weak alias gates: only accept 襄→襄城 etc. if jun nearest is strong
    bare = roster_bare(name)
    if bare != pick["bare"] and bare != pick["bare_ft"]:
        if how == "name_jun_nearest_weak":
            return {
                **base,
                "matched": False,
                "reason": "alias_ambiguous",
                "confidence": "unmatched",
                "candidate_names": names,
                "n_candidates": len(cands),
                "chgis_name": pick["name_ch"],
            }

    proj = project_seat(pick["lon"], pick["lat"], coast)
    if proj["status"] != "ok":
        return {
            **base,
            "matched": False,
            "reason": proj["status"],
            "confidence": "unmatched",
            "lon": pick["lon"],
            "lat": pick["lat"],
            "chgis_sys_id": pick["sys_id"],
            "chgis_name": pick["name_ch"],
            "source": "CHGIS_V6",
            "year_filter": used_year if used_year > 0 else f"{YEAR_LO}-{YEAR_HI}",
            "match_how": how,
        }

    # Confidence
    if conf_prefix == "y140" and how == "unique_name" and bare in (pick["bare"], pick["bare_ft"]):
        confidence = "high"
    elif conf_prefix == "y140" and how.startswith("name_jun"):
        confidence = "high" if how == "name_jun_nearest" else "medium"
    elif conf_prefix == "y140":
        confidence = "medium"
    else:
        confidence = "low"

    covers_primary = pick["beg"] <= YEAR_PRIMARY <= pick["end"]
    if not covers_primary and confidence == "high":
        confidence = "medium"

    return {
        **base,
        "matched": True,
        "lon": round(pick["lon"], 6),
        "lat": round(pick["lat"], 6),
        "col": proj["col"],
        "row": proj["row"],
        "q": proj["q"],
        "r": proj["r"],
        "snap_dist": proj["snap_dist"],
        "snap": proj["snap"],
        "confidence": confidence,
        "source": "CHGIS_V6",
        "citation": "CHGIS, Version 6 (c) Fairbank Center / Fudan CHGS, 2016",
        "chgis_sys_id": pick["sys_id"],
        "chgis_name": pick["name_ch"],
        "chgis_type": pick["type_ch"],
        "chgis_beg": pick["beg"],
        "chgis_end": pick["end"],
        "chgis_pres_loc": pick["pres_loc"],
        "year_filter": YEAR_PRIMARY if covers_primary else f"{pick['beg']}-{pick['end']}",
        "match_how": how,
        "match_name_key": bare,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cnty", type=Path, required=True)
    ap.add_argument("--pref", type=Path, required=True)
    ap.add_argument(
        "--kongming",
        type=Path,
        default=Path("/tmp/kongming/All_Towns.json"),
        help="Zhou Dadudu All_Towns.json (Kongming map seats)",
    )
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--roster", type=Path, default=Path("data/counties_roster.json"))
    ap.add_argument("--out", type=Path, default=Path("data/county_seats.json"))
    ap.add_argument("--report", type=Path, default=Path("data/county_seats_report.json"))
    args = ap.parse_args()

    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    if not isinstance(roster, list):
        raise SystemExit("roster must be array")

    coast = load_coast(args.coast)
    primary = read_pts(args.cnty, YEAR_PRIMARY, COUNTY_TYPES | {"国", "國"})
    # Prefer county-like; 国 included for cases like 甘陵
    ranged = read_pts_range(args.cnty, YEAR_LO, YEAR_HI, COUNTY_TYPES | {"国", "國"})
    # Prefs: year-140 first, then fill gaps from TK-era renames (冯翊/扶风 etc.).
    prefs = read_pts(args.pref, YEAR_PRIMARY, JUN_TYPES)
    prefs_range = read_pts_range(args.pref, YEAR_LO, YEAR_HI, JUN_TYPES)
    by_primary = index_by_bare(primary)
    by_range = index_by_bare(ranged)
    jun_seats = index_jun_seats(prefs)
    for k, v in index_jun_seats(prefs_range).items():
        jun_seats.setdefault(k, v)
    # Eastern Han 三辅 names → CHGIS TK-era 冯翊/扶风 seats when 140 slice lacks them.
    jun_alias_map = {
        "左冯翊": ["冯翊", "左冯翊"],
        "右扶风": ["扶风", "右扶风"],
        "辽东属国": ["辽东属国", "辽东"],
        "张掖属国": ["张掖属国", "张掖"],
        "居延属国": ["居延属国", "居延"],
        "犍为属国": ["犍为属国", "犍为"],
    }
    for want, alts in jun_alias_map.items():
        if want in jun_seats:
            continue
        for a in alts:
            if a in jun_seats:
                jun_seats[want] = jun_seats[a]
                break

    by_km = load_kongming_towns(args.kongming)
    print(
        f"roster={len(roster)} chgis@140={len(primary)} chgis@{YEAR_LO}-{YEAR_HI}={len(ranged)} "
        f"pref@140={len(prefs)} kongming_keys={len(by_km)} bbox=[{LON_MIN},{LON_MAX}]x[{LAT_MIN},{LAT_MAX}]"
    )

    seats: list[dict] = []
    unmatched: list[dict] = []
    conf_counts: dict[str, int] = defaultdict(int)
    src_counts: dict[str, int] = defaultdict(int)

    for row in roster:
        m = match_one(row, by_primary, by_range, jun_seats, coast, by_km)
        conf_counts[m["confidence"]] += 1
        if m.get("matched"):
            seats.append(m)
            src_counts[str(m.get("source", "?"))] += 1
        else:
            unmatched.append(m)

    # Runtime file: lean records for Godot (+ provenance kept for audit).
    runtime = []
    for s in seats:
        runtime.append(
            {
                "xian_id": s["xian_id"],
                "name": s["name"],
                "zhou": s["zhou"],
                "jun": s["jun"],
                "lon": s["lon"],
                "lat": s["lat"],
                "col": s["col"],
                "row": s["row"],
                "q": s["q"],
                "r": s["r"],
                "snap_dist": s.get("snap_dist", 0),
                "confidence": s["confidence"],
                "source": s["source"],
                "chgis_sys_id": s["chgis_sys_id"],
                "chgis_name": s["chgis_name"],
                "match_how": s["match_how"],
                "year_filter": s["year_filter"],
            }
        )

    meta = {
        "tier": "xian_seats",
        "era": "184-280",
        "baseline_year": YEAR_PRIMARY,
        "system": "东汉十三州",
        "roster_count": len(roster),
        "matched_count": len(seats),
        "unmatched_count": len(unmatched),
        "match_rate": round(len(seats) / max(len(roster), 1), 4),
        "confidence_counts": dict(conf_counts),
        "source_counts": dict(src_counts),
        "bbox": {
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "note": "Sinica CCTS ad0262 screenshot extent; 羌胡/发羌/高句丽/夷洲 land stays empty of admin",
        },
        "source": "CHGIS V6 + Kongming All_Towns (Zhou Dadudu) matched by name+州/郡; anchors for gaps",
        "citation": "CHGIS V6 (Fairbank/Fudan 2016); Kongming/Threekingdoms_Archive All_Towns.json; Sinica CCTS WMTS ad0262 for extent only (raster)",
        "method": "name_match_project_snap; no Voronoi; no hex xian_id fills; no peripheral 州 jam",
        "projection": f"Hex.lonlat_to_offset even-r {MAP_W}x{MAP_H}; sea snap≤{SNAP_MAX}",
        "note": "Seats alone do not paint 县/郡/州 territory. Hand-fill hex ranges later.",
    }

    out_doc = {"meta": meta, "seats": runtime}
    args.out.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Unmatched summary by 州
    by_zhou: dict[str, list[str]] = defaultdict(list)
    for u in unmatched:
        by_zhou[u["zhou"]].append(f"{u['jun']}/{u['name']} ({u.get('reason','')})")

    report = {
        "meta": meta,
        "unmatched_by_zhou": {k: v for k, v in sorted(by_zhou.items())},
        "unmatched": [
            {
                "xian_id": u["xian_id"],
                "zhou": u["zhou"],
                "jun": u["jun"],
                "name": u["name"],
                "reason": u.get("reason"),
                "n_candidates": u.get("n_candidates", 0),
            }
            for u in unmatched
        ],
        "matched_sample": runtime[:5],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"matched={len(seats)}/{len(roster)} ({meta['match_rate']:.1%}) "
        f"conf={dict(conf_counts)} src={dict(src_counts)} → {args.out}"
    )
    print(f"unmatched={len(unmatched)} report → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
