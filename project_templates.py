"""Offline starter projects with no model, package download, or shell execution.

Templates are ordinary editable source. Creation only claims a fresh direct child
of the selected workspace, stages every file first, then commits using exclusive
file creation. Existing files and directories are never replaced.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from typing import Callable


_TEMPLATES = (
    {"id": "browser-game", "name": "Neon Drift — browser arcade game", "title": "Neon Drift — browser arcade game",
     "description": "A complete offline canvas game with keyboard/touch controls, dash, score, pause, and restart.",
     "entrypoint": "index.html"},
    {"id": "python-cli", "name": "Taskbox — Python task manager", "title": "Taskbox — Python task manager",
     "description": "A working command-line task list with JSON storage, priorities, search, and completion.",
     "entrypoint": "taskbox.py"},
)
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def list_project_templates() -> list[dict]:
    """Return display metadata; callers cannot mutate the template registry."""
    return [dict(template) for template in _TEMPLATES]


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise InterruptedError("Project creation cancelled before committing files.")


def create_project_template(workspace: str | Path, template_id: str,
                            project_name: str, *,
                            cancelled: Callable[[], bool] | None = None) -> dict:
    """Create a runnable template under an existing workspace without overwrites.

    ``project_name`` is one portable directory name, never a relative path.
    All content is staged before the cancellation boundary. The commit is a
    short sequence of exclusive creates; an ordinary write failure rolls back
    only files created by this call. A process/power interruption may leave a
    partial new directory, which subsequent calls will refuse to overwrite.
    No model, network, subprocess, installer, or generated program is invoked.
    """
    metadata = next((item for item in _TEMPLATES if item["id"] == template_id), None)
    if metadata is None:
        raise ValueError("Unknown project template. Choose browser-game or python-cli.")
    if (not isinstance(project_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", project_name)
            or project_name != project_name.strip()
            or project_name.upper() in _RESERVED):
        raise ValueError("Use a folder name of 1–64 letters, numbers, spaces, hyphens, or underscores; start with a letter or number.")
    selected = Path(workspace).expanduser()
    if _linked(selected):
        raise ValueError("Choose a workspace directory directly, not a symbolic link or junction.")
    root = selected.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The selected workspace must be an existing directory.")
    destination = root / project_name
    if os.path.lexists(destination):
        raise FileExistsError(f"Project folder already exists; choose a new name: {destination}")
    if destination.resolve().parent != root:
        raise ValueError("The project must be a direct child of the selected workspace.")
    _check_cancelled(cancelled)
    contents = _game_files() if template_id == "browser-game" else _cli_files()
    staged = Path(tempfile.mkdtemp(prefix=".talktoai-template-", dir=root))
    created: list[Path] = []
    claimed = False
    try:
        for name, content in contents.items():
            _check_cancelled(cancelled)
            (staged / name).write_text(content, encoding="utf-8", newline="\n")
        _check_cancelled(cancelled)
        # Resolve again in case a link appeared while the content was staged.
        if selected.resolve(strict=True) != root or _linked(selected):
            raise ValueError("The selected workspace changed while preparing the project.")
        if os.path.lexists(destination):
            raise FileExistsError(f"Project folder already exists: {destination}")
        destination.mkdir(exist_ok=False)
        claimed = True
        for name in contents:
            path = destination / name
            # Exclusive creation avoids overwriting even a concurrently created file.
            with path.open("xb") as output:
                created.append(path)
                output.write((staged / name).read_bytes())
        launch = (f"Open {destination / 'index.html'} in your browser. No server or install is needed."
                  if template_id == "browser-game" else
                  f'Requires a separately installed Python 3.10 or later. In this folder run: python taskbox.py add "My first task"\nThen run: python taskbox.py list\nHelp: python taskbox.py --help')
        return {"status": "created", "template_id": template_id,
                "project_path": str(destination), "files": list(contents),
                "entrypoint": metadata["entrypoint"],
                "launch_instructions": launch,
                "summary": f"Created {metadata['name']} with {len(contents)} editable files. No AI or network was used."}
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if claimed:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise
    finally:
        # Flat owned staging files only; never recurse through a user directory.
        for name in contents:
            try:
                (staged / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            staged.rmdir()
        except OSError:
            pass


def _game_files() -> dict[str, str]:
    return {"index.html": _GAME_HTML, "style.css": _GAME_CSS,
            "game.js": _GAME_JS, "README.md": _GAME_README}


def _cli_files() -> dict[str, str]:
    return {"taskbox.py": _CLI_PY, "README.md": _CLI_README}


_GAME_HTML = r'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Neon Drift</title><link rel="stylesheet" href="style.css"></head>
<body><main>
<header><div><span class="eyebrow">OFFLINE ARCADE / 01</span><h1>NEON <em>DRIFT</em></h1></div>
<div class="record">PERSONAL BEST <strong id="best">0</strong></div></header>
<section class="arena" aria-label="Game area">
<canvas id="game" width="960" height="600" tabindex="0" aria-label="Neon Drift. Move with WASD or arrows, Space to dash, P to pause."></canvas>
<div class="hud" aria-hidden="true"><span>SCORE <b id="score">0</b></span><span>TIME <b id="time">0s</b></span><span>HULL <b id="hull">3 / 3</b></span></div>
<div class="overlay" id="overlay"><div class="card"><span class="eyebrow" id="badge">A SMALL SHIP. A BIG SURVIVAL PROBLEM.</span>
<h2 id="headline">Find your flow.</h2><p id="message">Collect golden sparks. Dodge the debris. Dash through danger. How long can you last?</p>
<button class="primary" id="start">Start flight</button></div></div>
<div class="live" id="live" role="status" aria-live="polite"></div>
</section>
<footer><p><strong>WASD / arrows</strong> move · <strong>Space</strong> dash · <strong>P</strong> pause<br>On touch screens, drag anywhere in the arena to steer.</p>
<div class="controls"><button id="pause" disabled>Pause</button><button id="dash" disabled>Dash ready</button><button id="restart" disabled>Restart</button></div></footer>
<p class="note">Golden sparks build your score. Blue shields repair your hull. Dashing makes you briefly invulnerable.<br>Runs stay on this device. No account, downloads, or connection required.</p>
</main><script src="game.js"></script></body></html>
'''

_GAME_CSS = r''':root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#eaf4ff;background:#090e19}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 20% 0%,#172b46,transparent 55%),#090e19}
main{max-width:1100px;margin:auto;padding:26px 24px}header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}
h1{font-size:clamp(28px,5vw,44px);letter-spacing:.07em;margin:7px 0 0;font-weight:850}h1 em{font-style:normal;color:#71f5d5}.eyebrow{font-size:10px;letter-spacing:.2em;color:#9cb5cc;font-weight:700}
.record{text-align:right;font-size:10px;letter-spacing:.13em;color:#9cb5cc}.record strong{display:block;font-size:28px;color:#ffd884;letter-spacing:.03em}
.arena{position:relative;border:1px solid #294057;border-radius:18px;overflow:hidden;box-shadow:0 24px 80px #0007;background:#080e1b;aspect-ratio:8/5}
canvas{display:block;width:100%;height:100%;touch-action:none;outline-offset:-5px}canvas:focus-visible{outline:2px solid #71f5d5}.hud{position:absolute;inset:20px 22px auto;display:flex;gap:32px;pointer-events:none;color:#93adc4;font-size:10px;letter-spacing:.14em}.hud b{display:block;color:#fff;font-size:21px;margin-top:5px;letter-spacing:.03em}
.overlay{position:absolute;inset:0;display:grid;place-items:center;padding:20px;background:linear-gradient(transparent,#091422bb);backdrop-filter:blur(3px)}.overlay[hidden]{display:none}.card{max-width:440px;text-align:center}.card h2{font-size:clamp(27px,5vw,49px);line-height:1.06;margin:17px 0}.card p{color:#b3c7db;line-height:1.6;margin:0 0 23px;font-size:15px}
button{font:inherit;font-size:13px;font-weight:700;color:#d4e6f5;background:#14283a;border:1px solid #345269;border-radius:9px;min-height:43px;padding:10px 18px;cursor:pointer;touch-action:manipulation}button:hover:enabled{background:#244259}button:focus-visible{outline:3px solid #ffe199;outline-offset:3px}button:disabled{opacity:.4;cursor:default}.primary{background:#75efd3;color:#082b28;border-color:#75efd3;min-width:155px}.primary:hover:enabled{background:#a3ffe8}#dash{color:#85f9de}
footer{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-top:17px}footer p,.note{font-size:12px;line-height:1.7;color:#95a9bf;margin:0}footer strong{color:#d5e3ed;font-weight:500}.controls{display:flex;gap:8px}.note{margin-top:22px;color:#6f859d}.live{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:700px){main{padding:18px 12px}header{margin-bottom:16px}.arena{aspect-ratio:3/4}.hud{inset:14px 15px auto;gap:24px}footer{flex-direction:column;align-items:stretch;gap:12px}.controls button{flex:1}.card p{font-size:14px}.eyebrow{font-size:9px}.note{margin-top:14px}}
@media(prefers-reduced-motion:reduce){.overlay{backdrop-filter:none}}
'''

_GAME_JS = r'''(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const canvas = $("game"), ctx = canvas.getContext("2d");
  const W = 960, H = 600, TAU = Math.PI * 2;
  const keys = new Set();
  let phase = "ready", score = 0, elapsed = 0, hull = 3, best = 0;
  let enemies = [], sparks = [], particles = [], trails = [];
  let spawn = 0, sparkClock = 0, last = 0, cooldown = 0, dashTime = 0, invuln = 0, hudClock = 0;
  let pointer = null, player = {x: W / 2, y: H / 2, angle: -Math.PI / 2};
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const stars = Array.from({length: 90}, () => ({x: Math.random()*W, y: Math.random()*H, r: Math.random()*1.3+.3}));
  try { best = Math.max(0, Math.floor(Number(localStorage.getItem("neon-drift-best")) || 0)); } catch (_) {}
  $("best").textContent = best;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  function announce(text) { $("live").textContent = text; }
  function fit() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(canvas.clientWidth * ratio);
    canvas.height = Math.round(canvas.clientHeight * ratio);
    ctx.setTransform(canvas.width/W, 0, 0, canvas.height/H, 0, 0);
  }
  window.addEventListener("resize", fit); fit();
  function overlay(headline, message, button, badge) {
    $("headline").textContent = headline; $("message").textContent = message;
    $("start").textContent = button; $("badge").textContent = badge;
    $("overlay").hidden = false;
  }
  function updateHud() {
    $("score").textContent = Math.floor(score); $("time").textContent = Math.floor(elapsed)+"s";
    $("hull").textContent = hull+" / 3";
    $("dash").textContent = cooldown > 0 ? "Dash "+cooldown.toFixed(1)+"s" : "Dash ready";
    $("dash").disabled = phase !== "playing" || cooldown > 0;
    $("pause").textContent = phase === "paused" ? "Resume" : "Pause";
  }
  function start() {
    phase = "playing"; score = 0; elapsed = 0; hull = 3;
    enemies = []; sparks = []; particles = []; trails = []; keys.clear(); pointer = null;
    spawn = .8; sparkClock = .25; cooldown = 0; dashTime = 0; invuln = 1;
    player = {x: W/2, y: H/2, angle: -Math.PI/2};
    $("overlay").hidden = true; $("pause").disabled = false; $("restart").disabled = false;
    updateHud(); canvas.focus(); announce("Flight started. Collect sparks and dodge debris.");
  }
  function pause() {
    if (phase === "playing") {
      phase = "paused"; keys.clear(); pointer = null;
      overlay("Take a breath.", "Your flight is paused. Resume when you are ready.", "Resume flight", "PAUSED");
      announce("Paused.");
    } else if (phase === "paused") {
      phase = "playing"; $("overlay").hidden = true; canvas.focus(); announce("Resumed.");
    }
    updateHud();
  }
  function dash() {
    if (phase === "playing" && cooldown <= 0) { dashTime = .22; cooldown = 2.8; invuln = Math.max(invuln, .38); canvas.focus(); }
  }
  function burst(x, y, colour, amount) {
    for (let i=0;i<(reduced?3:amount);i++) {
      const a=Math.random()*TAU, speed=30+Math.random()*150;
      particles.push({x,y,vx:Math.cos(a)*speed,vy:Math.sin(a)*speed,life:.35+Math.random()*.4,colour});
    }
  }
  function finish() {
    phase = "over"; keys.clear(); pointer = null;
    const result = Math.floor(score), record = result > best;
    best = Math.max(best, result); $("best").textContent = best;
    try { localStorage.setItem("neon-drift-best", String(best)); } catch (_) {}
    $("pause").disabled = true;
    overlay(record?"A new personal best.":"One more flight?", "You scored "+result+" points and survived "+Math.floor(elapsed)+" seconds. Collect sparks and save your dash for tight escapes.", "Fly again", "FLIGHT COMPLETE");
    announce("Flight complete. Score "+result+". Survived "+Math.floor(elapsed)+" seconds."); updateHud();
  }
  function addEnemy() {
    if (enemies.length >= 70) return;
    let x, y; const side = Math.floor(Math.random()*4);
    if(side<2){x=side===0?-25:W+25;y=Math.random()*H;}else{x=Math.random()*W;y=side===2?-25:H+25;}
    const a = Math.atan2(player.y-y, player.x-x)+(Math.random()-.5)*.65;
    const speed = 62+Math.min(elapsed*1.3,150)+Math.random()*60;
    enemies.push({x,y,vx:Math.cos(a)*speed,vy:Math.sin(a)*speed,r:11+Math.random()*12,angle:Math.random()*TAU,spin:Math.random()*2-1});
  }
  function step(dt) {
    elapsed += dt; score += dt*5; cooldown = Math.max(0,cooldown-dt); dashTime = Math.max(0,dashTime-dt); invuln = Math.max(0,invuln-dt);
    let dx = (keys.has("arrowright")||keys.has("d")?1:0)-(keys.has("arrowleft")||keys.has("a")?1:0);
    let dy = (keys.has("arrowdown")||keys.has("s")?1:0)-(keys.has("arrowup")||keys.has("w")?1:0);
    if(pointer){dx=pointer.x-player.x;dy=pointer.y-player.y;if(Math.hypot(dx,dy)<6){dx=0;dy=0;}}
    const distance = Math.hypot(dx,dy), speed = dashTime>0?800:285;
    if(distance){const travel=pointer?Math.min(distance,speed*dt):speed*dt; player.x+=dx/distance*travel;player.y+=dy/distance*travel;player.angle=Math.atan2(dy,dx);}
    else if(dashTime>0){player.x+=Math.cos(player.angle)*speed*dt;player.y+=Math.sin(player.angle)*speed*dt;}
    player.x=clamp(player.x,16,W-16);player.y=clamp(player.y,16,H-16);
    if(!reduced){trails.push({x:player.x,y:player.y,life:dashTime>0?.24:.12});trails=trails.filter(t=>(t.life-=dt)>0);}
    spawn-=dt;if(spawn<=0){addEnemy();spawn=Math.max(.16,.7-elapsed*.006);}
    sparkClock-=dt;if(sparkClock<=0){if(sparks.length<10)sparks.push({x:40+Math.random()*(W-80),y:65+Math.random()*(H-105),life:10,shield:Math.random()<.16});sparkClock=1.0;}
    for(const enemy of enemies){enemy.x+=enemy.vx*dt;enemy.y+=enemy.vy*dt;enemy.angle+=enemy.spin*dt;
      if(invuln<=0&&Math.hypot(player.x-enemy.x,player.y-enemy.y)<enemy.r+9){hull--;invuln=1.5;enemy.x=-200;burst(player.x,player.y,"#ff728c",24);announce("Hit. "+hull+" hull remaining.");if(hull<=0){finish();return;}}}
    enemies=enemies.filter(e=>e.x>-100&&e.x<W+100&&e.y>-100&&e.y<H+100);
    for(const spark of sparks){spark.life-=dt;if(Math.hypot(player.x-spark.x,player.y-spark.y)<24){spark.life=0;score+=spark.shield?25:80;if(spark.shield){hull=Math.min(3,hull+1);announce("Hull repaired.");}burst(spark.x,spark.y,spark.shield?"#79caff":"#ffdc88",15);}}
    sparks=sparks.filter(s=>s.life>0);
    for(const p of particles){p.x+=p.vx*dt;p.y+=p.vy*dt;p.life-=dt;}particles=particles.filter(p=>p.life>0);
    hudClock-=dt;if(hudClock<=0){updateHud();hudClock=.1;}
  }
  function draw() {
    ctx.clearRect(0,0,W,H);ctx.fillStyle="#080f1c";ctx.fillRect(0,0,W,H);
    ctx.strokeStyle="#183149";ctx.lineWidth=.5;ctx.globalAlpha=.45;
    for(let x=0;x<W;x+=60){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke();}
    for(let y=0;y<H;y+=60){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();}ctx.globalAlpha=1;
    for(const star of stars){ctx.fillStyle="#557991";ctx.beginPath();ctx.arc(star.x,star.y,star.r,0,TAU);ctx.fill();}
    for(const t of trails){ctx.globalAlpha=t.life*1.8;ctx.fillStyle="#76ffe0";ctx.beginPath();ctx.arc(t.x,t.y,6,0,TAU);ctx.fill();}ctx.globalAlpha=1;
    for(const spark of sparks){ctx.save();ctx.translate(spark.x,spark.y);ctx.rotate(spark.shield?0:elapsed);ctx.strokeStyle=spark.shield?"#81caff":"#ffdb82";ctx.fillStyle=spark.shield?"#164b76":"#715627";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,-10);ctx.lineTo(9,0);ctx.lineTo(0,10);ctx.lineTo(-9,0);ctx.closePath();ctx.fill();ctx.stroke();if(spark.shield){ctx.beginPath();ctx.moveTo(-4,0);ctx.lineTo(4,0);ctx.moveTo(0,-4);ctx.lineTo(0,4);ctx.stroke();}ctx.restore();}
    for(const e of enemies){ctx.save();ctx.translate(e.x,e.y);ctx.rotate(e.angle);ctx.fillStyle="#322335";ctx.strokeStyle="#cc718f";ctx.lineWidth=1.8;ctx.beginPath();for(let i=0;i<7;i++){const a=i/7*TAU,r=e.r*(i%2?.8:1);i?ctx.lineTo(Math.cos(a)*r,Math.sin(a)*r):ctx.moveTo(Math.cos(a)*r,Math.sin(a)*r);}ctx.closePath();ctx.fill();ctx.stroke();ctx.restore();}
    for(const p of particles){ctx.globalAlpha=Math.min(1,p.life*2);ctx.fillStyle=p.colour;ctx.fillRect(p.x,p.y,3,3);}ctx.globalAlpha=1;
    ctx.save();ctx.translate(player.x,player.y);ctx.rotate(player.angle);
    if(invuln>0){ctx.strokeStyle=dashTime>0?"#d2fff1":"#7ecbf3";ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(0,0,20,0,TAU);ctx.stroke();}
    ctx.fillStyle="#9affdf";ctx.strokeStyle="#effffb";ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(16,0);ctx.lineTo(-10,-10);ctx.lineTo(-6,0);ctx.lineTo(-10,10);ctx.closePath();ctx.fill();ctx.stroke();ctx.restore();
  }
  function frame(now) {const dt=Math.min((now-last)/1000||0,.04);last=now;if(phase==="playing")step(dt);draw();requestAnimationFrame(frame);}
  $("start").addEventListener("click",()=>phase==="paused"?pause():start());
  $("pause").addEventListener("click",pause);$("dash").addEventListener("click",dash);$("restart").addEventListener("click",start);
  window.addEventListener("keydown", event=>{if(event.target.tagName==="BUTTON")return;const key=event.key.toLowerCase();if(["arrowup","arrowdown","arrowleft","arrowright"," "].includes(key))event.preventDefault();if(!event.repeat){if(key==="p"||key==="escape")pause();if(key===" ")dash();if(key==="r"&&phase==="over")start();}keys.add(key);});
  window.addEventListener("keyup",event=>keys.delete(event.key.toLowerCase()));
  function aim(event){const box=canvas.getBoundingClientRect();pointer={x:(event.clientX-box.left)/box.width*W,y:(event.clientY-box.top)/box.height*H};}
  canvas.addEventListener("pointerdown",event=>{if(phase!=="playing")return;canvas.focus();canvas.setPointerCapture(event.pointerId);aim(event);});
  canvas.addEventListener("pointermove",event=>{if(canvas.hasPointerCapture(event.pointerId))aim(event);});
  for(const type of ["pointerup","pointercancel","lostpointercapture"])canvas.addEventListener(type,()=>pointer=null);
  window.addEventListener("blur",()=>{keys.clear();pointer=null;if(phase==="playing")pause();});
  document.addEventListener("visibilitychange",()=>{if(document.hidden&&phase==="playing")pause();});
  updateHud();requestAnimationFrame(frame);
})();
'''

_GAME_README = '''# Neon Drift

A complete small arcade game created offline by TalkToAi Code's built-in template.
Open **index.html** in a modern browser. No account, server, API, model, or package install is required.

## Play

- Move with WASD or arrow keys. On touch screens, drag in the arena to steer.
- Space or **Dash ready** gives a short burst of speed and protection; it recharges in 2.8 seconds.
- Collect golden sparks for 80 points. Blue shields repair one hull point.
- Avoid moving debris. Difficulty increases as you survive. You start with three hull points.
- P, Escape, or **Pause** pauses. The game also pauses when the window loses focus.
- **Restart** starts a fresh run. Your best score is stored only in this browser when storage is available.

## Make it yours

- `index.html`: page copy and accessible controls.
- `style.css`: colours, layout, and mobile styling.
- `game.js`: movement, spawning, collisions, scoring, drawing, and input.

Try changing player speed (285), spark score (80), or spawn interval (.7) in `game.js`.
Every drawing is generated by canvas; the template has no external assets or network requests.
This starter is intended to be modified and shared as part of your own project.
'''

_CLI_PY = r'''#!/usr/bin/env python3
"""Taskbox: a local task list. Python 3.10+; standard library only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


