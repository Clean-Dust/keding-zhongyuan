#!/usr/bin/env python3
"""Bake coast_mask only (Natural Earth land → 160×160 hex centers).

Admin geometry is NOT baked here. 州/郡/县 paint comes only from hand-filled
per-hex `xian_id` + `data/counties_roster.json` parents (unions of 县 hexes).
Do not regenerate CHGIS Voronoi / PIP admin_* or admin_hex.

Usage (from repo root):
  python3 tools/bake_coast_admin.py --ne /path/to/ne_50m_land.shp --out data
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import zlib
from pathlib import Path

# Match Hex.gd geographic constants.
LON_MIN, LON_MAX = 94.662, 125.69
LAT_MIN, LAT_MAX = 15.55, 41.272
MAP_W, MAP_H = 160, 160
PAD = 0.5


def lonlat_of_offset(col: int, row: int) -> tuple[float, float]:
	lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
	lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
	return lon, lat


def write_json(path: Path, obj: dict) -> None:
	path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
	print(f"wrote {path} ({path.stat().st_size} bytes)")


def bake_coast(land) -> tuple[dict, int, int]:
	from shapely.geometry import Point

	cells = []
	land_n = water_n = 0
	for row in range(MAP_H):
		for col in range(MAP_W):
			lon, lat = lonlat_of_offset(col, row)
			is_land = land.contains(Point(lon, lat)) or land.touches(Point(lon, lat))
			cells.append(1 if is_land else 0)
			if is_land:
				land_n += 1
			else:
				water_n += 1
	raw = bytes(cells)
	meta = {
		"format": "zlib_bytes_0water_1land",
		"width": MAP_W,
		"height": MAP_H,
		"lon_min": LON_MIN,
		"lon_max": LON_MAX,
		"lat_min": LAT_MIN,
		"lat_max": LAT_MAX,
		"source": "Natural Earth 50m land (public domain)",
		"land_count": land_n,
		"water_count": water_n,
		"payload_b64": base64.b64encode(zlib.compress(raw, 9)).decode("ascii"),
	}
	return meta, land_n, water_n


def load_land(ne_path: Path):
	import shapefile
	from shapely.geometry import box, shape
	from shapely.ops import unary_union
	from shapely.validation import make_valid

	r = shapefile.Reader(str(ne_path))
	geoms = []
	for sr in r.iterShapeRecords():
		g = shape(sr.shape.__geo_interface__)
		if g.is_empty:
			continue
		g = make_valid(g)
		geoms.append(g)
	land = unary_union(geoms)
	bbox = box(LON_MIN - PAD, LAT_MIN - PAD, LON_MAX + PAD, LAT_MAX + PAD)
	return make_valid(land.intersection(bbox))


def main() -> int:
	ap = argparse.ArgumentParser(description="Bake coast_mask only — no admin Voronoi/PIP.")
	ap.add_argument("--ne", required=True, help="Natural Earth 50m land shapefile")
	ap.add_argument("--out", required=True, help="Output directory (usually data/)")
	## Reject legacy CHGIS args so old recipes fail loudly instead of painting Voronoi.
	ap.add_argument("--cnty", default=None, help=argparse.SUPPRESS)
	ap.add_argument("--pref", default=None, help=argparse.SUPPRESS)
	ap.add_argument("--legacy-voronoi", action="store_true", help=argparse.SUPPRESS)
	args = ap.parse_args()

	if args.cnty or args.pref or args.legacy_voronoi:
		print(
			"ERROR: CHGIS / Voronoi admin bake is removed.\n"
			"  This script writes coast_mask only.\n"
			"  Admin names: data/counties_roster.json\n"
			"  Admin geometry: hand-fill per-hex xian_id (no Voronoi/PIP).",
			file=sys.stderr,
		)
		return 2

	out = Path(args.out)
	out.mkdir(parents=True, exist_ok=True)

	print("loading land…")
	land = load_land(Path(args.ne))
	print("baking coast…")
	coast, land_n, water_n = bake_coast(land)
	write_json(out / "coast_mask.json", coast)
	raw = zlib.decompress(base64.b64decode(coast["payload_b64"]))
	(out / "coast_mask.bin").write_bytes(raw)
	print(f"coast land={land_n} water={water_n} total={land_n + water_n}")
	print("skipped admin_* (county-based hand fill only; see data/counties_roster.json)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
