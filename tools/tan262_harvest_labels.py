#!/usr/bin/env python3
"""OCR 州/郡/县 labels from georeferenced Tan-262 分幅 sheets.

Tiles each sheet, runs RapidOCR, maps pixel centroids → lon/lat via georef JSON.
Does not read or write JPEG scans into the git repo.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR

ZHOU_RE = re.compile(r".{1,3}州$|司隶")
JUN_RE = re.compile(r".{1,4}(郡|国|國)$")
# traditional/simplified normalize
TRAD = str.maketrans({"國": "国", "縣": "县", "臨": "临", "廣": "广", "雞": "鸡", "領": "领", "濟": "济", "陽": "阳", "陰": "阴", "陝": "陕", "漢": "汉", "壽": "寿", "會": "会", "無": "无", "吳": "吴", "齊": "齐", "遼": "辽", "寧": "宁", "豐": "丰", "業": "业", "趙": "赵", "韋": "韦", "茲": "兹", "龜": "龟", "鬱": "郁", "粵": "粤"})


def normalize(t: str) -> str:
    t = t.translate(TRAD)
    t = re.sub(r"\s+", "", t)
    return t


def pix_to_lonlat(g: dict, x: float, y: float) -> tuple[float, float]:
    return float(g["lon_a"] * x + g["lon_b"]), float(g["lat_c"] * y + g["lat_d"])


def ocr_tile(engine: RapidOCR, im: Image.Image) -> list[tuple[str, float, float, float]]:
    arr = np.asarray(ImageOps.autocontrast(im.convert("RGB")))
    if arr.std() < 4:
        return []
    result = engine(arr)
    out: list[tuple[str, float, float, float]] = []
    if result is None or not result.txts:
        return out
    for t, s, b in zip(result.txts, result.scores, result.boxes):
        if s is None or s < 0.55:
            continue
        bb = np.array(b)
        out.append((str(t).strip(), float(s), float(bb[:, 0].mean()), float(bb[:, 1].mean())))
    return out


def classify(text: str) -> str | None:
    t = normalize(text)
    if not t or len(t) > 8:
        return None
    if any(k in t for k in ("公里", "比例", "附图", "附近", "现代", "省", "市", "县界", "年")):
        return None
    if re.fullmatch(r"\d+", t) or re.fullmatch(r"\d+[°º]?", t):
        return None
    if ZHOU_RE.search(t) or t in {"司隶", "司州"}:
        return "zhou"
    if JUN_RE.search(t):
        return "jun"
    # short place names as possible 县 (1–3 chars, Han)
    if re.fullmatch(r"[\u4e00-\u9fff]{1,3}", t):
        return "xian"
    if re.fullmatch(r"[\u4e00-\u9fff]{1,4}", t) and not t.endswith(("水", "江", "河", "山", "海", "湖")):
        return "xian"
    return None


def harvest_sheet(
    engine: RapidOCR,
    path: Path,
    g: dict,
    tile: int = 512,
    overlap: int = 64,
    min_conf: float = 0.62,
) -> list[dict]:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    labels: list[dict] = []
    y = 0
    while y < h:
        x = 0
        y1 = min(h, y + tile)
        while x < w:
            x1 = min(w, x + tile)
            crop = im.crop((x, y, x1, y1))
            for text, conf, px, py in ocr_tile(engine, crop):
                if conf < min_conf:
                    continue
                kind = classify(text)
                if not kind:
                    continue
                gx, gy = x + px, y + py
                # skip margin ticks
                if gx < 40 or gy < 40 or gx > w - 40 or gy > h - 40:
                    if re.search(r"\d", text):
                        continue
                lon, lat = pix_to_lonlat(g, gx, gy)
                labels.append(
                    {
                        "text": normalize(text),
                        "raw": text,
                        "kind": kind,
                        "conf": conf,
                        "lon": lon,
                        "lat": lat,
                        "px": gx,
                        "py": gy,
                        "sheet": path.name,
                    }
                )
            x += tile - overlap
        y += tile - overlap
    return labels


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--georef", type=Path, default=Path("/tmp/tan262/georef/all_georef.json"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/tan262/labels_all.json"))
    ap.add_argument("--only", type=str, default="", help="comma filenames to process")
    ap.add_argument("--tile", type=int, default=512)
    ap.add_argument("--min-conf", type=float, default=0.62)
    args = ap.parse_args()

    georef = json.loads(args.georef.read_text())
    engine = RapidOCR()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    all_labels: list[dict] = []
    per_sheet: dict[str, int] = {}
    for path in sorted(args.sheets.glob("*.jpg")):
        if only and path.name not in only:
            continue
        g = georef.get(path.name)
        if not g or not g.get("ok"):
            print(f"skip {path.name}: no georef", flush=True)
            continue
        print(f"OCR {path.name} …", flush=True)
        labs = harvest_sheet(engine, path, g, tile=args.tile, min_conf=args.min_conf)
        per_sheet[path.name] = len(labs)
        all_labels.extend(labs)
        print(f"  labels={len(labs)}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "authority": "Tan Qixiang 中国历史地图集 三国 分幅 (tan-262 sheets)",
        "n_labels": len(all_labels),
        "per_sheet": per_sheet,
        "labels": all_labels,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {args.out} n={len(all_labels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