def load(path):
    if not path.exists():
        return {"version": 1, "next_id": 1, "tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("unsupported task data version")
        if type(data.get("next_id")) is not int or data["next_id"] < 1 or not isinstance(data.get("tasks"), list):
            raise ValueError("invalid task database")
        ids = set()
        for task in data["tasks"]:
            if (not isinstance(task, dict) or type(task.get("id")) is not int
                    or task["id"] < 1 or task["id"] in ids
                    or not isinstance(task.get("text"), str)
                    or type(task.get("done")) is not bool
                    or task.get("priority") not in ("low", "normal", "high")):
                raise ValueError("invalid task entry")
            ids.add(task["id"])
        if ids and data["next_id"] <= max(ids):
            raise ValueError("invalid next task ID")
        return data
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}. Existing data has not been changed.") from exc


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".taskbox-", suffix=".tmp", delete=False) as output:
            name = output.name
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Taskbox — your offline local task list.")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().with_name("tasks.json"),
                        help="JSON database path (default: tasks.json beside this script)")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Create a task")
    add.add_argument("text", help="Task text; quote text containing spaces")
    add.add_argument("--priority", choices=("low", "normal", "high"), default="normal")
    listing = commands.add_parser("list", help="Show tasks")
    listing.add_argument("--all", action="store_true", help="Include completed tasks")
    listing.add_argument("--search", default="", help="Case-insensitive text filter")
    for name, description in (("done", "Mark a task completed"), ("undo", "Reopen a task"),
                              ("remove", "Delete one task by ID")):
        command = commands.add_parser(name, help=description)
        command.add_argument("id", type=int)
    commands.add_parser("stats", help="Show task counts and the database location")
    args = parser.parse_args(argv)
    path = args.data.expanduser().resolve()
    # An exclusive lock prevents concurrent writers from losing another change.
    lock = path.with_name(path.name + ".lock")
    locked = False
    try:
        if args.command not in ("list", "stats"):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with lock.open("x", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                locked = True
            except FileExistsError:
                raise ValueError(f"Database is in use ({lock}). Retry; after a crashed process, remove this lock only when no Taskbox command is running.")
        data = load(path)
        tasks = data["tasks"]
        if args.command == "add":
            text = args.text.strip()
            if not text or len(text) > 2000:
                raise ValueError("Task text must contain 1–2000 characters.")
            task = {"id": data["next_id"], "text": text, "priority": args.priority,
                    "done": False, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            tasks.append(task)
            data["next_id"] += 1
            save(path, data)
            print(f"Added #{task['id']}: {text}")
        elif args.command == "list":
            results = [task for task in tasks if (args.all or not task["done"])
                       and args.search.casefold() in task["text"].casefold()]
            rank = {"high": 0, "normal": 1, "low": 2}
            for task in sorted(results, key=lambda item: (item["done"], rank[item["priority"]], item["id"])):
                marker = "x" if task["done"] else " "
                print(f"[{marker}] #{task['id']: <4} {task['priority']:6} {task['text']}")
            if not results:
                print("No matching tasks. Add one with: python taskbox.py add \"Your next step\"")
        elif args.command == "stats":
            done = sum(task["done"] for task in tasks)
            print(f"Open: {len(tasks)-done} | Completed: {done} | Total: {len(tasks)}")
            print(f"Data: {path}")
        else:
            task = next((item for item in tasks if item["id"] == args.id), None)
            if task is None:
                raise ValueError(f"Task #{args.id} does not exist.")
            if args.command == "remove":
                tasks.remove(task)
            else:
                task["done"] = args.command == "done"
            save(path, data)
            print(f"{args.command.capitalize()}: #{task['id']} {task['text']}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Taskbox: {exc}", file=sys.stderr)
        return 1
    finally:
        if locked:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
'''

_CLI_README = '''# Taskbox

A useful local task manager created offline by TalkToAi Code's built-in template.
Requires Python 3.10 or later. It uses only Python's standard library: no packages, APIs, accounts, or model calls.

From this directory, run:

```sh
python taskbox.py add "Finish the game menu" --priority high
python taskbox.py add "Review project notes"
python taskbox.py list
python taskbox.py done 1
python taskbox.py list --all
python taskbox.py list --search notes
python taskbox.py undo 1
python taskbox.py stats
python taskbox.py remove 2
python taskbox.py --help
```

On some systems the Python command is `python3` or `py`.
Tasks live in **tasks.json beside the script**. Back up this file to keep your list.
Use `python taskbox.py --data "path/to/another-list.json" list` to select a different list.

Writes use a temporary file followed by replacement. Corrupt data is reported and preserved.
An exclusive lock prevents simultaneous writes from losing changes. If a process crashes,
a `.lock` file can remain; remove it only after confirming no Taskbox process is running.
The file is ordinary readable JSON and contains no encryption: keep sensitive notes elsewhere.

Edit `taskbox.py` to add commands or change output. This starter is intended to be modified and shared as part of your own project.
'''
