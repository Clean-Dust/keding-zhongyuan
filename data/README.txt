data — map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 160×160 hex centers (plain=1 / water=0).
  Bbox: 敦煌–乐浪–襄平–象林 (see scripts/hex.gd).
  Bake: tools/bake_coast_admin.py (coast only).

counties_roster.md / counties_roster.json  ← SOURCE OF TRUTH for admin names
  Curated 县名单 for era 184–280 under 东汉十三州 → 郡/国 → 县.
  Baseline: 续汉书·郡国志 / 后汉书 geography (永和五年 / AD 140 names).
  Names + 县→郡→州 membership only — no seats, no hex ranges, no geometry.
  Runtime: CountiesRoster looks up parents when a hex has xian_id set.
  Next (manual): place seats → hand-fill hex xian_id → 郡/州 = union of 县 hexes.
  Do NOT auto-bake Voronoi / CHGIS admin borders.

admin_zhou.json / admin_jun.json / admin_xian.json  ← LEGACY, unused for rendering
  Old CHGIS V6 seats → Voronoi clipped to NE land (schematic).
  Flagged unused_for_rendering=true. Kept for reference only — never load for paint.
  Academic research license on derived rings — not cadastral survey.

admin_hex.json  ← LEGACY, unused for rendering
  Old per-hex ids from PIP vs Voronoi rings (tools/bake_admin_hex.py).
  Flagged unused_for_rendering=true. Runtime no longer applies this file.
  Do not re-run PIP bake; replace via manual hex xian_id ranges.

county_seats.json  ← optional, not present yet
  Future hand-placed seats [{xian_id, lon, lat, name?}]. Admin modes may show
  seat marks when this file exists; still no invented county fills.

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast; empty xian_id).
