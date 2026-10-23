data — baked map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 160×160 hex centers (plain=1 / water=0).
  Bbox: 敦煌–乐浪–襄平–象林 (see scripts/hex.gd).
  Bake: tools/bake_coast_admin.py

admin_zhou.json / admin_jun.json / admin_xian.json
  Eastern Han (~AD 140) admin rings (州 always 十三州).
  Source: CHGIS V6 seats → Voronoi clipped to NE land.
  Used as geometry source for per-hex assignment (not drawn as free polygons).

admin_hex.json
  Per-hex 州/郡/县 ids (row-major, tables + indices).
  Method: hex-center point-in-polygon vs admin_* rings.
  Bake: tools/bake_admin_hex.py
  Runtime: MapFactory.apply_admin_to_hexes — visual regions = hex unions.

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast + admin_hex).
