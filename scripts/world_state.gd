class_name WorldState
extends RefCounted
## Authoritative world blob + compact JSON save/load (design-freeze).

signal changed
signal day_advanced(day: int)
signal contact_changed
signal lod_pause_forced(paused: bool)

const SAVE_USER := "user://dual_scale_save.json"
const SAVE_DEBUG := "res://data/debug_save.json"
const FOOD_PER_CITY_PER_DAY := 8
const GOLD_PER_CITY_PER_DAY := 3

var map_w: int = Hex.MAP_W
var map_h: int = Hex.MAP_H
var factions: Array = []
var hexes: Array = []
var units: Array = []
var time: Dictionary = {"day": 1, "paused": true, "speed": 0}

## Runtime / not always persisted extras
var selected_unit_id: String = ""
var micro_active: bool = false
var micro_side: String = ""  # faction_id whose turn it is in micro
var highlight_hexes: Array[Vector2i] = []
var path_preview: Array[Vector2i] = []

var _hex_index: Dictionary = {}  # "q,r" -> hex dict
var _unit_index: Dictionary = {}  # id -> unit dict
var _city_cells: Array[Vector2i] = []
var _pref_owner_cache: Dictionary = {}  # pref_id -> faction_id
var _pref_centroid_cache: Dictionary = {}  # pref_id -> Vector2


func rebuild_indexes() -> void:
	_hex_index.clear()
	_city_cells.clear()
	_pref_owner_cache.clear()
	_pref_centroid_cache.clear()
	for h in hexes:
		_hex_index[Hex.key(int(h.q), int(h.r))] = h
		if str(h.terrain) == "city":
			_city_cells.append(Vector2i(int(h.q), int(h.r)))
	_unit_index.clear()
	for u in units:
		_unit_index[str(u.id)] = u


func get_hex(q: int, r: int) -> Dictionary:
	return _hex_index.get(Hex.key(q, r), {})


func has_hex(cell: Vector2i) -> bool:
	return _hex_index.has(Hex.key(cell.x, cell.y))


func is_passable(cell: Vector2i) -> bool:
	var h := get_hex(cell.x, cell.y)
	if h.is_empty():
		return false
	return str(h.get("terrain", "plain")) != "water"


func get_unit(id: String) -> Dictionary:
	return _unit_index.get(id, {})


func get_faction(id: String) -> Dictionary:
	for f in factions:
		if str(f.id) == id:
			return f
	return {}


func player_faction() -> Dictionary:
	for f in factions:
		if bool(f.get("is_player", false)):
			return f
	return {}


func units_at(cell: Vector2i) -> Array:
	var out: Array = []
	for u in units:
		if int(u.q) == cell.x and int(u.r) == cell.y and int(u.get("hp", 0)) > 0:
			out.append(u)
	return out


func living_units() -> Array:
	var out: Array = []
	for u in units:
		if int(u.get("hp", 0)) > 0:
			out.append(u)
	return out


func city_cells() -> Array[Vector2i]:
	return _city_cells


static func _hex_is_sparse_worthy(h: Dictionary) -> bool:
	## Water comes from coast_mask on load — do not sparse-store every sea hex.
	var t := str(h.get("terrain", "plain"))
	if t == "water":
		return false
	return (
		t != "plain"
		or str(h.get("owner_faction_id", "")) != ""
		or str(h.get("prefecture_id", "")) != ""
		or str(h.get("xian_id", "")) != ""
		or bool(h.get("is_anchor", false))
	)


func to_dict() -> Dictionary:
	## Compact: full plains grid is rebuilt on load; only non-default hexes stored.
	var sparse: Array = []
	for h in hexes:
		if not _hex_is_sparse_worthy(h):
			continue
		sparse.append({
			"q": int(h.q),
			"r": int(h.r),
			"prefecture_id": str(h.get("prefecture_id", "")),
			"prefecture_name": str(h.get("prefecture_name", "")),
			"terrain": str(h.get("terrain", "plain")),
			"owner_faction_id": str(h.get("owner_faction_id", "")),
			"is_anchor": bool(h.get("is_anchor", false)),
			"xian_id": str(h.get("xian_id", "")),
		})
	return {
		"map_w": map_w,
		"map_h": map_h,
		"layout": "even-r",
		"geo": {
			"lon_min": Hex.LON_MIN,
			"lon_max": Hex.LON_MAX,
			"lat_min": Hex.LAT_MIN,
			"lat_max": Hex.LAT_MAX,
			"aspect": Hex.geo_aspect(),
		},
		"hex_sparse": sparse,
		"factions": factions.duplicate(true),
		"units": units.duplicate(true),
		"time": time.duplicate(true),
		"selected_unit_id": selected_unit_id,
		"micro_active": micro_active,
		"micro_side": micro_side,
	}


