class_name Hex
extends RefCounted
## Pointy-top axial hex helpers (design-freeze).
## Offset layout: even-r — even horizontal rows shoved right by half a hex (双数打横矮半格).
## Geographic map: lon/lat bbox → WIDTH×HEIGHT with mid-latitude aspect correction.

const NEIGHBORS: Array[Vector2i] = [
	Vector2i(1, 0), Vector2i(1, -1), Vector2i(0, -1),
	Vector2i(-1, 0), Vector2i(-1, 1), Vector2i(0, 1),
]

## Bounding box fitted to Sinica CCTS 三国 overlay view (ad0262 screenshot).
## West keeps 敦煌; east opens to full Korean peninsula; north covers 高句丽 margin;
## south keeps 象林/朱崖洲. Peripheral labels (羌胡/发羌/高句丽/夷洲) are view-only —
## land there stays empty of 州/郡/县 until hand-filled (never auto-assigned).
const LON_MIN := 94.662 ## 敦煌 (凉州西缘)
const LON_MAX := 130.0 ## east of 朝鲜半岛 / Busan sea margin
const LAT_MIN := 15.55 ## 象林 / 朱崖洲南缘
const LAT_MAX := 42.8 ## north of 高句丽 margin on Sinica crop

## 500×500 over Sinica ad0262 extent ≈6.9 km flat-to-flat (was 240≈14.3 km; 160≈19 km).
const MAP_W := 500
## Fixed square grid; lon/lat linear onto WIDTH×HEIGHT.
const MAP_H := 500


static func mid_lat() -> float:
	return (LAT_MIN + LAT_MAX) * 0.5


static func geo_aspect() -> float:
	return (LON_MAX - LON_MIN) * cos(deg_to_rad(mid_lat())) / (LAT_MAX - LAT_MIN)


static func compute_map_h(_width: int = MAP_W) -> int:
	## Aspect-derived height retired; grid is locked square.
	return MAP_H


static func key(q: int, r: int) -> String:
	return "%d,%d" % [q, r]


static func from_key(k: String) -> Vector2i:
	var parts := k.split(",")
	return Vector2i(int(parts[0]), int(parts[1]))


static func distance(a: Vector2i, b: Vector2i) -> int:
	var dq := a.x - b.x
	var dr := a.y - b.y
	return int((absi(dq) + absi(dq + dr) + absi(dr)) / 2)


static func neighbors_of(cell: Vector2i) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for d in NEIGHBORS:
		out.append(cell + d)
	return out


## even-r offset (col, row) → axial (q, r)
static func offset_to_axial(col: int, row: int) -> Vector2i:
	var q := col - (row + (row & 1)) / 2
	return Vector2i(q, row)


## axial (q, r) → even-r offset (col, row)
static func axial_to_offset(q: int, r: int) -> Vector2i:
	var col := q + (r + (r & 1)) / 2
	return Vector2i(col, r)


static func in_map_offset(col: int, row: int, w: int = MAP_W, h: int = MAP_H) -> bool:
	return col >= 0 and col < w and row >= 0 and row < h


static func in_map_axial(cell: Vector2i, w: int = MAP_W, h: int = MAP_H) -> bool:
	var o := axial_to_offset(cell.x, cell.y)
	return in_map_offset(o.x, o.y, w, h)


## lon/lat → nearest even-r offset. row 0 = north.
static func lonlat_to_offset(lon: float, lat: float, w: int = MAP_W, h: int = MAP_H) -> Vector2i:
	var col_f := (lon - LON_MIN) / (LON_MAX - LON_MIN) * float(w) - 0.5
	var row_f := (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * float(h) - 0.5
	return Vector2i(
		clampi(int(round(col_f)), 0, w - 1),
		clampi(int(round(row_f)), 0, h - 1)
	)


## even-r offset cell center → lon/lat (Vector2.x = lon, .y = lat).
static func offset_to_lonlat(col: int, row: int, w: int = MAP_W, h: int = MAP_H) -> Vector2:
	var lon := LON_MIN + (float(col) + 0.5) / float(w) * (LON_MAX - LON_MIN)
	var lat := LAT_MAX - (float(row) + 0.5) / float(h) * (LAT_MAX - LAT_MIN)
	return Vector2(lon, lat)


static func lonlat_to_axial(lon: float, lat: float, w: int = MAP_W, h: int = MAP_H) -> Vector2i:
	var o := lonlat_to_offset(lon, lat, w, h)
	return offset_to_axial(o.x, o.y)


## Continuous equirectangular → map pixel (aligns with even-row hex centers).
static func lonlat_to_pixel(lon: float, lat: float, size: float, w: int = MAP_W, h: int = MAP_H) -> Vector2:
	var sx := size * sqrt(3.0)
	var sy := size * 1.5
	var x := sx * ((lon - LON_MIN) / (LON_MAX - LON_MIN) * float(w) - 0.5)
	var y := sy * ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * float(h) - 0.5)
	return Vector2(x, y)


static func to_pixel(cell: Vector2i, size: float) -> Vector2:
	## Pointy-top axial; q + r/2 encodes even-row half-hex stagger.
	var x := size * sqrt(3.0) * (cell.x + cell.y * 0.5)
	var y := size * 1.5 * float(cell.y)
	return Vector2(x, y)


static func from_pixel(pos: Vector2, size: float) -> Vector2i:
	var q := (sqrt(3.0) / 3.0 * pos.x - 1.0 / 3.0 * pos.y) / size
	var r := (2.0 / 3.0 * pos.y) / size
	return cube_round(q, -q - r, r)


static func cube_round(fx: float, fy: float, fz: float) -> Vector2i:
	var rx := roundi(fx)
	var ry := roundi(fy)
	var rz := roundi(fz)
	var dx := absf(rx - fx)
	var dy := absf(ry - fy)
	var dz := absf(rz - fz)
	if dx > dy and dx > dz:
		rx = -ry - rz
	elif dy > dz:
		ry = -rx - rz
	else:
		rz = -rx - ry
	return Vector2i(rx, rz)


static func hex_corners(center: Vector2, size: float) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in 6:
		var angle := deg_to_rad(60.0 * float(i) - 30.0)
		pts.append(center + Vector2(cos(angle), sin(angle)) * size)
	return pts


static func line(a: Vector2i, b: Vector2i) -> Array[Vector2i]:
	var n := distance(a, b)
	var out: Array[Vector2i] = []
	if n == 0:
		out.append(a)
		return out
	for i in n + 1:
		var t := float(i) / float(n)
		var q := lerpf(float(a.x), float(b.x), t)
		var r := lerpf(float(a.y), float(b.y), t)
		out.append(cube_round(q, -q - r, r))
	return out


## Pixel bounds for even-r pointy-top grid of size (w,h).
## Even rows: x = S*√3*col; odd rows: x = S*√3*(col - 0.5).
static func map_pixel_rect(size: float, w: int = MAP_W, h: int = MAP_H) -> Rect2:
	var sx := size * sqrt(3.0)
	var min_x := -0.5 * sx
	var max_x := float(w - 1) * sx
	var min_y := 0.0
	var max_y := size * 1.5 * float(h - 1)
	var pad := size * 1.2
	return Rect2(
		Vector2(min_x - pad, min_y - pad),
		Vector2((max_x - min_x) + pad * 2.0, (max_y - min_y) + pad * 2.0)
	)
