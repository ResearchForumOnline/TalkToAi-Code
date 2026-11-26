"""Preview a registered project scene in the offline HTML viewer."""
import json
import re
import tempfile
import uuid
from pathlib import Path

from engine_discovery import find_blender
from scene_kit import mesh_viewer_html
from workspace_outputs import register_output


_REQUEST = re.compile(
    r"^(?:(how (?:do|can|could|would) (?:i|we))\s+)?(?:open|view|preview|see|show)\s+"
    r"(.{1,200}?)\s+(?:in|with|using|on)\s+(?:(?:a|the|my|web|default)\s+)*browser"
    r"(?:\s+(?:please|sir|now))*[?!. ]*$", re.I)
_REFERENCE = re.compile(
    r"(?:(?:me|my|the|this|that|your|last|latest|saved|generated|3d|blender|blend)\s+)*"
    r"(?:it|this|that|file|model|scene|design|result|preview|output)", re.I)


def preview_request(body, artifacts):
    """Only claim browser requests that refer to scene evidence from this conversation."""
    match = _REQUEST.fullmatch(body.strip())
    if not match:
        return None
    scenes = [record for record in reversed(artifacts) if isinstance(record, dict)
              and Path(str(record.get('artifact', ''))).suffix.lower() in ('.blend', '.glb')]
    if not scenes:
        return None
    target = re.sub(r'\s+(?:please|sir)$', '', match[2], flags=re.I).strip()
    if _REFERENCE.fullmatch(target):
        source = scenes[0]
        # “Open it” after an unrelated file was delivered belongs to that newer output.
        if not re.search(r'\b(?:scene|model|design|blender|3d)\b', target, re.I):
            later = artifacts[artifacts.index(source) + 1:]
            source_path = Path(source['artifact'])
            for item in later:
                other = Path(item.get('artifact', ''))
                if (item.get('source_scene') == str(source_path) or item.get('title') == 'Preview picture'
                        or other == source_path.with_suffix('.py')
                        or other == source_path.with_name(source_path.stem + '-viewer.html')):
                    continue
                return None
    else:
        name = re.sub(r'^(?:the\s+)?(?:file\s+)?', '', target, flags=re.I).strip(" \x22'\x60")
        matches = [record for record in scenes if Path(record['artifact']).name.casefold() == name.casefold()]
        if len(matches) != 1:
            return None
        source = matches[0]
    return source, not bool(match[1])


def verified_viewer(tools, record):
    """Links in model prose can only open a registered, byte-matching project viewer."""
    if record.get('type') != 'viewer' or not record.get('sha256'):
        raise ValueError('The scene viewer is missing from Evidence.')
    path = tools.path(record['artifact'])
    if path.suffix.lower() != '.html' or not path.is_file():
        raise ValueError('The scene viewer is missing. Ask to preview the scene again.')
    current = json.loads(register_output(tools, str(path)))
    if current['sha256'] != record['sha256']:
        raise ValueError('The scene viewer has changed. Ask to preview the scene again.')
    return path


