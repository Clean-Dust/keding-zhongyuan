#!/usr/bin/env python3
"""Parallel prefetch Sinica ad0262 PNG tiles from a tiles-json list."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TILE_URL = "https://gis.sinica.edu.tw/ccts/file-exists.php?img=ad0262-png-{z}-{x}-{y}"
UA = "keding-zhongyuan-bake/1.0 (academic; Sinica CCTS ad0262 prefetch)"


def fetch_one(z: int, x: int, y: int, cache: Path) -> tuple[str, int]:
    path = cache / f"ad0262_{z}_{x}_{y}.png"
    if path.is_file() and path.stat().st_size >= 40:
        return ("cached", path.stat().st_size)
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read()
        path.write_bytes(data)
        return ("ok", len(data))
    except Exception as e:
        return ("fail", 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoom", type=int, required=True)
    ap.add_argument("--tiles-json", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=Path("/tmp/sinica_ad0262_tiles"))
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    tiles = [tuple(xy) for xy in json.loads(args.tiles_json.read_text())]
    print(f"prefetch z{args.zoom} n={len(tiles)} workers={args.workers}", flush=True)
    t0 = time.time()
    counts = {"cached": 0, "ok": 0, "fail": 0, "big": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, args.zoom, tx, ty, args.cache): (tx, ty) for tx, ty in tiles}
        done = 0
        for fut in as_completed(futs):
            status, nbytes = fut.result()
            counts[status] = counts.get(status, 0) + 1
            if nbytes >= 1600:
                counts["big"] += 1
            done += 1
            if done % 200 == 0 or done == len(tiles):
                print(
                    f"  {done}/{len(tiles)} {counts} elapsed={time.time()-t0:.0f}s",
                    flush=True,
                )
    print(f"DONE {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
