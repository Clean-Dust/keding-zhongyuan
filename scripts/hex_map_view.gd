extends Node2D
## Batched map draw: sea blue always; admin gray by LOD; hex grid only at HEX.

const HEX_SIZE := 36.0
const HEX_DRAW := HEX_SIZE * 0.96
const VIEW_REDRAW_EPS_SQ := 4.0
const PLAINS_GREEN := Color(0.32, 0.48, 0.30)
const SEA_BLUE := Color(0.20, 0.40, 0.58)
const ADMIN_FILL := Color(0.58, 0.58, 0.55, 0.78)
const ADMIN_BORDER := Color(0.30, 0.30, 0.28, 0.95)
const ADMIN_NAME := Color(0.12, 0.12, 0.10, 0.92)
const DRAW_MAP_MARKERS := false

const EXTREME_ANCHOR_IDS := {
	"dunhuang": true, "yongchang": true, "jiaozhi": true,
	"dongye": true, "xiangping": true, "lelang": true,
}

var world: WorldState
var lod: String = "zhou"
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

## Pre-projected admin polygons: {name, centroid: Vector2, polys: Array[PackedVector2Array]}
var _admin_zhou: Array = []
var _admin_jun: Array = []
var _admin_xian: Array = []
var _admin_loaded: bool = false

@onready var labels_root: Node2D = $Labels


func setup(ws: WorldState) -> void:
	world = ws
	_init_geometry()
	_ensure_admin_loaded()
	_rebuild_faction_colors()
	_rebuild_color_caches()
	_rebuild_highlight_set()
	if not world.changed.is_connected(_on_world_changed):
		world.changed.connect(_on_world_changed)
	_cam = get_viewport().get_camera_2d()
	_rebuild_pref_labels()
	queue_redraw()


func bind_camera(cam: CameraLod) -> void:
	_cam = cam
	if not cam.view_changed.is_connected(_on_view_changed):
		cam.view_changed.connect(_on_view_changed)


func set_lod(new_lod: String) -> void:
	lod = new_lod
	_rebuild_pref_labels()
	queue_redraw()


func set_hover(cell: Vector2i) -> void:
	if hover_cell == cell:
		return
	hover_cell = cell
	queue_redraw()


func _on_world_changed() -> void:
	_rebuild_faction_colors()
	_rebuild_color_caches()
	_rebuild_highlight_set()
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


## World-space font so on-screen px ≈ screen_px (camera zoom = 1/lod_scale).
func _world_font_px(screen_px: float) -> int:
	return maxi(10, int(round(screen_px * _current_lod_scale())))


func _init_geometry() -> void:
	_corner_off = Hex.hex_corners(Vector2.ZERO, HEX_DRAW)
	_scratch_pts.resize(6)
	for i in 6:
		_scratch_pts[i] = _corner_off[i]


func _ensure_admin_loaded() -> void:
	if _admin_loaded:
		return
	_admin_zhou = _load_admin_tier("res://data/admin_zhou.json")
	_admin_jun = _load_admin_tier("res://data/admin_jun.json")
	_admin_xian = _load_admin_tier("res://data/admin_xian.json")
	_admin_loaded = true


func _load_admin_tier(path: String) -> Array:
	var out: Array = []
	if not FileAccess.file_exists(path):
		push_warning("missing admin data %s" % path)
		return out
	var txt := FileAccess.get_file_as_string(path)
	var data: Variant = JSON.parse_string(txt)
	if typeof(data) != TYPE_DICTIONARY:
		return out
	var root: Dictionary = data
	for f_any in root.get("features", []):
		if typeof(f_any) != TYPE_DICTIONARY:
			continue
		var f: Dictionary = f_any
		var rings: Array = f.get("rings", [])
		if rings.is_empty():
			continue
		var polys: Array = []
		for ring_any in rings:
			if typeof(ring_any) != TYPE_ARRAY:
				continue
			var ring: Array = ring_any
			var pts := PackedVector2Array()
			for p_any in ring:
				if typeof(p_any) != TYPE_ARRAY:
					continue
				var p: Array = p_any
				if p.size() < 2:
					continue
				pts.append(Hex.lonlat_to_pixel(float(p[0]), float(p[1]), HEX_SIZE))
			if pts.size() >= 3:
				polys.append(pts)
		if polys.is_empty():
			continue
		var cent := Vector2.ZERO
		var c_raw: Variant = f.get("centroid", null)
		if typeof(c_raw) == TYPE_ARRAY and (c_raw as Array).size() >= 2:
			var ca: Array = c_raw
			cent = Hex.lonlat_to_pixel(float(ca[0]), float(ca[1]), HEX_SIZE)
		else:
			## Fallback: average first ring.
			var acc := Vector2.ZERO
			var n := 0
			var first_poly: PackedVector2Array = polys[0]
			for pt in first_poly:
				acc += pt
				n += 1
			if n > 0:
				cent = acc / float(n)
		out.append({
			"name": str(f.get("name", "")),
			"centroid": cent,
			"polys": polys,
		})
	return out


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


