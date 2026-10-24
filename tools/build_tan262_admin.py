#!/usr/bin/env python3
"""Build admin_points.json from Tan-262 分幅 OCR labels (authority).

Sinica points are kept only as fallback where Tan has no matching label.
Does not copy JPEG scans into the repo.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

TRAD = str.maketrans(
    {
        "國": "国",
        "縣": "县",
        "臨": "临",
        "廣": "广",
        "雞": "鸡",
        "領": "领",
        "濟": "济",
        "陽": "阳",
        "陰": "阴",
        "漢": "汉",
        "壽": "寿",
        "會": "会",
        "無": "无",
        "吳": "吴",
        "齊": "齐",
        "遼": "辽",
        "寧": "宁",
        "豐": "丰",
        "業": "业",
        "趙": "赵",
        "鬱": "郁",
        "峽": "峡",
        "潁": "颍",
        "廬": "庐",
        "嶽": "岳",
        "恆": "恒",
        "恆": "恒",
    }
)

# Manual Tan-sheet confirmations (vision + OCR), overriding fuzzy OCR
MANUAL_JUN = [
    # 济北国 on 兖/豫/青 overlap sheets — seats from sheet 10 vision + OCR 县
    {
        "jun": "济北国",
        "zhou": "兖州",
        "lon": 116.75,
        "lat": 36.28,
        "source": "tan262:10-yan-yu-yang",
        "note": "purple-bounded 国 on sheet 10; OCR also on 12/16",
    },
]
MANUAL_XIAN = [
    {"name": "卢", "jun": "济北国", "zhou": "兖州", "lon": 116.67, "lat": 36.36, "source": "tan262:10"},
    {"name": "肥成", "jun": "济北国", "zhou": "兖州", "lon": 116.75, "lat": 36.26, "source": "tan262:10"},
    {"name": "蛇丘", "jun": "济北国", "zhou": "兖州", "lon": 116.78, "lat": 35.93, "source": "tan262:10"},
    {"name": "临邑", "jun": "济北国", "zhou": "兖州", "lon": 116.23, "lat": 36.34, "source": "tan262:10"},
    # 西充国 is a 县 under 巴西郡 on sheet 06 (not a separate 郡 face)
    {"name": "西充国", "jun": "巴西郡", "zhou": "益州", "lon": 105.95, "lat": 31.25, "source": "tan262:06"},
    {"name": "南充国", "jun": "巴西郡", "zhou": "益州", "lon": 105.97, "lat": 31.18, "source": "tan262:06"},
]

# Sheets → default 州 when label is ambiguous
SHEET_ZHOU = {
    "03-jiaozhou.jpg": "交州",
    "04-yangzhou-wu.jpg": "扬州",
    "05-jingzhou-wu.jpg": "荆州",
    "06-yizhou-north.jpg": "益州",
    "07-yizhou-south.jpg": "益州",
    "09-sizhou.jpg": "司州",
    "10-yan-yu-yang.jpg": "兖州",
    "11-qing-xu.jpg": "青州",
    "12-youzhou.jpg": "幽州",
    "13-liangzhou.jpg": "凉州",
    "14-jingzhou-wei.jpg": "荆州",
    "15-yongzhou.jpg": "雍州",
    "16-ji-bing.jpg": "冀州",
}

CANON_ZHOU = {
    "交州",
    "扬州",
    "荆州",
    "益州",
    "司州",
    "兖州",
    "豫州",
    "青州",
    "徐州",
    "幽州",
    "凉州",
    "雍州",
    "冀州",
    "并州",
}


def norm(t: str) -> str:
    t = t.translate(TRAD)
    t = re.sub(r"\s+", "", t)
    return t


def clean_jun_name(t: str) -> str | None:
    t = norm(t)
    t = t.replace("西西充国", "西充国")
    # strip glued prefixes/suffixes
    t = re.sub(r"^(曲靖|味县|徐闻|漳邺县|绵虒|达润山)", "", t)
    m = re.search(r"([\u4e00-\u9fff]{1,4}(?:郡|国))$", t)
    if not m:
        return None
    name = m.group(1)
    if name in {"含浦郡"}:
        name = "合浦郡"
    if len(name) < 2:
        return None
    # drop obvious modern / junk
    if any(k in name for k in ("市", "省", "自治")):
        return None
    return name


def clean_zhou_name(t: str) -> str | None:
    t = norm(t)
    if t == "司隶":
        return "司州"
    # glued 郡+州 → take 州
    m = re.search(r"([\u4e00-\u9fff]{1,3}州)$", t)
    if not m:
        return None
    name = m.group(1)
    if name not in CANON_ZHOU:
        return None
    return name


def clean_xian_name(t: str) -> str | None:
    t = norm(t)
    t = t.replace("县", "")
    if not re.fullmatch(r"[\u4e00-\u9fff]{1,3}", t):
        return None
    if t in {"河", "水", "江", "山", "海", "湖", "郡", "州", "国", "北", "南", "东", "西", "中"}:
        return None
    if any(k in t for k in ("市", "省", "族", "壮", "自治")):
        return None
    return t


def centroid(hits: list[dict]) -> tuple[float, float, float]:
    ws = [h["conf"] for h in hits]
    lon = sum(h["lon"] * w for h, w in zip(hits, ws)) / sum(ws)
    lat = sum(h["lat"] * w for h, w in zip(hits, ws)) / sum(ws)
    return lon, lat, max(ws)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", type=Path, default=Path("/tmp/tan262/labels_all.json"))
    ap.add_argument("--sinica", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--out", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--report", type=Path, default=Path("/tmp/tan262/build_admin_report.json"))
    args = ap.parse_args()

    labels = json.loads(args.labels.read_text())["labels"]
    sinica = json.loads(args.sinica.read_text()) if args.sinica.is_file() else {"zhou": [], "jun": [], "xian": []}

    # --- 州 ---
    zhou_hits: dict[str, list] = defaultdict(list)
    for l in labels:
        if l["kind"] != "zhou" or l["conf"] < 0.8:
            continue
        name = clean_zhou_name(l["text"])
        if name:
            zhou_hits[name].append(l)
    zhou_pts = []
    for name, hits in sorted(zhou_hits.items()):
        lon, lat, conf = centroid(hits)
        zhou_pts.append(
            {
                "zhou": name,
                "name": name,
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "source": "tan262",
                "conf": round(conf, 4),
                "n_hits": len(hits),
            }
        )
    # ensure all canon 州 present (Sinica fallback)
    have = {z["zhou"] for z in zhou_pts}
    for z in sinica.get("zhou", []):
        if z["zhou"] not in have and z["zhou"] in CANON_ZHOU:
            zz = dict(z)
            zz["source"] = "sinica_fallback"
            zhou_pts.append(zz)
            have.add(z["zhou"])

    # --- 郡 ---
    # Whitelist: Sinica 郡 names + confirmed Tan additions
    jun_whitelist = {j["jun"] for j in sinica.get("jun", [])}
    jun_whitelist.update(m["jun"] for m in MANUAL_JUN)
    jun_whitelist.discard("西充国")  # 县 on Tan sheet 06

    jun_hits: dict[str, list] = defaultdict(list)
    for l in labels:
        if l["kind"] != "jun" or l["conf"] < 0.8:
            continue
        name = clean_jun_name(l["text"])
        if not name:
            continue
        # 西充国 / 南充国 are 县 on Tan sheet 06, not 郡
        if name in {"西充国", "南充国"}:
            continue
        if name not in jun_whitelist:
            continue
        jun_hits[name].append(l)

    # parent 州: nearest Tan 州 seed, biased by sheet
    def nearest_zhou(lon, lat, sheet=""):
        best, bestd = SHEET_ZHOU.get(sheet, ""), 1e18
        for z in zhou_pts:
            d = (z["lon"] - lon) ** 2 + (z["lat"] - lat) ** 2
            # prefer sheet default if close enough
            if z["zhou"] == SHEET_ZHOU.get(sheet) and d < 40:
                return z["zhou"]
            if d < bestd:
                bestd, best = d, z["zhou"]
        return best

    jun_pts = []
    for name, hits in sorted(jun_hits.items()):
        lon, lat, conf = centroid(hits)
        sheet = hits[0]["sheet"]
        zhou = nearest_zhou(lon, lat, sheet)
        jun_pts.append(
            {
                "jun": name,
                "name": name,
                "zhou": zhou,
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "source": "tan262",
                "conf": round(conf, 4),
                "n_hits": len(hits),
                "sheet": sheet,
            }
        )
    jun_names = {j["jun"] for j in jun_pts}
    for mj in MANUAL_JUN:
        if mj["jun"] not in jun_names:
            jun_pts.append(mj)
            jun_names.add(mj["jun"])

    # Sinica fallback 郡 not seen on Tan
    for j in sinica.get("jun", []):
        if j["jun"] not in jun_names:
            jj = dict(j)
            jj["source"] = "sinica_fallback"
            jun_pts.append(jj)
            jun_names.add(j["jun"])

    # Drop bogus 西充国 郡 if still present from sinica
    jun_pts = [j for j in jun_pts if not (j["jun"] == "西充国" and j.get("source") == "sinica_fallback")]
    # Also drop tan OCR 西充国 jun if any slipped
    jun_pts = [j for j in jun_pts if j["jun"] != "西充国"]

    # --- 县 ---
    # Match against sinica roster names first; add Tan-only when clean
    sinica_xian_by_name = defaultdict(list)
    for x in sinica.get("xian", []):
        sinica_xian_by_name[norm(x["name"])].append(x)

    xian_hits: dict[tuple[str, str], list] = defaultdict(list)  # (jun,name) or ("?",name)
    for l in labels:
        if l["kind"] != "xian" or l["conf"] < 0.85:
            continue
        name = clean_xian_name(l["text"])
        if not name:
            continue
        xian_hits[("?", name)].append(l)

    xian_pts = []
    used_keys = set()  # (jun, name)

    def add_xian(name, jun, zhou, lon, lat, source, conf=1.0):
        key = (jun, name)
        if key in used_keys:
            return
        xid = f"xian:{zhou}/{jun}/{name}"
        xian_pts.append(
            {
                "xian_id": xid,
                "name": name,
                "jun": jun,
                "zhou": zhou,
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "source": source,
                "conf": conf,
            }
        )
        used_keys.add(key)

    # Roster whitelist: Sinica 县 names (historical), plus manuals
    roster_names = {norm(x["name"]) for x in sinica.get("xian", [])}
    roster_names.update(mx["name"] for mx in MANUAL_XIAN)
    roster_names.update({"卢", "肥成", "蛇丘", "临邑", "西充国", "南充国", "临浦"})

    # Sinica (jun,name) → point for parent hint
    sinica_by_name: dict[str, list] = defaultdict(list)
    for x in sinica.get("xian", []):
        sinica_by_name[norm(x["name"])].append(x)

    jun_index = [(j["jun"], j["zhou"], j["lon"], j["lat"]) for j in jun_pts]

    def nearest_jun(lon, lat, candidates: list[str] | None = None):
        best = ("", "", 1e18)
        for jn, jz, jlon, jlat in jun_index:
            if candidates is not None and jn not in candidates:
                continue
            d = (jlon - lon) ** 2 + (jlat - lat) ** 2
            if d < best[2]:
                best = (jn, jz, d)
        return best[0], best[1], best[2]

    # Tan hits: only roster names (avoid modern OCR junk like 南宁市)
    for (_k, name), hits in xian_hits.items():
        if name not in roster_names:
            continue
        lon, lat, conf = centroid(hits)
        cand = None
        if name in sinica_by_name:
            cand = list({x["jun"] for x in sinica_by_name[name]})
        jn, jz, d = nearest_jun(lon, lat, cand)
        if not jn:
            jn, jz, d = nearest_jun(lon, lat, None)
        if not jn or d > 5.0**2:
            continue
        add_xian(name, jn, jz, lon, lat, "tan262", round(conf, 4))

    for mx in MANUAL_XIAN:
        add_xian(mx["name"], mx["jun"], mx["zhou"], mx["lon"], mx["lat"], mx["source"])

    # Sinica fallback for 县 not already placed under same jun
    for x in sinica.get("xian", []):
        if x.get("jun") == "西充国":
            # retired 郡 — remap
            if x["name"] == "梓潼":
                add_xian(x["name"], "梓潼郡", "益州", x["lon"], x["lat"], "sinica_fallback")
            continue
        add_xian(x["name"], x["jun"], x["zhou"], x["lon"], x["lat"], "sinica_fallback")

    out = {
        "meta": {
            "phase": 2,
            "authority": "Tan Qixiang 中国历史地图集 三国 分幅 (tan-262 sheets)",
            "authority_visual": "tan-262 分幅 purple boundaries + printed degree grid",
            "coords": "OCR/label centroids on georeferenced 分幅 sheets",
            "fallback": "Sinica ad0262 OCR only where Tan sheet has no label",
            "not_authority": "Zhoudadudu / ThreeKingdoms262 redraw; CHGIS polygons",
            "unread_absent_on_sheets": ["领方", "临尘", "雍鸡"],
            "unread_note": "三国分幅 郁林郡 shows 临浦/布山/阴平等 — not 领方/临尘/雍鸡",
            "sheets_used": sorted({l["sheet"] for l in labels}),
        },
        "zhou": sorted(zhou_pts, key=lambda z: z["zhou"]),
        "jun": sorted(jun_pts, key=lambda j: (j["zhou"], j["jun"])),
        "xian": xian_pts,
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    report = {
        "n_zhou": len(out["zhou"]),
        "n_jun": len(out["jun"]),
        "n_xian": len(out["xian"]),
        "zhou": [z["zhou"] for z in out["zhou"]],
        "tan_zhou": sum(1 for z in out["zhou"] if z.get("source") == "tan262"),
        "tan_jun": sum(1 for j in out["jun"] if str(j.get("source", "")).startswith("tan262")),
        "tan_xian": sum(1 for x in out["xian"] if str(x.get("source", "")).startswith("tan262")),
        "has_jibei": any(j["jun"] == "济北国" for j in out["jun"]),
        "jibei_xian": [x["name"] for x in out["xian"] if x["jun"] == "济北国"],
        "xichong_as_xian": any(x["name"] == "西充国" for x in out["xian"]),
        "no_xichong_jun": all(j["jun"] != "西充国" for j in out["jun"]),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
