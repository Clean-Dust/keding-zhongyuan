class_name CameraLod
extends Camera2D
## Camera zoom only (lod_scale larger = farther). Admin paint is UI mode, not zoom.
## Godot Camera2D.zoom is inverted: zoom = 1/lod_scale.
## Pan/zoom rates come from InputSettings tiers (1–10).
## Zoom band ≤ Z_HEX still gates micro combat / strategic-time pause.

signal lod_changed(lod: String, lod_scale: float)
signal view_changed
## Left button released without exceeding drag threshold (select / order).
signal map_short_click(screen_pos: Vector2)

## Near threshold for micro combat (zoom band only — not admin display).
const Z_HEX := 0.85
## Soft bands kept for smoke / legacy call sites (not used for map paint).
const Z_XIAN := 2.2
const Z_JUN := 5.0

const LOD_MIN := 0.35
const LOD_MAX := 16.0
## Fallback wheel step if settings missing (matches tier ~7).
const WHEEL_STEP := 0.28
const ZOOM_SMOOTH := 18.0
## Screen px before left-drag becomes pan (short click = select).
const DRAG_THRESH_PX := 6.0

## Legacy aliases used by smoke / older call sites.
const Z_MICRO := Z_HEX
const Z_MACRO := Z_JUN

const _InputSettings := preload("res://scripts/input_settings.gd")

var lod_scale: float = 6.5
var _target_lod_scale: float = 6.5
var _last_lod: String = ""
var _bounds: Rect2 = Rect2()
var _settings ## InputSettings instance
var _lmb_held: bool = false
var _lmb_origin: Vector2 = Vector2.ZERO
var _lmb_panning: bool = false


func _ready() -> void:
	_settings = _InputSettings.instance()
	make_current()
	_target_lod_scale = lod_scale
	_apply_zoom()
	_emit_lod(true)
	view_changed.emit()


func set_map_bounds(rect: Rect2) -> void:
	_bounds = rect
	_clamp_position()
	view_changed.emit()


## Center on map and pick lod_scale so the full rect fits the viewport (with margin).
func frame_map(rect: Rect2, margin: float = 1.08) -> void:
	_bounds = rect
	if rect.size == Vector2.ZERO:
		return
	position = rect.get_center()
	var vp := Vector2(1280, 720)
	var tree_vp := get_viewport()
	if tree_vp != null:
		var vs := tree_vp.get_visible_rect().size
		if vs.x >= 1.0 and vs.y >= 1.0:
			vp = vs
	var fit_x := rect.size.x * margin / maxf(vp.x, 1.0)
	var fit_y := rect.size.y * margin / maxf(vp.y, 1.0)
	set_lod_scale(maxf(fit_x, fit_y))


func _settings_safe():
	if _settings == null:
		_settings = _InputSettings.instance()
	return _settings


func _process(delta: float) -> void:
	var moved := false
	var pan := Vector2.ZERO
	if Input.is_action_pressed("camera_pan_left"):
		pan.x -= 1.0
	if Input.is_action_pressed("camera_pan_right"):
		pan.x += 1.0
	if Input.is_action_pressed("camera_pan_up"):
		pan.y -= 1.0
	if Input.is_action_pressed("camera_pan_down"):
		pan.y += 1.0
	if pan != Vector2.ZERO:
		var s = _settings_safe()
		var speed: float = float(s.keyboard_base_speed()) * pow(maxf(lod_scale, LOD_MIN), float(s.keyboard_lod_exp()))
		position += pan.normalized() * speed * delta
		_clamp_position()
		moved = true
	if not is_equal_approx(lod_scale, _target_lod_scale):
		var t := clampf(delta * ZOOM_SMOOTH, 0.0, 1.0)
		lod_scale = lerpf(lod_scale, _target_lod_scale, t)
		if absf(lod_scale - _target_lod_scale) < 0.002:
			lod_scale = _target_lod_scale
		_apply_zoom()
		_clamp_position()
		_emit_lod(false)
		moved = true
	if moved:
		view_changed.emit()


