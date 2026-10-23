extends Node2D
## Dual-scale vertical slice — admin display modes via buttons; wheel only zooms.

const HEX_SIZE := 36.0

var world: WorldState
var strategic := StrategicSim.new()
var micro := MicroCombat.new()
## Zoom band for micro/time (not admin paint).
var lod: String = "far"
var display_mode: String = "zhou"
var _was_hex := false
var _log: PackedStringArray = []
var _mode_buttons: Dictionary = {} ## mode -> Button

@onready var camera: CameraLod = $CameraLod
@onready var map_view: Node2D = $HexMapView
@onready var hud: CanvasLayer = $HUD
@onready var pause_menu: Control = $HUD/PauseMenu
@onready var settings_panel: Control = $HUD/PauseMenu/SettingsPanel
@onready var pause_panel: Control = $HUD/PauseMenu/Panel

const _InputSettings := preload("res://scripts/input_settings.gd")

var _input_settings
var _settings_value_labels: Dictionary = {} ## key -> Label
var _settings_sliders: Dictionary = {} ## key -> HSlider


func _ready() -> void:
	_input_settings = _InputSettings.instance()
	world = MapFactory.create_initial()
	strategic.setup(world)
	micro.setup(world)
	map_view.setup(world)
	map_view.bind_camera(camera)
	camera.lod_changed.connect(_on_zoom_band_changed)
	camera.map_short_click.connect(_on_map_short_click)
	world.changed.connect(_refresh_hud)
	world.day_advanced.connect(func(d): _push_log("第 %d 天破晓" % d))
	world.contact_changed.connect(func(): _push_log("接敌！滚轮放大进入 hex 交战"))
	micro.log_line.connect(_push_log)
	micro.turn_changed.connect(func(side): _push_log("微观回合：%s" % world.get_faction(side).get("name", side)))
	## Frame whole map so land/sea/州 are visible (fixes near-black launch).
	var map_rect := Hex.map_pixel_rect(HEX_SIZE, world.map_w, world.map_h)
	camera.frame_map(map_rect, 1.06)
	_build_mode_buttons()
	_build_settings_rows()
	_wire_hud()
	_configure_hud_mouse_filters()
	_set_display_mode("zhou")
	_refresh_hud()
	_push_log("160×%d。左键拖拽平移；短按选择。顶栏 州/郡/县/地形；Esc → 设置调「鼠标拖拽」。" % Hex.MAP_H)
	world.save_to_path("user://dual_scale_debug.json")


func _process(delta: float) -> void:
	var time_ok := camera.is_macro_time_allowed() and not world.micro_active
	strategic.process_frame(delta, time_ok)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("toggle_pause_menu"):
		if pause_menu.visible:
			if settings_panel.visible:
				_show_settings(false)
			else:
				_set_pause_menu(false)
		else:
			_set_pause_menu(true)
		get_viewport().set_input_as_handled()
		return
	if pause_menu.visible:
		return
	if event.is_action_pressed("end_turn") and (lod == "hex" or world.micro_active):
		micro.end_turn()
		_refresh_selection_highlights()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseMotion:
		var cell: Vector2i = map_view.cell_at_global(get_global_mouse_position())
		map_view.set_hover(cell if world.has_hex(cell) else Vector2i(9999, 9999))
	## Left: CameraLod owns press→drag pan / short-click select (map_short_click).
	elif event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_RIGHT:
		world.selected_unit_id = ""
		world.highlight_hexes.clear()
		world.path_preview.clear()
		world.changed.emit()
		get_viewport().set_input_as_handled()


func _on_map_short_click(_screen_pos: Vector2) -> void:
	if pause_menu.visible:
		return
	_handle_click(get_global_mouse_position())


## Decorative HUD must not eat map drag/click; buttons/sliders stay STOP.
func _configure_hud_mouse_filters() -> void:
	var ignore := Control.MOUSE_FILTER_IGNORE
	for path in ["Hint", "LogPanel", "LogPanel/LogLabel"]:
		var n: Control = hud.get_node_or_null(path)
		if n:
			n.mouse_filter = ignore
	var bar: Panel = hud.get_node_or_null("TopBar")
	if bar:
		## Empty bar chrome passes through; child Buttons still receive clicks.
		bar.mouse_filter = ignore
		for c in bar.get_children():
			if c is Label:
				(c as Label).mouse_filter = ignore


