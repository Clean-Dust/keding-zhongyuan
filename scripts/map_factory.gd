class_name MapFactory
extends RefCounted
## Builds 500×500 even-r map: Natural Earth coast (plain/water) + edge-city anchors.
## Fixed square grid; south bbox to 象林 (docs/edge-cities-sanguo.md). Labels only at anchors.
## Admin paint: per-hex `zhou_id` / `jun_id` from real CHGIS polygons (`zhou_jun_fills.*`).
## 县: no CHGIS county faces for Han — `xian_id` stays empty; seats are markers only.
## Never loads legacy admin_zhou/jun/xian rings or admin_hex.json.

const COAST_BIN := "res://data/coast_mask.bin"
const ZHOU_JUN_FILLS_META := "res://data/zhou_jun_fills.json"
const ZHOU_JUN_FILLS_BIN := "res://data/zhou_jun_fills.bin"


## Extreme + optional helper anchors: name, lat °N, lon °E, id, optional owner.
static func anchor_specs() -> Array:
	return [
		## Extreme bbox anchors
		{"id": "dunhuang", "name": "敦煌", "lat": 40.142, "lon": 94.662, "owner": ""},
		{"id": "yongchang", "name": "永昌", "lat": 25.155, "lon": 99.258, "owner": ""},
		{"id": "jiaozhi", "name": "交趾", "lat": 21.05, "lon": 106.05, "owner": ""},
		{"id": "rinan", "name": "日南", "lat": 16.60, "lon": 106.68, "owner": ""},
		{"id": "xianglin", "name": "象林", "lat": 15.55, "lon": 108.05, "owner": ""},
		{"id": "dongye", "name": "东冶", "lat": 26.096, "lon": 119.299, "owner": ""},
		{"id": "xiangping", "name": "襄平", "lat": 41.272, "lon": 123.173, "owner": ""},
		{"id": "lelang", "name": "乐浪", "lat": 38.96, "lon": 125.69, "owner": ""},
		## Optional mid-map helpers (also labeled)
		{"id": "jincheng", "name": "金城", "lat": 36.090, "lon": 103.295, "owner": "dong"},
		{"id": "ji", "name": "蓟", "lat": 39.904, "lon": 116.407, "owner": "yuan"},
		{"id": "panyu", "name": "番禺", "lat": 23.129, "lon": 113.264, "owner": ""},
		{"id": "kuaiji", "name": "会稽", "lat": 30.002, "lon": 120.582, "owner": "cao"},
	]


static func create_initial() -> WorldState:
	var ws := WorldState.new()
	ws.map_w = Hex.MAP_W
	ws.map_h = Hex.MAP_H
	ws.factions = [
		{
			"id": "cao",
			"name": "曹操",
			"color": "#3D7A42",
			"is_player": true,
			"food": 120,
			"gold": 80,
		},
		{
			"id": "yuan",
			"name": "袁绍",
			"color": "#2F5E6C",
			"is_player": false,
			"food": 110,
			"gold": 70,
		},
		{
			"id": "dong",
			"name": "董卓残部",
			"color": "#C45C5C",
			"is_player": false,
			"food": 90,
			"gold": 50,
		},
	]
	ws.hexes = _build_hexes(ws.map_w, ws.map_h)
	apply_zhou_jun_fills(ws.hexes, ws.map_w, ws.map_h)
	ws.units = _build_units()
	ws.time = {"day": 1, "paused": true, "speed": 0}
	ws.rebuild_indexes()
	return ws


## Load baked coast bytes (0=water, 1=land); empty if missing.
static func load_coast_bytes(w: int = Hex.MAP_W, h: int = Hex.MAP_H) -> PackedByteArray:
	if not FileAccess.file_exists(COAST_BIN):
		push_warning("coast_mask.bin missing — all land")
		return PackedByteArray()
	var raw := FileAccess.get_file_as_bytes(COAST_BIN)
	if raw.size() != w * h:
		push_warning("coast_mask.bin size %d != %d" % [raw.size(), w * h])
		return PackedByteArray()
	return raw


static func apply_coast_to_hexes(hexes: Array, w: int = Hex.MAP_W, h: int = Hex.MAP_H) -> void:
	var raw := load_coast_bytes(w, h)
	if raw.is_empty():
		return
	for row in h:
		for col in w:
			var idx := row * w + col
			if idx >= hexes.size() or idx >= raw.size():
				continue
			var hcell: Dictionary = hexes[idx]
			## Keep city/anchor terrain; only flip plain ↔ water from mask.
			if str(hcell.get("terrain", "")) == "city":
				continue
			hcell.terrain = "plain" if raw[idx] != 0 else "water"


