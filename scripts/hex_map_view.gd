extends Node2D
## Hex-based map draw. Display mode (州/郡/县/地形) is UI-driven — not zoom.
## Phase pipeline: admin POINTS always drawn (州治/郡治/县治). Faces when baked.
## 底图 = Sinica CCTS ad0262 raster QA (not fill source).

const HEX_SIZE := 36.0
const HEX_DRAW := HEX_SIZE * 0.96
const VIEW_REDRAW_EPS_SQ := 4.0
const PLAINS_GREEN := Color(0.32, 0.48, 0.30)
const SEA_BLUE := Color(0.20, 0.40, 0.58)
const CITY_GREEN := Color(0.38, 0.52, 0.34)
const UNASSIGNED_GRAY := Color(0.52, 0.52, 0.50)
const ADMIN_BORDER := Color(0.12, 0.10, 0.08, 0.85)
const ADMIN_NAME := Color(0.10, 0.10, 0.08, 0.92)
const HINT_COLOR := Color(0.12, 0.12, 0.10, 0.88)
const MARK_ZHOU := Color(0.75, 0.15, 0.12, 0.95)
const MARK_JUN := Color(0.12, 0.22, 0.55, 0.92)
const MARK_XIAN := Color(0.12, 0.12, 0.10, 0.88)
const DRAW_MAP_MARKERS := false
const EMPTY_ADMIN_HINT := "本区无行政面；点标记 + 底图可对照"
const ADMIN_POINTS_PATH := "res://data/admin_points.json"

## Display modes (buttons): zhou | jun | xian | terrain
const MODE_ZHOU := "zhou"
const MODE_JUN := "jun"
const MODE_XIAN := "xian"
const MODE_TERRAIN := "terrain"

const EXTREME_ANCHOR_IDS := {
	"dunhuang": true, "yongchang": true, "jiaozhi": true,
	"dongye": true, "xiangping": true, "lelang": true,
}

var world: WorldState
var display_mode: String = MODE_ZHOU
var hover_cell: Vector2i = Vector2i(9999, 9999)

var _corner_off: PackedVector2Array = PackedVector2Array()
var _scratch_pts: PackedVector2Array = PackedVector2Array()
var _fill_hex: PackedColorArray = PackedColorArray()
var _highlight_set: Dictionary = {}
var _faction_colors: Dictionary = {}
var _cam: Camera2D
var _last_draw_cam_pos: Vector2 = Vector2(INF, INF)
var _last_draw_lod_scale: float = -1.0
var _redraw_scheduled: bool = false

## Label entries for current admin mode: {name, pos: Vector2}
var _admin_labels: Array = []
## True when current mode has something to paint (州/郡 polygon fills; never 县).
var _has_admin_fills: bool = false
## Admin points: {level, name, pos} from data/admin_points.json (fallback county_seats).
var _admin_points: Array = []
## Sinica CCTS ad0262 raster underlay (offline bake — not vector admin).
var _sinica_tex: Texture2D
var _sinica_enabled: bool = false
var _sinica_opacity: float = 0.55

@onready var labels_root: Node2D = $Labels


func setup(ws: WorldState) -> void:
	world = ws
	CountiesRoster.ensure_loaded()
	_load_sinica_underlay()
	_init_geometry()
	_rebuild_faction_colors()
	_rebuild_color_caches()
	_rebuild_highlight_set()
	_rebuild_admin_labels()
	_rebuild_admin_points()
	if not world.changed.is_connected(_on_world_changed):
		world.changed.connect(_on_world_changed)
	_cam = get_viewport().get_camera_2d()
	_rebuild_pref_labels()
	queue_redraw()


func _load_sinica_underlay() -> void:
	_sinica_tex = null
	var path := "res://data/sinica_ad0262_underlay.jpg"
	if not FileAccess.file_exists(path):
		push_warning("sinica underlay missing — toggle will no-op")
		return
	var abs_path := ProjectSettings.globalize_path(path)
	var img := Image.load_from_file(abs_path)
	if img == null or img.is_empty():
		push_warning("sinica underlay failed to load")
		return
	_sinica_tex = ImageTexture.create_from_image(img)