func from_dict(d: Dictionary) -> void:
	factions = d.get("factions", []).duplicate(true)
	units = d.get("units", []).duplicate(true)
	time = d.get("time", {"day": 1, "paused": true, "speed": 0}).duplicate(true)
	selected_unit_id = str(d.get("selected_unit_id", ""))
	micro_active = bool(d.get("micro_active", false))
	micro_side = str(d.get("micro_side", ""))
	highlight_hexes.clear()
	path_preview.clear()
	if d.has("hex_sparse") or d.has("map_w"):
		map_w = int(d.get("map_w", Hex.MAP_W))
		map_h = int(d.get("map_h", Hex.MAP_H))
		## Legacy 160×160 (or other) saves → rebuild on current locked grid/bbox.
		if map_w != Hex.MAP_W or map_h != Hex.MAP_H:
			map_w = Hex.MAP_W
			map_h = Hex.MAP_H
			hexes = MapFactory.build_blank_grid(map_w, map_h)
			rebuild_indexes()
			changed.emit()
			return
		hexes = MapFactory.build_blank_grid(map_w, map_h)
		rebuild_indexes()
		for s in d.get("hex_sparse", []):
			var q := int(s.q)
			var r := int(s.r)
			var h := get_hex(q, r)
			if h.is_empty():
				continue
			h.prefecture_id = str(s.get("prefecture_id", ""))
			h.prefecture_name = str(s.get("prefecture_name", ""))
			h.terrain = str(s.get("terrain", "plain"))
			h.owner_faction_id = str(s.get("owner_faction_id", ""))
			h.is_anchor = bool(s.get("is_anchor", str(h.terrain) == "city"))
			h.xian_id = str(s.get("xian_id", ""))
	elif d.has("hexes"):
		## Legacy full-array saves (tiny map) — always remake on current grid.
		map_w = Hex.MAP_W
		map_h = Hex.MAP_H
		hexes = MapFactory.build_blank_grid(map_w, map_h)
	rebuild_indexes()
	changed.emit()


func save_to_path(path: String) -> Error:
	var json := JSON.stringify(to_dict(), "\t")
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		return FileAccess.get_open_error()
	f.store_string(json)
	return OK


func load_from_path(path: String) -> Error:
	if not FileAccess.file_exists(path):
		return ERR_FILE_NOT_FOUND
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return FileAccess.get_open_error()
	var parsed = JSON.parse_string(f.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		return ERR_INVALID_DATA
	from_dict(parsed)
	return OK


func save_user() -> Error:
	return save_to_path(SAVE_USER)


func load_user() -> Error:
	return load_from_path(SAVE_USER)


func prefecture_owner(pref_id: String) -> String:
	if pref_id == "":
		return ""
	if _pref_owner_cache.has(pref_id):
		return str(_pref_owner_cache[pref_id])
	## MVP: owner of the city hex, else plurality of cell owners.
	var city_owner := ""
	var counts: Dictionary = {}
	for h in hexes:
		if str(h.prefecture_id) != pref_id:
			continue
		var own := str(h.get("owner_faction_id", ""))
		if str(h.terrain) == "city" and own != "":
			city_owner = own
		if own == "":
			continue
		counts[own] = int(counts.get(own, 0)) + 1
	var result := city_owner
	if result == "":
		var best := ""
		var best_n := 0
		for k in counts.keys():
			if int(counts[k]) > best_n:
				best_n = int(counts[k])
				best = str(k)
		result = best
	_pref_owner_cache[pref_id] = result
	return result


func prefecture_centroid(pref_id: String) -> Vector2:
	if pref_id == "":
		return Vector2.ZERO
	if _pref_centroid_cache.has(pref_id):
		return _pref_centroid_cache[pref_id]
	var sum := Vector2.ZERO
	var n := 0
	for h in hexes:
		if str(h.prefecture_id) == pref_id:
			sum += Vector2(float(h.q), float(h.r))
			n += 1
	var c := Vector2.ZERO if n == 0 else sum / float(n)
	_pref_centroid_cache[pref_id] = c
	return c


func invalidate_pref_caches() -> void:
	_pref_owner_cache.clear()
	_pref_centroid_cache.clear()


func owned_city_count(faction_id: String) -> int:
	var n := 0
	for cell in _city_cells:
		var h := get_hex(cell.x, cell.y)
		if str(h.get("owner_faction_id", "")) == faction_id:
			n += 1
	return n


func apply_daily_income() -> void:
	for f in factions:
		var cities := owned_city_count(str(f.id))
		f.food = int(f.get("food", 0)) + cities * FOOD_PER_CITY_PER_DAY
		f.gold = int(f.get("gold", 0)) + cities * GOLD_PER_CITY_PER_DAY


func set_speed(speed: int) -> void:
	speed = clampi(speed, 0, 3)
	time.speed = speed
	time.paused = speed == 0
	changed.emit()


func force_pause_for_micro(on: bool) -> void:
	if on:
		time.paused = true
		# keep last speed remembered in time.speed if > 0 for resume hint
	lod_pause_forced.emit(on)
	changed.emit()


func mark_contacts() -> void:
	var any := false
	for u in units:
		u.in_contact = false
	for u in living_units():
		var cell := Vector2i(int(u.q), int(u.r))
		for ncell in Hex.neighbors_of(cell):
			for other in units_at(ncell):
				if str(other.faction_id) != str(u.faction_id):
					u.in_contact = true
					other.in_contact = true
					any = true
	if any:
		contact_changed.emit()


func unit_blocking(cell: Vector2i, for_faction: String) -> bool:
	for u in units_at(cell):
		if str(u.faction_id) != for_faction:
			return true
	return false


func remove_dead_units() -> void:
	var kept: Array = []
	for u in units:
		if int(u.get("hp", 0)) > 0:
			kept.append(u)
	units = kept
	rebuild_indexes()


func flip_city_if_empty(cell: Vector2i, faction_id: String) -> bool:
	var h := get_hex(cell.x, cell.y)
	if h.is_empty():
		return false
	if str(h.terrain) != "city":
		return false
	if str(h.get("owner_faction_id", "")) == faction_id:
		return false
	## Enemy/unowned city: flip if no living enemy units on hex.
	for u in units_at(cell):
		if str(u.faction_id) != faction_id:
			return false
	h.owner_faction_id = faction_id
	invalidate_pref_caches()
	changed.emit()
	return true
