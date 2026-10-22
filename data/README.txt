dual_scale/data — baked map assets (offline; runtime never downloads)

coast_mask.bin / coast_mask.json
  Natural Earth 50m land → 160×122 hex centers (plain=1 / water=0).
  Bbox: 敦煌–乐浪–襄平–龙编 (see scripts/hex.gd).
  Bake: tools/bake_coast_admin.py

admin_zhou.json / admin_jun.json / admin_xian.json
  Eastern Han (~AD 140) admin for whole Three Kingdoms era (州 always 十三州).
  Source: CHGIS V6 county + prefecture seat points (Harvard / Fudan, 2016),
  Voronoi neighbor extents clipped to NE land; 郡→州 from 续汉书·郡国志 roster.
  All features owner=null (gray). Gaps / unmapped seats documented in JSON.
  Academic research license — derived simplified rings only; not cadastral survey.

debug_save.json — optional legacy seed (runtime prefers MapFactory + coast).