func set_sinica_underlay(enabled: bool) -> void:
	_sinica_enabled = enabled and _sinica_tex != null
	_rebuild_color_caches()
	queue_redraw()


func set_sinica_opacity(opacity: float) -> void:
	_sinica_opacity = clampf(opacity, 0.0, 1.0)
	_rebuild_color_caches()
	queue_redraw()


func has_sinica_underlay() -> bool:
	return _sinica_tex != null


func is_sinica_underlay_enabled() -> bool:
	return _sinica_enabled


func bind_camera(cam: CameraLod) -> void:
	_cam = cam
	if not cam.view_changed.is_connected(_on_view_changed):
		cam.view_changed.connect(_on_view_changed)


func set_display_mode(mode: String) -> void:
	if mode != MODE_ZHOU and mode != MODE_JUN and mode != MODE_XIAN and mode != MODE_TERRAIN:
		mode = MODE_ZHOU
	if display_mode == mode:
		return
	display_mode = mode
	_rebuild_color_caches()
	_rebuild_admin_labels()
	_rebuild_admin_points()
	_rebuild_pref_labels()
	queue_redraw()


## Legacy alias — zoom no longer drives admin paint.
func set_lod(_new_lod: String) -> void:
	pass


## True when 州/郡 polygon fills exist (县 mode always false — seats only).
func has_xian_fills() -> bool:
	## True when 县 mode has face paint (not merely seats).
	if world == null or display_mode != MODE_XIAN:
		return false
	for h in world.hexes:
		if str(h.get("xian_id", "")) != "":
			return true
	return false


func has_admin_fills() -> bool:
	return _has_admin_fills


func set_hover(cell: Vector2i) -> void:
	if hover_cell == cell:
		return
	hover_cell = cell
	queue_redraw()


func _on_world_changed() -> void:
	_rebuild_faction_colors()
	_rebuild_color_caches()
	_rebuild_highlight_set()
	_rebuild_admin_labels()
	_rebuild_admin_points()
	_rebuild_pref_labels()
	queue_redraw()


func _on_view_changed() -> void:
	if _redraw_scheduled:
		return
	_redraw_scheduled = true
	call_deferred("_flush_view_redraw")


func _flush_view_redraw() -> void:
	_redraw_scheduled = false
	if _cam == null:
		queue_redraw()
		return
	var pos := _cam.global_position
	var scale := 1.0 / maxf(_cam.zoom.x, 0.001)
	var zoom_changed := not is_equal_approx(scale, _last_draw_lod_scale)
	if zoom_changed:
		_sync_anchor_label_zoom()
	if pos.distance_squared_to(_last_draw_cam_pos) < VIEW_REDRAW_EPS_SQ and not zoom_changed:
		return
	queue_redraw()


func _current_lod_scale() -> float:
	if _cam is CameraLod:
		return maxf((_cam as CameraLod).lod_scale, 0.35)
	if _cam != null:
		return 1.0 / maxf(_cam.zoom.x, 0.001)
	return 1.0


func _world_font_px(screen_px: float) -> int:
	return maxi(10, int(round(screen_px * _current_lod_scale())))


func _init_geometry() -> void:
	_corner_off = Hex.hex_corners(Vector2.ZERO, HEX_DRAW)
	_scratch_pts.resize(6)
	for i in 6:
		_scratch_pts[i] = _corner_off[i]


func _rebuild_faction_colors() -> void:
	_faction_colors.clear()
	if world == null:
		return
	for f in world.factions:
		_faction_colors[str(f.id)] = Color.html(str(f.get("color", "#888888")))


func _rebuild_highlight_set() -> void:
	_highlight_set.clear()
	if world == null:
		return
	for c in world.highlight_hexes:
		_highlight_set[Hex.key(c.x, c.y)] = true


func _admin_id_of(h: Dictionary) -> String:
	match display_mode:
		MODE_ZHOU:
			return str(h.get("zhou_id", ""))
		MODE_JUN:
			return str(h.get("jun_id", ""))
		MODE_XIAN:
			return str(h.get("xian_id", ""))
		_:
			return ""


