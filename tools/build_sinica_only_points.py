#!/usr/bin/env python3
"""Build admin_points.json from Sinica ad0262 OCR labels only.

- 县 master set = OCR 县 labels (not CHGIS / not 1181 roster)
- 州治 / 郡治 = OCR 州 / 郡 label positions
- Parent 郡 for each 县 = 郡 face under the label (existing Sinica-traced faces),
  else nearest 郡 label within max_deg

Usage:
  python3 tools/build_sinica_only_points.py \\
    --labels /tmp/sinica_labels_z10.json \\
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

ZHOU_13 = {
    "凉州",
    "并州",
    "幽州",
    "冀州",
    "青州",
    "兖州",
    "徐州",
    "豫州",
    "司隶",
    "荆州",
    "扬州",
    "益州",
    "交州",
}

# Sinica ad0262 labels 司州 for what the game/faces call 司隶
ZHOU_ALIASES = {"司州": "司隶", "司隸": "司隶", "司隶校尉": "司隶"}

# Non-county ink on the map (peoples / junk OCR)
XIAN_DENY = {
    "鲜卑",
    "羌胡",
    "发羌",
    "葱茈羌",
    "沃沮",
    "县",
    "郡",
    "州",
    "邑",
    "国",
    "城",
    "胡",
    "羌",
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
    """Split OCR glued labels like '興古郡，宛溫'."""
    t = str(text or "")
    parts = re.split(r"[，,、;；\s]+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # also split ...郡X where X looks like a following 县 (rare)
        m = re.match(r"^(.+?[郡尹國国])([\u3400-\u9fff]{2,4})$", clean_text(p) and p or p)
        if m and (m.group(1).endswith(("郡", "尹", "国", "國"))):
            out.extend([m.group(1), m.group(2)])
        else:
            out.append(p)
    return out or [text]


def classify(text: str) -> str:
    t = clean_text(text)
    if not t:
        return "junk"
    # hydronyms / passes / noise
    if t.endswith(("水", "河", "江", "海", "湖", "泽", "澤", "渠", "溪")):
        return "hydro"
    if t.endswith(("关", "關", "塞", "戍")):
        return "pass"
    if t in ZHOU_ALIASES or t in ZHOU_13 or t in {_s2t.convert(z) for z in ZHOU_13}:
        return "zhou"
    if t.endswith("州") and (
        clean_text(t[:-1]) in {z.replace("州", "") for z in ZHOU_13 if z.endswith("州")}
        or t in ZHOU_ALIASES
    ):
        return "zhou"
    if t in ("司隶", "司隸", "司州"):
        return "zhou"
    if t.endswith(("属国", "屬國")):
        return "jun"
    if t.endswith(("郡", "尹")):
        # reject 1-char stem garbage like bare OCR '古郡' when stem not a real mono-char 郡
        # keep 蜀郡/吴郡 etc.; drop only if conf handled later — mark jun, filter in accept
        return "jun"
    # 王国 / 公国 style 郡-level (2–3 chars + 国)
    if t.endswith(("国", "國")) and 2 <= len(t) <= 4:
        return "jun"
    if t.endswith(("县", "縣", "邑", "道")):
        return "xian"
    if t in XIAN_DENY:
        return "junk"
    # bare 2–3 char place labels are usually 县 on this map
    if 2 <= len(t) <= 3:
        return "xian"
    if len(t) == 1:
        return "short"
    return "other"


def normalize_zhou(name: str) -> str:
    n = clean_text(name)
    n = ZHOU_ALIASES.get(n, n)
    if n in ZHOU_13:
        return n
    for z in ZHOU_13:
        if _s2t.convert(z) == n or _t2s.convert(n) == z:
            return z
    return n


def dedupe(items: list[dict], radius_deg: float) -> list[dict]:
    """Keep highest-conf per (norm_name, spatial cluster)."""
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


def load_jun_faces(meta_path: Path, bin_path: Path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    zhou_table = meta["zhou_table"]
    jun_table = meta["jun_table"]
    raw = bin_path.read_bytes()
    n = MAP_W * MAP_H
    # layout: uint16 zhou, uint16 jun per cell
    cells = struct.unpack(f"<{n * 2}H", raw)
    # build jun -> zhou from cells
    jun_to_zhou: dict[str, str] = {}
    for i in range(n):
        zid, jid = cells[2 * i], cells[2 * i + 1]
        if jid and jid < len(jun_table) and zid and zid < len(zhou_table):
            jn, zn = jun_table[jid], zhou_table[zid]
            if jn and zn:
                jun_to_zhou.setdefault(jn, zn)
    return zhou_table, jun_table, cells, jun_to_zhou


def face_at(cells, lon: float, lat: float, zhou_table, jun_table):
    col, row = lonlat_to_col_row(lon, lat)
    i = row * MAP_W + col
    zid, jid = cells[2 * i], cells[2 * i + 1]
    zhou = zhou_table[zid] if zid and zid < len(zhou_table) else ""
    jun = jun_table[jid] if jid and jid < len(jun_table) else ""
    return zhou, jun


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", type=Path, nargs="+", required=True)
    ap.add_argument("--old-points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--fills-meta", type=Path, default=Path("data/zhou_jun_fills.json"))
    ap.add_argument("--fills-bin", type=Path, default=Path("data/zhou_jun_fills.bin"))
    ap.add_argument("--roster", type=Path, default=Path("data/counties_roster.json"))
    ap.add_argument("--points-out", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--report", type=Path, default=Path("/tmp/sinica_only_points_report.json"))
    ap.add_argument("--min-conf-xian", type=float, default=0.85)
    ap.add_argument("--min-conf-jun", type=float, default=0.80)
    ap.add_argument("--min-conf-zhou", type=float, default=0.80)
    ap.add_argument("--dedupe-deg", type=float, default=0.12)
    ap.add_argument("--max-jun-deg", type=float, default=3.5)
    args = ap.parse_args()

    raw_labels: list[dict] = []
    for lp in args.labels:
        blob = json.loads(lp.read_text(encoding="utf-8"))
        raw_labels.extend(blob.get("labels", []))
    print(f"raw labels {len(raw_labels)} from {len(args.labels)} file(s)")

    # expand compounds into separate pseudo-labels (same lon/lat)
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
        if kind == "xian":
            norm = strip_xian_suffix(norm)
            if norm in XIAN_DENY or not norm:
                kind = "junk"
        if kind == "zhou":
            norm = normalize_zhou(L["text"])
        if kind == "jun":
            norm = clean_text(L["text"])
            # drop obvious fragments
            if norm in ("古郡", "郡", "国", "國", "尹"):
                kind = "junk"
            # glued leftover
            if "，" in L["text"] or "," in L["text"]:
                kind = "junk"
        classified[kind].append(
            {
                **L,
                "kind": kind,
                "norm": norm,
                "conf": float(L["conf"]),
            }
        )

    zhou_cands = [x for x in classified["zhou"] if x["conf"] >= args.min_conf_zhou and x["norm"] in ZHOU_13]
    jun_cands = [x for x in classified["jun"] if x["conf"] >= args.min_conf_jun]
    xian_cands = []
    for x in classified["xian"]:
        if x["conf"] < args.min_conf_xian:
            continue
        if not x["norm"] or x["norm"] in XIAN_DENY:
            continue
        raw_t = clean_text(x["text"])
        has_suf = raw_t.endswith(("县", "縣", "邑", "道"))
        # bare labels: stricter conf; 1-char only if explicit 縣/县 suffix
        if len(x["norm"]) == 1 and not has_suf:
            continue
        if not has_suf and x["conf"] < 0.92:
            continue
        xian_cands.append(x)

    zhou_pts = dedupe(zhou_cands, args.dedupe_deg)
    jun_pts = dedupe(jun_cands, args.dedupe_deg)
    xian_pts = dedupe(xian_cands, args.dedupe_deg)

    zhou_table, jun_table, cells, jun_to_zhou = load_jun_faces(args.fills_meta, args.fills_bin)

    # Prefer one label per 州: highest conf, but 司隶 prefers label near 河南尹 face/OCR
    best_zhou: dict[str, dict] = {}
    for z in sorted(zhou_pts, key=lambda x: -x["conf"]):
        best_zhou.setdefault(z["norm"], z)
    # refine 司隶: closest 司州/司隶 label to 河南尹 label if present
    henan = None
    for L in classified["jun"]:
        if clean_text(L["text"]) == "河南尹" and L["conf"] >= 0.9:
            if henan is None or L["conf"] > henan["conf"]:
                henan = L
    if henan:
        sili_cands = [z for z in zhou_pts if z["norm"] == "司隶"]
        if sili_cands:
            sili_cands.sort(
                key=lambda z: math.hypot(z["lon"] - henan["lon"], z["lat"] - henan["lat"])
            )
            best_zhou["司隶"] = sili_cands[0]
    zhou_pts = list(best_zhou.values())

    # Build 郡 seats from OCR; attach parent 州 from face under label (江夏→荆州 etc.)
    jun_out = []
    for j in jun_pts:
        zhou_face, jun_face = face_at(cells, j["lon"], j["lat"], zhou_table, jun_table)
        # Prefer face 州; fall back to nearest 州 label
        zhou = zhou_face
        if not zhou:
            best = None
            best_d = 1e9
            for z in zhou_pts:
                d = math.hypot(z["lon"] - j["lon"], z["lat"] - j["lat"])
                if d < best_d:
                    best_d = d
                    best = z["norm"]
            zhou = best or ""
        name = j["norm"]
        # unify 尹/郡 display
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
                "note": "Sinica label position (=郡治 on map)",
            }
        )

    # Dedupe jun by name keeping highest conf (one seat per 郡 name)
    jun_by_name: dict[str, dict] = {}
    for j in sorted(jun_out, key=lambda x: -x["sinica_ocr_conf"]):
        jun_by_name.setdefault(j["jun"], j)
    jun_out = list(jun_by_name.values())

    zhou_out = []
    for z in zhou_pts:
        col, row = lonlat_to_col_row(z["lon"], z["lat"])
        zhou_out.append(
            {
                "level": "zhou",
                "zhou": z["norm"],
                "name": z["norm"] + ("治" if not z["norm"].endswith("治") else ""),
                "seat_name": z["norm"],
                "lon": z["lon"],
                "lat": z["lat"],
                "col": col,
                "row": row,
                "source": "sinica_ad0262",
                "sinica_label": z["text"],
                "sinica_ocr_conf": z["conf"],
                "sinica_zoom": z.get("zoom"),
                "note": "Sinica 州 label position",
            }
        )

    # 县: assign parent from face, else nearest OCR 郡
    jun_lonlat = [(j["jun"], j["zhou"], j["lon"], j["lat"]) for j in jun_out]
    xian_out = []
    xian_unassigned = []
    for x in xian_pts:
        zhou_face, jun_face = face_at(cells, x["lon"], x["lat"], zhou_table, jun_table)
        jun = jun_face
        zhou = zhou_face
        if not jun:
            best = None
            best_d = args.max_jun_deg
            for jn, zn, jlon, jlat in jun_lonlat:
                d = math.hypot(jlon - x["lon"], jlat - x["lat"])
                if d < best_d:
                    best_d = d
                    best = (jn, zn)
            if best:
                jun, zhou = best
        if not jun:
            xian_unassigned.append(
                {
                    "name": x["norm"],
                    "label": x["text"],
                    "lon": x["lon"],
                    "lat": x["lat"],
                    "conf": x["conf"],
                    "reason": "no_parent_jun_face_or_nearby_label",
                }
            )
            continue
        if not zhou:
            zhou = jun_to_zhou.get(jun, "")
        col, row = lonlat_to_col_row(x["lon"], x["lat"])
        xian_id = f"xian:{zhou}/{jun}/{x['norm']}"
        xian_out.append(
            {
                "level": "xian",
                "xian_id": xian_id,
                "zhou": zhou,
                "jun": jun,
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

    # Dedupe xian_id collisions: keep higher conf
    by_id: dict[str, dict] = {}
    for s in sorted(xian_out, key=lambda x: -x["sinica_ocr_conf"]):
        by_id.setdefault(s["xian_id"], s)
    xian_out = list(by_id.values())

    # Compare to old CHGIS-heavy points
    old = json.loads(args.old_points.read_text(encoding="utf-8")) if args.old_points.is_file() else {}
    old_xian = old.get("xian", [])
    old_names = {}
    for s in old_xian:
        old_names.setdefault(strip_xian_suffix(clean_text(s["name"])), []).append(s)

    new_norms = {s["name"] for s in xian_out}
    chgis_removed = []
    for s in old_xian:
        n = strip_xian_suffix(clean_text(s["name"]))
        # removed if no Sinica seat with same norm within 0.35°
        hit = False
        for ns in xian_out:
            if ns["name"] != n:
                continue
            if math.hypot(ns["lon"] - s["lon"], ns["lat"] - s["lat"]) <= 0.35:
                hit = True
                break
        if not hit:
            # name-only match elsewhere still counts as "has Sinica label"
            if n not in new_norms:
                chgis_removed.append(
                    {
                        "xian_id": s.get("xian_id"),
                        "name": s["name"],
                        "zhou": s.get("zhou"),
                        "jun": s.get("jun"),
                        "old_source": s.get("source"),
                        "reason": "no_sinica_label",
                    }
                )

    roster_names = set()
    if args.roster.is_file():
        rost = json.loads(args.roster.read_text(encoding="utf-8"))
        # flexible: list of counties or nested
        def walk(o):
            if isinstance(o, dict):
                if "name" in o and ("jun" in o or "郡" in o or o.get("level") == "xian"):
                    roster_names.add(strip_xian_suffix(clean_text(o["name"])))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(rost)
        if not roster_names and isinstance(rost, list):
            for o in rost:
                if isinstance(o, dict) and "name" in o:
                    roster_names.add(strip_xian_suffix(clean_text(o["name"])))

    sinica_not_on_roster = [s for s in xian_out if s["name"] not in roster_names] if roster_names else []

    # Unreadable: land 郡 faces with zero 县 seats
    seats_by_jun = Counter(s["jun"] for s in xian_out)
    jun_with_face = set(jun_to_zhou.keys())
    unreadable_juns = sorted(j for j in jun_with_face if seats_by_jun.get(j, 0) == 0)

    pts = {
        "meta": {
            "phase": 1,
            "authority": "Sinica CCTS ad0262 OCR labels only",
            "authority_visual": "Sinica CCTS ad0262",
            "coords": "OCR label centroids on WMTS tiles (z≥10 for 县)",
            "jiangxia": "江夏郡 parent from Sinica-traced 郡 face (荆州 if map face says so)",
            "faces": "县 rebuilt nearest Sinica 县治 inside parent 郡; 州/郡 faces kept unless noted",
            "not_authority": "CHGIS seats and 1181 roster are not the master 县 set",
        },
        "stats": {
            "zhou_seats": len(zhou_out),
            "jun_seats": len(jun_out),
            "xian_seats_matched": len(xian_out),
            "xian_sinica_only": len(xian_out),
            "xian_unassigned_labels": len(xian_unassigned),
            "chgis_seats_removed": len(chgis_removed),
            "sinica_not_on_roster": len(sinica_not_on_roster),
            "old_xian_count": len(old_xian),
            "unreadable_jun_faces": len(unreadable_juns),
            "label_class_counts": {k: len(v) for k, v in classified.items()},
        },
        "zhou": sorted(zhou_out, key=lambda x: x["zhou"]),
        "jun": sorted(jun_out, key=lambda x: (x["zhou"], x["jun"])),
        "xian": sorted(xian_out, key=lambda x: (x["zhou"], x["jun"], x["name"])),
        "xian_unmatched": xian_unassigned,
        "jun_gaps": [{"jun": j, "zhou": jun_to_zhou.get(j, ""), "reason": "no_sinica_xian_label_in_face"} for j in unreadable_juns],
        "chgis_removed_sample": chgis_removed[:80],
    }
    args.points_out.write_text(json.dumps(pts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "sinica_xian_count": len(xian_out),
        "sinica_jun_count": len(jun_out),
        "sinica_zhou_count": len(zhou_out),
        "old_xian_count": len(old_xian),
        "chgis_seats_removed": len(chgis_removed),
        "sinica_not_on_roster": len(sinica_not_on_roster),
        "xian_unassigned_labels": len(xian_unassigned),
        "unreadable_jun_faces": unreadable_juns,
        "zhou_names": [z["zhou"] for z in zhou_out],
        "class_counts": {k: len(v) for k, v in classified.items()},
        "jiangxia": next((j for j in jun_out if "江夏" in j["jun"]), None),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"DONE sinica_xian={len(xian_out)} jun={len(jun_out)} zhou={len(zhou_out)} "
        f"removed_chgis≈{len(chgis_removed)} unreadable_juns={len(unreadable_juns)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
