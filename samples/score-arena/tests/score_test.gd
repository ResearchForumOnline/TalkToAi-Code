extends SceneTree

func _initialize() -> void:
	var scene = load("res://main.tscn").instantiate()
	root.add_child(scene)
	assert(scene.score == 0, "New game must start with zero score")
	assert(scene.coins.size() == 5, "New game must contain five signals")
	var positions = scene.coins.duplicate()
	for position in positions:
		scene.player = position
		scene._process(0.0)
	assert(scene.score == 50, "Collecting five signals must add fifty points")
	assert(scene.coins.is_empty(), "All collected signals must be removed")
	scene._process(0.0)
	assert(scene.score == 50, "Collected signals must not award points twice")
	print("SCORE_ARENA_TESTS_PASSED")
	quit(0)
