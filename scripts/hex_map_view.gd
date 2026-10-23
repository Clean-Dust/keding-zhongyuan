extends Node2D
## Hex-based map draw. Display mode (州/郡/县/地形) is UI-driven — not zoom.
## 州/郡/县 fills = unions of hexes sharing xian_id (parents via counties_roster).
## No legacy Voronoi polygon / admin_hex PIP paint.

const HEX_SIZE := 36.0
const HEX_DRAW := HEX_SIZE * 0.96
const VIEW_REDRAW_EPS_SQ := 4.0
const PLAINS_GREEN := Color(0.32, 0.48, 0.30)
const SEA_BLUE := Color(0.20, 0.40, 0.58)
const CITY_GREEN := Color(0.38, 0.52, 0.34)
const UNASSIGNED_GRAY := Color(0.52, 0.52, 0.50)
const ADMIN_BORDER := Color(0.22, 0.22, 0.20, 0.45)
const ADMIN_NAME := Color(0.10, 0.10, 0.08, 0.92)
const HINT_COLOR := Color(0.12, 0.12, 0.10, 0.88)
const SEAT_MARK := Color(0.15, 0.12, 0.08, 0.9)
const DRAW_MAP_MARKERS := false
const EMPTY_ADMIN_HINT := "县范围尚未铺设"

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
## True when at least one land hex has a non-empty xian_id.
var _has_xian_fills: bool = false
## Optional seat marks from data/county_seats.json (pixel positions).
var _seat_marks: Array = []

@onready var labels_root: Node2D = $Labels


func setup(ws: WorldState) -> void:
	world = ws
	CountiesRoster.ensure_loaded()
	_init_geometry()
	_rebuild_faction_colors()
	_rebuild_color_caches()
	_rebuild_highlight_set()
	_rebuild_admin_labels()
	_rebuild_seat_marks()
	if not world.changed.is_connected(_on_world_changed):
		world.changed.connect(_on_world_changed)
	_cam = get_viewport().get_camera_2d()
	_rebuild_pref_labels()
	queue_redraw()


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
	_rebuild_seat_marks()
	_rebuild_pref_labels()
	queue_redraw()


## Legacy alias — zoom no longer drives admin paint.
func set_lod(_new_lod: String) -> void:
	pass


func has_xian_fills() -> bool:
	return _has_xian_fills


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
	_rebuild_seat_marks()
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


func _xian_id_of(h: Dictionary) -> String:
	return str(h.get("xian_id", ""))


func _admin_id_of(h: Dictionary) -> String:
	return CountiesRoster.admin_key(display_mode, _xian_id_of(h))


func _admin_name_of(h: Dictionary) -> String:
	return CountiesRoster.admin_label(display_mode, _xian_id_of(h))


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


func _color_for_hex(h: Dictionary) -> Color:
	var terrain := str(h.get("terrain", "plain"))
	if terrain == "water":
		return SEA_BLUE
	if display_mode == MODE_TERRAIN:
		return _terrain_color(h)
	## Roster may exist, but zero hexes filled → flat unpainted gray (not Voronoi / not 地形 green).
	if not _has_xian_fills:
		return UNASSIGNED_GRAY
	var aid := _admin_id_of(h)
	if aid == "":
		return UNASSIGNED_GRAY
	return _tint_for_admin_id(aid)


func _rebuild_color_caches() -> void:
	if world == null:
		return
	_has_xian_fills = false
	for h in world.hexes:
		if str(h.get("terrain", "")) == "water":
			continue
		if _xian_id_of(h) != "":
			_has_xian_fills = true
			break
	var n := world.hexes.size()
	_fill_hex.resize(n)
	for i in n:
		_fill_hex[i] = _color_for_hex(world.hexes[i])


func _rebuild_admin_labels() -> void:
	_admin_labels.clear()
	if world == null or display_mode == MODE_TERRAIN or not _has_xian_fills:
		return
	## Accumulate pixel centroids per admin id (union of member 县 hexes).
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