func _rebuild_color_caches() -> void:
	if world == null:
		return
	var n := world.hexes.size()
	_fill_hex.resize(n)
	for i in n:
		var h: Dictionary = world.hexes[i]
		_fill_hex[i] = SEA_BLUE if str(h.get("terrain", "")) == "water" else PLAINS_GREEN


func _rebuild_pref_labels() -> void:
	for c in labels_root.get_children():
		c.queue_free()
	if world == null:
		return
	## Edge anchors: fewer on ZHOU to reduce clutter.
	## Holder scaled by lod_scale → roughly constant screen size while zooming.
	var s := _current_lod_scale()
	for h in world.hexes:
		if not bool(h.get("is_anchor", false)):
			continue
		var pid := str(h.get("prefecture_id", ""))
		if lod == "zhou" and not EXTREME_ANCHOR_IDS.has(pid):
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
		## Screen-ish size inside scaled holder (zhou a bit larger).
		var fs := 16 if lod == "zhou" else 14
		lab.add_theme_font_size_override("font_size", fs)
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
	## Sea underlay — admin must never paint over ocean.
	draw_rect(Hex.map_pixel_rect(HEX_SIZE, world.map_w, world.map_h), SEA_BLUE)

	if lod == "hex":
		_draw_hex_layer(vis)
	else:
		_draw_admin_layer(vis)
		## Re-stamp water hexes so any admin bleed over sea is corrected.
		_stamp_water_hexes(vis)

	if DRAW_MAP_MARKERS and world.path_preview.size() >= 2:
		for i in range(world.path_preview.size() - 1):
			var a := Hex.to_pixel(world.path_preview[i], HEX_SIZE)
			var b := Hex.to_pixel(world.path_preview[i + 1], HEX_SIZE)
			draw_line(a, b, Color(1, 0.92, 0.55, 0.85), 3.0, false)
	if DRAW_MAP_MARKERS:
		_draw_units()


func _admin_list_for_lod() -> Array:
	match lod:
		"zhou":
			return _admin_zhou
		"jun":
			return _admin_jun
		"xian":
			return _admin_xian
		_:
			return []


func _draw_admin_layer(vis: Rect2) -> void:
	var feats: Array = _admin_list_for_lod()
	## Target on-screen px; world font = screen_px * lod_scale (zoom-compensated).
	var screen_px := 34.0 if lod == "zhou" else (24.0 if lod == "jun" else 18.0)
	var font_size := _world_font_px(screen_px)
	var name_budget := 40 if lod == "zhou" else (80 if lod == "jun" else 120)
	var named := 0
	var pad := float(font_size) * 1.5
	for f in feats:
		var polys: Array = f.polys
		var on_screen := false
		for poly in polys:
			## Quick cull: any vertex in vis expanded.
			for pt in poly:
				if vis.has_point(pt):
					on_screen = true
					break
			if on_screen:
				break
			## Also: centroid in view.
			if vis.has_point(f.centroid):
				on_screen = true
		if not on_screen and lod != "zhou":
			## Far 州 always draw (few features); others cull hard.
			continue
		for poly in polys:
			if poly.size() >= 3:
				draw_colored_polygon(poly, ADMIN_FILL)
				for i in poly.size():
					draw_line(poly[i], poly[(i + 1) % poly.size()], ADMIN_BORDER, 1.5 if lod == "zhou" else 1.0, false)
		if named < name_budget and vis.grow(pad).has_point(f.centroid):
			var nm: String = str(f.name)
			if lod == "xian" and nm.length() > 4:
				continue
			draw_string(
				ThemeDB.fallback_font,
				f.centroid + Vector2(-float(font_size) * 0.55, float(font_size) * 0.35),
				nm,
				HORIZONTAL_ALIGNMENT_LEFT,
				-1,
				font_size,
				ADMIN_NAME
			)
			named += 1


func _stamp_water_hexes(vis: Rect2) -> void:
	var w := world.map_w
	var h := world.map_h
	var bounds := _visible_offset_bounds(vis, w, h)
	var sx := HEX_SIZE * sqrt(3.0)
	var sy := HEX_SIZE * 1.5
	for row in range(bounds.y, bounds.w + 1):
		var x_bias := 0.0 if (row & 1) == 0 else -0.5 * sx
		var cy := sy * float(row)
		for col in range(bounds.x, bounds.z + 1):
			var idx := row * w + col
			if idx < 0 or idx >= _fill_hex.size():
				continue
			if _fill_hex[idx] != SEA_BLUE:
				continue
			var cx := sx * float(col) + x_bias
			_draw_hex_fill(Vector2(cx, cy), SEA_BLUE)


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
			_draw_hex_fill(center, fill)
			if fill != SEA_BLUE:
				_draw_hex_border(center)


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


func _draw_hex_border(center: Vector2) -> void:
	var border := Color(0.22, 0.34, 0.20, 0.55)
	for i in 6:
		_scratch_pts[i] = center + _corner_off[i]
	for i in 6:
		draw_line(_scratch_pts[i], _scratch_pts[(i + 1) % 6], border, 1.0, false)


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
		if lod != "hex":
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
