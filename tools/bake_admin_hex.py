#!/usr/bin/env python3
"""REMOVED from the game pipeline — exits non-zero.

Former tool: point-in-polygon of hex centers vs CHGIS Voronoi admin_* rings
→ data/admin_hex.json. Runtime no longer loads that file.

Admin paint = hand-filled per-hex xian_id + counties_roster parents
(郡/州 = unions of 县 hexes). Do not resurrect Voronoi/PIP.

If you truly need the old archaeology bake, pass --force-legacy (writes
unused_for_rendering stubs only — rings/PIP arrays are not regenerated).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


STUBS = {
	"admin_zhou.json": {
		"tier": "zhou",
		"unused_for_rendering": True,
		"retired": "CHGIS Voronoi dissolve removed; 州 = union of filled 县 hexes",
		"features": [],
		"feature_count": 0,
	},
	"admin_jun.json": {
		"tier": "jun",
		"unused_for_rendering": True,
		"retired": "CHGIS Voronoi removed; 郡 = union of filled 县 hexes",
		"method": "retired_no_voronoi",
		"features": [],
		"feature_count": 0,
	},
	"admin_xian.json": {
		"tier": "xian",
		"unused_for_rendering": True,
		"retired": "CHGIS Voronoi removed; paint by hand-filled xian_id only",
		"method": "retired_no_voronoi",
		"features": [],
		"feature_count": 0,
	},
	"admin_hex.json": {
		"map_w": 160,
		"map_h": 160,
		"unused_for_rendering": True,
		"retired": "PIP vs Voronoi removed; runtime never loads this file",
		"method": "retired_no_pip",
		"zhou_table": [{"id": "", "name": ""}],
		"jun_table": [{"id": "", "name": ""}],
		"xian_table": [{"id": "", "name": ""}],
		"zhou": [],
		"jun": [],
		"xian": [],
		"stats": {"land": 0, "zhou": 0, "jun": 0, "xian": 0},
	},
}


def main() -> int:
	ap = argparse.ArgumentParser(description="Deprecated — refuses Voronoi/PIP bake.")
	ap.add_argument(
		"--force-legacy",
		action="store_true",
		help="Overwrite admin_*.json with empty retired stubs (no geometry).",
	)
	ap.add_argument("--out", default="data", help="Output directory (with --force-legacy)")
	args = ap.parse_args()

	if not args.force_legacy:
		print(
			"ERROR: bake_admin_hex.py is retired.\n"
			"  Do not regenerate Voronoi/PIP admin_hex.\n"
			"  Use hand-filled xian_id + data/counties_roster.json.\n"
			"  Optional: --force-legacy writes empty unused_for_rendering stubs.",
			file=sys.stderr,
		)
		return 2

	out = Path(args.out)
	out.mkdir(parents=True, exist_ok=True)
	for name, obj in STUBS.items():
		path = out / name
		path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
		print(f"wrote stub {path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