## Drag world units per screen pixel (settings-aware).
func pan_factor() -> float:
	var s = _settings_safe()
	return pow(maxf(lod_scale, LOD_MIN), float(s.drag_lod_exp())) * float(s.drag_mul())


## Apply mouse-drag pan (uses 鼠标拖拽 + 视野补偿 tiers).
func apply_drag_pan(relative: Vector2) -> void:
	if relative == Vector2.ZERO:
		return
	position -= relative * pan_factor()
	_clamp_position()
	view_changed.emit()


func clear_drag_state() -> void:
	_lmb_held = false
	_lmb_panning = false
	_lmb_origin = Vector2.ZERO


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_WHEEL_UP and mb.pressed:
			_nudge_zoom(-float(_settings_safe().wheel_step()))
			get_viewport().set_input_as_handled()
			return
		if mb.button_index == MOUSE_BUTTON_WHEEL_DOWN and mb.pressed:
			_nudge_zoom(float(_settings_safe().wheel_step()))
			get_viewport().set_input_as_handled()
			return
		if mb.button_index == MOUSE_BUTTON_MIDDLE:
			## Middle-drag pans immediately; consume so UI does not steal.
			get_viewport().set_input_as_handled()
			return
		if mb.button_index == MOUSE_BUTTON_LEFT:
			if mb.pressed:
				_lmb_held = true
				_lmb_origin = mb.position
				_lmb_panning = false
			else:
				if _lmb_held and not _lmb_panning:
					map_short_click.emit(mb.position)
				clear_drag_state()
			get_viewport().set_input_as_handled()
			return
	elif event is InputEventMouseMotion:
		var mm := event as InputEventMouseMotion
		if Input.is_mouse_button_pressed(MOUSE_BUTTON_MIDDLE):
			apply_drag_pan(mm.relative)
			get_viewport().set_input_as_handled()
			return
		if _lmb_held and Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
			if not _lmb_panning:
				if mm.position.distance_to(_lmb_origin) >= DRAG_THRESH_PX:
					_lmb_panning = true
			if _lmb_panning:
				apply_drag_pan(mm.relative)
				get_viewport().set_input_as_handled()



func _nudge_zoom(delta_scale: float) -> void:
	_target_lod_scale = clampf(_target_lod_scale + delta_scale, LOD_MIN, LOD_MAX)
	lod_scale = lerpf(lod_scale, _target_lod_scale, 0.55)
	_apply_zoom()
	_clamp_position()
	_emit_lod(false)
	view_changed.emit()


func set_lod_scale(v: float) -> void:
	lod_scale = clampf(v, LOD_MIN, LOD_MAX)
	_target_lod_scale = lod_scale
	_apply_zoom()
	_clamp_position()
	_emit_lod(false)
	view_changed.emit()


func _clamp_position() -> void:
	if _bounds.size == Vector2.ZERO:
		return
	position.x = clampf(position.x, _bounds.position.x, _bounds.end.x)
	position.y = clampf(position.y, _bounds.position.y, _bounds.end.y)


func _apply_zoom() -> void:
	var z := 1.0 / lod_scale
	zoom = Vector2(z, z)


func current_lod() -> String:
	## Zoom band for micro/time only (display mode is separate).
	if lod_scale <= Z_HEX:
		return "hex"
	if lod_scale <= Z_XIAN:
		return "near"
	if lod_scale <= Z_JUN:
		return "mid"
	return "far"


func is_macro_time_allowed() -> bool:
	## Strategic time when not in close-up hex band.
	return not is_hex_lod()


func is_hex_lod() -> bool:
	return current_lod() == "hex"


func _emit_lod(force: bool) -> void:
	var lod := current_lod()
	if force or lod != _last_lod:
		_last_lod = lod
		lod_changed.emit(lod, lod_scale)
