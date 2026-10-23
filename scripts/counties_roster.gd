class_name CountiesRoster
extends RefCounted
## Source of truth for 县 names and 县→郡→州 membership.
## Geometry never comes from here — only ids/names for paint after hand-filled xian_id.

const PATH := "res://data/counties_roster.json"
## Seats file: {meta, seats:[{xian_id, lon, lat, col, row, ...}]} or legacy bare array.
const SEATS_PATH := "res://data/county_seats.json"

static var _by_id: Dictionary = {} ## xian_id -> {id, name, zhou, jun, notes?}
static var _loaded: bool = false
static var _seats: Array = []
static var _seats_loaded: bool = false


static func ensure_loaded() -> void:
	if _loaded:
		return
	_loaded = true
	_by_id.clear()
	if not FileAccess.file_exists(PATH):
		push_warning("counties_roster.json missing")
		return
	var data: Variant = JSON.parse_string(FileAccess.get_file_as_string(PATH))
	if typeof(data) != TYPE_ARRAY:
		push_warning("counties_roster.json expected array")
		return
	for row_any in data:
		if typeof(row_any) != TYPE_DICTIONARY:
			continue
		var row: Dictionary = row_any
		var xid := str(row.get("id", ""))
		if xid == "":
			continue
		_by_id[xid] = row


static func lookup(xian_id: String) -> Dictionary:
	ensure_loaded()
	if xian_id == "":
		return {}
	return _by_id.get(xian_id, {})


static func zhou_of(xian_id: String) -> String:
	return str(lookup(xian_id).get("zhou", ""))


static func jun_of(xian_id: String) -> String:
	return str(lookup(xian_id).get("jun", ""))


static func name_of(xian_id: String) -> String:
	return str(lookup(xian_id).get("name", ""))


## Stable paint keys for admin modes (empty if no xian / unknown roster row).
static func admin_key(mode: String, xian_id: String) -> String:
	if xian_id == "":
		return ""
	var row := lookup(xian_id)
	if row.is_empty():
		return ""
	match mode:
		"zhou":
			return str(row.get("zhou", ""))
		"jun":
			var z := str(row.get("zhou", ""))
			var j := str(row.get("jun", ""))
			if z == "" or j == "":
				return j
			return "%s/%s" % [z, j]
		"xian":
			return xian_id
		_:
			return ""


static func admin_label(mode: String, xian_id: String) -> String:
	if xian_id == "":
		return ""
	var row := lookup(xian_id)
	if row.is_empty():
		return ""
	match mode:
		"zhou":
			return str(row.get("zhou", ""))
		"jun":
			return str(row.get("jun", ""))
		"xian":
			return str(row.get("name", ""))
		_:
			return ""


static func county_count() -> int:
	ensure_loaded()
	return _by_id.size()


## Optional seats for admin modes when ranges are not yet painted.
## Does not set hex xian_id — seats never invent 县/郡/州 territory paint.
static func load_seats() -> Array:
	if _seats_loaded:
		return _seats
	_seats_loaded = true
	_seats = []
	if not FileAccess.file_exists(SEATS_PATH):
		return _seats
	var data: Variant = JSON.parse_string(FileAccess.get_file_as_string(SEATS_PATH))
	var rows: Array = []
	if typeof(data) == TYPE_ARRAY:
		rows = data
	elif typeof(data) == TYPE_DICTIONARY:
		var root: Dictionary = data
		var seats_any: Variant = root.get("seats", [])
		if typeof(seats_any) == TYPE_ARRAY:
			rows = seats_any
	for row_any in rows:
		if typeof(row_any) != TYPE_DICTIONARY:
			continue
		_seats.append(row_any)
	return _seats


static func seat_count() -> int:
	return load_seats().size()
