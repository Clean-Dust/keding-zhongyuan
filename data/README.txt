data — map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 240×240 hex centers (plain=1 / water=0).
  Bbox (Sinica ad0262 crop): lon 94.662–130.0, lat 15.55–42.8 (see scripts/hex.gd).
  ≈14.5 km flat-to-flat. Bake: tools/bake_coast_admin.py --ne … --out data (coast ONLY).

counties_roster.md / counties_roster.json  ← SOURCE OF TRUTH for admin names
  Curated 县名单 for era 184–280 under 东汉十三州 → 郡/国 → 县.
  Baseline: 续汉书·郡国志 / 后汉书 geography (永和五年 / AD 140 names).
  Names + 县→郡→州 membership only — no hex ranges.
  Runtime: CountiesRoster looks up parents when a hex has xian_id set.
  Next (manual): hand-fill hex xian_id → 郡/州 = union of 县 hexes.
  Do NOT auto-bake Voronoi / CHGIS admin borders.

county_seats.json / county_seats_report.json  ← step 2: 治所 points
  Matched to CHGIS V6 county seats (year≈140; TK overlap fallback) by name
  (+郡 proximity); a few MapFactory anchors fill CHGIS gaps (蓟/襄平/象林).
  Each seat: xian_id, lon/lat, even-r col/row, confidence, source.
  Bake: tools/match_county_seats.py --cnty … --pref … --out data/county_seats.json
  Runtime: markers in 州/郡/县; labels in 县 mode. Does NOT set xian_id fills.
  Unmatched counties stay out of seats file (see report). No invented coords.

admin_zhou.json / admin_jun.json / admin_xian.json / admin_hex.json
  RETIRED stubs (unused_for_rendering=true, empty features / no PIP arrays).
  Former CHGIS Voronoi + hex PIP paint has been removed from the repo.
  Runtime never loads these. tools/bake_admin_hex.py exits unless --force-legacy
  (rewrites empty stubs only).

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast; empty xian_id).
