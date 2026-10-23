extends SceneTree
## Headless smoke: coast, empty xian_id (no Voronoi fill), roster, tick, save/load.


func _init() -> void:
	var failed := 0
	if Hex.MAP_W != 240 or Hex.MAP_H != 240 or Hex.MAP_H != Hex.compute_map_h():
		push_error("MAP=%dx%d expected 240x240 compute=%d" % [Hex.MAP_W, Hex.MAP_H, Hex.compute_map_h()])
		failed += 1
	if not is_equal_approx(Hex.LAT_MIN, 15.55):
		push_error("LAT_MIN=%s expected 15.55 (象林)" % Hex.LAT_MIN)
		failed += 1
	if not is_equal_approx(Hex.LON_MAX, 130.0) or not is_equal_approx(Hex.LAT_MAX, 42.8):
		push_error("bbox LON_MAX=%s LAT_MAX=%s expected 130.0 / 42.8 (Sinica crop)" % [Hex.LON_MAX, Hex.LAT_MAX])
		failed += 1
	CountiesRoster.ensure_loaded()
	if CountiesRoster.county_count() < 1000:
		push_error("counties_roster too small: %d" % CountiesRoster.county_count())
		failed += 1
	var sample_id := "xian:司隶/河南尹/雒阳"
	if CountiesRoster.zhou_of(sample_id) != "司隶" or CountiesRoster.jun_of(sample_id) != "河南尹":
		push_error("roster lookup failed for %s" % sample_id)
		failed += 1
	var ws := MapFactory.create_initial()
	var expect := Hex.MAP_W * Hex.MAP_H
	if ws.hexes.size() != expect or ws.map_h != Hex.MAP_H:
		push_error("hex count %d map_h=%d expected %d×%d" % [ws.hexes.size(), ws.map_h, Hex.MAP_W, Hex.MAP_H])
		failed += 1
	var land_n := 0
	var water_n := 0
	var xian_n := 0
	for h in ws.hexes:
		if str(h.get("terrain", "")) == "water":
			water_n += 1
			if str(h.get("xian_id", "")) != "":
				push_error("water hex has xian_id")
				failed += 1
				break
		else:
			land_n += 1
			if str(h.get("xian_id", "")) != "":
				xian_n += 1
	if water_n < 1000 or land_n < 10000:
		push_error("coast bake look wrong land=%d water=%d" % [land_n, water_n])
		failed += 1
	## Until hand-filled: almost all land hexes have empty xian_id (no Voronoi/PIP paint).
	## Seats must not invent territory — xian_fills stay 0 even when county_seats.json exists.
	if xian_n > 0:
		push_error("expected no xian fills yet, got %d" % xian_n)
		failed += 1
	var seats_n := CountiesRoster.seat_count()
	if seats_n < 500:
		push_error("county_seats too few: %d" % seats_n)
		failed += 1
	## Sample seat: 雒阳 should project near mid-north China hex.
	var luoyang_ok := false
	for s_any in CountiesRoster.load_seats():
		if typeof(s_any) != TYPE_DICTIONARY:
			continue
		var s: Dictionary = s_any
		if str(s.get("name", "")) != "雒阳":
			continue
		luoyang_ok = true
		var col := int(s.get("col", -1))
		var row := int(s.get("row", -1))
		## Rough window: eastern-central plain on 240×240 Sinica bbox.
		if col < 100 or col > 170 or row < 40 or row > 100:
			push_error("雒阳 seat hex unexpected (%d,%d)" % [col, row])
			failed += 1
		break
	if not luoyang_ok:
		push_error("雒阳 seat missing from county_seats.json")
		failed += 1
	## Extreme anchors: expect Hex.lonlat_to_offset of their declared lon/lat.
	for a in MapFactory.resolved_anchors():
		var name := str(a.name)
		var want := Hex.lonlat_to_offset(float(a.lon), float(a.lat))
		if int(a.col) != want.x or int(a.row) != want.y:
			push_error("anchor %s at (%d,%d) expected (%d,%d)" % [name, a.col, a.row, want.x, want.y])
			failed += 1
	## 乐浪 must no longer sit on the east rim (bbox opens to Korea).
	var lelang := Hex.lonlat_to_offset(125.69, 38.96)
	if lelang.x >= Hex.MAP_W - 5:
		push_error("乐浪 still near east rim col=%d (bbox not widened)" % lelang.x)
		failed += 1
	var p0 := Hex.to_pixel(Hex.offset_to_axial(0, 0), 10.0)
	var p1 := Hex.to_pixel(Hex.offset_to_axial(0, 1), 10.0)
	var p2 := Hex.to_pixel(Hex.offset_to_axial(0, 2), 10.0)
	if not is_equal_approx(p0.x, p2.x) or is_equal_approx(p0.x, p1.x):
		push_error("even-r stagger failed p0=%s p1=%s p2=%s" % [p0, p1, p2])
		failed += 1
	for path in [
		"res://data/counties_roster.json",
		"res://data/coast_mask.bin",
	]:
		if not FileAccess.file_exists(path):
			push_error("missing %s" % path)
			failed += 1
	## Legacy admin files may exist but must not drive paint — stubs only, no rings/PIP.
	for path2 in [
		"res://data/admin_zhou.json",
		"res://data/admin_jun.json",
		"res://data/admin_xian.json",
		"res://data/admin_hex.json",
	]:
		if not FileAccess.file_exists(path2):
			continue
		var raw: Variant = JSON.parse_string(FileAccess.get_file_as_string(path2))
		if typeof(raw) != TYPE_DICTIONARY:
			push_error("%s not a dict" % path2)
			failed += 1
			continue
		var root2: Dictionary = raw
		if not bool(root2.get("unused_for_rendering", false)):
			push_error("%s missing unused_for_rendering flag" % path2)
			failed += 1
		if root2.has("features") and (root2.get("features") as Array).size() > 0:
			push_error("%s still has Voronoi features" % path2)
			failed += 1
		if root2.has("zhou") and (root2.get("zhou") as Array).size() > 0:
			push_error("%s still has PIP zhou array" % path2)
			failed += 1
	## 郡/州 keys derive only from roster parents of a filled xian_id.
	var zhou_k := CountiesRoster.admin_key("zhou", sample_id)
	var jun_k := CountiesRoster.admin_key("jun", sample_id)
	var xian_k := CountiesRoster.admin_key("xian", sample_id)
	if zhou_k != "司隶" or not str(jun_k).contains("河南尹") or xian_k != sample_id:
		push_error("admin_key derive failed zhou=%s jun=%s xian=%s" % [zhou_k, jun_k, xian_k])
		failed += 1
	if CountiesRoster.admin_key("zhou", "") != "" or CountiesRoster.admin_key("jun", "") != "":
		push_error("empty xian_id must not invent 州/郡 keys")
		failed += 1
	## MapFactory must not load/apply legacy admin_hex (comments OK; code paths not).
	var mf_src := FileAccess.get_file_as_string("res://scripts/map_factory.gd")
	if mf_src.find("apply_admin_to_hexes") >= 0 or mf_src.find("ADMIN_HEX") >= 0:
		push_error("map_factory still has apply_admin_to_hexes / ADMIN_HEX")
		failed += 1
	if mf_src.find("res://data/admin_hex.json") >= 0:
		push_error("map_factory still hardcodes admin_hex path")
		failed += 1
	## Camera: micro band still exists; admin display is not zoom-driven.
	if not is_equal_approx(CameraLod.Z_HEX, 0.85):
		push_error("Z_HEX unexpected")
		failed += 1
	if CameraLod.WHEEL_STEP < 0.2:
		push_error("WHEEL_STEP too small: %s" % CameraLod.WHEEL_STEP)
		failed += 1
	if CameraLod.DRAG_THRESH_PX < 1.0:
		push_error("DRAG_THRESH_PX missing/too small")
		failed += 1
	## Camera framing helpers exist.
	var cam := CameraLod.new()
	var rect := Hex.map_pixel_rect(36.0, Hex.MAP_W, Hex.MAP_H)
	cam.frame_map(rect, 1.06)
	if cam.lod_scale < 8.0:
		push_error("frame_map lod_scale too close: %s" % cam.lod_scale)
		failed += 1
	if cam.position.distance_to(rect.get_center()) > 2.0:
		push_error("frame_map center off: %s vs %s" % [cam.position, rect.get_center()])
		failed += 1
	cam.free()
	var Sens := preload("res://scripts/input_settings.gd")
	var sens = Sens.new()
	sens.from_dict({})
	if int(sens.wheel_zoom) != 7 or int(sens.keyboard_pan) != 3 or int(sens.mouse_drag) != 5 or int(sens.view_comp) != 5:
		push_error("input settings defaults wrong")
		failed += 1
	## Left + middle drag share settings-driven pan_factor / drag_mul.
	if float(sens.drag_mul()) < 0.3 or float(sens.drag_mul()) > 2.0:
		push_error("drag_mul out of range: %s" % sens.drag_mul())
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
		## Coast reapplied on load; admin stays empty until hand-filled.
		var w2 := 0
		var x2 := 0
		for h2 in ws2.hexes:
			if str(h2.get("terrain", "")) == "water":
				w2 += 1
			elif str(h2.get("xian_id", "")) != "":
				x2 += 1
		if w2 < 1000:
			push_error("load lost coast water=%d" % w2)
			failed += 1
		if x2 > 0:
			push_error("load invented xian fills=%d" % x2)
			failed += 1
	print(
		"dual_scale_smoke passed=%d failed=%d hexes=%d map=%dx%d land=%d water=%d xian_fills=%d roster=%d seats=%d z_hex=%.2f" % [
			1 if failed == 0 else 0, failed, ws.hexes.size(), ws.map_w, ws.map_h,
			land_n, water_n, xian_n, CountiesRoster.county_count(), seats_n, CameraLod.Z_HEX,
		]
	)
	call_deferred("quit", 1 if failed else 0)