func _admin_name_of(h: Dictionary) -> String:
	var aid := _admin_id_of(h)
	if display_mode == MODE_XIAN and aid != "":
		return CountiesRoster.name_of(aid)
	return aid


func _tint_for_admin_id(aid: String) -> Color:
	if aid == "":
		return UNASSIGNED_GRAY
	## Stable muted HSV from id hash.
	var hv: int = absi(int(hash(aid)))
	var hue := float(hv % 360) / 360.0
	var sat := 0.22 + float((hv / 360) % 40) / 200.0
	var val := 0.55 + float((hv / 14400) % 30) / 150.0
	return Color.from_hsv(hue, sat, val)


func _terrain_color(h: Dictionary) -> Color:
	var terrain := str(h.get("terrain", "plain"))
	if terrain == "water":
		return SEA_BLUE
	return CITY_GREEN if terrain == "city" else PLAINS_GREEN


func _underlay_land_alpha() -> float:
	## Let Sinica raster show through land fills when underlay is on.
	if not _sinica_enabled or _sinica_opacity <= 0.05:
		return 1.0
	return clampf(1.0 - _sinica_opacity * 0.72, 0.18, 1.0)


func _color_for_hex(h: Dictionary) -> Color:
	var terrain := str(h.get("terrain", "plain"))
	if terrain == "water":
		return SEA_BLUE
	var a := _underlay_land_alpha()
	if display_mode == MODE_TERRAIN:
		var tc := _terrain_color(h)
		tc.a = a
		return tc
	if not _has_admin_fills:
		var g2 := UNASSIGNED_GRAY
		g2.a = a * 0.45
		return g2
	var aid := _admin_id_of(h)
	if aid == "":
		var g3 := UNASSIGNED_GRAY
		g3.a = a * 0.45
		return g3
	var tint := _tint_for_admin_id(aid)
	tint.a = clampf(a + 0.15, 0.25, 1.0)
	return tint


func _rebuild_color_caches() -> void:
	if world == null:
		return
	_has_admin_fills = false
	if display_mode == MODE_ZHOU or display_mode == MODE_JUN or display_mode == MODE_XIAN:
		for h in world.hexes:
			if str(h.get("terrain", "")) == "water":
				continue
			if _admin_id_of(h) != "":
				_has_admin_fills = true
				break
	var n := world.hexes.size()
	_fill_hex.resize(n)
	if display_mode == MODE_TERRAIN or not _has_admin_fills:
		for i in n:
			_fill_hex[i] = _color_for_hex(world.hexes[i])
		return
	for i in n:
		_fill_hex[i] = _color_for_hex(world.hexes[i])


func _rebuild_admin_labels() -> void:
	_admin_labels.clear()
	if world == null or display_mode == MODE_TERRAIN or not _has_admin_fills:
		return
	## Skip dense 县 name centroids — points carry 县 labels.
	if display_mode == MODE_XIAN:
		return
	## Accumulate pixel centroids per 州/郡 id.
	var acc: Dictionary = {} ## id -> {name, sum: Vector2, n: int}
	for h in world.hexes:
		if str(h.get("terrain", "")) == "water":
			continue
		var aid := _admin_id_of(h)
		if aid == "":
			continue
		var cell := Vector2i(int(h.q), int(h.r))
		var px := Hex.to_pixel(cell, HEX_SIZE)
		if not acc.has(aid):
			acc[aid] = {"name": _admin_name_of(h), "sum": px, "n": 1}
		else:
			var e: Dictionary = acc[aid]
			e.sum = e.sum + px
			e.n = int(e.n) + 1
	for aid in acc.keys():
		var e2: Dictionary = acc[aid]
		var n2: int = int(e2.n)
		if n2 <= 0:
			continue
		_admin_labels.append({
			"name": str(e2.name),
			"pos": e2.sum / float(n2),
		})