func _handle_click(global_pos: Vector2) -> void:
	var cell: Vector2i = map_view.cell_at_global(global_pos)
	if not world.has_hex(cell):
		return
	if lod == "hex" or world.micro_active or lod == "near":
		_click_micro_or_near(cell)
	else:
		_click_macro(cell)


func _click_macro(cell: Vector2i) -> void:
	var stack := world.units_at(cell)
	var player_id := str(world.player_faction().get("id", ""))
	var pick: Dictionary = {}
	for u in stack:
		if str(u.faction_id) == player_id:
			pick = u
			break
	if pick.is_empty() and world.selected_unit_id != "":
		if not world.is_passable(cell):
			_push_log("海域不可通行")
			return
		if strategic.issue_move_order(world.selected_unit_id, cell):
			var u := world.get_unit(world.selected_unit_id)
			_push_log("%s 受命前往 (%d,%d)" % [str(u.get("general_name", u.id)), cell.x, cell.y])
			world.path_preview.clear()
			var path_cells: Array[Vector2i] = [Vector2i(int(u.q), int(u.r))]
			for step in u.get("order_path", []):
				path_cells.append(Vector2i(int(step.q), int(step.r)))
			world.path_preview = path_cells
			world.changed.emit()
		return
	if not pick.is_empty():
		world.selected_unit_id = str(pick.id)
		world.highlight_hexes.clear()
		for n in Hex.neighbors_of(cell):
			if world.has_hex(n) and world.is_passable(n):
				world.highlight_hexes.append(n)
		_push_log("选中 %s（%s）。再点目标格下达行军。" % [str(pick.get("general_name", pick.id)), lod])
		world.changed.emit()


func _click_micro_or_near(cell: Vector2i) -> void:
	if not world.micro_active and lod != "hex":
		var stack := world.units_at(cell)
		if stack.is_empty():
			return
	if not world.micro_active and lod == "hex":
		micro.enter_micro()
		_push_log("进入 hex 微观：战略时间已暂停")
	var player_id := str(world.player_faction().get("id", ""))
	if world.selected_unit_id != "":
		var u := world.get_unit(world.selected_unit_id)
		if not u.is_empty() and str(u.faction_id) == player_id:
			for t in micro.attack_targets(world.selected_unit_id):
				if t == cell:
					micro.try_attack(world.selected_unit_id, cell)
					_refresh_selection_highlights()
					return
			if micro.try_move(world.selected_unit_id, cell):
				_refresh_selection_highlights()
				return
	var stack2 := world.units_at(cell)
	var pick: Dictionary = {}
	for u2 in stack2:
		if str(u2.faction_id) == player_id:
			pick = u2
			break
	if pick.is_empty() and not stack2.is_empty() and lod == "hex":
		pick = stack2[0]
	if not pick.is_empty():
		world.selected_unit_id = str(pick.id)
		_refresh_selection_highlights()
		_push_log("选中 %s HP%d 攻%d 移%d" % [
			str(pick.get("general_name", pick.id)),
			int(pick.hp), int(pick.attack), int(pick.move),
		])


func _refresh_selection_highlights() -> void:
	world.highlight_hexes.clear()
	world.path_preview.clear()
	if world.selected_unit_id == "":
		world.changed.emit()
		return
	if world.micro_active or lod == "hex":
		for c in micro.reachable(world.selected_unit_id):
			world.highlight_hexes.append(c)
		for c in micro.attack_targets(world.selected_unit_id):
			world.highlight_hexes.append(c)
	world.changed.emit()


func _on_zoom_band_changed(new_lod: String, _scale: float) -> void:
	lod = new_lod
	if new_lod == "hex":
		if not world.micro_active:
			micro.enter_micro()
			_push_log("近距：战略时间自动暂停（hex 交战）")
		_was_hex = true
	elif _was_hex and new_lod != "hex":
		micro.exit_micro_keep_state()
		world.force_pause_for_micro(false)
		world.set_speed(0)
		_push_log("离开近距：战果已写入。选择速度继续时间。")
		_was_hex = false
		world.mark_contacts()
	_refresh_hud()


