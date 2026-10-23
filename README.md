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
| WASD / arrows / middle-drag | Pan |
| LMB | Select unit; second click issues move order; near/hex: move / attack |
| RMB | Clear selection |
| Space | End micro turn (AI acts) |
| Esc | Pause menu → **设置…** for sensitivity tiers (1–10); resume / save / load / quit |
| 暂停 / 1x / 2x / 3x | Strategic time (not while in close-up hex band) |

## Display modes

Admin regions are **unions of hexes** with the same id (not free polygon overlays). Each land hex has `zhou_id` / `jun_id` / `xian_id` from baked point-in-polygon on hex centers (`data/admin_hex.json`).

| Mode | Paint |
|------|--------|
| **州** | Hex fills tinted by 州; unassigned land gray; sea blue; 州 labels |
| **郡** | Same by 郡/国 |
| **县** | Same by 县 |
| **地形** | Land green / sea blue (city slightly brighter); hex borders clearer |

## Vertical loop

1. Launch framed on the full map (green/blue/gray visible). Start paused — set **1x** to advance days.
2. Issue move orders: select your stack → click destination.
3. When units are adjacent (**接敌**), wheel **in** to micro — time pauses.
4. Select unit, move within `move` hexes or attack adjacent enemy. Occupy empty enemy **city** hex to flip ownership.
5. Wheel **out** — strengths/owners persist; choose a speed to resume time.

## Save / load

- Pause menu **保存** → `user://dual_scale_save.json`
- **读取** loads that file (falls back to `res://data/debug_save.json` if missing)
- Sparse hex save; coast + admin ids reapplied on load

## Settings (Esc → 设置…)

Sensitivity tiers **1–10** (live apply, saved to `user://dual_scale_input.json`):

| Category | Default | Effect |
|----------|---------|--------|
| 滚轮缩放 | **7** | Mouse-wheel zoom step size |
| 键盘移动 | **3** | WASD / arrow pan (kept gentle) |
| 鼠标拖拽 | **5** | Middle-mouse drag pan |
| 视野补偿 | **5** | How much pan speeds up when zoomed out (mainly drag) |

## Map / factions (MVP)

- **160 × 160** hexes; pointy-top even-r (双数打横矮半格)
- Geographic bbox: `lon 94.662…125.69`, `lat 15.55…41.272` (象林→襄平)
- **Coast**: `data/coast_mask.*` — water impassable
- **Admin**: 东汉十三州 + 郡/县 rings → per-hex ids in `admin_hex.json` (`tools/bake_admin_hex.py`)
- Edge anchors: 敦煌 / 永昌 / 交趾 / 日南 / 象林 / 东冶 / 襄平 / 乐浪 (+ 金城 / 蓟 / 番禺 / 会稽)
- Owned helpers: **会稽** 曹操, **蓟** 袁绍, **金城** 董卓残部

### Zoom band (combat only)

`lod_scale ≤ 0.85` (`CameraLod.Z_HEX`) enters hex micro and pauses strategic time. Admin paint stays on the selected button.

### Re-bake

```bash
# Coast + admin rings (needs NE + CHGIS shapefiles)
python3 tools/bake_coast_admin.py --ne … --cnty … --pref … --out data

# Per-hex admin ids from existing admin_*.json + coast
python3 tools/bake_admin_hex.py
```

## Windows export

Preset file: `export_presets.cfg` (preset **Windows Desktop**) — copy from `export_presets.cfg.example`.

```bash
mkdir -p build
godot --headless --path . --export-release "Windows Desktop" build/克定中原.exe
```

## Lineage

Seeded from `Clean-Dust/qunxiong` `dual_scale/`. New work lives here.