func _rebuild_admin_points() -> void:
	_admin_points.clear()
	var rows: Array = []
	if FileAccess.file_exists(ADMIN_POINTS_PATH):
		var txt := FileAccess.get_file_as_string(ADMIN_POINTS_PATH)
		var parsed: Variant = JSON.parse_string(txt)
		if typeof(parsed) == TYPE_DICTIONARY:
			var root: Dictionary = parsed
			for key in ["zhou", "jun", "xian"]:
				var arr: Variant = root.get(key, [])
				if typeof(arr) == TYPE_ARRAY:
					for row_any in arr:
						if typeof(row_any) == TYPE_DICTIONARY:
							rows.append(row_any)
	if rows.is_empty():
		## Fallback: county seats only as 县.
		for row_any in CountiesRoster.load_seats():
			if typeof(row_any) == TYPE_DICTIONARY:
				var r: Dictionary = row_any
				r["level"] = "xian"
				rows.append(r)
	for row_any in rows:
		var row: Dictionary = row_any
		var level := str(row.get("level", "xian"))
		## Mode filter: 州 shows 州治(+郡治 faint); 郡 shows 郡治; 县 shows 县治.
		if display_mode == MODE_ZHOU and level == "xian":
			continue
		if display_mode == MODE_JUN and level == "xian":
			continue
		if display_mode == MODE_XIAN and level != "xian":
			continue
		if display_mode == MODE_TERRAIN:
			continue
		var px: Vector2
		if row.has("col") and row.has("row"):
			var ax := Hex.offset_to_axial(int(row.col), int(row.row))
			px = Hex.to_pixel(ax, HEX_SIZE)
		elif row.has("lon") and row.has("lat"):
			px = Hex.lonlat_to_pixel(float(row.lon), float(row.lat), HEX_SIZE)
		else:
			continue
		var nm := str(row.get("name", ""))
		if nm == "" and level == "zhou":
			nm = str(row.get("zhou", ""))
		_admin_points.append({"pos": px, "name": nm, "level": level})


func _rebuild_pref_labels() -> void:
	for c in labels_root.get_children():
		c.queue_free()
	if world == null:
		return
	## Edge anchors only on 州 / 地形 (reduce clutter on 郡/县).
	## When no 县 hexes are filled, hide anchors in admin modes so empty state is obvious.
	if display_mode == MODE_JUN or display_mode == MODE_XIAN:
		return
	if display_mode != MODE_TERRAIN and not _has_admin_fills:
		return
	var s := _current_lod_scale()
	for h in world.hexes:
		if not bool(h.get("is_anchor", false)):
			continue
		var pid := str(h.get("prefecture_id", ""))
		if display_mode == MODE_ZHOU and not EXTREME_ANCHOR_IDS.has(pid):
			continue
		var cell := Vector2i(int(h.q), int(h.r))
		var pos := Hex.to_pixel(cell, HEX_SIZE)
		var holder := Node2D.new()
		holder.position = pos
		holder.scale = Vector2(s, s)
		holder.set_meta("anchor_zoom_holder", true)
		labels_root.add_child(holder)
		var mark := Polygon2D.new()
		mark.polygon = PackedVector2Array([
			Vector2(0, -4), Vector2(4, 3), Vector2(-4, 3),
		])
		mark.color = Color(0.12, 0.14, 0.10, 0.85)
		holder.add_child(mark)
		var lab := Label.new()
		lab.text = str(h.get("prefecture_name", ""))
		lab.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		lab.add_theme_font_size_override("font_size", 14)
		lab.add_theme_color_override("font_color", Color(0.92, 0.90, 0.82, 0.95))
		lab.add_theme_color_override("font_outline_color", Color(0.05, 0.08, 0.04, 0.9))
		lab.add_theme_constant_override("outline_size", 3)
		lab.position = Vector2(-40, 5)
		lab.size = Vector2(80, 22)
		holder.add_child(lab)


func _sync_anchor_label_zoom() -> void:
	if labels_root == null:
		return
	var s := _current_lod_scale()
	var v := Vector2(s, s)
	for c in labels_root.get_children():
		if c is Node2D and bool(c.get_meta("anchor_zoom_holder", false)):
			(c as Node2D).scale = v


