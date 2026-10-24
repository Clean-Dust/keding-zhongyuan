#!/usr/bin/env python3
"""Match unmatched roster 县 to Sinica CCTS ad0262 county labels (OCR).

Confirmed: ad0262 shows 县 names from zoom ~10 upward (郡 earlier; 县 denser at z10–z11).
Places a seat only on high-confidence name match within the parent 郡 neighborhood.
Does not invent coordinates; does not move existing CHGIS seats.

Usage:
  python3 tools/match_sinica_xian_points.py \\
    --points data/admin_points.json --zoom 10 --out data/admin_points.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
from opencc import OpenCC
from PIL import Image
from rapidocr import RapidOCR

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W, MAP_H = 500, 500
TILE_URL = "https://gis.sinica.edu.tw/ccts/file-exists.php?img=ad0262-png-{z}-{x}-{y}"
UA = "keding-zhongyuan-bake/1.0 (academic; Sinica CCTS ad0262 xian OCR)"
ORIGIN = 20037508.34278925
TILE = 256

_s2t = OpenCC("s2t")
_t2s = OpenCC("t2s")


def lonlat_to_col_row(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return max(0, min(MAP_W - 1, col)), max(0, min(MAP_H - 1, row))


def lonlat_to_tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_xy_to_lonlat(fx: float, fy: float, z: int) -> tuple[float, float]:
    n = 2**z
    lon = fx / n * 360.0 - 180.0
    # Web Mercator tile Y → lat
    merc_y = ORIGIN - (fy / n) * (2 * ORIGIN)
    lat = math.degrees(2.0 * math.atan(math.exp(merc_y * math.pi / ORIGIN)) - math.pi / 2.0)
    return lon, lat


# Keep CJK Unified + Ext A/B/C so rare roster graphs are not stripped.
_CJK_RE = re.compile(
    r"[^\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002b73f\U0002b740-\U0002b81f]"
)


def normalize_name(s: str) -> str:
    s = _t2s.convert(str(s or "").strip())
    for suf in ("县", "縣", "侯国", "国", "道", "城"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    # drop punctuation / noise (keep rare CJK extensions)
    s = _CJK_RE.sub("", s)
    return s


def name_variants(name: str) -> set[str]:
    simp = _t2s.convert(name)
    trad = _s2t.convert(name)
    out = {normalize_name(simp), normalize_name(trad), simp, trad, name}
    for v in list(out):
        out.add(normalize_name(v))
        out.add(v + "县")
        out.add(v + "縣")
        out.add(_s2t.convert(v + "县"))
    return {x for x in out if x}


def fetch_tile(z: int, x: int, y: int, cache: Path, sleep_s: float) -> Image.Image | None:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"ad0262_{z}_{x}_{y}.png"
    if not path.is_file() or path.stat().st_size < 80:
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                path.write_bytes(resp.read())
            time.sleep(sleep_s)
        except Exception as e:
            print(f"  fail tile {z}/{x}/{y}: {e}")
            return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def mosaic_around(
    lon: float, lat: float, z: int, rad: int, cache: Path, sleep_s: float
) -> tuple[Image.Image, int, int] | None:
    fx, fy = lonlat_to_tile_xy(lon, lat, z)
    tx, ty = int(fx), int(fy)
    tiles: list[list[Image.Image]] = []
    ok = 0
    for dy in range(-rad, rad + 1):
        row: list[Image.Image] = []
        for dx in range(-rad, rad + 1):
            im = fetch_tile(z, tx + dx, ty + dy, cache, sleep_s)
            if im is None:
                im = Image.new("RGB", (TILE, TILE), (210, 190, 160))
            else:
                ok += 1
            row.append(im)
        tiles.append(row)
    if ok < 3:
        return None
    mosa = Image.new("RGB", (TILE * (2 * rad + 1), TILE * (2 * rad + 1)))
    for j, row in enumerate(tiles):
        for i, im in enumerate(row):
            mosa.paste(im, (i * TILE, j * TILE))
    return mosa, tx - rad, ty - rad


def pixel_to_lonlat(
    px: float, py: float, z: int, tx0: int, ty0: int
) -> tuple[float, float]:
    fx = tx0 + px / TILE
    fy = ty0 + py / TILE
    return tile_xy_to_lonlat(fx, fy, z)


def ocr_labels(engine: RapidOCR, im: Image.Image) -> list[tuple[str, float, float, float]]:
    """Return (text, conf, cx, cy) in image pixels."""
    # RapidOCR wants path or ndarray
    arr = np.array(im)
    result = engine(arr)
    out: list[tuple[str, float, float, float]] = []
    if result is None or not result.txts:
        return out
    for t, s, b in zip(result.txts, result.scores, result.boxes):
        if s is None:
            continue
        bb = np.array(b)
        out.append((str(t), float(s), float(bb[:, 0].mean()), float(bb[:, 1].mean())))
    return out


def haversine_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return math.hypot(lon1 - lon2, lat1 - lat2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--zoom", type=int, default=10, help="tile zoom; 县 visible from ~10")
    ap.add_argument("--rad", type=int, default=2, help="tile radius around 郡治")
    ap.add_argument("--min-conf", type=float, default=0.80)
    ap.add_argument("--max-deg", type=float, default=2.2, help="max distance from 郡治")
    ap.add_argument("--cache", type=Path, default=Path("/tmp/sinica_ad0262_tiles"))
    ap.add_argument("--sleep", type=float, default=0.04)
    ap.add_argument("--out", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--report", type=Path, default=Path("/tmp/sinica_xian_match_report.json"))
    ap.add_argument("--dbg-dir", type=Path, default=Path("/tmp/sinica_xian_ocr"))
    args = ap.parse_args()

    pts = json.loads(args.points.read_text(encoding="utf-8"))
    before_matched = len(pts["xian"])
    before_unmatched = len(pts["xian_unmatched"])

    jun_by_key = {(j["zhou"], j["jun"]): j for j in pts["jun"]}
    unmatched_by_jun: dict[tuple[str, str], list] = defaultdict(list)
    for u in pts["xian_unmatched"]:
        unmatched_by_jun[(u["zhou"], u["jun"])].append(u)

    engine = RapidOCR()
    args.dbg_dir.mkdir(parents=True, exist_ok=True)

    new_seats: list[dict] = []
    still: list[dict] = []
    match_log: list[dict] = []
    skipped_juns: list[str] = []

    for (zhou, jun), ulist in sorted(unmatched_by_jun.items()):
        jp = jun_by_key.get((zhou, jun))
        if not jp:
            for u in ulist:
                u2 = dict(u)
                u2["reason"] = "no_jun_seat_for_sinica_search"
                still.append(u2)
            skipped_juns.append(f"{zhou}/{jun}:no_jun")
            continue
        jlon, jlat = float(jp["lon"]), float(jp["lat"])
        if not (LON_MIN <= jlon <= LON_MAX and LAT_MIN <= jlat <= LAT_MAX):
            for u in ulist:
                u2 = dict(u)
                u2["reason"] = "jun_outside_sinica_bbox"
                still.append(u2)
            skipped_juns.append(f"{zhou}/{jun}:oob")
            continue

        print(f"OCR {zhou}/{jun} unmatched={len(ulist)} @({jlon:.2f},{jlat:.2f})")
        mosa = mosaic_around(jlon, jlat, args.zoom, args.rad, args.cache, args.sleep)
        if not mosa:
            for u in ulist:
                u2 = dict(u)
                u2["reason"] = "sinica_tiles_unavailable"
                still.append(u2)
            continue
        im, tx0, ty0 = mosa
        dbg_path = args.dbg_dir / f"{zhou}_{jun}_z{args.zoom}.jpg"
        im.save(dbg_path, quality=88)

        labels = ocr_labels(engine, im)
        # index by normalized name
        by_norm: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
        for text, conf, cx, cy in labels:
            if conf < args.min_conf:
                continue
            # skip obvious hydronyms / 州郡 titles unless exact 县 name
            nrm = normalize_name(text)
            if not nrm:
                continue
            if nrm.endswith("水") or nrm.endswith("河") or nrm.endswith("渠"):
                continue
            if nrm.endswith("郡") or nrm.endswith("尹") or nrm.endswith("州"):
                continue
            by_norm[nrm].append((text, conf, cx, cy))
            by_norm[normalize_name(text)].append((text, conf, cx, cy))

        used_px: set[tuple[int, int]] = set()
        for u in ulist:
            variants = name_variants(u["name"])
            cands: list[tuple[float, str, float, float, float, float]] = []
            for v in variants:
                nrm = normalize_name(v)
                for text, conf, cx, cy in by_norm.get(nrm, []):
                    lon, lat = pixel_to_lonlat(cx, cy, args.zoom, tx0, ty0)
                    d = haversine_deg(lon, lat, jlon, jlat)
                    if d > args.max_deg:
                        continue
                    # exact-ish: OCR text normalizes to name (allow 县 suffix only)
                    ot = normalize_name(text)
                    if ot != normalize_name(u["name"]) and text not in variants:
                        # allow if OCR is name+县 already normalized equal
                        if ot != normalize_name(u["name"]):
                            continue
                    cands.append((conf, text, cx, cy, lon, lat))
            if not cands:
                u2 = dict(u)
                u2["reason"] = "no_sinica_label"
                still.append(u2)
                continue
            # best confidence, then closest to 郡治
            cands.sort(key=lambda x: (-x[0], haversine_deg(x[4], x[5], jlon, jlat)))
            conf, text, cx, cy, lon, lat = cands[0]
            key = (int(cx) // 8, int(cy) // 8)
            if key in used_px:
                u2 = dict(u)
                u2["reason"] = "sinica_label_collision"
                still.append(u2)
                continue
            # short-name guard: 1-char names need higher conf + closer
            if len(normalize_name(u["name"])) <= 1 and (conf < 0.95 or haversine_deg(lon, lat, jlon, jlat) > 1.2):
                u2 = dict(u)
                u2["reason"] = "short_name_low_confidence"
                still.append(u2)
                continue
            used_px.add(key)
            col, row = lonlat_to_col_row(lon, lat)
            seat = {
                "level": "xian",
                "xian_id": u["xian_id"],
                "zhou": zhou,
                "jun": jun,
                "name": u["name"],
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "col": col,
                "row": row,
                "source": "sinica_ad0262",
                "confidence": "high",
                "sinica_label": text,
                "sinica_ocr_conf": round(conf, 3),
                "sinica_zoom": args.zoom,
                "note": f"OCR label near {jun}治; not CHGIS",
            }
            new_seats.append(seat)
            match_log.append(
                {
                    "xian_id": u["xian_id"],
                    "name": u["name"],
                    "zhou": zhou,
                    "jun": jun,
                    "label": text,
                    "conf": conf,
                    "lon": lon,
                    "lat": lat,
                    "deg_from_jun": round(haversine_deg(lon, lat, jlon, jlat), 3),
                }
            )
            print(f"  MATCH {u['name']} ← {text!r} conf={conf:.2f} @({lon:.3f},{lat:.3f})")

    # merge: keep all CHGIS seats unchanged; append Sinica matches
    xian = list(pts["xian"]) + new_seats
    # de-dupe by xian_id (shouldn't collide)
    seen_ids = set()
    xian_dedup = []
    for s in xian:
        if s["xian_id"] in seen_ids:
            continue
        seen_ids.add(s["xian_id"])
        xian_dedup.append(s)

    pts["xian"] = xian_dedup
    pts["xian_unmatched"] = still
    pts["stats"]["xian_seats_matched"] = len(xian_dedup)
    pts["stats"]["xian_unmatched"] = len(still)
    pts["stats"]["xian_sinica_matched"] = len(new_seats)
    pts["meta"]["xian_unmatched_policy"] = (
        "CHGIS first; Sinica ad0262 OCR for unmatched only when label matches "
        "name under parent 郡 (source=sinica_ad0262). No invented coords."
    )
    pts["meta"]["sinica_xian_zoom"] = (
        f"ad0262 县 labels confirmed at WMTS zoom>={args.zoom} "
        "(郡 earlier; 县 denser at z10–z11)"
    )

    args.out.write_text(json.dumps(pts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "before_matched": before_matched,
        "before_unmatched": before_unmatched,
        "sinica_matched": len(new_seats),
        "after_matched": len(xian_dedup),
        "after_unmatched": len(still),
        "zoom": args.zoom,
        "min_conf": args.min_conf,
        "max_deg_from_jun": args.max_deg,
        "matches": match_log,
        "skipped_juns": skipped_juns,
        "still_reasons": dict(
            sorted(
                {
                    r: sum(1 for u in still if u.get("reason") == r)
                    for r in {u.get("reason") for u in still}
                }.items(),
                key=lambda x: -x[1],
            )
        ),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"DONE before {before_matched}/{before_unmatched} → "
        f"sinica+{len(new_seats)} → after {len(xian_dedup)} matched / {len(still)} unmatched"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
