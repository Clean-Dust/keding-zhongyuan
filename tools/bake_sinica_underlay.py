#!/usr/bin/env python3
"""Bake Sinica CCTS WMTS layer ad0262 (三國歷史地圖) into an offline underlay.

Important: ad0262 is **raster tiles** (painted map imagery), NOT vector 州/郡
polygons. This tool mosaics GoogleMapsCompatible tiles covering the game
equirectangular bbox and warps them to match Hex.gd lon/lat (linear lat).

Does NOT color-pick tiles into hex admin fills.

Service: https://gis.sinica.edu.tw/ccts/wmts
Layer: ad0262 — 三國歷史地圖(Sanguo)
Tile template:
  https://gis.sinica.edu.tw/ccts/file-exists.php?img=ad0262-png-{z}-{TileCol}-{TileRow}

Attribution: Academia Sinica CCTS / 中華文明之時空基礎架構 (academic research use).
Be polite: small sleep between tile GETs; cache under /tmp; prefer offline bake
checked into data/ for Godot (no runtime WMTS required).

Usage:
  python3 tools/bake_sinica_underlay.py --zoom 7 --out-png data/sinica_ad0262_underlay.jpg
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path

from PIL import Image

# Match Hex.gd
LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8

TILE_URL = (
    "https://gis.sinica.edu.tw/ccts/file-exists.php?img=ad0262-png-{z}-{x}-{y}"
)
ORIGIN = 20037508.34278925  # Web Mercator half-world
TILE = 256
USER_AGENT = "keding-zhongyuan-bake/1.0 (academic; Sinica CCTS ad0262 underlay)"


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    x = lon * ORIGIN / 180.0
    lat = max(min(lat, 85.05112878), -85.05112878)
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * ORIGIN / math.pi
    return x, y


def merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = x * 180.0 / ORIGIN
    lat = math.degrees(2.0 * math.atan(math.exp(y * math.pi / ORIGIN)) - math.pi / 2.0)
    return lon, lat


def lonlat_to_tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_pixel_to_merc(z: int, tx: int, ty: int, px: float, py: float) -> tuple[float, float]:
    n = 2**z
    mx = (tx + px / TILE) / n * (2 * ORIGIN) - ORIGIN
    my = ORIGIN - (ty + py / TILE) / n * (2 * ORIGIN)
    return mx, my


def fetch_tile(z: int, x: int, y: int, cache: Path, sleep_s: float) -> Image.Image | None:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"ad0262_{z}_{x}_{y}.png"
    if not path.is_file():
        url = TILE_URL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            path.write_bytes(data)
        except Exception as e:
            print(f"  fail {z}/{x}/{y}: {e}")
            return None
        time.sleep(sleep_s)
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def mosaic(z: int, cache: Path, sleep_s: float) -> tuple[Image.Image, int, int, int, int]:
    x0f, y1f = lonlat_to_tile_xy(LON_MIN, LAT_MIN, z)
    x1f, y0f = lonlat_to_tile_xy(LON_MAX, LAT_MAX, z)
    tx0, tx1 = math.floor(x0f), math.ceil(x1f) - 1
    ty0, ty1 = math.floor(y0f), math.ceil(y1f) - 1
    cols = tx1 - tx0 + 1
    rows = ty1 - ty0 + 1
    print(f"zoom={z} tiles x={tx0}..{tx1} y={ty0}..{ty1} ({cols}×{rows}={cols*rows})")
    mosa = Image.new("RGBA", (cols * TILE, rows * TILE), (0, 0, 0, 0))
    ok = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            im = fetch_tile(z, tx, ty, cache, sleep_s)
            if im is None:
                continue
            mosa.paste(im, ((tx - tx0) * TILE, (ty - ty0) * TILE))
            ok += 1
    print(f"  fetched/cached ok={ok}/{cols*rows}")
    return mosa, tx0, ty0, tx1, ty1


def warp_equirect(
    mosa: Image.Image,
    z: int,
    tx0: int,
    ty0: int,
    out_w: int,
    out_h: int,
) -> Image.Image:
    """Sample mosaic (Web Mercator tiles) onto linear lon/lat image matching Hex.gd."""
    src = mosa.load()
    mw, mh = mosa.size
    out = Image.new("RGB", (out_w, out_h), (32, 48, 64))
    dst = out.load()
    for j in range(out_h):
        # row 0 = north = LAT_MAX
        lat = LAT_MAX - (j + 0.5) / out_h * (LAT_MAX - LAT_MIN)
        for i in range(out_w):
            lon = LON_MIN + (i + 0.5) / out_w * (LON_MAX - LON_MIN)
            mx, my = lonlat_to_merc(lon, lat)
            # merc → pixel in mosaic
            n = 2**z
            fx = (mx + ORIGIN) / (2 * ORIGIN) * n
            fy = (ORIGIN - my) / (2 * ORIGIN) * n
            px = (fx - tx0) * TILE
            py = (fy - ty0) * TILE
            ix, iy = int(px), int(py)
            if 0 <= ix < mw and 0 <= iy < mh:
                r, g, b, a = src[ix, iy]
                if a > 8:
                    dst[i, j] = (r, g, b)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoom", type=int, default=7)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--sleep", type=float, default=0.12, help="seconds between tile downloads")
    ap.add_argument("--cache", type=Path, default=Path("/tmp/sinica_ad0262_tiles"))
    ap.add_argument("--out-png", type=Path, default=Path("data/sinica_ad0262_underlay.jpg"))
    ap.add_argument("--out-meta", type=Path, default=Path("data/sinica_ad0262_underlay.json"))
    ap.add_argument("--jpeg-quality", type=int, default=85)
    args = ap.parse_args()

    mosa, tx0, ty0, tx1, ty1 = mosaic(args.zoom, args.cache, args.sleep)
    aspect = (LON_MAX - LON_MIN) / (LAT_MAX - LAT_MIN)
    out_w = args.width
    out_h = max(64, int(round(out_w / aspect)))
    print(f"warping to equirect {out_w}×{out_h} (linear lat, Hex.gd bbox)…")
    eq = warp_equirect(mosa, args.zoom, tx0, ty0, out_w, out_h)
    args.out_png.parent.mkdir(parents=True, exist_ok=True)
    if args.out_png.suffix.lower() in (".jpg", ".jpeg"):
        eq.convert("RGB").save(args.out_png, quality=args.jpeg_quality, optimize=True)
    else:
        eq.save(args.out_png, optimize=True)

    meta = {
        "layer": "ad0262",
        "title": "三國歷史地圖(Sanguo)",
        "service": "https://gis.sinica.edu.tw/ccts/wmts",
        "showwmts": "https://gis.sinica.edu.tw/showwmts/index.php?s=ccts&l=ad0262",
        "kind": "raster_wmts_tiles",
        "not_vector": (
            "CCTS ad0262 is raster imagery of a historical map (州 borders painted in the "
            "image). It is NOT downloadable vector admin polygons — do not treat as CHGIS "
            "pref_pgn. Underlay only; no color-pick hex fills."
        ),
        "tile_matrix_set": "GoogleMapsCompatible",
        "bake_zoom": args.zoom,
        "tile_range": {"tx0": tx0, "ty0": ty0, "tx1": tx1, "ty1": ty1},
        "bbox": {
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "note": "Same Sinica screenshot extent as Hex.gd / 500×500 game map",
        },
        "projection": "equirectangular linear lon/lat (matches Hex.lonlat_to_pixel)",
        "image": str(args.out_png.name),
        "width": out_w,
        "height": out_h,
        "attribution": (
            "Academia Sinica CCTS — 中華文明之時空基礎架構 WMTS layer ad0262 "
            "三國歷史地圖(Sanguo). Academic research use; offline bake of public WMTS tiles."
        ),
    }
    args.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out_png} ({args.out_png.stat().st_size // 1024} KiB) + {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
