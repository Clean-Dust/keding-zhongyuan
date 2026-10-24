#!/usr/bin/env python3
"""Harvest all readable 州/郡/县 labels from Sinica CCTS ad0262 via OCR.

Authority: tile labels only. No CHGIS / roster coordinates.

Usage:
  python3 -u tools/harvest_sinica_ad0262_labels.py --zoom 10 --out /tmp/sinica_labels_z10.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr import RapidOCR

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
TILE_URL = "https://gis.sinica.edu.tw/ccts/file-exists.php?img=ad0262-png-{z}-{x}-{y}"
UA = "keding-zhongyuan-bake/1.0 (academic; Sinica CCTS ad0262 full-label OCR)"
ORIGIN = 20037508.34278925
TILE = 256

# Skip tiny / empty parchment tiles
MIN_TILE_BYTES = 1600


def lonlat_to_tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_xy_to_lonlat(fx: float, fy: float, z: int) -> tuple[float, float]:
    n = 2**z
    lon = fx / n * 360.0 - 180.0
    merc_y = ORIGIN - (fy / n) * (2 * ORIGIN)
    lat = math.degrees(2.0 * math.atan(math.exp(merc_y * math.pi / ORIGIN)) - math.pi / 2.0)
    return lon, lat


def tile_range(z: int) -> tuple[int, int, int, int]:
    x0, y0 = lonlat_to_tile_xy(LON_MIN, LAT_MAX, z)
    x1, y1 = lonlat_to_tile_xy(LON_MAX, LAT_MIN, z)
    return int(x0), int(x1), int(y0), int(y1)


def fetch_tile(z: int, x: int, y: int, cache: Path, sleep_s: float) -> Path | None:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"ad0262_{z}_{x}_{y}.png"
    if not path.is_file() or path.stat().st_size < 40:
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                path.write_bytes(resp.read())
            time.sleep(sleep_s)
        except Exception as e:
            print(f"  fail {z}/{x}/{y}: {e}", flush=True)
            return None
    return path


def ocr_tile(engine: RapidOCR, path: Path) -> list[tuple[str, float, float, float]]:
    im = Image.open(path).convert("RGB")
    # Skip near-blank parchment
    arr = np.asarray(im)
    if arr.std() < 6.0:
        return []
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zoom", type=int, default=10)
    ap.add_argument("--cache", type=Path, default=Path("/tmp/sinica_ad0262_tiles"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/sinica_labels_z10.json"))
    ap.add_argument("--sleep", type=float, default=0.03)
    ap.add_argument("--min-conf", type=float, default=0.75)
    ap.add_argument("--min-bytes", type=int, default=MIN_TILE_BYTES)
    ap.add_argument("--fetch-missing", action="store_true", default=True)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--resume", type=Path, default=None, help="prior out json to merge")
    ap.add_argument(
        "--tiles-json",
        type=Path,
        default=None,
        help="optional [[tx,ty],…] list; default = full bbox range",
    )
    ap.add_argument(
        "--big-only",
        action="store_true",
        help="only OCR tiles already cached with size >= min-bytes (skip fetch/tiny)",
    )
    ap.add_argument("--progress-every", type=int, default=25)
    args = ap.parse_args()
    fetch = args.fetch_missing and not args.no_fetch and not args.big_only

    if args.tiles_json and args.tiles_json.is_file():
        tile_list = [tuple(xy) for xy in json.loads(args.tiles_json.read_text())]
        print(f"z{args.zoom} tiles-json n={len(tile_list)}", flush=True)
    else:
        x0, x1, y0, y1 = tile_range(args.zoom)
        tile_list = [(tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)]
        print(f"z{args.zoom} tile range x={x0}..{x1} y={y0}..{y1} n={len(tile_list)}", flush=True)

    done_keys: set[str] = set()
    labels: list[dict] = []
    if args.resume and args.resume.is_file():
        prev = json.loads(args.resume.read_text(encoding="utf-8"))
        labels = list(prev.get("labels", []))
        done_keys = {f"{L['tx']}_{L['ty']}" for L in labels}
        # also track tiles scanned with zero labels
        for t in prev.get("empty_tiles", []):
            done_keys.add(f"{t['tx']}_{t['ty']}")
        print(f"resume {len(labels)} labels, {len(done_keys)} tiles marked", flush=True)

    empty_tiles: list[dict] = []
    engine = RapidOCR()
    n_ocr = 0
    n_skip = 0
    t0 = time.time()

    scanned = 0
    for tx, ty in tile_list:
        key = f"{tx}_{ty}"
        if key in done_keys:
            continue
        path = args.cache / f"ad0262_{args.zoom}_{tx}_{ty}.png"
        if not path.is_file() or path.stat().st_size < 40:
            if args.big_only or not fetch:
                n_skip += 1
                scanned += 1
                continue
            path2 = fetch_tile(args.zoom, tx, ty, args.cache, args.sleep)
            if path2 is None:
                empty_tiles.append({"tx": tx, "ty": ty, "reason": "fetch_fail"})
                done_keys.add(key)
                scanned += 1
                continue
            path = path2
        if path.stat().st_size < args.min_bytes:
            empty_tiles.append({"tx": tx, "ty": ty, "reason": "tiny", "bytes": path.stat().st_size})
            done_keys.add(key)
            n_skip += 1
            scanned += 1
            if scanned % 500 == 0:
                print(
                    f"  scan={scanned} ocr={n_ocr} labels={len(labels)} skip={n_skip} "
                    f"elapsed={time.time()-t0:.0f}s",
                    flush=True,
                )
            continue

        hits = ocr_tile(engine, path)
        n_ocr += 1
        scanned += 1
        if not hits:
            empty_tiles.append({"tx": tx, "ty": ty, "reason": "no_text"})
            done_keys.add(key)
        for text, conf, px, py in hits:
            if conf < args.min_conf:
                continue
            # strip obvious junk
            if not re.search(r"[\u3400-\u9fff]", text):
                continue
            lon, lat = tile_xy_to_lonlat(tx + px / TILE, ty + py / TILE, args.zoom)
            if not (LON_MIN - 0.5 <= lon <= LON_MAX + 0.5 and LAT_MIN - 0.5 <= lat <= LAT_MAX + 0.5):
                continue
            labels.append(
                {
                    "text": text,
                    "conf": round(conf, 4),
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "tx": tx,
                    "ty": ty,
                    "px": round(px, 1),
                    "py": round(py, 1),
                    "zoom": args.zoom,
                }
            )
        done_keys.add(key)
        if n_ocr % args.progress_every == 0 or scanned % 200 == 0:
            print(
                f"  ocr={n_ocr} labels={len(labels)} skip={n_skip} "
                f"at {tx},{ty} elapsed={time.time()-t0:.0f}s",
                flush=True,
            )
            # checkpoint
            args.out.write_text(
                json.dumps(
                    {
                        "zoom": args.zoom,
                        "bbox": {
                            "lon_min": LON_MIN,
                            "lon_max": LON_MAX,
                            "lat_min": LAT_MIN,
                            "lat_max": LAT_MAX,
                        },
                        "labels": labels,
                        "empty_tiles": empty_tiles,
                        "progress": {"n_ocr": n_ocr, "n_skip": n_skip},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    out = {
        "zoom": args.zoom,
        "layer": "ad0262",
        "authority": "Sinica CCTS ad0262 OCR labels only",
        "bbox": {"lon_min": LON_MIN, "lon_max": LON_MAX, "lat_min": LAT_MIN, "lat_max": LAT_MAX},
        "min_conf": args.min_conf,
        "n_labels": len(labels),
        "n_ocr_tiles": n_ocr,
        "n_skip_tiles": n_skip,
        "labels": labels,
        "empty_tiles": empty_tiles,
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DONE labels={len(labels)} ocr_tiles={n_ocr} skip={n_skip} → {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
