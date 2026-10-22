# 克定中原

Single-player vs AI desktop slice: **one** pointy-top axial hex map. Wheel zooms **州 → 郡/国 → 县 → hex**; strategic time on admin LODs, auto-pause on HEX (war chess). No second battle scene.

**Cloud-first:** code and bake on Cursor Cloud Agents; Mac is for Godot playtest / local push when cloud GitHub App rights are missing. Godot project lives at **repo root** (not nested `dual_scale/`).

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
| Mouse wheel up / down | Zoom **in** / out (州↔郡↔县↔hex) |
| WASD / arrows / middle-drag | Pan |
| LMB | Select unit; admin LODs: second click issues move order; hex: move / attack |
| RMB | Clear selection |
| Space | End micro turn (AI acts) |
| Esc | Pause menu → **设置…** for sensitivity tiers (1–10); resume / save / load / quit |
| 暂停 / 1x / 2x / 3x | Strategic time (州/郡/县 only) |

## Vertical loop

1. Start paused in macro — set **1x** to advance days (food/gold from owned cities; AI marches toward enemy cities).
2. Issue move orders: select your stack → click destination.
3. When units are adjacent (**接敌**), wheel **in** to micro — time pauses.
4. Select unit, move within `move` hexes or attack adjacent enemy (`damage = attack`). Occupy empty enemy **city** hex to flip ownership.
5. Wheel **out** — strengths/owners persist; choose a speed to resume time.

## Save / load

- Pause menu **保存** → `user://dual_scale_save.json`
- **读取** loads that file (falls back to `res://data/debug_save.json` if missing)
- Full `WorldState` JSON round-trip: factions, hexes, units, time

## Settings (Esc → 设置…)

Sensitivity tiers **1–10** (live apply, saved to `user://dual_scale_input.json`):

| Category | Default | Effect |
|----------|---------|--------|
| 滚轮缩放 | **7** | Mouse-wheel zoom step size |
| 键盘移动 | **3** | WASD / arrow pan (kept gentle) |
| 鼠标拖拽 | **5** | Middle-mouse drag pan |
| 视野补偿 | **5** | How much pan speeds up when zoomed out (mainly drag) |

## Map / factions (MVP)

- **160 × 160** hexes (25,600 cells); pointy-top; **even horizontal rows offset by half a hex** (even-r / 双数打横矮半格)
- Geographic bbox: `lon 94.662…125.69`, `lat 15.55…41.272` (象林→襄平; anchors include **日南** 16.60,106.68 and **象林** 15.55,108.05)
- Fixed square grid — lon/lat linear onto WIDTH×HEIGHT (aspect-derived height retired)
- Offset cols `0..159`, rows `0..159` (row 0 = north) → axial for logic; pixel via `q + r/2`
- **Coast**: Natural Earth 50m land baked to `data/coast_mask.*` — land green / sea blue; **water impassable**. No rivers/mountains.
- **Admin** (all `owner=null`, gray fills; ocean stays blue):
  - **州** = 东汉十三州 (representative for the whole Three Kingdoms period)
  - **郡/国** / **县** from CHGIS V6 seats @ ~AD 140 → Voronoi clipped to land (schematic; see `data/README.txt`)
  - Files: `admin_zhou.json`, `admin_jun.json`, `admin_xian.json`
- Edge anchors: **敦煌 / 永昌 / 交趾 / 日南 / 象林 / 东冶 / 襄平 / 乐浪** (+ helpers 金城 / 蓟 / 番禺 / 会稽); fewer labels on 州 LOD
- Owned helpers seed factions: **会稽** 曹操, **蓟** 袁绍, **金城** 董卓残部
- Saves store sparse non-default hexes only (coast reapplied on load)

### Zoom LOD (`lod_scale`, larger = farther)

| LOD | `lod_scale` | Draw | Hide |
|-----|-------------|------|------|
| ZHOU | `> 5.0` | gray 州 fills, borders, names | hex grid, jun, xian |
| JUN | `≤ 5.0` | gray 郡/国 | hex grid, xian |
| XIAN | `≤ 2.2` | gray 县 | hex grid |
| HEX | `≤ 0.85` | land/sea hex + grid lines | solid 州 color blocks |

Constants: `CameraLod.Z_JUN=5.0`, `Z_XIAN=2.2`, `Z_HEX=0.85`.

### Re-bake coast / admin

```bash
# Needs NE 50m land + CHGIS V6 county/pref point shapefiles locally
python3 tools/bake_coast_admin.py \
  --ne /path/to/ne_50m_land.shp \
  --cnty /path/to/v6_time_cnty_pts_utf_wgs84.shp \
  --pref /path/to/v6_time_pref_pts_utf_wgs84.shp \
  --out data
```

## Windows export

Preset file: `export_presets.cfg` (preset **Windows Desktop**) — copy from `export_presets.cfg.example`.

1. Install Godot 4.4 export templates (Editor → Manage Export Templates).
2. From repo root:

```bash
mkdir -p build
godot --headless --path . --export-release "Windows Desktop" build/克定中原.exe
```

3. Ship the `.exe` (PCK embedded in preset). First Windows run may need “More info → Run anyway” if unsigned.

## Acceptance checklist

- [x] Wheel zooms 州→郡→县→hex without resetting world state
- [x] Coastline land/sea; water impassable; admin gray unowned
- [x] Admin LODs: time speeds, resources, move orders, AI advance, contact
- [x] HEX: turn move/attack, city ownership flip, shared WorldState
- [x] Zoom out keeps results; time can resume
- [x] UI: resources, day, pause/speed, end turn, pause menu save/load/quit

## Lineage

Seeded from `Clean-Dust/qunxiong` branch `cursor/dual-scale-mvp-1f99` path `dual_scale/` (coast + admin LOD + sensitivity). New work lives here.

## Cloud verify

Cursor GitHub App push check: 2026-10-22.
