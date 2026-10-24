# 克定中原

Single-player vs AI desktop slice: **one** pointy-top axial hex map. Top-bar buttons switch **州 / 郡 / 县 / 地形** display; mouse wheel only zooms. Strategic time while zoomed out; auto-pause when zoomed into hex combat. No second battle scene.

**Cloud-first:** code and bake on Cursor Cloud Agents; Mac is for Godot playtest. Godot project lives at **repo root**.

Design authority (project store): `docs/design-freeze.md` / `docs/edge-cities-sanguo.md`.

## Requirements

- Godot **4.4.x** (cloud: `/usr/local/bin/godot`; Mac playtest: local Godot 4.4 app)
- Desktop (Windows / Linux / macOS)

## Run

```bash
# from repo root
godot --path .

# headless smoke
godot --headless --path . --script res://scripts/run_smoke.gd
```

Optional: open `project.godot` in the Godot editor and press Play.

## Controls

| Input | Action |
|-------|--------|
| Top bar **州 / 郡 / 县 / 地形** | Display mode (which admin/terrain paint) |
| Mouse wheel up / down | Zoom in / out only (does **not** change display mode) |
| **左键拖拽** / middle-drag / WASD / arrows | Pan map (primary: left-drag; Esc → 设置 → **鼠标拖拽** sensitivity) |
| LMB short click | Select unit; second click issues move order; near/hex: move / attack |
| RMB | Clear selection |
| Space | End micro turn (AI acts) |
| Esc | Pause menu → **设置…** for sensitivity tiers (1–10); resume / save / load / quit |
| 暂停 / 1x / 2x / 3x | Strategic time (not while in close-up hex band) |

## Display modes

**州 / 郡 / 县** faces digitized from Sinica CCTS `ad0262` (seeded from CHGIS 治所 points in `admin_points.json`). Unit borders where neighbor ids differ. **底图** toggle shows the same Sinica raster for QA. 县 faces = nearest 县治 inside each 郡 (not whole-map Thiessen).

**Points**: 州治 / 郡治 / 县治 markers (distinct shapes). Mode **地形** is land/sea as usual.

Retired stubs: `admin_zhou.json` / `admin_jun.json` / `admin_xian.json` / `admin_hex.json` (`unused_for_rendering`, empty — never loaded).

## Vertical loop

1. Launch framed on the full map (green/blue/gray visible). Start paused — set **1x** to advance days.
2. Issue move orders: select your stack → click destination.
3. When units are adjacent (**接敌**), wheel **in** to micro — time pauses.
4. Select unit, move within `move` hexes or attack adjacent enemy. Occupy empty enemy **city** hex to flip ownership.
5. Wheel **out** — strengths/owners persist; choose a speed to resume time.

## Save / load

- Pause menu **保存** → `user://dual_scale_save.json`
- **读取** loads that file (falls back to `res://data/debug_save.json` if missing)
- Sparse hex save; coast reapplied on load; `xian_id` persisted when set (from CHGIS fill bake)

## Settings (Esc → 设置…)

Sensitivity tiers **1–10** (live apply, saved to `user://dual_scale_input.json`):

| Category | Default | Effect |
|----------|---------|--------|
| 滚轮缩放 | **7** | Mouse-wheel zoom step size |
| 键盘移动 | **3** | WASD / arrow pan (kept gentle) |
| 鼠标拖拽 | **5** | 左键/中键拖拽平移速度 |
| 视野补偿 | **5** | How much pan speeds up when zoomed out (mainly drag) |

## Map / factions (MVP)

- **500 × 500** hexes; pointy-top even-r (双数打横矮半格); ≈6.9 km flat-to-flat
- Geographic bbox (Sinica CCTS `ad0262` screenshot): `lon 94.662…130.0`, `lat 15.55…42.8` (敦煌→朝鲜海缘；象林→高句丽北缘). Peripheral labels (羌胡/发羌/高句丽/夷洲) stay **empty** of 州/郡/县.
- **Coast**: `data/coast_mask.*` — water impassable
- **Admin names** (source of truth): `data/counties_roster.md` + `data/counties_roster.json` — 续汉书·郡国志 baseline for **184–280**, 东汉十三州 → 郡/国 → 县. Paint uses per-hex `xian_id` + roster parents.
- **县 fills**: `data/xian_fills.json` + `data/xian_fills.bin` — bake via `tools/bake_xian_fills.py` (CHGIS@140 hex-center PIP). Coverage stats in `xian_fills_report.json`.
- **治所**: `data/county_seats.json` — **CHGIS V6 only** (no Kongming); bake via `tools/match_county_seats.py`. Unmatched listed in `county_seats_report.json`.
- **Retired stubs** (never loaded): `admin_*.json` — empty, `unused_for_rendering`. See `data/README.txt`.
- **Sinica underlay**: `data/sinica_ad0262_underlay.jpg` — offline bake of CCTS WMTS `ad0262` (三國歷史地圖 **raster**, not vector). Toggle **底图** + opacity in the top bar. Bake: `python3 tools/bake_sinica_underlay.py`. Attribution: Academia Sinica CCTS.
- Edge anchors: 敦煌 / 永昌 / 交趾 / 日南 / 象林 / 东冶 / 襄平 / 乐浪 (+ 金城 / 蓟 / 番禺 / 会稽)
- Owned helpers: **会稽** 曹操, **蓟** 袁绍, **金城** 董卓残部

### Zoom band (combat only)

`lod_scale ≤ 0.85` (`CameraLod.Z_HEX`) enters hex micro and pauses strategic time. Admin paint stays on the selected button.

### Re-bake coast / 县 fills

```bash
# Coast only (no admin_* rings).
python3 tools/bake_coast_admin.py --ne /path/to/ne_50m_land.shp --out data

# CHGIS-only 治所, then hex fills (year 140 county points → Thiessen → hex-center PIP).
python3 tools/match_county_seats.py \
  --cnty /tmp/chgis/v6_time_cnty_pts_utf_wgs84.shp \
  --pref /tmp/chgis/v6_time_pref_pts_utf_wgs84.shp
python3 tools/bake_xian_fills.py \
  --cnty /tmp/chgis/v6_time_cnty_pts_utf_wgs84.shp \
  --ne /tmp/ne/ne_50m_land.shp
```

Admin names: edit `data/counties_roster.*`. 郡/州 = unions of filled 县; do not invent separate 州/郡 boundaries.

## Windows export

Preset file: `export_presets.cfg` (preset **Windows Desktop**) — copy from `export_presets.cfg.example`.

```bash
mkdir -p build
godot --headless --path . --export-release "Windows Desktop" build/克定中原.exe
```

## Lineage

Seeded from `Clean-Dust/qunxiong` `dual_scale/`. New work lives here.
