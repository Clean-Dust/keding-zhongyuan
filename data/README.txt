data — map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 160×160 hex centers (plain=1 / water=0).
  Bbox: 敦煌–乐浪–襄平–象林 (see scripts/hex.gd).
  Bake: tools/bake_coast_admin.py (coast only going forward).

counties_roster.md / counties_roster.json  ← CURRENT admin names (step 1)
  Curated 县名单 for era 184–280 under 东汉十三州 → 郡/国 → 县.
  Baseline: 续汉书·郡国志 / 后汉书 geography (永和五年 / AD 140 names).
  Names only — no seats, no hex ranges, no geometry.
  Next (manual): place seats one-by-one → hand-fill hex ranges → 郡/州 = merge of 县.
  Do NOT auto-bake Voronoi / CHGIS admin borders or regenerate admin_hex from PIP.

admin_zhou.json / admin_jun.json / admin_xian.json  ← LEGACY until replaced
  Old CHGIS V6 seats → Voronoi clipped to NE land (schematic).
  Formerly geometry source for per-hex assignment; keep for reference only.
  Academic research license on derived rings — not cadastral survey.

admin_hex.json  ← LEGACY until hand-filled
  Per-hex 州/郡/县 ids from old PIP vs admin_* rings (tools/bake_admin_hex.py).
  Do not re-run PIP bake against Voronoi; replace via manual hex ranges.

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast + admin_hex).
