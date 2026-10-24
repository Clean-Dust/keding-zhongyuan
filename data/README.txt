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

zhou_jun_fills.json / zhou_jun_fills.bin / zhou_jun_fills_report.json
  州: CHGIS V4 prov polygons @ year 262 (扬/荆/交 only; none @140), reprojected WGS84.
  郡: CHGIS V6 pref polygons @ year 140 (≈15 SE 郡/国). Hex-center PIP; outside → empty.
  Bake: tools/bake_zhou_jun_fills.py --pref-pgn … --prov-pgn … --prov-prj …
  Runtime: MapFactory.apply_zhou_jun_fills → hex.zhou_id / hex.jun_id.

xian_fills.json / xian_fills.bin
  DISABLED — no Han county polygons in CHGIS; 县 mode = seats only (no guessed 县界).


admin_zhou.json / admin_jun.json / admin_xian.json / admin_hex.json
  RETIRED stubs (unused_for_rendering=true, empty features / no PIP arrays).
  Runtime never loads these. tools/bake_admin_hex.py exits unless --force-legacy
  (rewrites empty stubs only).

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast + xian_fills).

sinica_ad0262_underlay.jpg / sinica_ad0262_underlay.json
  Offline equirectangular bake of Academia Sinica CCTS WMTS layer ad0262
  (三國歷史地圖) for the game bbox. RASTER only — not vector 州/郡.
  Bake: tools/bake_sinica_underlay.py (polite tile fetch + warp).
  Runtime: HexMapView underlay toggle; do not color-pick into hex fills.
