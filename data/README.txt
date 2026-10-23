data — map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 500×500 hex centers (plain=1 / water=0).
  Bbox (Sinica ad0262 crop): lon 94.662–130.0, lat 15.55–42.8 (see scripts/hex.gd).
  ≈6.9 km flat-to-flat. Bake: tools/bake_coast_admin.py --ne … --out data (coast ONLY).

counties_roster.md / counties_roster.json  ← SOURCE OF TRUTH for admin names
  Curated 县名单 for era 184–280 under 东汉十三州 → 郡/国 → 县.
  Baseline: 续汉书·郡国志 / 后汉书 geography (永和五年 / AD 140 names).
  Names + 县→郡→州 membership only.
  Runtime: CountiesRoster looks up parents when a hex has xian_id set.
  郡/州 = union of filled 县 hexes (no separate 州/郡 geometry).

county_seats.json / county_seats_report.json  ← 治所 points (CHGIS-only)
  Matched to CHGIS V6 county seats (year≈140; TK overlap fallback) by name
  (+郡 proximity). No Kongming / no map_factory_anchor seat sources.
  Each seat: xian_id, lon/lat, even-r col/row, confidence, source=CHGIS_V6.
  Bake: tools/match_county_seats.py --cnty … --pref … --out data/county_seats.json
  Runtime: markers in 州/郡/县; labels in 县 mode. Does NOT invent fills.
  Unmatched counties stay out of seats file (see report). No invented coords.

xian_fills.json / xian_fills.bin / xian_fills_report.json  ← 县 hex fills
  CHGIS V6 county points @ year 140 → Thiessen cells clipped to NE land →
  each land hex assigned by center point-in-polygon (500×500; not vector-identical).
  CHGIS has no Han-era county polygon layer; cells are schematic neighbor extents.
  Roster link via county_seats.chgis_sys_id. Unmatched / outside → empty.
  Bake: tools/bake_xian_fills.py --cnty … --ne …
  Runtime: MapFactory.apply_xian_fills. Saves do not sparse-store every fill
  (reapplied on load via build_blank_grid).

admin_zhou.json / admin_jun.json / admin_xian.json / admin_hex.json
  RETIRED stubs (unused_for_rendering=true, empty features / no PIP arrays).
  Runtime never loads these. tools/bake_admin_hex.py exits unless --force-legacy
  (rewrites empty stubs only).

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast + xian_fills).
