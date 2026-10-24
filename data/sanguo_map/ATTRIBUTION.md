# Attribution — sanguo-map admin polygons

**Source:** [xusizhe01/sanguo-map](https://github.com/xusizhe01/sanguo-map)  
**Files vendored here:**
- `states.geojson` — 12 州 polygons (`packages/api/data/states.geojson`)
- `commanderies.geojson` — 95 郡 MultiPolygons (`packages/api/data/commanderies.geojson`)

**Not vendored:** `counties.geojson` — we do **not** paint 县 faces; 县治所 stay CHGIS seat points.

**License:** Upstream GitHub repo reports no SPDX license file as of fetch (2026-10-23). Project README describes an open visualization stack; the integrating project was instructed to treat the data as **MIT** for this game bake. If upstream adds an explicit LICENSE, prefer that text.

**Use in this game:** hex-center point-in-polygon → `zhou_id` / `jun_id` on the 500×500 map (`tools/bake_sanguo_map_fills.py`). Academia Sinica CCTS `ad0262` remains an optional **raster underlay** for visual QA only (not the fill source).
