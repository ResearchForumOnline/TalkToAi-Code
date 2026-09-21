extends Node2D
## Original dependency-free game fixture for TalkToAi Code development/testing.
var player := Vector2(480, 350)
var coins: Array[Vector2] = [Vector2(200, 250), Vector2(740, 250), Vector2(200, 480), Vector2(740, 480), Vector2(480, 180)]
var score := 0
var seconds := 0.0

func _ready() -> void:
	queue_redraw()
	var args := OS.get_cmdline_user_args()
	if args.size() == 2 and args[0] == "--capture":
		await RenderingServer.frame_post_draw
		var result := get_viewport().get_texture().get_image().save_png(args[1])
		print("VIEWPORT_CAPTURE_RESULT=", result)

func _process(delta: float) -> void:
	seconds += delta
	var direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	player += direction * 250.0 * delta
	player = player.clamp(Vector2(72, 150), Vector2(888, 536))
	for index in range(coins.size() - 1, -1, -1):
		if player.distance_to(coins[index]) < 28:
			coins.remove_at(index)
			score += 10
	if Input.is_key_pressed(KEY_R):
		player = Vector2(480, 350)
		coins = [Vector2(200, 250), Vector2(740, 250), Vector2(200, 480), Vector2(740, 480), Vector2(480, 180)]
		score = 0
	queue_redraw()

func _draw() -> void:
	var font := ThemeDB.fallback_font
	draw_string(font, Vector2(52, 55), "TALKTOAI CODE  /  GAME LAB", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("849bb8"))
	draw_string(font, Vector2(52, 99), "Score Arena", HORIZONTAL_ALIGNMENT_LEFT, -1, 34, Color("eef4ff"))
	draw_string(font, Vector2(736, 92), "SCORE  %02d" % score, HORIZONTAL_ALIGNMENT_LEFT, -1, 22, Color("83e9bc"))
	draw_style_box(_arena_style(), Rect2(48, 126, 864, 436))
	for x in range(72, 900, 32):
		for y in range(150, 548, 32):
			draw_circle(Vector2(x, y), 1.0, Color("263347"))
	for coin in coins:
		draw_circle(coin, 14.0, Color("9cebbd"))
		draw_circle(coin, 6.0, Color("233e35"))
	draw_circle(player + Vector2(0, 5), 19, Color(0, 0, 0, 0.35))
	draw_circle(player, 18, Color("8baaff"))
	draw_circle(player + Vector2(5, -4), 4, Color("ffffff"))
	draw_string(font, Vector2(52, 604), "ARROW KEYS  Move     R  Restart     Collect all five signals", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("9fadc0"))
	if coins.is_empty():
		draw_string(font, Vector2(318, 344), "ALL SIGNALS COLLECTED", HORIZONTAL_ALIGNMENT_LEFT, -1, 24, Color("ffffff"))

func _arena_style() -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = Color("141d2c")
	style.border_color = Color("30405a")
	style.set_border_width_all(1)
	style.set_corner_radius_all(16)
	return style
