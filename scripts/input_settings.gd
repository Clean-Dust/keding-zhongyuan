class_name InputSettings
extends RefCounted
## Per-category sensitivity tiers 1–10, persisted to user://.

signal changed

const PATH := "user://dual_scale_input.json"

## Defaults: wheel mid-high, keyboard low, drag mid, view compensation mid.
const DEF_WHEEL := 7
const DEF_KEYBOARD := 3
const DEF_DRAG := 5
const DEF_VIEW := 5

const KEYS: PackedStringArray = [
	"wheel_zoom",
	"keyboard_pan",
	"mouse_drag",
	"view_comp",
]

const LABELS := {
	"wheel_zoom": "滚轮缩放",
	"keyboard_pan": "键盘移动",
	"mouse_drag": "鼠标拖拽",
	"view_comp": "视野补偿",
}

const HINTS := {
	"wheel_zoom": "每格滚轮缩放幅度",
	"keyboard_pan": "WASD / 方向键平移速度",
	"mouse_drag": "中键拖拽平移速度",
	"view_comp": "缩小时平移加快的强度（主要影响拖拽）",
}

var wheel_zoom: int = DEF_WHEEL
var keyboard_pan: int = DEF_KEYBOARD
var mouse_drag: int = DEF_DRAG
var view_comp: int = DEF_VIEW

## Untyped to avoid class_name self-reference compile issues under --script.
static var _instance


static func instance():
	if _instance == null:
		_instance = new()
		_instance.load_user()
	return _instance


func get_tier(key: String) -> int:
	match key:
		"wheel_zoom":
			return wheel_zoom
		"keyboard_pan":
			return keyboard_pan
		"mouse_drag":
			return mouse_drag
		"view_comp":
			return view_comp
		_:
			return 5


func set_tier(key: String, tier: int) -> void:
	tier = clampi(tier, 1, 10)
	match key:
		"wheel_zoom":
			if wheel_zoom == tier:
				return
			wheel_zoom = tier
		"keyboard_pan":
			if keyboard_pan == tier:
				return
			keyboard_pan = tier
		"mouse_drag":
			if mouse_drag == tier:
				return
			mouse_drag = tier
		"view_comp":
			if view_comp == tier:
				return
			view_comp = tier
		_:
			return
	save_user()
	changed.emit()


func _tier01(tier: int) -> float:
	return float(clampi(tier, 1, 10) - 1) / 9.0


## Wheel lod_scale delta per notch (tier 7 ≈ 0.30).
func wheel_step() -> float:
	return lerpf(0.06, 0.42, _tier01(wheel_zoom))


## Keyboard world units/sec at lod_scale==1 before view scaling.
func keyboard_base_speed() -> float:
	return lerpf(90.0, 520.0, _tier01(keyboard_pan))


## Drag: world units per screen pixel multiplier (before lod power).
func drag_mul() -> float:
	return lerpf(0.35, 1.85, _tier01(mouse_drag))


## Exponent on lod_scale for drag pan (higher = much faster when zoomed out).
func drag_lod_exp() -> float:
	return lerpf(0.55, 1.55, _tier01(view_comp))


## Milder exponent for keyboard so WASD stays gentler than drag.
func keyboard_lod_exp() -> float:
	return lerpf(0.20, 0.75, _tier01(view_comp))


func to_dict() -> Dictionary:
	return {
		"wheel_zoom": wheel_zoom,
		"keyboard_pan": keyboard_pan,
		"mouse_drag": mouse_drag,
		"view_comp": view_comp,
	}


func from_dict(d: Dictionary) -> void:
	wheel_zoom = clampi(int(d.get("wheel_zoom", DEF_WHEEL)), 1, 10)
	keyboard_pan = clampi(int(d.get("keyboard_pan", DEF_KEYBOARD)), 1, 10)
	mouse_drag = clampi(int(d.get("mouse_drag", DEF_DRAG)), 1, 10)
	view_comp = clampi(int(d.get("view_comp", DEF_VIEW)), 1, 10)


func load_user() -> void:
	if not FileAccess.file_exists(PATH):
		return
	var txt := FileAccess.get_file_as_string(PATH)
	var data: Variant = JSON.parse_string(txt)
	if typeof(data) == TYPE_DICTIONARY:
		from_dict(data)


func save_user() -> void:
	var f := FileAccess.open(PATH, FileAccess.WRITE)
	if f == null:
		push_warning("could not save input settings")
		return
	f.store_string(JSON.stringify(to_dict(), "\t"))
