extends SceneTree
## Headless smoke: coast, admin counts, LOD thresholds, tick, save/load.


func _init() -> void:
	var failed := 0
	if Hex.MAP_W != 160 or Hex.MAP_H != 160 or Hex.MAP_H != Hex.compute_map_h():
		push_error("MAP=%dx%d expected 160x160 compute=%d" % [Hex.MAP_W, Hex.MAP_H, Hex.compute_map_h()])
		failed += 1
	if not is_equal_approx(Hex.LAT_MIN, 15.55):
		push_error("LAT_MIN=%s expected 15.55 (象林)" % Hex.LAT_MIN)
		failed += 1
	var ws := MapFactory.create_initial()
	var expect := Hex.MAP_W * Hex.MAP_H
	if ws.hexes.size() != expect or ws.map_h != Hex.MAP_H:
		push_error("hex count %d map_h=%d expected %d×%d" % [ws.hexes.size(), ws.map_h, Hex.MAP_W, Hex.MAP_H])
		failed += 1
	var land_n := 0
	var water_n := 0
	for h in ws.hexes:
		if str(h.get("terrain", "")) == "water":
			water_n += 1
		else:
			land_n += 1
	if water_n < 1000 or land_n < 10000:
		push_error("coast bake look wrong land=%d water=%d" % [land_n, water_n])
		failed += 1
	## Extreme anchors on nearest hex (row 0 = north); 160×160 south to 象林.
	var extremes := {
		"敦煌": Vector2i(0, 7),
		"乐浪": Vector2i(159, 14),
		"襄平": Vector2i(147, 0),
		"交趾": Vector2i(58, 125),
		"日南": Vector2i(61, 153),
		"象林": Vector2i(69, 159),
	}
	for a in MapFactory.resolved_anchors():
		var name := str(a.name)
		if not extremes.has(name):
			continue
		var want: Vector2i = extremes[name]
		if int(a.col) != want.x or int(a.row) != want.y:
			push_error("anchor %s at (%d,%d) expected (%d,%d)" % [name, a.col, a.row, want.x, want.y])
			failed += 1
	var p0 := Hex.to_pixel(Hex.offset_to_axial(0, 0), 10.0)
	var p1 := Hex.to_pixel(Hex.offset_to_axial(0, 1), 10.0)
	var p2 := Hex.to_pixel(Hex.offset_to_axial(0, 2), 10.0)
	if not is_equal_approx(p0.x, p2.x) or is_equal_approx(p0.x, p1.x):
		push_error("even-r stagger failed p0=%s p1=%s p2=%s" % [p0, p1, p2])
		failed += 1
	## Admin JSON present.
	for path in ["res://data/admin_zhou.json", "res://data/admin_jun.json", "res://data/admin_xian.json", "res://data/coast_mask.bin"]:
		if not FileAccess.file_exists(path):
			push_error("missing %s" % path)
			failed += 1
	var zhou: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://data/admin_zhou.json"))
	var jun: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://data/admin_jun.json"))
	var xian: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://data/admin_xian.json"))
	var zhou_n := 0
	var jun_n := 0
	var xian_n := 0
	if typeof(zhou) == TYPE_DICTIONARY:
		zhou_n = (zhou.get("features", []) as Array).size()
	if typeof(jun) == TYPE_DICTIONARY:
		jun_n = int(jun.get("feature_count", (jun.get("features", []) as Array).size()))
	if typeof(xian) == TYPE_DICTIONARY:
		xian_n = int(xian.get("feature_count", (xian.get("features", []) as Array).size()))
	if zhou_n != 13:
		push_error("expected 13 州, got %d" % zhou_n)
		failed += 1
	if jun_n < 50 or xian_n < 500:
		push_error("admin counts low jun=%d xian=%d" % [jun_n, xian_n])
		failed += 1
	## LOD thresholds.
	if not is_equal_approx(CameraLod.Z_HEX, 0.85) or not is_equal_approx(CameraLod.Z_XIAN, 2.2) or not is_equal_approx(CameraLod.Z_JUN, 5.0):
		push_error("LOD thresholds unexpected")
		failed += 1
	if CameraLod.WHEEL_STEP < 0.2:
		push_error("WHEEL_STEP too small: %s" % CameraLod.WHEEL_STEP)
		failed += 1
	var Sens := preload("res://scripts/input_settings.gd")
	var sens = Sens.new()
	sens.from_dict({})
	if int(sens.wheel_zoom) != 7 or int(sens.keyboard_pan) != 3 or int(sens.mouse_drag) != 5 or int(sens.view_comp) != 5:
		push_error("input settings defaults wrong")
		failed += 1
	var wstep: float = float(sens.wheel_step())
	var kb: float = float(sens.keyboard_base_speed())
	if wstep < 0.2 or kb > 280.0:
		push_error("tier mapping unexpected wheel=%s kb=%s" % [wstep, kb])
		failed += 1
	var d: Dictionary = ws.to_dict()
	var sparse: Array = d.get("hex_sparse", [])
	if sparse.size() >= expect:
		push_error("save not sparse: %d" % sparse.size())
		failed += 1
	if ws.factions.size() != 3:
		failed += 1
	if ws.units.size() < 6:
		failed += 1
	var sim := StrategicSim.new()
	sim.setup(ws)
	ws.set_speed(1)
	ws.time.paused = false
	for i in 5:
		sim.advance_day()
	if int(ws.time.day) != 6:
		failed += 1
	var pf := ws.player_faction()
	if int(pf.food) <= 120:
		failed += 1
	ws.mark_contacts()
	var micro := MicroCombat.new()
	micro.setup(ws)
	micro.enter_micro()
	if not bool(ws.time.paused):
		failed += 1
	for u in ws.living_units():
		if not bool(ws.get_faction(str(u.faction_id)).get("is_player", false)):
			continue
		var reach := micro.reachable(str(u.id))
		if reach.is_empty():
			continue
		micro.try_move(str(u.id), reach[0])
		break
	var path := "user://dual_scale_smoke.json"
	if ws.save_to_path(path) != OK:
		failed += 1
	var ws2 := WorldState.new()
	if ws2.load_from_path(path) != OK:
		failed += 1
	elif ws2.hexes.size() != ws.hexes.size() or int(ws2.time.day) != int(ws.time.day):
		failed += 1
	else:
		## Coast reapplied on load.
		var w2 := 0
		for h2 in ws2.hexes:
			if str(h2.get("terrain", "")) == "water":
				w2 += 1
		if w2 < 1000:
			push_error("load lost coast water=%d" % w2)
			failed += 1
	print(
		"dual_scale_smoke passed=%d failed=%d hexes=%d map=%dx%d land=%d water=%d zhou=%d jun=%d xian=%d lod=%.2f/%.2f/%.2f" % [
			1 if failed == 0 else 0, failed, ws.hexes.size(), ws.map_w, ws.map_h,
			land_n, water_n, zhou_n, jun_n, xian_n,
			CameraLod.Z_HEX, CameraLod.Z_XIAN, CameraLod.Z_JUN,
		]
	)
	call_deferred("quit", 1 if failed else 0)