## Blank plains grid in offset cols 0..w-1, rows 0..h-1 (axial stored), then coast + 州/郡 fills.
static func build_blank_grid(w: int, h: int) -> Array:
	var out: Array = []
	out.resize(w * h)
	var i := 0
	for row in h:
		for col in w:
			var axial := Hex.offset_to_axial(col, row)
			out[i] = {
				"q": axial.x,
				"r": axial.y,
				"col": col,
				"row": row,
				"prefecture_id": "",
				"prefecture_name": "",
				"terrain": "plain",
				"owner_faction_id": "",
				"is_anchor": false,
				"xian_id": "",
				"zhou_id": "",
				"jun_id": "",
			}
			i += 1
	apply_coast_to_hexes(out, w, h)
	apply_zhou_jun_fills(out, w, h)
	return out


## Load CHGIS polygon hex-center fills (zhou_idx + jun_idx uint16 LE per cell).
static func apply_zhou_jun_fills(hexes: Array, w: int = Hex.MAP_W, h: int = Hex.MAP_H) -> void:
	## Ensure fields exist even if bake missing.
	for h0 in hexes:
		var cell0: Dictionary = h0
		if not cell0.has("zhou_id"):
			cell0.zhou_id = ""
		if not cell0.has("jun_id"):
			cell0.jun_id = ""
		cell0.xian_id = "" ## no Han county faces — do not revive Thiessen 县 paint
	if not FileAccess.file_exists(ZHOU_JUN_FILLS_META) or not FileAccess.file_exists(ZHOU_JUN_FILLS_BIN):
		push_warning("zhou_jun_fills missing — 州/郡 modes stay empty")
		return
	var f := FileAccess.open(ZHOU_JUN_FILLS_META, FileAccess.READ)
	if f == null:
		push_warning("zhou_jun_fills.json unreadable")
		return
	var parsed: Variant = JSON.parse_string(f.get_as_text())
	f.close()
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("zhou_jun_fills.json not an object")
		return
	var meta: Dictionary = parsed
	if int(meta.get("map_w", 0)) != w or int(meta.get("map_h", 0)) != h:
		push_warning("zhou_jun_fills map size mismatch")
		return
	var zhou_any: Variant = meta.get("zhou_table", [])
	var jun_any: Variant = meta.get("jun_table", [])
	if typeof(zhou_any) != TYPE_ARRAY or typeof(jun_any) != TYPE_ARRAY:
		push_warning("zhou_jun_fills tables missing")
		return
	var zhou_table: Array = zhou_any
	var jun_table: Array = jun_any
	var raw := FileAccess.get_file_as_bytes(ZHOU_JUN_FILLS_BIN)
	if raw.size() != w * h * 4:
		push_warning("zhou_jun_fills.bin size %d != %d" % [raw.size(), w * h * 4])
		return
	for row in h:
		for col in w:
			var idx := row * w + col
			if idx >= hexes.size():
				continue
			var hcell: Dictionary = hexes[idx]
			hcell.xian_id = ""
			if str(hcell.get("terrain", "")) == "water":
				hcell.zhou_id = ""
				hcell.jun_id = ""
				continue
			var off := idx * 4
			var zi: int = int(raw[off]) | (int(raw[off + 1]) << 8)
			var ji: int = int(raw[off + 2]) | (int(raw[off + 3]) << 8)
			hcell.zhou_id = str(zhou_table[zi]) if zi > 0 and zi < zhou_table.size() else ""
			hcell.jun_id = str(jun_table[ji]) if ji > 0 and ji < jun_table.size() else ""


static func _disk(center: Vector2i, radius: int) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for q in range(center.x - radius, center.x + radius + 1):
		for r in range(center.y - radius, center.y + radius + 1):
			var c := Vector2i(q, r)
			if Hex.distance(center, c) <= radius:
				out.append(c)
	return out


