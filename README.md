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

**州 / 郡 shapes are only unions of hexes whose `xian_id` belongs to that 州/郡** (parents from `data/counties_roster.*`). There is no separate 州/郡 geometry and no Voronoi/PIP fill.

Until county **ranges** are hand-filled, every land hex has empty `xian_id`. Modes **州 / 郡 / 县** then show unpainted gray land (not green 地形, not Voronoi blobs) plus HUD「县范围尚未铺设」. **治所** from `data/county_seats.json` draw as markers (labels in **县** mode); seats never invent territory paint. Mode **地形** is land/sea green/blue as usual.

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
- Sparse hex save; coast reapplied on load; `xian_id` persisted when set (empty until hand-filled)

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
- **Admin names** (source of truth): `data/counties_roster.md` + `data/counties_roster.json` — 续汉书·郡国志 baseline for **184–280**, 东汉十三州 → 郡/国 → 县. Paint uses per-hex `xian_id` + roster parents; **no** Voronoi / CHGIS auto-bake.
- **治所**: `data/county_seats.json` — CHGIS V6 seats matched by name (+郡 proximity), projected to even-r hex; bake via `tools/match_county_seats.py`. Unmatched listed in `county_seats_report.json`.
- **Retired stubs** (never loaded): `admin_*.json` — empty, `unused_for_rendering`. See `data/README.txt`.
- Edge anchors: 敦煌 / 永昌 / 交趾 / 日南 / 象林 / 东冶 / 襄平 / 乐浪 (+ 金城 / 蓟 / 番禺 / 会稽)
- Owned helpers: **会稽** 曹操, **蓟** 袁绍, **金城** 董卓残部

### Zoom band (combat only)

`lod_scale ≤ 0.85` (`CameraLod.Z_HEX`) enters hex micro and pauses strategic time. Admin paint stays on the selected button.

### Re-bake coast (admin borders: manual pipeline only)

```bash
# Coast only. Do NOT regenerate admin_* Voronoi or admin_hex from PIP.
python3 tools/bake_coast_admin.py --ne /path/to/ne_50m_land.shp --out data
```

Admin names: edit `data/counties_roster.*`. Geometry TBD by hand (seats → hex ranges).

## Windows export

Preset file: `export_presets.cfg` (preset **Windows Desktop**) — copy from `export_presets.cfg.example`.

```bash
mkdir -p build
godot --headless --path . --export-release "Windows Desktop" build/克定中原.exe
```

## Lineage

Seeded from `Clean-Dust/qunxiong` `dual_scale/`. New work lives here.