func cell_at_global(global_pos: Vector2) -> Vector2i:
	return Hex.from_pixel(to_local(global_pos), HEX_SIZE)


func _visible_world_rect() -> Rect2:
	if _cam == null:
		_cam = get_viewport().get_camera_2d()
	if _cam == null:
		return Hex.map_pixel_rect(HEX_SIZE, world.map_w if world else Hex.MAP_W, world.map_h if world else Hex.MAP_H)
	var vp := get_viewport().get_visible_rect().size
	var half := vp * 0.5 / _cam.zoom
	var center := _cam.global_position
	var pad := HEX_SIZE * 2.0
	return Rect2(center - half - Vector2(pad, pad), half * 2.0 + Vector2(pad * 2.0, pad * 2.0))


func _draw() -> void:
	if world == null:
		return
	if _cam != null:
		_last_draw_cam_pos = _cam.global_position
		_last_draw_lod_scale = 1.0 / maxf(_cam.zoom.x, 0.001)
	var vis := _visible_world_rect()
	## Full-map sea underlay so off-hex edges stay blue (not clear-color black).
	draw_rect(Hex.map_pixel_rect(HEX_SIZE, world.map_w, world.map_h), SEA_BLUE)
	## Sinica CCTS ad0262 raster (georeferenced to Hex bbox) — not vector admin.
	_draw_sinica_underlay()
	_draw_hex_layer(vis)
	_draw_admin_names(vis)
	_draw_admin_points(vis)
	_draw_empty_admin_hint(vis)
	if DRAW_MAP_MARKERS and world.path_preview.size() >= 2:
		for i in range(world.path_preview.size() - 1):
			var a := Hex.to_pixel(world.path_preview[i], HEX_SIZE)
			var b := Hex.to_pixel(world.path_preview[i + 1], HEX_SIZE)
			draw_line(a, b, Color(1, 0.92, 0.55, 0.85), 3.0, false)
	if DRAW_MAP_MARKERS:
		_draw_units()


func _draw_sinica_underlay() -> void:
	if not _sinica_enabled or _sinica_tex == null or _sinica_opacity <= 0.01:
		return
	## Align texture corners to game equirectangular bbox (same as Hex.lonlat_to_*).
	var tl := Hex.lonlat_to_pixel(Hex.LON_MIN, Hex.LAT_MAX, HEX_SIZE)
	var br := Hex.lonlat_to_pixel(Hex.LON_MAX, Hex.LAT_MIN, HEX_SIZE)
	var rect := Rect2(tl, br - tl)
	draw_texture_rect(_sinica_tex, rect, false, Color(1, 1, 1, _sinica_opacity))


func _draw_empty_admin_hint(vis: Rect2) -> void:
	## World-space backup; primary cue is HUD EmptyAdminHint (screen-fixed).
	## 县: always seats-only (no county faces). 州/郡: hint only if bake empty.
	if display_mode == MODE_TERRAIN:
		return
	if display_mode != MODE_XIAN and _has_admin_fills:
		return
	var font_size := _world_font_px(22.0)
	var center := vis.get_center()
	var msg := "县治所标记；无县域面" if display_mode == MODE_XIAN else EMPTY_ADMIN_HINT
	draw_string(
		ThemeDB.fallback_font,
		center + Vector2(-float(font_size) * 3.5, float(font_size) * 0.35),
		msg,
		HORIZONTAL_ALIGNMENT_LEFT,
		-1,
		font_size,
		HINT_COLOR
	)