static func _build_hexes(w: int, h: int) -> Array:
	## Coast-aware grid — geographic anchors as city hexes on land.
	var coast := load_coast_bytes(w, h)
	var by_cell: Dictionary = {}
	for row in h:
		for col in w:
			var axial := Hex.offset_to_axial(col, row)
			var k := Hex.key(axial.x, axial.y)
			var idx := row * w + col
			var is_land := coast.is_empty() or coast[idx] != 0
			by_cell[k] = {
				"q": axial.x,
				"r": axial.y,
				"col": col,
				"row": row,
				"prefecture_id": "",
				"prefecture_name": "",
				"terrain": "plain" if is_land else "water",
				"owner_faction_id": "",
				"is_anchor": false,
				"xian_id": "",
				"zhou_id": "",
				"jun_id": "",
			}
	for s in anchor_specs():
		var o := Hex.lonlat_to_offset(float(s.lon), float(s.lat), w, h)
		var center := Hex.offset_to_axial(o.x, o.y)
		var owner := str(s.get("owner", ""))
		for c in _disk(center, 1):
			if not Hex.in_map_axial(c, w, h):
				continue
			var k2 := Hex.key(c.x, c.y)
			var off := Hex.axial_to_offset(c.x, c.y)
			var is_core := c == center
			## Force land under anchors (coast bake may mark littoral as water).
			by_cell[k2] = {
				"q": c.x,
				"r": c.y,
				"col": off.x,
				"row": off.y,
				"prefecture_id": s.id,
				"prefecture_name": s.name,
				"terrain": "city" if is_core else "plain",
				"owner_faction_id": owner if is_core or owner != "" else "",
				"is_anchor": is_core,
				"xian_id": "",
				"zhou_id": "",
				"jun_id": "",
			}
	var out: Array = []
	out.resize(w * h)
	for row2 in h:
		for col2 in w:
			var axial2 := Hex.offset_to_axial(col2, row2)
			var k3 := Hex.key(axial2.x, axial2.y)
			out[row2 * w + col2] = by_cell[k3]
	return out


static func _build_units() -> Array:
	## Place stacks near owned helper anchors (会稽 / 蓟 / 金城).
	var kuaiji := Hex.lonlat_to_axial(120.582, 30.002)
	var ji := Hex.lonlat_to_axial(116.407, 39.904)
	var jincheng := Hex.lonlat_to_axial(103.295, 36.090)
	return [
		_u("u_cao_1", "cao", kuaiji.x + 0, kuaiji.y + 0, "曹仁", 40, 2, 12, 40),
		_u("u_cao_2", "cao", kuaiji.x + 1, kuaiji.y + 0, "夏侯惇", 35, 2, 14, 35),
		_u("u_cao_3", "cao", kuaiji.x + 0, kuaiji.y + 1, "李典", 30, 2, 10, 30),
		_u("u_yuan_1", "yuan", ji.x + 0, ji.y + 0, "颜良", 38, 2, 13, 38),
		_u("u_yuan_2", "yuan", ji.x + 1, ji.y + 0, "文丑", 36, 2, 13, 36),
		_u("u_yuan_3", "yuan", ji.x + 0, ji.y + 1, "淳于琼", 28, 2, 9, 28),
		_u("u_dong_1", "dong", jincheng.x + 0, jincheng.y + 0, "李傕", 32, 2, 11, 32),
		_u("u_dong_2", "dong", jincheng.x + 1, jincheng.y + 0, "郭汜", 30, 2, 11, 30),
	]


static func _u(id: String, fac: String, q: int, r: int, name: String, strength: int, move: int, attack: int, hp: int) -> Dictionary:
	return {
		"id": id,
		"faction_id": fac,
		"q": q,
		"r": r,
		"strength": strength,
		"move": move,
		"attack": attack,
		"hp": hp,
		"general_name": name,
		"move_points": 0,
		"order_path": [],
		"in_contact": false,
		"moved_this_turn": false,
		"attacked_this_turn": false,
	}


## Camera focus: 蓟 (幽州) as a northern reference city.
static func cluster_focus_axial() -> Vector2i:
	return Hex.lonlat_to_axial(116.407, 39.904)


## Resolved anchor positions for labels / smoke checks: {name, col, row, q, r, lat, lon}.
static func resolved_anchors(w: int = Hex.MAP_W, h: int = Hex.MAP_H) -> Array:
	var out: Array = []
	for s in anchor_specs():
		var o := Hex.lonlat_to_offset(float(s.lon), float(s.lat), w, h)
		var ax := Hex.offset_to_axial(o.x, o.y)
		out.append({
			"id": s.id,
			"name": s.name,
			"lat": s.lat,
			"lon": s.lon,
			"col": o.x,
			"row": o.y,
			"q": ax.x,
			"r": ax.y,
		})
	return out