# Runs inside Blender. The saved file supplies evaluated geometry and materials.
# Its embedded Python is disabled at startup.
_GEOMETRY = """
import base64
import bpy
import json
import math
import struct
import sys

def colour(material):
    rgb = (0.55, 0.55, 0.55)
    if material:
        rgb = material.diffuse_color[:3]
        nodes = material.node_tree.nodes if material.node_tree else ()
        shader = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
        if shader and not shader.inputs['Base Color'].is_linked:
            rgb = shader.inputs['Base Color'].default_value[:3]
    def srgb(x):
        x = min(1.0, max(0.0, x))
        return round(12.92*x if x <= 0.0031308 else 1.055*x**(1/2.4)-0.055, 4)
    return tuple(srgb(x) for x in rgb)

groups = {}
low, high = [math.inf]*3, [-math.inf]*3
total = 0
neutral = colour(None)
for inst in bpy.context.evaluated_depsgraph_get().object_instances:
    obj = inst.object
    if not inst.show_self or obj.hide_render or obj.type not in {'MESH','CURVE','SURFACE','FONT','META'}:
        continue
    mesh = obj.to_mesh()
    if mesh is None:
        continue
    try:
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        if total > 300000:
            raise ValueError('This scene exceeds the 300,000-triangle interactive preview limit.')
        matrix = inst.matrix_world.copy()
        colours = [colour(slot.material) for slot in obj.material_slots]
        vertices = {}
        for tri in mesh.loop_triangles:
            rgb = colours[tri.material_index] if tri.material_index < len(colours) else neutral
            points = groups.setdefault(rgb, bytearray())
            for index in tri.vertices:
                if index not in vertices:
                    x, y, z = matrix @ mesh.vertices[index].co
                    point = (x, z, -y)
                    for axis, value in enumerate(point):
                        low[axis] = min(low[axis], value)
                        high[axis] = max(high[axis], value)
                    vertices[index] = struct.pack('<3f', *point)
                points.extend(vertices[index])
    finally:
        obj.to_mesh_clear()
if not total:
    raise ValueError('This scene has no visible mesh surfaces to preview in the browser.')
centre = [(a+b)/2 for a,b in zip(low,high)]
reach = max(0.01, math.dist(low, high)/2)
data = {'batches': [{'c':rgb, 'g':0, 'n':len(points)//12, 'p':base64.b64encode(points).decode('ascii')}
                    for rgb,points in groups.items()], 'centre':centre, 'reach':reach, 'dist':reach*2.8,
        'pitch':0.36, 'yaw':0.65, 'sun':[0.4,0.8,0.45], 'sky':[0.1,0.13,0.17]}
with open(sys.argv[-1], 'x', encoding='utf-8') as out:
    json.dump(data, out, separators=(',', ':'), allow_nan=False)
"""


def prepare_viewer(tools, source, artifacts):
    path = tools.path(source['artifact'])
    current = json.loads(register_output(tools, str(path)))
    for record in reversed(artifacts):
        if record.get('type') != 'viewer':
            continue
        same_blend = (record.get('source_scene') == str(path)
                      and record.get('source_sha256') == current['sha256'])
        # Portable scenes from earlier releases have a same-stem sibling in Evidence.
        same_glb = (path.suffix.lower() == '.glb' and current['sha256'] == source.get('sha256')
                    and Path(record['artifact']) == path.with_name(path.stem + '-viewer.html'))
        if same_blend or same_glb:
            try:
                verified_viewer(tools, record)
                return record
            except (OSError, ValueError):
                continue
    if path.suffix.lower() != '.blend':
        raise ValueError('The matching offline viewer is unavailable. Open the GLB in Blender.')
    if not tools.act:
        raise ValueError('Switch to Act and ask again to create a browser preview in this project.')
    blender = find_blender()
    if not blender:
        raise ValueError('Blender is needed to read this .blend file. Install it or select its executable in Settings.')
    # Use the project volume so sandboxed Blender distributions can read the driver.
    with tempfile.TemporaryDirectory(prefix='.talktoai-preview-', dir=tools.root) as directory:
        driver = Path(directory, 'preview.py')
        geometry = Path(directory, 'mesh.json')
        driver.write_text(_GEOMETRY, encoding='utf-8')
        output = tools._engine_command('Blender browser preview', blender, [
            '--background', '--factory-startup', '--disable-autoexec', str(path),
            '--python-exit-code', '1', '--python', str(driver), '--', str(geometry)], 180)
        if tools.cancel.is_set():
            raise InterruptedError('Preview stopped.')
        if not output.startswith('Exit 0\n') or not geometry.is_file():
            detail = next((line for line in reversed(output.splitlines()) if 'Error:' in line), '')
            raise ValueError('Blender could not export a mesh preview. ' + detail[:300])
        data = json.loads(geometry.read_text(encoding='utf-8'))
    if json.loads(register_output(tools, str(path)))['sha256'] != current['sha256']:
        raise ValueError('The Blender scene changed during export. Ask to preview the saved revision again.')
    note = 'Mesh preview of the saved frame using base material colours. Open Blender for textures, animation and rendered effects.'
    html = mesh_viewer_html(path.stem, data, note)
    name = path.with_name(path.stem + '-viewer-' + uuid.uuid4().hex[:8] + '.html')
    viewer = tools.write_generated(str(name), html.encode('utf-8'))
    return dict(json.loads(register_output(tools, str(viewer), 'Browser preview of ' + path.name)),
                type='viewer', source_scene=str(path), source_sha256=current['sha256'])