func _draw_admin_points(vis: Rect2) -> void:
	if display_mode == MODE_TERRAIN or _admin_points.is_empty():
		return
	var show_labels := true
	var font_size := _world_font_px(12.0)
	var label_budget := 200 if display_mode == MODE_XIAN else 80
	var named := 0
	for pt in _admin_points:
		var pos: Vector2 = pt.pos
		if not vis.has_point(pos):
			continue
		var level: String = str(pt.get("level", "xian"))
		var col: Color = MARK_XIAN
		var r := HEX_SIZE * 0.10
		if level == "zhou":
			col = MARK_ZHOU
			r = HEX_SIZE * 0.22
		elif level == "jun":
			col = MARK_JUN
			r = HEX_SIZE * 0.15
		## Distinct shapes: 州 = square, 郡 = diamond, 县 = circle.
		if level == "zhou":
			var hs := r * 0.85
			draw_colored_polygon(
				PackedVector2Array([
					pos + Vector2(-hs, -hs), pos + Vector2(hs, -hs),
					pos + Vector2(hs, hs), pos + Vector2(-hs, hs),
				]),
				col
			)
		elif level == "jun":
			draw_colored_polygon(
				PackedVector2Array([
					pos + Vector2(0, -r), pos + Vector2(r, 0),
					pos + Vector2(0, r), pos + Vector2(-r, 0),
				]),
				col
			)
		else:
			draw_circle(pos, r, col)
		if not show_labels:
			continue
		var nm: String = str(pt.get("name", ""))
		if nm == "" or named >= label_budget:
			continue
		## In 州 mode label 州治; in 郡 mode label 郡; in 县 mode label 县.
		if display_mode == MODE_ZHOU and level != "zhou":
			continue
		if display_mode == MODE_JUN and level != "jun":
			continue
		named += 1
		draw_string(
			ThemeDB.fallback_font,
			pos + Vector2(6, 4),
			nm,
			HORIZONTAL_ALIGNMENT_LEFT,
			-1,
			font_size,
			ADMIN_NAME
		)


func _draw_admin_names(vis: Rect2) -> void:
	if display_mode == MODE_TERRAIN or _admin_labels.is_empty():
		return
	var screen_px := 34.0 if display_mode == MODE_ZHOU else (22.0 if display_mode == MODE_JUN else 16.0)
	var font_size := _world_font_px(screen_px)
	var budget := 40 if display_mode == MODE_ZHOU else (90 if display_mode == MODE_JUN else 140)
	var pad := float(font_size) * 2.0
	var named := 0
	var grown := vis.grow(pad)
	for lab in _admin_labels:
		if named >= budget:
			break
		var pos: Vector2 = lab.pos
		if not grown.has_point(pos):
			continue
		var nm: String = str(lab.name)
		if display_mode == MODE_XIAN and nm.length() > 4:
			continue
		draw_string(
			ThemeDB.fallback_font,
			pos + Vector2(-float(font_size) * 0.55, float(font_size) * 0.35),
			nm,
			HORIZONTAL_ALIGNMENT_LEFT,
			-1,
			font_size,
			ADMIN_NAME
		)
		named += 1


