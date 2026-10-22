class_name MicroCombat
extends RefCounted
## Micro LOD turn move/attack on the same WorldState hexes.

signal turn_changed(side: String)
signal log_line(text: String)

var world: WorldState


func setup(ws: WorldState) -> void:
	world = ws


func enter_micro() -> void:
	world.micro_active = true
	world.force_pause_for_micro(true)
	_reset_turn_flags()
	var pf := world.player_faction()
	world.micro_side = str(pf.get("id", "cao"))
	turn_changed.emit(world.micro_side)
	world.changed.emit()


func exit_micro_keep_state() -> void:
	world.micro_active = false
	world.highlight_hexes.clear()
	world.changed.emit()


func _reset_turn_flags() -> void:
	for u in world.living_units():
		u.moved_this_turn = false
		u.attacked_this_turn = false


func reachable(unit_id: String) -> Array[Vector2i]:
	var u := world.get_unit(unit_id)
	var out: Array[Vector2i] = []
	if u.is_empty():
		return out
	var move_left := int(u.get("move", 1))
	if bool(u.get("moved_this_turn", false)):
		return out
	var start := Vector2i(int(u.q), int(u.r))
	var frontier: Array = [{"c": start, "d": 0}]
	var seen: Dictionary = {Hex.key(start.x, start.y): 0}
	while not frontier.is_empty():
		var item: Dictionary = frontier.pop_front()
		var cur: Vector2i = item.c
		var dist: int = int(item.d)
		if dist > 0:
			out.append(cur)
		if dist >= move_left:
			continue
		for n in Hex.neighbors_of(cur):
			if not world.has_hex(n) or not world.is_passable(n):
				continue
			if world.unit_blocking(n, str(u.faction_id)):
				continue
			var nk := Hex.key(n.x, n.y)
			var nd := dist + 1
			if seen.has(nk) and int(seen[nk]) <= nd:
				continue
			seen[nk] = nd
			frontier.append({"c": n, "d": nd})
	return out


func attack_targets(unit_id: String) -> Array[Vector2i]:
	var u := world.get_unit(unit_id)
	var out: Array[Vector2i] = []
	if u.is_empty() or bool(u.get("attacked_this_turn", false)):
		return out
	var cell := Vector2i(int(u.q), int(u.r))
	for n in Hex.neighbors_of(cell):
		for other in world.units_at(n):
			if str(other.faction_id) != str(u.faction_id):
				out.append(n)
				break
	return out


func try_move(unit_id: String, dest: Vector2i) -> bool:
	var u := world.get_unit(unit_id)
	if u.is_empty():
		return false
	if str(u.faction_id) != world.micro_side:
		return false
	if bool(u.get("moved_this_turn", false)):
		return false
	var reach := reachable(unit_id)
	var ok := false
	for c in reach:
		if c == dest:
			ok = true
			break
	if not ok:
		return false
	u.q = dest.x
	u.r = dest.y
	u.moved_this_turn = true
	world.flip_city_if_empty(dest, str(u.faction_id))
	world.mark_contacts()
	log_line.emit("%s 移动至 (%d,%d)" % [str(u.get("general_name", u.id)), dest.x, dest.y])
	world.changed.emit()
	return true


func try_attack(attacker_id: String, target_cell: Vector2i) -> bool:
	var u := world.get_unit(attacker_id)
	if u.is_empty():
		return false
	if str(u.faction_id) != world.micro_side:
		return false
	if bool(u.get("attacked_this_turn", false)):
		return false
	var enemies := world.units_at(target_cell)
	var target: Dictionary = {}
	for e in enemies:
		if str(e.faction_id) != str(u.faction_id):
			target = e
			break
	if target.is_empty():
		return false
	if Hex.distance(Vector2i(int(u.q), int(u.r)), target_cell) != 1:
		return false
	var dmg := int(u.get("attack", 1))
	target.hp = int(target.hp) - dmg
	target.strength = maxi(0, int(target.get("strength", 0)) - int(dmg / 2.0))
	u.attacked_this_turn = true
	u.moved_this_turn = true  # attack consumes the action for MVP
	log_line.emit("%s 攻击 %s，伤害 %d（HP→%d）" % [
		str(u.get("general_name", u.id)),
		str(target.get("general_name", target.id)),
		dmg,
		maxi(0, int(target.hp)),
	])
	if int(target.hp) <= 0:
		log_line.emit("%s 溃灭" % str(target.get("general_name", target.id)))
		world.flip_city_if_empty(target_cell, str(u.faction_id))
	world.remove_dead_units()
	world.mark_contacts()
	world.changed.emit()
	return true


func end_turn() -> void:
	_ai_micro_turn_if_needed()
	_reset_turn_flags()
	## Alternate: after player ends, AI acts immediately then back to player.
	var pf := world.player_faction()
	world.micro_side = str(pf.get("id", "cao"))
	turn_changed.emit(world.micro_side)
	world.changed.emit()


func _ai_micro_turn_if_needed() -> void:
	## Simple AI: each AI unit tries attack adjacent player, else move toward nearest enemy.
	var player_id := str(world.player_faction().get("id", "cao"))
	for u in world.living_units():
		if str(u.faction_id) == player_id:
			continue
		world.micro_side = str(u.faction_id)
		var attacked := false
		for cell in attack_targets(str(u.id)):
			if try_attack(str(u.id), cell):
				attacked = true
				break
		if attacked:
			continue
		var nearest := _nearest_enemy_unit(Vector2i(int(u.q), int(u.r)), str(u.faction_id))
		if nearest == Vector2i(9999, 9999):
			continue
		var best := Vector2i(int(u.q), int(u.r))
		var best_d := Hex.distance(best, nearest)
		for c in reachable(str(u.id)):
			var d := Hex.distance(c, nearest)
			if d < best_d:
				best_d = d
				best = c
		if best != Vector2i(int(u.q), int(u.r)):
			try_move(str(u.id), best)
			## After move, opportunity attack.
			for cell in attack_targets(str(u.id)):
				if try_attack(str(u.id), cell):
					break


func _nearest_enemy_unit(from: Vector2i, my_faction: String) -> Vector2i:
	var best := Vector2i(9999, 9999)
	var best_d := 999999
	for u in world.living_units():
		if str(u.faction_id) == my_faction:
			continue
		var c := Vector2i(int(u.q), int(u.r))
		var d := Hex.distance(from, c)
		if d < best_d:
			best_d = d
			best = c
	return best


func skirmish_over() -> bool:
	## Slice: over when only one faction has living units.
	var seen: Dictionary = {}
	for u in world.living_units():
		seen[str(u.faction_id)] = true
	return seen.size() <= 1