func _rebuild_seat_marks() -> void:
	_seat_marks.clear()
	## Seats are markers only — never paint xian_id / 郡/州 fills from seats.
	for row_any in CountiesRoster.load_seats():
		if typeof(row_any) != TYPE_DICTIONARY:
			continue
		var row: Dictionary = row_any
		var px: Vector2
		if row.has("col") and row.has("row"):
			var ax := Hex.offset_to_axial(int(row.col), int(row.row))
			px = Hex.to_pixel(ax, HEX_SIZE)
		elif row.has("lon") and row.has("lat"):
			px = Hex.lonlat_to_pixel(float(row.lon), float(row.lat), HEX_SIZE)
		else:
			continue
		var nm := str(row.get("name", ""))
		if nm == "" and str(row.get("xian_id", "")) != "":
			nm = CountiesRoster.name_of(str(row.xian_id))
		_seat_marks.append({"pos": px, "name": nm})


func _rebuild_pref_labels() -> void:
	for c in labels_root.get_children():
		c.queue_free()
	if world == null:
		return
	## Edge anchors only on 州 / 地形 (reduce clutter on 郡/县).
	## When no 县 hexes are filled, hide anchors in admin modes so empty state is obvious.
	if display_mode == MODE_JUN or display_mode == MODE_XIAN:
		return
	if display_mode != MODE_TERRAIN and not _has_xian_fills:
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
	_draw_hex_layer(vis)
	_draw_admin_names(vis)
	_draw_seat_marks(vis)
	_draw_empty_admin_hint(vis)
	if DRAW_MAP_MARKERS and world.path_preview.size() >= 2:
		for i in range(world.path_preview.size() - 1):
			var a := Hex.to_pixel(world.path_preview[i], HEX_SIZE)
			var b := Hex.to_pixel(world.path_preview[i + 1], HEX_SIZE)
			draw_line(a, b, Color(1, 0.92, 0.55, 0.85), 3.0, false)
	if DRAW_MAP_MARKERS:
		_draw_units()


func _draw_empty_admin_hint(vis: Rect2) -> void:
	## World-space backup; primary cue is HUD EmptyAdminHint (screen-fixed).
	if display_mode == MODE_TERRAIN or _has_xian_fills:
		return
	var font_size := _world_font_px(22.0)
	var center := vis.get_center()
	draw_string(
		ThemeDB.fallback_font,
		center + Vector2(-float(font_size) * 3.5, float(font_size) * 0.35),
		EMPTY_ADMIN_HINT,
		HORIZONTAL_ALIGNMENT_LEFT,
		-1,
		font_size,
		HINT_COLOR
	)


func _draw_seat_marks(vis: Rect2) -> void:
	## 县 mode: markers + labels. 州/郡: markers only (no territory from seats).
	if display_mode == MODE_TERRAIN or _seat_marks.is_empty():
		return
	var show_labels := display_mode == MODE_XIAN
	var font_size := _world_font_px(13.0)
	var label_budget := 160
	var named := 0
	var mark_r := HEX_SIZE * (0.14 if display_mode == MODE_XIAN else 0.10)
	for seat in _seat_marks:
		var pos: Vector2 = seat.pos
		if not vis.has_point(pos):
			continue
		draw_circle(pos, mark_r, SEAT_MARK)
		if not show_labels:
			continue
		var nm: String = str(seat.get("name", ""))
		if nm == "" or named >= label_budget:
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
	## Far zoom: axis-aligned rects (faster); near: true hexes + borders.
	var use_rects := lod_s >= 4.0
	var show_borders := true
	var border_w := 1.2 if display_mode == MODE_TERRAIN else 0.7
	for row in range(bounds.y, bounds.w + 1):
		var x_bias := 0.0 if (row & 1) == 0 else -0.5 * sx
		var cy := sy * float(row)
		if cy < vis_t - HEX_SIZE or cy > vis_b + HEX_SIZE:
			continue
		for col in range(bounds.x, bounds.z + 1):
			var cx := sx * float(col) + x_bias
			if cx < vis_l or cx > vis_r:
				continue
			var idx := row * w + col
			if idx < 0 or idx >= _fill_hex.size():
				continue
			var center := Vector2(cx, cy)
			var fill: Color = _fill_hex[idx]
			if DRAW_MAP_MARKERS:
				var cell := Hex.offset_to_axial(col, row)
				if cell == hover_cell:
					fill = fill.lightened(0.12)
				if _highlight_set.has(Hex.key(cell.x, cell.y)):
					fill = fill.lerp(Color(0.95, 0.85, 0.35), 0.45)
			if use_rects:
				draw_rect(Rect2(center.x - sx * 0.5, center.y - sy * 0.5, sx, sy), fill)
			else:
				_draw_hex_fill(center, fill)
				if show_borders and fill != SEA_BLUE:
					_draw_hex_border(center, border_w)


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