func _draw_hex_layer(vis: Rect2) -> void:
	var w := world.map_w
	var h := world.map_h
	var bounds := _visible_offset_bounds(vis, w, h)
	var sx := HEX_SIZE * sqrt(3.0)
	var sy := HEX_SIZE * 1.5
	var vis_l := vis.position.x
	var vis_r := vis.end.x
	var vis_t := vis.position.y
	var vis_b := vis.end.y
	var lod_s := _current_lod_scale()
	## 500² mitigations: far zoom → rects; skip sea (underlay already blue);
	## when framed full-map would draw ~250k cells, step the grid.
	## 州/郡: draw borders only on edges where neighbor admin id differs.
	var use_rects := lod_s >= 2.5
	var border_w := 1.6 if display_mode == MODE_ZHOU else 1.2
	var cols_v := bounds.z - bounds.x + 1
	var rows_v := bounds.w - bounds.y + 1
	var vis_cells := cols_v * rows_v
	var step := 1
	if vis_cells > 35000:
		step = 2
	if vis_cells > 90000:
		step = 3
	if vis_cells > 160000:
		step = 4
	## Unit borders need full resolution — skip when stepped / far zoom.
	var show_unit_borders := (
		step == 1 and lod_s < 2.5 and _has_admin_fills
		and (display_mode == MODE_ZHOU or display_mode == MODE_JUN)
	)
	var cell_w := sx * float(step)
	var cell_h := sy * float(step)
	for row in range(bounds.y, bounds.w + 1, step):
		var x_bias := 0.0 if (row & 1) == 0 else -0.5 * sx
		var cy := sy * float(row)
		if cy < vis_t - HEX_SIZE or cy > vis_b + HEX_SIZE:
			continue
		for col in range(bounds.x, bounds.z + 1, step):
			var cx := sx * float(col) + x_bias
			if cx < vis_l or cx > vis_r:
				continue
			var idx := row * w + col
			if idx < 0 or idx >= _fill_hex.size():
				continue
			var fill: Color = _fill_hex[idx]
			## Sea underlay already painted — skip water cells (big win on 500²).
			if fill == SEA_BLUE:
				continue
			var center := Vector2(cx, cy)
			if DRAW_MAP_MARKERS:
				var cell := Hex.offset_to_axial(col, row)
				if cell == hover_cell:
					fill = fill.lightened(0.12)
				if _highlight_set.has(Hex.key(cell.x, cell.y)):
					fill = fill.lerp(Color(0.95, 0.85, 0.35), 0.45)
			if use_rects or step > 1:
				draw_rect(Rect2(center.x - cell_w * 0.5, center.y - cell_h * 0.5, cell_w, cell_h), fill)
			else:
				_draw_hex_fill(center, fill)
				if show_unit_borders:
					_draw_unit_borders(col, row, center, border_w)
				elif display_mode == MODE_TERRAIN:
					_draw_hex_border(center, 1.0)


## Draw only edges shared with a different 州/郡 (or empty / water).
func _draw_unit_borders(col: int, row: int, center: Vector2, width: float) -> void:
	if world == null:
		return
	var idx := row * world.map_w + col
	if idx < 0 or idx >= world.hexes.size():
		return
	var self_id := _admin_id_of(world.hexes[idx])
	if self_id == "":
		return
	## Pointy-top even-r edge order matches Hex.hex_corners (i=0 at -30°).
	## Neighbor offsets in even-r for edges 0..5:
	var nbrs: Array[Vector2i] = _even_r_edge_neighbors(col, row)
	for i in 6:
		var n: Vector2i = nbrs[i]
		var other := ""
		if Hex.in_map_offset(n.x, n.y, world.map_w, world.map_h):
			var nidx := n.y * world.map_w + n.x
			var nh: Dictionary = world.hexes[nidx]
			if str(nh.get("terrain", "")) != "water":
				other = _admin_id_of(nh)
		if other == self_id:
			continue
		var a: Vector2 = center + _corner_off[i]
		var b: Vector2 = center + _corner_off[(i + 1) % 6]
		draw_line(a, b, ADMIN_BORDER, width, false)


func _even_r_edge_neighbors(col: int, row: int) -> Array[Vector2i]:
	## Neighbors for pointy-top corners starting at -30° (E, NE, NW, W, SW, SE).
	var odd := (row & 1) == 1
	if odd:
		return [
			Vector2i(col + 1, row),
			Vector2i(col + 1, row - 1),
			Vector2i(col, row - 1),
			Vector2i(col - 1, row),
			Vector2i(col, row + 1),
			Vector2i(col + 1, row + 1),
		]
	return [
		Vector2i(col + 1, row),
		Vector2i(col, row - 1),
		Vector2i(col - 1, row - 1),
		Vector2i(col - 1, row),
		Vector2i(col - 1, row + 1),
		Vector2i(col, row + 1),
	]


