class_name StrategicSim
extends RefCounted
## Macro loop: day ticks, food, move orders, AI, contact.

var world: WorldState
var tick_accum: float = 0.0
const BASE_DAY_SEC := 1.15


func setup(ws: WorldState) -> void:
	world = ws


func process_frame(delta: float, time_allowed: bool) -> void:
	if world == null:
		return
	if not time_allowed or bool(world.time.get("paused", true)):
		tick_accum = 0.0
		return
	var speed := int(world.time.get("speed", 0))
	if speed <= 0:
		return
	tick_accum += delta * float(speed)
	while tick_accum >= BASE_DAY_SEC:
		tick_accum -= BASE_DAY_SEC
		advance_day()


func advance_day() -> void:
	world.time.day = int(world.time.day) + 1
	_step_move_orders()
	_ai_step_once()
	world.apply_daily_income()
	world.mark_contacts()
	world.day_advanced.emit(int(world.time.day))
	world.changed.emit()


func issue_move_order(unit_id: String, dest: Vector2i) -> bool:
	var u := world.get_unit(unit_id)
	if u.is_empty():
		return false
	var start := Vector2i(int(u.q), int(u.r))
	if not world.has_hex(dest):
		return false
	if world.unit_blocking(dest, str(u.faction_id)) and dest != start:
		## Allow path toward adjacent contact; pathfinding skips blocked final if enemy.
		pass
	var path := _find_path(start, dest, str(u.faction_id))
	if path.is_empty():
		return false
	## Store remaining steps (exclude current cell).
	var steps: Array = []
	for i in range(1, path.size()):
		steps.append({"q": path[i].x, "r": path[i].y})
	u.order_path = steps
	world.changed.emit()
	return true


func _step_move_orders() -> void:
	for u in world.living_units():
		if bool(u.get("in_contact", false)):
			continue
		var path: Array = u.get("order_path", [])
		if path.is_empty():
			continue
		var next: Dictionary = path[0]
		var cell := Vector2i(int(next.q), int(next.r))
		if not world.has_hex(cell):
			u.order_path = []
			continue
		if world.unit_blocking(cell, str(u.faction_id)):
			## Stop before enemy; contact will mark next dawn/check.
			u.order_path = []
			continue
		## Friendly stack OK on same hex for MVP (formation).
		u.q = cell.x
		u.r = cell.y
		path.remove_at(0)
		u.order_path = path
		world.flip_city_if_empty(cell, str(u.faction_id))


func _ai_step_once() -> void:
	## MVP: each dawn, if not in contact, move one step toward nearest enemy city.
	for u in world.living_units():
		var fac := world.get_faction(str(u.faction_id))
		if fac.is_empty() or bool(fac.get("is_player", false)):
			continue
		if bool(u.get("in_contact", false)):
			continue
		var from := Vector2i(int(u.q), int(u.r))
		var target := _nearest_enemy_city(from, str(u.faction_id))
		if target == Vector2i(9999, 9999):
			continue
		var step := _one_step_toward(from, target, str(u.faction_id))
		if step == from:
			continue
		if world.unit_blocking(step, str(u.faction_id)):
			## Sit adjacent — contact marking handles the rest.
			continue
		u.q = step.x
		u.r = step.y
		u.order_path = []
		world.flip_city_if_empty(step, str(u.faction_id))


func _nearest_enemy_city(from: Vector2i, my_faction: String) -> Vector2i:
	## Only scan city list (cached), not all 17k hexes.
	var best := Vector2i(9999, 9999)
	var best_d := 999999
	for cell in world.city_cells():
		var h := world.get_hex(cell.x, cell.y)
		if h.is_empty():
			continue
		if str(h.get("owner_faction_id", "")) == my_faction:
			continue
		var d := Hex.distance(from, cell)
		if d < best_d:
			best_d = d
			best = cell
	return best


func _one_step_toward(from: Vector2i, to: Vector2i, faction_id: String) -> Vector2i:
	var best := from
	var best_d := Hex.distance(from, to)
	for n in Hex.neighbors_of(from):
		if not world.has_hex(n) or not world.is_passable(n):
			continue
		if world.unit_blocking(n, faction_id):
			## Step adjacent to enemy is OK for contact.
			if Hex.distance(n, to) < best_d:
				best_d = Hex.distance(n, to)
				best = n
			continue
		var d := Hex.distance(n, to)
		if d < best_d:
			best_d = d
			best = n
	return best


func _find_path(start: Vector2i, goal: Vector2i, faction_id: String) -> Array[Vector2i]:
	## BFS on hex graph. Cap visit count so cross-map paths stay bounded.
	const MAX_VISIT := 8000
	if start == goal:
		return [start] as Array[Vector2i]
	if not world.is_passable(goal) and not world.unit_blocking(goal, faction_id):
		return _find_path_near(start, goal, faction_id)
	var q: Array[Vector2i] = [start]
	var came: Dictionary = {}
	var seen: Dictionary = {Hex.key(start.x, start.y): true}
	var found := false
	var visits := 0
	while not q.is_empty():
		var cur: Vector2i = q.pop_front()
		visits += 1
		if visits > MAX_VISIT:
			break
		if cur == goal:
			found = true
			break
		for n in Hex.neighbors_of(cur):
			if not world.has_hex(n):
				continue
			if not world.is_passable(n) and n != goal:
				continue
			var nk := Hex.key(n.x, n.y)
			if seen.has(nk):
				continue
			## Cannot path through enemy-occupied hexes (except goal for adjacent attack setup).
			if world.unit_blocking(n, faction_id) and n != goal:
				continue
			seen[nk] = true
			came[nk] = cur
			q.append(n)
	if not found and not seen.has(Hex.key(goal.x, goal.y)):
		## Goal blocked: path to nearest reachable adjacent.
		return _find_path_near(start, goal, faction_id)
	var path: Array[Vector2i] = []
	var c := goal
	if not seen.has(Hex.key(goal.x, goal.y)):
		return path
	while c != start:
		path.push_front(c)
		c = came[Hex.key(c.x, c.y)]
	path.push_front(start)
	return path


func _find_path_near(start: Vector2i, goal: Vector2i, faction_id: String) -> Array[Vector2i]:
	var best_path: Array[Vector2i] = []
	var best_d := 999999
	for n in Hex.neighbors_of(goal):
		if not world.has_hex(n) or not world.is_passable(n):
			continue
		if world.unit_blocking(n, faction_id):
			continue
		var p := _find_path(start, n, faction_id)
		if p.is_empty():
			continue
		var d := Hex.distance(n, goal)
		if d < best_d or (d == best_d and p.size() < best_path.size()):
			best_d = d
			best_path = p
	return best_path