func _build_mode_buttons() -> void:
	var bar: Panel = hud.get_node("TopBar")
	var modes := [
		["zhou", "州"],
		["jun", "郡"],
		["xian", "县"],
		["terrain", "地形"],
	]
	var x := 520.0
	for m in modes:
		var mode: String = m[0]
		var label: String = m[1]
		var btn := Button.new()
		btn.text = label
		btn.position = Vector2(x, 10)
		btn.size = Vector2(48, 32)
		btn.toggle_mode = true
		btn.focus_mode = Control.FOCUS_NONE
		var captured := mode
		btn.pressed.connect(func(): _set_display_mode(captured))
		bar.add_child(btn)
		_mode_buttons[mode] = btn
		x += 52.0
	## Shift turn label right of mode buttons.
	var turn_l: Label = hud.get_node("TopBar/TurnLabel")
	turn_l.position.x = x + 8.0


func _set_display_mode(mode: String) -> void:
	display_mode = mode
	map_view.set_display_mode(mode)
	for k in _mode_buttons.keys():
		var btn: Button = _mode_buttons[k]
		btn.set_pressed_no_signal(str(k) == mode)
	if mode != "terrain" and not map_view.has_xian_fills():
		_push_log("县范围尚未铺设（州/郡 = 县 hex 并集；名单见 counties_roster）")
	_refresh_hud()


func _wire_hud() -> void:
	hud.get_node("TopBar/BtnPause").pressed.connect(func(): world.set_speed(0))
	hud.get_node("TopBar/Btn1x").pressed.connect(func(): _try_set_speed(1))
	hud.get_node("TopBar/Btn2x").pressed.connect(func(): _try_set_speed(2))
	hud.get_node("TopBar/Btn3x").pressed.connect(func(): _try_set_speed(3))
	hud.get_node("TopBar/BtnEndTurn").pressed.connect(func():
		if world.micro_active or lod == "hex":
			micro.end_turn()
			_refresh_selection_highlights()
	)
	hud.get_node("TopBar/BtnMenu").pressed.connect(func(): _set_pause_menu(true))
	pause_menu.get_node("Panel/VBox/BtnResume").pressed.connect(func(): _set_pause_menu(false))
	pause_menu.get_node("Panel/VBox/BtnSettings").pressed.connect(func(): _show_settings(true))
	pause_menu.get_node("Panel/VBox/BtnSave").pressed.connect(_save_game)
	pause_menu.get_node("Panel/VBox/BtnLoad").pressed.connect(_load_game)
	pause_menu.get_node("Panel/VBox/BtnQuit").pressed.connect(func(): get_tree().quit())
	settings_panel.get_node("VBox/BtnBack").pressed.connect(func(): _show_settings(false))


func _build_settings_rows() -> void:
	var rows: VBoxContainer = settings_panel.get_node("VBox/Rows")
	for c in rows.get_children():
		c.free()
	_settings_value_labels.clear()
	_settings_sliders.clear()
	for key in _InputSettings.KEYS:
		var block := VBoxContainer.new()
		block.add_theme_constant_override("separation", 2)
		var head := HBoxContainer.new()
		var title := Label.new()
		title.text = str(_InputSettings.LABELS.get(key, key))
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		head.add_child(title)
		var val := Label.new()
		val.text = str(_input_settings.get_tier(key))
		val.custom_minimum_size = Vector2(28, 0)
		val.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		head.add_child(val)
		_settings_value_labels[key] = val
		block.add_child(head)
		var hint := Label.new()
		hint.text = str(_InputSettings.HINTS.get(key, ""))
		hint.add_theme_font_size_override("font_size", 10)
		hint.add_theme_color_override("font_color", Color(0.65, 0.62, 0.52, 0.85))
		block.add_child(hint)
		var slider := HSlider.new()
		slider.min_value = 1
		slider.max_value = 10
		slider.step = 1
		## Godot 4: step already snaps; round_values was removed (was crashing _ready).
		slider.value = float(_input_settings.get_tier(key))
		slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var k := str(key)
		slider.value_changed.connect(func(v: float): _on_tier_slider(k, int(round(v))))
		_settings_sliders[key] = slider
		block.add_child(slider)
		rows.add_child(block)


