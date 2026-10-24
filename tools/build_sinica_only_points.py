#!/usr/bin/env python3
"""Build admin_points.json from Sinica ad0262 OCR labels only.

Authority: map labels — not 东汉十三州, not CHGIS 郡/县 lists.
- 州 / 郡 / 县 sets = names labeled on ad0262
- Positions = label centroids
- 县 may be preserved from a prior Sinica-only pass (--keep-xian)

Usage:
  python3 tools/build_sinica_only_points.py \\
    --labels data/sinica_ad0262_ocr_labels_z10.json \\
    --keep-xian data/admin_points.json \\
    --points-out data/admin_points.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from opencc import OpenCC

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500

_t2s = OpenCC("t2s")
_s2t = OpenCC("s2t")

_CJK_RE = re.compile(
    r"[^\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002b73f\U0002b740-\U0002b81f]"
)

# Ends with 州 but are 县/城 on this map, not 州-level units
ZHOU_REJECT = {
    "州",  # bare
    "泉州",  # 渔阳属县
    "江州",  # 巴郡治所城名
    "广州",
    "杭州",
    "婺州",
    "越州",
}

# OCR junk / non-admin fragments that look like 郡/国
JUN_DENY = {
    "郡",
    "国",
    "國",
    "尹",
    "古郡",
    "昌郡",
    "海郡",
    "木郡",
    "湘秉郡",
    "黄平郡",
    "未提郡",
    "柯郡",  # OCR fragment of 牂柯郡
    "南国",
    "每国",
    "兴国",
    "郧国",
    "潮平郡",
}

# Non-county ink / ethnolabels / hydro fragments
XIAN_DENY = {
    "鲜卑",
    "羌胡",
    "发羌",
    "葱茈羌",
    "河西鲜卑",
    "沃沮",
    "县",
    "郡",
    "州",
    "邑",
    "国",
    "城",
    "胡",
    "羌",
    "交州",
    "益州",
    "凉州",
    "雍州",
    "冀州",
    "兖州",
    "青州",
    "徐州",
    "扬州",
    "荆州",
    "豫州",
    "并州",
    "幽州",
    "司州",
    "司隶",
}


def lonlat_to_col_row(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return max(0, min(MAP_W - 1, col)), max(0, min(MAP_H - 1, row))


def clean_text(s: str) -> str:
    s = _t2s.convert(str(s or "").strip())
    s = s.replace(" ", "").replace(".", "").replace("·", "")
    s = _CJK_RE.sub("", s)
    return s


def strip_xian_suffix(s: str) -> str:
    for suf in ("县", "縣"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def split_compound(text: str) -> list[str]:
    t = str(text or "")
    parts = re.split(r"[，,、;；\s]+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 郁林郡交州 / 济南国青州
        m = re.match(r"^(.+?[郡尹國国])([\u3400-\u9fff]{2,3}州)$", p)
        if m:
            out.extend([m.group(1), m.group(2)])
            continue
        m = re.match(r"^(.+?[郡尹國国])([\u3400-\u9fff]{2,4})$", p)
        if m and clean_text(m.group(1)).endswith(("郡", "尹", "国", "國")):
            out.extend([m.group(1), m.group(2)])
        else:
            out.append(p)
    return out or [text]


# 县 names that end with hydro glyphs on this plate
XIAN_HYDRO_OK = {
    "会水",
    "白水",  # 阴平/武都 area 县
}


def classify(text: str) -> str:
    t = clean_text(text)
    if not t:
        return "junk"
    if t in XIAN_HYDRO_OK:
        return "xian"
    if t.endswith(("水", "河", "江", "海", "湖", "泽", "澤", "渠", "溪")):
        return "hydro"
    if t.endswith(("关", "關", "塞", "戍")):
        return "pass"
    if t.endswith("州"):
        if t in ZHOU_REJECT:
            # 泉州/江州 → treat as 县-level place name on map
            return "xian" if t != "州" else "junk"
        # admin 州: 2–3 chars (司州/雍州/荆州…)
        if 2 <= len(t) <= 3:
            return "zhou"
        return "junk"
    if t in ("司隶", "司隸"):
        # map uses 司州; keep if somehow present
        return "zhou"
    if t.endswith(("属国", "屬國")):
        return "jun"
    if t.endswith(("郡", "尹")):
        return "jun"
    if t.endswith(("国", "國")) and 2 <= len(t) <= 4:
        return "jun"
    if t.endswith(("县", "縣", "邑", "道")):
        return "xian"
    if t in XIAN_DENY:
        return "junk"
    if 2 <= len(t) <= 3:
        return "xian"
    if len(t) == 1:
        return "short"
    return "other"


def normalize_zhou(name: str) -> str:
    n = clean_text(name)
    if n in ("司隶", "司隸"):
        return "司州"  # Sinica map authority
    return n


# Nearest-州 OCR fails for some 郡 whose labels sit near another 州's toponyms.
# These follow the Sinica plate's 州 assignment (ink / legend), not CHGIS/TK262.
SINICA_JUN_PARENT = {
    "江夏郡": "荆州",
    "颍川郡": "豫州",
    "金城郡": "凉州",
    "魏郡": "冀州",
    "南安郡": "雍州",
    "阴平郡": "益州",
}


def dedupe(items: list[dict], radius_deg: float) -> list[dict]:
    items = sorted(items, key=lambda x: -x["conf"])
    kept: list[dict] = []
    for it in items:
        nrm = it["norm"]
        lon, lat = it["lon"], it["lat"]
        clash = False
        for k in kept:
            if k["norm"] != nrm:
                continue
            if math.hypot(k["lon"] - lon, k["lat"] - lat) <= radius_deg:
                clash = True
                break
        if not clash:
            kept.append(it)
    return kept


def geometric_median(points: list[tuple[float, float]], eps: float = 1e-3) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    if len(points) == 1:
        return points[0]
    x = sum(p[0] for p in points) / len(points)
    y = sum(p[1] for p in points) / len(points)
    for _ in range(80):
        num_x = num_y = 0.0
        den = 0.0
        for px, py in points:
            d = math.hypot(x - px, y - py)
            if d < eps:
                return px, py
            w = 1.0 / d
            num_x += px * w
            num_y += py * w
            den += w
        nx, ny = num_x / den, num_y / den
        if math.hypot(nx - x, ny - y) < eps:
            return nx, ny
        x, y = nx, ny
    return x, y


def load_jun_faces(meta_path: Path, bin_path: Path):
    if not meta_path.is_file() or not bin_path.is_file():
        return [""], [""], None, {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    zhou_table = meta["zhou_table"]
    jun_table = meta["jun_table"]
    raw = bin_path.read_bytes()
    n = MAP_W * MAP_H
    cells = struct.unpack(f"<{n * 2}H", raw)
    jun_to_zhou: dict[str, str] = {}
    for i in range(n):
        zid, jid = cells[2 * i], cells[2 * i + 1]
        if jid and jid < len(jun_table) and zid and zid < len(zhou_table):
            jn, zn = jun_table[jid], zhou_table[zid]
            if jn and zn:
                jun_to_zhou.setdefault(jn, zn)
    return zhou_table, jun_table, cells, jun_to_zhou


def face_at(cells, lon: float, lat: float, zhou_table, jun_table):
    if cells is None:
        return "", ""
    col, row = lonlat_to_col_row(lon, lat)
    i = row * MAP_W + col
    zid, jid = cells[2 * i], cells[2 * i + 1]
    zhou = zhou_table[zid] if zid and zid < len(zhou_table) else ""
    jun = jun_table[jid] if jid and jid < len(jun_table) else ""
    return zhou, jun


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", type=Path, nargs="+", required=True)
    ap.add_argument("--keep-xian", type=Path, default=None, help="preserve 县 from prior Sinica-only JSON")
    ap.add_argument("--old-points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--fills-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--fills-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--points-out", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--report", type=Path, default=Path("/tmp/sinica_only_zhou_jun_report.json"))
    ap.add_argument("--min-conf-xian", type=float, default=0.85)
    ap.add_argument("--min-conf-jun", type=float, default=0.80)
    ap.add_argument("--min-conf-zhou", type=float, default=0.88)
    ap.add_argument("--dedupe-deg", type=float, default=0.15)
    ap.add_argument("--max-zhou-deg", type=float, default=12.0)
    ap.add_argument("--max-jun-deg", type=float, default=3.5)
    args = ap.parse_args()

    raw_labels: list[dict] = []
    for lp in args.labels:
        blob = json.loads(lp.read_text(encoding="utf-8"))
        raw_labels.extend(blob.get("labels", []))
    print(f"raw labels {len(raw_labels)} from {len(args.labels)} file(s)")

    expanded: list[dict] = []
    for L in raw_labels:
        parts = split_compound(L["text"])
        if len(parts) == 1:
            expanded.append(L)
        else:
            for p in parts:
                expanded.append({**L, "text": p})

    classified: dict[str, list[dict]] = defaultdict(list)
    for L in expanded:
        kind = classify(L["text"])
        norm = clean_text(L["text"])
        if kind == "zhou":
            norm = normalize_zhou(L["text"])
            if norm in ZHOU_REJECT:
                kind = "junk"
        if kind == "xian":
            # 江州/泉州 classified as xian keep full name stem
            if clean_text(L["text"]) in ("江州", "泉州"):
                norm = clean_text(L["text"])
            else:
                norm = strip_xian_suffix(norm)
            if norm in XIAN_DENY or not norm:
                kind = "junk"
        if kind == "jun":
            norm = clean_text(L["text"])
            if norm in JUN_DENY or "□" in norm or "？" in norm or "?" in norm:
                kind = "junk"
            # drop glued 州 suffix leftovers (郁林郡交州 → handled by split; leftover junk)
            if norm.endswith("州") and ("郡" in norm or "国" in norm):
                kind = "junk"
        classified[kind].append({**L, "kind": kind, "norm": norm, "conf": float(L["conf"])})

    zhou_cands = [
        x
        for x in classified["zhou"]
        if x["conf"] >= args.min_conf_zhou and x["norm"] not in ZHOU_REJECT and len(x["norm"]) >= 2
    ]
    jun_cands = [
        x
        for x in classified["jun"]
        if x["conf"] >= args.min_conf_jun and x["norm"] not in JUN_DENY
    ]
    zhou_seeds = dedupe(zhou_cands, args.dedupe_deg)
    jun_seeds = dedupe(jun_cands, args.dedupe_deg)

    # Group 州 seeds; pick display seat = geometric median (司州: prefer near 河南尹)
    by_zhou: dict[str, list[dict]] = defaultdict(list)
    for z in zhou_seeds:
        by_zhou[z["norm"]].append(z)

    henan = None
    for L in classified["jun"]:
        if clean_text(L["text"]) == "河南尹" and L["conf"] >= 0.9:
            if henan is None or L["conf"] > henan["conf"]:
                henan = L

    zhou_out = []
    for name, seeds in sorted(by_zhou.items()):
        pts_ll = [(s["lon"], s["lat"]) for s in seeds]
        if name == "司州" and henan:
            seat = min(seeds, key=lambda s: math.hypot(s["lon"] - henan["lon"], s["lat"] - henan["lat"]))
            slon, slat = seat["lon"], seat["lat"]
        else:
            slon, slat = geometric_median(pts_ll)
            # snap seat to nearest actual OCR seed (no invented coords)
            seat = min(seeds, key=lambda s: math.hypot(s["lon"] - slon, s["lat"] - slat))
            slon, slat = seat["lon"], seat["lat"]
        col, row = lonlat_to_col_row(slon, slat)
        zhou_out.append(
            {
                "level": "zhou",
                "zhou": name,
                "name": name + "治",
                "seat_name": name,
                "lon": slon,
                "lat": slat,
                "col": col,
                "row": row,
                "source": "sinica_ad0262",
                "sinica_label": seat["text"],
                "sinica_ocr_conf": seat["conf"],
                "sinica_zoom": seat.get("zoom"),
                "seeds": [
                    {
                        "lon": s["lon"],
                        "lat": s["lat"],
                        "label": s["text"],
                        "conf": s["conf"],
                    }
                    for s in seeds
                ],
                "note": "Sinica 州 label(s); seat=OCR point (no invented coords)",
            }
        )

    zhou_ll = {z["zhou"]: (z["lon"], z["lat"]) for z in zhou_out}
    # also all seed positions for nearest-州 assignment
    zhou_seed_ll: list[tuple[str, float, float]] = []
    for z in zhou_out:
        for s in z["seeds"]:
            zhou_seed_ll.append((z["zhou"], s["lon"], s["lat"]))

    def nearest_zhou(lon: float, lat: float) -> tuple[str, float]:
        best, best_d = "", 1e9
        for zn, zlon, zlat in zhou_seed_ll:
            d = math.hypot(zlon - lon, zlat - lat)
            if d < best_d:
                best_d = d
                best = zn
        return best, best_d

    # 郡: one seat per name = OCR label nearest geometric median of all seeds
    # (avoids a single north-margin legend hit stealing 酒泉/郁林)
    jun_groups: dict[str, list[dict]] = defaultdict(list)
    for j in jun_seeds:
        jun_groups[j["norm"]].append(j)

    jun_out = []
    for name, seeds in sorted(jun_groups.items()):
        if name in JUN_DENY:
            continue
        # map OCR fragment □柯郡 already denied; also skip if 牂柯郡 exists and name is 柯郡
        pts_ll = [(s["lon"], s["lat"]) for s in seeds]
        mlon, mlat = geometric_median(pts_ll)
        j = min(seeds, key=lambda s: math.hypot(s["lon"] - mlon, s["lat"] - mlat))
        # Prefer higher-zoom / higher-conf when nearly tied
        near = [
            s
            for s in seeds
            if math.hypot(s["lon"] - j["lon"], s["lat"] - j["lat"]) < 0.35
        ]
        if near:
            j = max(near, key=lambda s: (float(s.get("zoom") or 0), s["conf"]))
        zhou, d = nearest_zhou(j["lon"], j["lat"])
        if not zhou or d > args.max_zhou_deg:
            zhou = ""
        if name in SINICA_JUN_PARENT:
            zhou = SINICA_JUN_PARENT[name]
            d = 0.0
        col, row = lonlat_to_col_row(j["lon"], j["lat"])
        jun_out.append(
            {
                "level": "jun",
                "zhou": zhou,
                "jun": name,
                "name": name,
                "lon": j["lon"],
                "lat": j["lat"],
                "col": col,
                "row": row,
                "source": "sinica_ad0262",
                "sinica_label": j["text"],
                "sinica_ocr_conf": j["conf"],
                "sinica_zoom": j.get("zoom"),
                "deg_to_zhou_label": round(d, 3) if zhou else None,
                "seeds": [
                    {
                        "lon": s["lon"],
                        "lat": s["lat"],
                        "label": s["text"],
                        "conf": s["conf"],
                        "zoom": s.get("zoom"),
                    }
                    for s in seeds
                ],
                "note": "Sinica 郡/国 label (=郡治); parent 州=plate/nearest Sinica 州 label",
            }
        )

    # 县: keep prior Sinica-only set, or rebuild from OCR
    old = json.loads(args.old_points.read_text(encoding="utf-8")) if args.old_points.is_file() else {}
    if args.keep_xian and args.keep_xian.is_file():
        keep = json.loads(args.keep_xian.read_text(encoding="utf-8"))
        xian_out = list(keep.get("xian", []))
        print(f"keep-xian {len(xian_out)} from {args.keep_xian}")
    else:
        xian_cands = []
        for x in classified["xian"]:
            if x["conf"] < args.min_conf_xian:
                continue
            if not x["norm"] or x["norm"] in XIAN_DENY:
                continue
            raw_t = clean_text(x["text"])
            # ridges / mouths / non-seat fragments
            if raw_t.endswith(("岭", "嶺", "口", "戍", "塞", "陂", "渚")):
                continue
            has_suf = raw_t.endswith(("县", "縣", "邑", "道")) or raw_t in ("江州", "泉州")
            if len(x["norm"]) == 1 and not has_suf:
                continue
            if not has_suf and x["conf"] < 0.92:
                continue
            xian_cands.append(x)
        xian_pts = dedupe(xian_cands, args.dedupe_deg)
        xian_out = []
        for x in xian_pts:
            # parent from nearest 郡 label for now (faces rebuilt next)
            best = None
            best_d = args.max_jun_deg
            for j in jun_out:
                d = math.hypot(j["lon"] - x["lon"], j["lat"] - x["lat"])
                if d < best_d:
                    best_d = d
                    best = j
            if not best:
                continue
            col, row = lonlat_to_col_row(x["lon"], x["lat"])
            xian_out.append(
                {
                    "level": "xian",
                    "xian_id": f"xian:{best['zhou']}/{best['jun']}/{x['norm']}",
                    "zhou": best["zhou"],
                    "jun": best["jun"],
                    "name": x["norm"],
                    "lon": x["lon"],
                    "lat": x["lat"],
                    "col": col,
                    "row": row,
                    "source": "sinica_ad0262",
                    "confidence": "high" if x["conf"] >= 0.9 else "medium",
                    "sinica_label": x["text"],
                    "sinica_ocr_conf": x["conf"],
                    "sinica_zoom": x.get("zoom"),
                    "note": "Sinica 县 label (=县治); OCR authority",
                }
            )

    # Re-parent kept 县 to nearest Sinica 郡 label (faces will refine later)
    jun_ll = [(j["jun"], j["zhou"], j["lon"], j["lat"]) for j in jun_out]
    reparented = []
    for s in xian_out:
        best = None
        best_d = args.max_jun_deg
        for jn, zn, jlon, jlat in jun_ll:
            d = math.hypot(jlon - s["lon"], jlat - s["lat"])
            if d < best_d:
                best_d = d
                best = (jn, zn)
        if best:
            jn, zn = best
            s = dict(s)
            s["jun"] = jn
            s["zhou"] = zn
            s["xian_id"] = f"xian:{zn}/{jn}/{s['name']}"
        reparented.append(s)
    # dedupe ids
    by_id: dict[str, dict] = {}
    for s in sorted(reparented, key=lambda x: -float(x.get("sinica_ocr_conf") or 0)):
        by_id.setdefault(s["xian_id"], s)
    xian_out = list(by_id.values())

    old_zhou = {z["zhou"] for z in old.get("zhou", [])}
    old_jun = {j["jun"] for j in old.get("jun", [])}
    new_zhou = {z["zhou"] for z in zhou_out}
    new_jun = {j["jun"] for j in jun_out}

    # 河西 check
    hex_i = [j for j in jun_out if any(k in j["jun"] for k in ("张掖", "酒泉", "敦煌", "武威", "西平"))]

    pts = {
        "meta": {
            "phase": 1,
            "authority": "Sinica CCTS ad0262 OCR labels only",
            "authority_visual": "Sinica CCTS ad0262",
            "coords": "OCR label centroids on WMTS tiles",
            "zhou_policy": "州 set = labels on map (司州/雍州…); not fixed 东汉十三州; not CHGIS",
            "jun_policy": "郡/国 set = labels on map; parent 州 = nearest Sinica 州 label",
            "xian_policy": "Sinica-only 县 points preserved/rebuilt from OCR; faces = nearest inside 郡",
            "jiangxia": "follows whatever 州 the map face assigns (expect 荆州)",
            "not_authority": "CHGIS and fixed 十三州 roster are not masters",
        },
        "stats": {
            "zhou_seats": len(zhou_out),
            "zhou_seed_labels": sum(len(z["seeds"]) for z in zhou_out),
            "jun_seats": len(jun_out),
            "xian_seats_matched": len(xian_out),
            "xian_sinica_only": len(xian_out),
            "zhou_added": sorted(new_zhou - old_zhou),
            "zhou_removed": sorted(old_zhou - new_zhou),
            "jun_added_count": len(new_jun - old_jun),
            "jun_removed_count": len(old_jun - new_jun),
            "hexi_jun_on_map": [j["jun"] for j in hex_i],
            "label_class_counts": {k: len(v) for k, v in classified.items()},
        },
        "zhou": zhou_out,
        "jun": sorted(jun_out, key=lambda x: (x["zhou"], x["jun"])),
        "xian": sorted(xian_out, key=lambda x: (x["zhou"], x["jun"], x["name"])),
        "xian_unmatched": [],
        "jun_gaps": [],
    }
    args.points_out.write_text(json.dumps(pts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "sinica_zhou_count": len(zhou_out),
        "sinica_zhou_names": sorted(new_zhou),
        "zhou_added": sorted(new_zhou - old_zhou),
        "zhou_removed": sorted(old_zhou - new_zhou),
        "sinica_jun_count": len(jun_out),
        "jun_added": sorted(new_jun - old_jun),
        "jun_removed": sorted(old_jun - new_jun)[:80],
        "jun_removed_count": len(old_jun - new_jun),
        "xian_count": len(xian_out),
        "hexi_jun": [j["jun"] for j in hex_i],
        "zhou_seed_counts": {z["zhou"]: len(z["seeds"]) for z in zhou_out},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"DONE zhou={len(zhou_out)} {sorted(new_zhou)} "
        f"jun={len(jun_out)} (+{len(new_jun-old_jun)}/-{len(old_jun-new_jun)}) "
        f"xian={len(xian_out)} hexi={report['hexi_jun']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
