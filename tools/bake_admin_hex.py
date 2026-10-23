#!/usr/bin/env python3
"""Assign 州/郡/县 ids to each land hex via point-in-polygon on hex centers.

Reads existing data/admin_{zhou,jun,xian}.json + coast_mask.bin.
Writes data/admin_hex.json (tables + per-cell indices, row-major).

Usage (from repo root):
  python3 tools/bake_admin_hex.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Match Hex.gd
LON_MIN, LON_MAX = 94.662, 125.69
LAT_MIN, LAT_MAX = 15.55, 41.272
MAP_W, MAP_H = 160, 160


def offset_to_lonlat(col: int, row: int) -> tuple[float, float]:
	lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
	lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
	return lon, lat


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
	n = len(ring)
	inside = False
	j = n - 1
	for i in range(n):
		xi, yi = ring[i][0], ring[i][1]
		xj, yj = ring[j][0], ring[j][1]
		if ((yi > lat) != (yj > lat)) and (
			lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-30) + xi
		):
			inside = not inside
		j = i
	return inside


def load_feats(path: Path) -> list[dict]:
	data = json.loads(path.read_text(encoding="utf-8"))
	out: list[dict] = []
	for f in data["features"]:
		polys = []
		for ring in f.get("rings", []):
			if len(ring) < 3:
				continue
			xs = [p[0] for p in ring]
			ys = [p[1] for p in ring]
			polys.append((min(xs), min(ys), max(xs), max(ys), ring))
		out.append({"id": f["id"], "name": f["name"], "polys": polys})
	return out


def assign(feats: list[dict], lon: float, lat: float) -> tuple[str, str]:
	for f in feats:
		for minx, miny, maxx, maxy, ring in f["polys"]:
			if lon < minx or lon > maxx or lat < miny or lat > maxy:
				continue
			if point_in_ring(lon, lat, ring):
				return f["id"], f["name"]
	return "", ""


def idx_of(table: list, index_map: dict, fid: str, name: str) -> int:
	if fid not in index_map:
		index_map[fid] = len(table)
		table.append({"id": fid, "name": name})
	return index_map[fid]


def main() -> None:
	root = Path(__file__).resolve().parents[1]
	data = root / "data"
	t0 = time.time()
	zhou = load_feats(data / "admin_zhou.json")
	jun = load_feats(data / "admin_jun.json")
	xian = load_feats(data / "admin_xian.json")
	coast = (data / "coast_mask.bin").read_bytes()
	assert len(coast) == MAP_W * MAP_H

	zhou_table: list[dict] = [{"id": "", "name": ""}]
	jun_table: list[dict] = [{"id": "", "name": ""}]
	xian_table: list[dict] = [{"id": "", "name": ""}]
	zhou_index = {"": 0}
	jun_index = {"": 0}
	xian_index = {"": 0}

	zhou_cells: list[int] = []
	jun_cells: list[int] = []
	xian_cells: list[int] = []
	stats = {"land": 0, "zhou": 0, "jun": 0, "xian": 0}

	for row in range(MAP_H):
		for col in range(MAP_W):
			idx = row * MAP_W + col
			if coast[idx] == 0:
				zhou_cells.append(0)
				jun_cells.append(0)
				xian_cells.append(0)
				continue
			stats["land"] += 1
			lon, lat = offset_to_lonlat(col, row)
			zid, zn = assign(zhou, lon, lat)
			jid, jn = assign(jun, lon, lat)
			xid, xn = assign(xian, lon, lat)
			if zid:
				stats["zhou"] += 1
			if jid:
				stats["jun"] += 1
			if xid:
				stats["xian"] += 1
			zhou_cells.append(idx_of(zhou_table, zhou_index, zid, zn))
			jun_cells.append(idx_of(jun_table, jun_index, jid, jn))
			xian_cells.append(idx_of(xian_table, xian_index, xid, xn))

	out = {
		"map_w": MAP_W,
		"map_h": MAP_H,
		"method": "hex-center point-in-polygon vs admin_* rings",
		"stats": stats,
		"zhou_table": zhou_table,
		"jun_table": jun_table,
		"xian_table": xian_table,
		"zhou": zhou_cells,
		"jun": jun_cells,
		"xian": xian_cells,
	}
	path = data / "admin_hex.json"
	path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
	print(
		f"wrote {path} in {time.time() - t0:.2f}s "
		f"stats={stats} tables zhou={len(zhou_table)} jun={len(jun_table)} xian={len(xian_table)}"
	)


if __name__ == "__main__":
	main()