func _on_tier_slider(key: String, tier: int) -> void:
	_input_settings.set_tier(key, tier)
	if _settings_value_labels.has(key):
		(_settings_value_labels[key] as Label).text = str(tier)


func _show_settings(on: bool) -> void:
	settings_panel.visible = on
	pause_panel.visible = not on
	if on:
		for key in _InputSettings.KEYS:
			var t: int = int(_input_settings.get_tier(key))
			if _settings_value_labels.has(key):
				(_settings_value_labels[key] as Label).text = str(t)
			if _settings_sliders.has(key):
				var sl: HSlider = _settings_sliders[key]
				if int(round(sl.value)) != t:
					sl.value = float(t)


func _try_set_speed(s: int) -> void:
	if lod == "hex" or world.micro_active:
		_push_log("hex 微观中无法启动战略时间（滚轮缩小）")
		return
	if not camera.is_macro_time_allowed():
		_push_log("请再缩小一点再开时间")
		return
	world.set_speed(s)
	_push_log("时间速度 %dx" % s)


func _set_pause_menu(on: bool) -> void:
	pause_menu.visible = on
	camera.clear_drag_state()
	if on:
		world.time.paused = true
		_show_settings(false)
	else:
		settings_panel.visible = false
		pause_panel.visible = true


func _save_game() -> void:
	var err := world.save_user()
	if err == OK:
		_push_log("已保存到 user://dual_scale_save.json")
	else:
		_push_log("保存失败：%s" % error_string(err))
	_set_pause_menu(false)


func _load_game() -> void:
	var err := world.load_user()
	if err != OK:
		err = world.load_from_path(WorldState.SAVE_DEBUG)
	if err == OK:
		strategic.setup(world)
		micro.setup(world)
		map_view.setup(world)
		_push_log("读档完成")
	else:
		_push_log("读档失败：%s" % error_string(err))
	_set_pause_menu(false)
	_refresh_hud()


func _push_log(t: String) -> void:
	_log.append(t)
	while _log.size() > 8:
		_log.remove_at(0)
	var box: Label = hud.get_node("LogPanel/LogLabel")
	box.text = "\n".join(_log)


func _refresh_hud() -> void:
	var pf := world.player_faction()
	var day_l: Label = hud.get_node("TopBar/DayLabel")
	var res_l: Label = hud.get_node("TopBar/ResLabel")
	var lod_l: Label = hud.get_node("TopBar/LodLabel")
	var turn_l: Label = hud.get_node("TopBar/TurnLabel")
	day_l.text = "第 %d 天" % int(world.time.day)
	res_l.text = "粮 %d · 金 %d · 城 %d" % [
		int(pf.get("food", 0)),
		int(pf.get("gold", 0)),
		world.owned_city_count(str(pf.get("id", ""))),
	]
	var speed := int(world.time.get("speed", 0))
	var paused := bool(world.time.get("paused", true))
	var mode_names := {"zhou": "州", "jun": "郡", "xian": "县", "terrain": "地形"}
	var mode_label: String = str(mode_names.get(display_mode, display_mode))
	lod_l.text = "显示 %s · 缩放 %s · %s" % [
		mode_label,
		lod.to_upper(),
		("暂停" if paused or speed == 0 else "%dx" % speed),
	]
	if world.micro_active or lod == "hex":
		var side := world.get_faction(world.micro_side)
		turn_l.text = "微观回合：%s（空格结束）" % str(side.get("name", ""))
		hud.get_node("TopBar/BtnEndTurn").visible = true
	else:
		turn_l.text = "选部队 → 点目标行军"
		hud.get_node("TopBar/BtnEndTurn").visible = false
	for b in ["BtnPause", "Btn1x", "Btn2x", "Btn3x"]:
		hud.get_node("TopBar/%s" % b).disabled = lod == "hex" or world.micro_active