## Returns Vector4i(min_col, min_row, max_col, max_row) — uses x,y,z,w.
func _visible_offset_bounds(vis: Rect2, w: int, h: int) -> Vector4i:
	var min_col := w
	var max_col := -1
	var min_row := h
	var max_row := -1
	var samples: Array[Vector2] = [
		vis.position,
		Vector2(vis.end.x, vis.position.y),
		Vector2(vis.position.x, vis.end.y),
		vis.end,
	]
	for p in samples:
		var axial := Hex.from_pixel(p, HEX_SIZE)
		var o := Hex.axial_to_offset(axial.x, axial.y)
		min_col = mini(min_col, o.x)
		max_col = maxi(max_col, o.x)
		min_row = mini(min_row, o.y)
		max_row = maxi(max_row, o.y)
	min_col = clampi(min_col - 1, 0, w - 1)
	max_col = clampi(max_col + 1, 0, w - 1)
	min_row = clampi(min_row - 1, 0, h - 1)
	max_row = clampi(max_row + 1, 0, h - 1)
	return Vector4i(min_col, min_row, max_col, max_row)


func _draw_hex_fill(center: Vector2, fill: Color) -> void:
	for i in 6:
		_scratch_pts[i] = center + _corner_off[i]
	draw_colored_polygon(_scratch_pts, fill)


func _draw_hex_border(center: Vector2, width: float = 1.0) -> void:
	for i in 6:
		_scratch_pts[i] = center + _corner_off[i]
	for i in 6:
		draw_line(_scratch_pts[i], _scratch_pts[(i + 1) % 6], ADMIN_BORDER, width, false)


func _draw_units() -> void:
	if not DRAW_MAP_MARKERS:
		return
	var stacks: Dictionary = {}
	for u in world.living_units():
		var k := Hex.key(int(u.q), int(u.r))
		if not stacks.has(k):
			stacks[k] = []
		stacks[k].append(u)
	for k in stacks.keys():
		var cell := Hex.from_key(str(k))
		var center := Hex.to_pixel(cell, HEX_SIZE)
		var list: Array = stacks[k]
		if _current_lod_scale() > CameraLod.Z_HEX:
			_draw_stack_icon(center, list)
		else:
			for i in list.size():
				var u: Dictionary = list[i]
				var offset := Vector2(float(i) * 10.0 - float(list.size() - 1) * 5.0, float(i) * -6.0)
				_draw_unit_sprite(center + offset, u)


func _draw_stack_icon(center: Vector2, list: Array) -> void:
	var u0: Dictionary = list[0]
	var col: Color = _faction_colors.get(str(u0.faction_id), Color(0.5, 0.5, 0.5))
	if bool(u0.get("in_contact", false)):
		draw_circle(center, 14.0, Color(0.95, 0.35, 0.25, 0.55))
	draw_circle(center, 11.0, col)
	draw_circle(center, 11.0, Color(0.05, 0.05, 0.05), false, 2.0)
	var str_sum := 0
	for u in list:
		str_sum += int(u.get("strength", 0))
	draw_string(ThemeDB.fallback_font, center + Vector2(-10, 4), str(str_sum), HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.98, 0.95, 0.85))


func _draw_unit_sprite(center: Vector2, u: Dictionary) -> void:
	var col: Color = _faction_colors.get(str(u.faction_id), Color(0.5, 0.5, 0.5))
	var selected := str(u.id) == world.selected_unit_id
	if bool(u.get("in_contact", false)):
		draw_circle(center, 13.0, Color(1.0, 0.4, 0.3, 0.45))
	draw_circle(center, 10.0 if not selected else 12.0, col)
	draw_circle(center, 10.0 if not selected else 12.0, Color(1, 1, 1) if selected else Color(0.05, 0.05, 0.05), false, 2.0)
	var label := str(u.get("general_name", "?"))
	if label.length() > 1:
		label = label.substr(label.length() - 1, 1)
	draw_string(ThemeDB.fallback_font, center + Vector2(-6, 4), label, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color(0.98, 0.95, 0.85))
	var hp := float(u.get("hp", 1))
	var mx := maxf(float(u.get("strength", 1)), 1.0)
	var ratio := clampf(hp / mx, 0.0, 1.0)
	draw_rect(Rect2(center + Vector2(-12, 14), Vector2(24, 3)), Color(0.1, 0.1, 0.1, 0.7))
	draw_rect(Rect2(center + Vector2(-12, 14), Vector2(24 * ratio, 3)), Color(0.35, 0.75, 0.4) if ratio > 0.4 else Color(0.85, 0.3, 0.25))
