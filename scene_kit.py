"""Deterministic 3D scenes: a small scene description in, real files out.

No model, network, process or third-party 3D library is used. A scene is a
list of simple shapes with positions, sizes and colours. This module checks
that description, builds the geometry in plain Python and writes:

* a Blender script that rebuilds the same scene with bpy (run by the app's
  existing Blender check, which saves and reopens the .blend);
* a binary glTF (.glb) that Blender, Godot, Windows 3D Viewer and web viewers open;
* a single self-contained HTML viewer that needs no internet connection;
* a PNG preview drawn with Pillow.

The description can come from a model (it is far easier to write than a bpy
script) or from the built-in designs in ``template_scene``. Either way the
result is labelled with where it came from.
"""
import base64
import colorsys
import hashlib
import json
import math
import random
import re
import struct


SHAPES = ('box', 'sphere', 'cylinder', 'cone', 'torus', 'pyramid', 'crystal', 'gem', 'plane')
MAX_SOURCE_OBJECTS = 160
MAX_INSTANCES = 900
MAX_TRIANGLES = 70000

# The geometry code is kept as text so the Blender script can carry exactly the
# code that is tested here. It is plain Python that also runs on the older
# interpreters bundled with Blender 2.9x. Nothing in it touches files.
KERNEL_SOURCE = r'''
def _circle(radius, z, count, phase=0.0):
    step = 2.0 * math.pi / count
    return [(radius * math.cos(phase + step * i), radius * math.sin(phase + step * i), z) for i in range(count)]


def _segments(base, detail, least):
    return max(least, int(round(base * detail)))


def mesh_for(shape, params, detail=1.0):
    """Return (vertices, faces) centred on the origin, faces wound outward."""
    if shape == 'box':
        x, y, z = params['size'][0] / 2.0, params['size'][1] / 2.0, params['size'][2] / 2.0
        return ([(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)],
                [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    if shape == 'plane':
        x, y = params['size'][0] / 2.0, params['size'][1] / 2.0
        return [(-x, -y, 0.0), (x, -y, 0.0), (x, y, 0.0), (-x, y, 0.0)], [(0, 1, 2, 3)]
    if shape == 'pyramid':
        x, y, z = params['size'][0] / 2.0, params['size'][1] / 2.0, params['size'][2] / 2.0
        return ([(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (0.0, 0.0, z)],
                [(0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    if shape == 'sphere':
        radius = params['radius']
        around = _segments(16, detail, 6)
        rings = _segments(10, detail, 4)
        vertices = [(0.0, 0.0, radius)]
        for ring in range(1, rings):
            angle = math.pi * ring / rings
            vertices += _circle(radius * math.sin(angle), radius * math.cos(angle), around)
        vertices.append((0.0, 0.0, -radius))
        last = len(vertices) - 1
        faces = []
        for i in range(around):
            j = (i + 1) % around
            faces.append((0, 1 + i, 1 + j))
            for ring in range(rings - 2):
                upper = 1 + ring * around
                lower = upper + around
                faces.append((upper + i, lower + i, lower + j, upper + j))
            base = 1 + (rings - 2) * around
            faces.append((last, base + j, base + i))
        return vertices, faces
    if shape in ('cylinder', 'cone'):
        height = params['height']
        bottom = params['radius']
        top = params.get('top', bottom if shape == 'cylinder' else 0.0)
        count = _segments(16, detail, 6)
        vertices = _circle(bottom, -height / 2.0, count)
        faces = [tuple(range(count - 1, -1, -1))]
        if top <= 1e-6:
            vertices.append((0.0, 0.0, height / 2.0))
            for i in range(count):
                faces.append((i, (i + 1) % count, count))
            return vertices, faces
        vertices += _circle(top, height / 2.0, count)
        for i in range(count):
            j = (i + 1) % count
            faces.append((i, j, count + j, count + i))
        faces.append(tuple(range(count, 2 * count)))
        return vertices, faces
    if shape == 'torus':
        major, tube = params['radius'], params['tube']
        around = _segments(24, detail, 8)
        across = _segments(8, detail, 4)
        vertices = []
        for i in range(around):
            u = 2.0 * math.pi * i / around
            for j in range(across):
                v = 2.0 * math.pi * j / across
                reach = major + tube * math.cos(v)
                vertices.append((reach * math.cos(u), reach * math.sin(u), tube * math.sin(v)))
        faces = []
        for i in range(around):
            for j in range(across):
                a = i * across + j
                b = ((i + 1) % around) * across + j
                c = ((i + 1) % around) * across + (j + 1) % across
                d = i * across + (j + 1) % across
                faces.append((a, b, c, d))
        return vertices, faces
    if shape == 'crystal':
        radius, height = params['radius'], params['height']
        waist = height * 0.22
        vertices = [(0.0, 0.0, -height / 2.0)] + _circle(radius, -waist, 6) + _circle(radius, waist, 6)
        vertices.append((0.0, 0.0, height / 2.0))
        faces = []
        for i in range(6):
            j = (i + 1) % 6
            faces.append((0, 1 + j, 1 + i))
            faces.append((1 + i, 1 + j, 7 + j, 7 + i))
            faces.append((13, 7 + i, 7 + j))
        return vertices, faces
    if shape == 'gem':
        radius = params['radius']
        t = (1.0 + math.sqrt(5.0)) / 2.0
        scale = radius / math.sqrt(1.0 + t * t)
        raw = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
               (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
        vertices = [(x * scale, y * scale, z * scale) for x, y, z in raw]
        faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
                 (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
                 (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        return vertices, faces
    raise ValueError('Unknown shape: ' + str(shape))


def place(vertices, scale, rotate, at):
    """Scale, rotate by XYZ Euler degrees (X first, as Blender does) and move."""
    rx, ry, rz = (math.radians(angle) for angle in rotate)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    result = []
    for x, y, z in vertices:
        x, y, z = x * scale[0], y * scale[1], z * scale[2]
        y, z = y * cx - z * sx, y * sx + z * cx
        x, z = x * cy + z * sy, -x * sy + z * cy
        x, y = x * cz - y * sz, x * sz + y * cz
        result.append((x + at[0], y + at[1], z + at[2]))
    return result


def instances(scene):
    """Expand ring, repeat and scatter copies into one entry per placed object."""
    result = []
    for number, item in enumerate(scene['objects']):
        base = dict(item)
        array = base.pop('array', None)
        if not array:
            result.append(base)
            continue
        at, rotate, scale = item['at'], item['rotate'], item['scale']
        if array['kind'] == 'ring':
            for i in range(array['count']):
                angle = 2.0 * math.pi * i / array['count'] + math.radians(array.get('start', 0.0))
                copy = dict(base)
                copy['at'] = [at[0] + array['radius'] * math.cos(angle), at[1] + array['radius'] * math.sin(angle), at[2]]
                copy['rotate'] = [rotate[0], rotate[1], rotate[2] + math.degrees(angle)]
                copy['name'] = item['name'] + ' ' + str(i + 1)
                result.append(copy)
        elif array['kind'] == 'repeat':
            for i in range(array['count']):
                copy = dict(base)
                copy['at'] = [at[axis] + array['step'][axis] * i for axis in range(3)]
                copy['rotate'] = [rotate[axis] + array.get('turn', [0.0, 0.0, 0.0])[axis] * i for axis in range(3)]
                grow = array.get('grow', 1.0) ** i
                copy['scale'] = [scale[axis] * grow for axis in range(3)]
                copy['name'] = item['name'] + ' ' + str(i + 1)
                result.append(copy)
        else:
            source = random.Random(array['seed'] * 7919 + number)
            for i in range(array['count']):
                copy = dict(base)
                size = 1.0 + (source.random() * 2.0 - 1.0) * array['vary']
                copy['at'] = [at[0] + (source.random() - 0.5) * array['area'][0],
                              at[1] + (source.random() - 0.5) * array['area'][1],
                              at[2] + source.random() * array['lift']]
                copy['rotate'] = [rotate[0], rotate[1], rotate[2] + (source.random() * 360.0 if array['spin'] else 0.0)]
                copy['scale'] = [scale[axis] * size for axis in range(3)]
                copy['name'] = item['name'] + ' ' + str(i + 1)
                result.append(copy)
    return result


def build_meshes(scene):
    """Return one placed mesh per object: name, vertices, faces and material index."""
    meshes = []
    for item in instances(scene):
        vertices, faces = mesh_for(item['shape'], item['params'], scene.get('detail', 1.0))
        meshes.append({'name': item['name'], 'material': item['material'],
                       'vertices': place(vertices, item['scale'], item['rotate'], item['at']), 'faces': faces})
    return meshes
'''
exec(compile(KERNEL_SOURCE, 'scene_kit kernel', 'exec'), globals())  # one source of truth for app and Blender


_NAMED_COLOURS = {
    'white': '#ffffff', 'black': '#111111', 'red': '#e53935', 'green': '#43a047', 'blue': '#1e88e5',
    'yellow': '#fdd835', 'orange': '#fb8c00', 'purple': '#8e24aa', 'pink': '#ec407a', 'brown': '#795548',
    'grey': '#9e9e9e', 'gray': '#9e9e9e', 'cyan': '#26c6da', 'teal': '#00897b', 'gold': '#ffc107',
    'silver': '#c0c0c0', 'navy': '#1a237e', 'beige': '#e8dcc0', 'magenta': '#d81b60', 'lime': '#c0ca33',
}
_TRIANGLES = {'box': 12, 'plane': 2, 'pyramid': 6, 'crystal': 24, 'gem': 20}


def parse_colour(value, default=(0.8, 0.8, 0.8)):
    """Accept #rgb, #rrggbb, a basic colour name or three numbers. Returns sRGB floats."""
    if isinstance(value, str):
        text = _NAMED_COLOURS.get(value.strip().lower(), value.strip())
        match = re.fullmatch(r'#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})', text)
        if not match:
            return default
        digits = match.group(1)
        if len(digits) == 3:
            digits = ''.join(char * 2 for char in digits)
        return tuple(int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    if isinstance(value, (list, tuple)) and len(value) >= 3 and all(_is_number(part) for part in value[:3]):
        parts = [float(part) for part in value[:3]]
        if max(parts) > 1.0:
            parts = [part / 255.0 for part in parts]
        return tuple(min(1.0, max(0.0, part)) for part in parts)
    return default


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(value, default, low, high):
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return default
    if not _is_number(value):
        return default
    return min(high, max(low, float(value)))


def _triple(value, default, low, high):
    if _is_number(value):
        value = [value, value, value]
    if not isinstance(value, (list, tuple)) or not value:
        return list(default)
    parts = [_number(part, default[min(i, 2)], low, high) for i, part in enumerate(value[:3])]
    while len(parts) < 3:
        parts.append(default[len(parts)])
    return parts


def linear(colour):
    """sRGB to linear light, which Blender materials and glTF both expect."""
    return tuple(part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4 for part in colour)


def _text(value, default, limit=80):
    if not isinstance(value, str):
        return default
    cleaned = re.sub(r'[^\w .,:;!?&()+\-]', '', value, flags=re.UNICODE).strip()
    return cleaned[:limit] or default


def _triangle_estimate(shape, detail):
    if shape in _TRIANGLES:
        return _TRIANGLES[shape]
    around = max(6, int(round(16 * detail)))
    if shape == 'sphere':
        return around * 2 * (max(4, int(round(10 * detail))) - 1)
    if shape == 'torus':
        return max(8, int(round(24 * detail))) * max(4, int(round(8 * detail))) * 2
    return around * 4


def normalize_scene(data, *, origin='model'):
    """Validate a scene description and fill in what it leaves out.

    Raises ValueError with a sentence a model can act on. The result is the
    only form the builders accept, so nothing unchecked reaches a file.
    """
    if not isinstance(data, dict):
        raise ValueError('The scene must be one JSON object with a list called objects.')
    source = data.get('objects')
    if not isinstance(source, list) or not source:
        raise ValueError('The scene needs a non-empty list called objects.')
    limit = MAX_INSTANCES if origin == 'template' else MAX_SOURCE_OBJECTS
    if len(source) > limit:
        raise ValueError(f'Use at most {limit} objects; repeat shapes with ring, repeat or scatter.')
    materials, lookup, objects = [], {}, []
    for index, entry in enumerate(source):
        if not isinstance(entry, dict):
            raise ValueError(f'Object {index + 1} must be a JSON object.')
        shape = str(entry.get('shape', '')).strip().lower()
        shape = {'cube': 'box', 'block': 'box', 'ball': 'sphere', 'uv_sphere': 'sphere', 'icosphere': 'gem',
                 'tube': 'cylinder', 'ring': 'torus', 'donut': 'torus', 'floor': 'plane', 'ground': 'plane',
                 'tetrahedron': 'pyramid', 'diamond': 'crystal'}.get(shape, shape)
        if shape not in SHAPES:
            raise ValueError(f'Object {index + 1} has shape "{str(entry.get("shape"))[:30]}". Use one of: '
                             + ', '.join(SHAPES) + '.')
        size = entry.get('size')
        if shape in ('box', 'pyramid'):
            params = {'size': _triple(size if size is not None else entry.get('scale_size'), (1.0, 1.0, 1.0), 0.01, 500.0)}
        elif shape == 'plane':
            pair = _triple(size, (10.0, 10.0, 0.0), 0.01, 2000.0)
            params = {'size': [pair[0], pair[1] if (isinstance(size, (list, tuple)) and len(size) > 1) else pair[0]]}
        else:
            fallback = size[0] / 2.0 if isinstance(size, (list, tuple)) and size and _is_number(size[0]) else (
                size / 2.0 if _is_number(size) else 0.5)
            radius = _number(entry.get('radius'), fallback, 0.005, 500.0)
            params = {'radius': radius}
            if shape in ('cylinder', 'cone', 'crystal'):
                tall = size[2] if isinstance(size, (list, tuple)) and len(size) > 2 and _is_number(size[2]) else radius * 2.0
                params['height'] = _number(entry.get('height'), tall, 0.01, 1000.0)
            if shape == 'cone' and entry.get('top') is not None:
                params['top'] = _number(entry.get('top'), 0.0, 0.0, 500.0)
            if shape == 'torus':
                params['tube'] = _number(entry.get('tube'), radius * 0.2, 0.002, radius)
        colour = parse_colour(entry.get('color', entry.get('colour')))
        glow = _number(entry.get('glow', entry.get('emission')), 0.0, 0.0, 50.0)
        if entry.get('glow') is True:
            glow = 2.0
        metallic = _number(entry.get('metallic'), 0.0, 0.0, 1.0)
        roughness = _number(entry.get('roughness'), 0.55, 0.0, 1.0)
        key = (tuple(round(part, 3) for part in colour), round(glow, 2), round(metallic, 2), round(roughness, 2))
        if key not in lookup:
            lookup[key] = len(materials)
            materials.append({'name': 'Material ' + str(len(materials) + 1), 'color': list(key[0]),
                              'linear': [round(part, 5) for part in linear(key[0])],
                              'glow': key[1], 'metallic': key[2], 'roughness': key[3]})
        item = {'name': _text(entry.get('name'), shape.capitalize() + ' ' + str(index + 1), 48), 'shape': shape,
                'params': params, 'at': _triple(entry.get('at', entry.get('location', entry.get('position'))),
                                                (0.0, 0.0, 0.0), -2000.0, 2000.0),
                'rotate': _triple(entry.get('rotate', entry.get('rotation')), (0.0, 0.0, 0.0), -3600.0, 3600.0),
                'scale': _triple(entry.get('scale'), (1.0, 1.0, 1.0), 0.001, 1000.0), 'material': lookup[key]}
        ring, repeat, scatter = entry.get('ring'), entry.get('repeat'), entry.get('scatter')
        if isinstance(ring, dict):
            item['array'] = {'kind': 'ring', 'count': int(_number(ring.get('count'), 8, 1, 400)),
                             'radius': _number(ring.get('radius'), 4.0, 0.0, 2000.0),
                             'start': _number(ring.get('start'), 0.0, -3600.0, 3600.0)}
        elif isinstance(repeat, dict):
            item['array'] = {'kind': 'repeat', 'count': int(_number(repeat.get('count'), 4, 1, 400)),
                             'step': _triple(repeat.get('step'), (1.0, 0.0, 0.0), -500.0, 500.0),
                             'turn': _triple(repeat.get('turn'), (0.0, 0.0, 0.0), -360.0, 360.0),
                             'grow': _number(repeat.get('grow'), 1.0, 0.5, 1.5)}
        elif isinstance(scatter, dict):
            area = _triple(scatter.get('area'), (10.0, 10.0, 0.0), 0.0, 4000.0)
            item['array'] = {'kind': 'scatter', 'count': int(_number(scatter.get('count'), 20, 1, 600)),
                             'area': [area[0], area[1]], 'seed': int(_number(scatter.get('seed'), index + 1, 0, 100000)),
                             'vary': _number(scatter.get('vary'), 0.25, 0.0, 0.9),
                             'lift': _number(scatter.get('lift', scatter.get('height')), 0.0, 0.0, 2000.0),
                             'spin': scatter.get('spin', True) is not False}
        objects.append(item)
    scene = {'title': _text(data.get('title'), '3D scene'), 'origin': origin,
             'background': list(parse_colour(data.get('background'), (0.05, 0.07, 0.12))),
             'materials': materials, 'objects': objects, 'detail': 1.0}
    placed = instances(scene)  # noqa: F821 - defined by KERNEL_SOURCE
    if len(placed) > MAX_INSTANCES:
        raise ValueError(f'The scene expands to {len(placed)} objects. Keep it under {MAX_INSTANCES}.')
    for detail in (1.0, 0.7, 0.5, 0.38):
        scene['detail'] = detail
        if sum(_triangle_estimate(item['shape'], detail) for item in placed) <= MAX_TRIANGLES:
            break
    else:
        raise ValueError('The scene has too much geometry. Use fewer spheres and tori, or fewer copies.')
    low, high = bounds(scene)
    centre = [(low[axis] + high[axis]) / 2.0 for axis in range(3)]
    reach = max(1.0, max(high[axis] - low[axis] for axis in range(3)) / 2.0)
    camera = data.get('camera') if isinstance(data.get('camera'), dict) else {}
    look_at = _triple(camera.get('look_at', camera.get('target')), centre, -4000.0, 4000.0)
    default_eye = (centre[0] + reach * 1.35, centre[1] - reach * 1.9, max(centre[2], low[2]) + reach * 0.95)
    scene['camera'] = {'at': _triple(camera.get('at', camera.get('location')), default_eye, -8000.0, 8000.0),
                       'look_at': look_at}
    lights = []
    for entry in (data.get('lights') if isinstance(data.get('lights'), list) else [])[:12]:
        if not isinstance(entry, dict):
            continue
        kind = 'sun' if str(entry.get('type', entry.get('kind', 'point'))).lower().startswith('sun') else 'point'
        lights.append({'kind': kind, 'at': _triple(entry.get('at', entry.get('location')), (reach, -reach, reach * 2),
                                                   -8000.0, 8000.0),
                       'color': list(parse_colour(entry.get('color', entry.get('colour')), (1.0, 0.97, 0.9))),
                       'power': _number(entry.get('power', entry.get('energy')), 3.0 if kind == 'sun' else 600.0, 0.0, 100000.0)})
    if not lights:
        lights = [{'kind': 'sun', 'at': [centre[0] + reach, centre[1] - reach * 0.8, high[2] + reach * 2.0],
                   'color': [1.0, 0.96, 0.88], 'power': 3.0},
                  {'kind': 'point', 'at': [centre[0] - reach * 0.8, centre[1] - reach, centre[2] + reach * 1.2],
                   'color': [0.72, 0.82, 1.0], 'power': 60.0 * reach * reach}]
    scene['lights'] = lights
    scene['centre'] = centre
    scene['reach'] = reach
    return scene


def bounds(scene):
    low, high = [math.inf] * 3, [-math.inf] * 3
    for mesh in build_meshes(scene):  # noqa: F821 - defined by KERNEL_SOURCE
        for vertex in mesh['vertices']:
            for axis in range(3):
                low[axis] = min(low[axis], vertex[axis])
                high[axis] = max(high[axis], vertex[axis])
    return low, high


def _triangles(faces):
    for face in faces:
        for index in range(1, len(face) - 1):
            yield face[0], face[index], face[index + 1]


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    x, y, z = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    return x / length, y / length, z / length


def statistics(scene):
    meshes = build_meshes(scene)  # noqa: F821
    return {'objects': len(meshes), 'triangles': sum(sum(len(face) - 2 for face in mesh['faces']) for mesh in meshes),
            'materials': len(scene['materials'])}


# --------------------------------------------------------------------- glTF

def to_glb(scene):
    """Binary glTF 2.0 with one node per object and flat-shaded triangles."""
    meshes = build_meshes(scene)  # noqa: F821
    positions, normals = bytearray(), bytearray()
    accessors, gltf_meshes, nodes = [], [], []
    for mesh in meshes:
        start = len(positions)
        count = 0
        low, high = [math.inf] * 3, [-math.inf] * 3
        for a, b, c in _triangles(mesh['faces']):
            corners = [mesh['vertices'][a], mesh['vertices'][b], mesh['vertices'][c]]
            # Blender is Z-up; glTF is Y-up: (x, y, z) -> (x, z, -y).
            converted = [(x, z, -y) for x, y, z in corners]
            normal = _normal(*converted)
            for point in converted:
                positions += struct.pack('<3f', *point)
                normals += struct.pack('<3f', *normal)
                for axis in range(3):
                    low[axis] = min(low[axis], point[axis])
                    high[axis] = max(high[axis], point[axis])
                count += 1
        if not count:
            continue
        # min/max must describe the stored 32-bit values exactly.
        low = [struct.unpack('<f', struct.pack('<f', value))[0] for value in low]
        high = [struct.unpack('<f', struct.pack('<f', value))[0] for value in high]
        accessors.append({'bufferView': 0, 'byteOffset': start, 'componentType': 5126, 'count': count,
                          'type': 'VEC3', 'min': low, 'max': high})
        accessors.append({'bufferView': 1, 'byteOffset': start, 'componentType': 5126, 'count': count, 'type': 'VEC3'})
        gltf_meshes.append({'name': mesh['name'], 'primitives': [{
            'attributes': {'POSITION': len(accessors) - 2, 'NORMAL': len(accessors) - 1},
            'material': mesh['material'], 'mode': 4}]})
        nodes.append({'name': mesh['name'], 'mesh': len(gltf_meshes) - 1})
    if not nodes:
        raise ValueError('The scene has no geometry to export.')
    materials = []
    strong = False
    for item in scene['materials']:
        colour = item['linear']
        material = {'name': item['name'], 'doubleSided': True, 'pbrMetallicRoughness': {
            'baseColorFactor': [colour[0], colour[1], colour[2], 1.0],
            'metallicFactor': item['metallic'], 'roughnessFactor': item['roughness']}}
        if item['glow'] > 0:
            material['emissiveFactor'] = [min(1.0, part * min(1.0, item['glow'])) for part in colour]
            if item['glow'] > 1.0:
                material['extensions'] = {'KHR_materials_emissive_strength': {'emissiveStrength': item['glow']}}
                strong = True
        materials.append(material)
    binary = bytes(positions) + bytes(normals)
    document = {'asset': {'version': '2.0', 'generator': 'TalkToAi Code scene kit'}, 'scene': 0,
                'scenes': [{'name': scene['title'], 'nodes': list(range(len(nodes)))}], 'nodes': nodes,
                'meshes': gltf_meshes, 'materials': materials, 'accessors': accessors,
                'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions), 'target': 34962},
                                {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(normals), 'target': 34962}],
                'buffers': [{'byteLength': len(binary)}]}
    if strong:
        document['extensionsUsed'] = ['KHR_materials_emissive_strength']
    text = json.dumps(document, separators=(',', ':')).encode('utf-8')
    text += b' ' * (-len(text) % 4)
    binary += b'\x00' * (-len(binary) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(text), b'JSON') + text
            + struct.pack('<I4s', len(binary), b'BIN\x00') + binary)


def check_glb(data):
    """Read a .glb back and confirm its structure. Returns counts; raises ValueError.

    This checks the container, the JSON and every accessor against the binary
    chunk. It does not render the file.
    """
    if len(data) < 28 or data[:4] != b'glTF':
        raise ValueError('The file is not a binary glTF.')
    version, total = struct.unpack('<II', data[4:12])
    json_length, json_kind = struct.unpack('<I4s', data[12:20])
    if version != 2 or total != len(data) or json_kind != b'JSON' or json_length % 4 or 20 + json_length + 8 > total:
        raise ValueError('The glTF header does not match the file.')
    document = json.loads(data[20:20 + json_length])
    bin_length, bin_kind = struct.unpack('<I4s', data[20 + json_length:28 + json_length])
    if bin_kind != b'BIN\x00' or 28 + json_length + bin_length != total or bin_length % 4:
        raise ValueError('The glTF binary chunk does not match the file.')
    views = document['bufferViews']
    if any(view['byteOffset'] + view['byteLength'] > bin_length for view in views):
        raise ValueError('A glTF buffer view is outside the file.')
    triangles = 0
    for mesh in document['meshes']:
        for primitive in mesh['primitives']:
            position = document['accessors'][primitive['attributes']['POSITION']]
            normal = document['accessors'][primitive['attributes']['NORMAL']]
            for accessor in (position, normal):
                if accessor['byteOffset'] + accessor['count'] * 12 > views[accessor['bufferView']]['byteLength']:
                    raise ValueError('A glTF accessor is outside its buffer view.')
            if position['count'] != normal['count'] or position['count'] % 3 or not position['count']:
                raise ValueError('A glTF mesh has an incomplete triangle list.')
            if not 0 <= primitive['material'] < len(document['materials']):
                raise ValueError('A glTF mesh names a missing material.')
            triangles += position['count'] // 3
    if len(document['scenes'][0]['nodes']) != len(document['nodes']) or not document['nodes']:
        raise ValueError('The glTF scene does not list its objects.')
    return {'objects': len(document['nodes']), 'triangles': triangles, 'materials': len(document['materials'])}


# ------------------------------------------------------------------ Blender

def _literal(value, indent=0):
    """Python source for plain data, one object per line so the script stays readable."""
    pad = ' ' * indent
    if isinstance(value, dict):
        if all(not isinstance(item, (dict, list)) or (isinstance(item, list) and all(_is_number(x) for x in item))
               for item in value.values()) or indent >= 8:
            return '{' + ', '.join(repr(key) + ': ' + _literal(item, 99) for key, item in value.items()) + '}'
        rows = [pad + '    ' + repr(key) + ': ' + _literal(item, indent + 4) + ',' for key, item in value.items()]
        return '{\n' + '\n'.join(rows) + '\n' + pad + '}'
    if isinstance(value, (list, tuple)):
        if all(_is_number(item) for item in value) or indent >= 99:
            return '[' + ', '.join(_literal(item, 99) for item in value) + ']'
        rows = [pad + '    ' + _literal(item, 99 if isinstance(item, dict) else indent + 4) + ',' for item in value]
        return '[\n' + '\n'.join(rows) + '\n' + pad + ']'
    if isinstance(value, float):
        return repr(round(value, 5))
    return repr(value)


_BLENDER_BUILDER = r'''
def _set_input(node, names, value):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return


def _aim(obj, target):
    from mathutils import Vector
    direction = Vector(target) - obj.location
    if direction.length > 1e-6:
        obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def build_in_blender(scene):
    import bpy
    target = bpy.context.scene
    materials = []
    for spec in scene['materials']:
        material = bpy.data.materials.new(spec['name'])
        colour = (spec['linear'][0], spec['linear'][1], spec['linear'][2], 1.0)
        material.diffuse_color = colour
        try:  # appearance only: an API difference here must not stop the geometry
            material.use_nodes = True
            shader = next((node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
            if shader is not None:
                _set_input(shader, ('Base Color',), colour)
                _set_input(shader, ('Metallic',), spec['metallic'])
                _set_input(shader, ('Roughness',), spec['roughness'])
                if spec['glow'] > 0:
                    _set_input(shader, ('Emission Color', 'Emission'), colour)
                    _set_input(shader, ('Emission Strength',), spec['glow'])
        except Exception as error:
            print('Material detail skipped:', error)
        materials.append(material)
    for item in build_meshes(scene):
        mesh = bpy.data.meshes.new(item['name'])
        mesh.from_pydata(item['vertices'], [], item['faces'])
        mesh.update()
        mesh.materials.append(materials[item['material']])
        target.collection.objects.link(bpy.data.objects.new(item['name'], mesh))
    try:
        for number, spec in enumerate(scene['lights']):
            light = bpy.data.lights.new('Light ' + str(number + 1), 'SUN' if spec['kind'] == 'sun' else 'POINT')
            light.color = spec['color']
            light.energy = spec['power']
            holder = bpy.data.objects.new(light.name, light)
            holder.location = spec['at']
            target.collection.objects.link(holder)
            if spec['kind'] == 'sun':
                _aim(holder, scene['centre'])
        camera = bpy.data.cameras.new('Camera')
        camera.clip_end = max(1000.0, scene['reach'] * 20.0)
        holder = bpy.data.objects.new('Camera', camera)
        holder.location = scene['camera']['at']
        target.collection.objects.link(holder)
        _aim(holder, scene['camera']['look_at'])
        target.camera = holder
        world = bpy.data.worlds.new('World')
        target.world = world
        sky = linear_rgb(scene['background'])
        world.color = sky
        world.use_nodes = True
        backdrop = next((node for node in world.node_tree.nodes if node.type == 'BACKGROUND'), None)
        if backdrop is not None:
            backdrop.inputs[0].default_value = (sky[0], sky[1], sky[2], 1.0)
    except Exception as error:
        print('Lighting or camera detail skipped:', error)


def linear_rgb(colour):
    return [part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4 for part in colour]
'''


def to_blender_script(scene):
    """A stand-alone bpy script: the geometry code above plus this scene as data."""
    origin = {'template': 'TalkToAi Code built-in design (no model call)',
              'model': 'a scene description written by the selected model'}.get(scene.get('origin'), 'TalkToAi Code')
    data = {key: scene[key] for key in ('title', 'background', 'detail', 'centre', 'reach', 'camera', 'lights',
                                        'materials', 'objects')}
    return ('# ' + scene['title'] + '\n'
            '# Built by the TalkToAi Code scene kit from ' + origin + '.\n'
            '# Edit SCENE below and run this file in Blender (Scripting > Run Script) to rebuild it.\n'
            '# It only creates objects, materials, lights and a camera in the open scene.\n'
            'import math\nimport random\n' + KERNEL_SOURCE + _BLENDER_BUILDER
            + '\n\nSCENE = ' + _literal(data) + '\n\n\nbuild_in_blender(SCENE)\n')


# ------------------------------------------------------------------- viewer

def _viewer_batches(scene):
    """Shared-vertex triangle batches per material, small enough to embed in a page."""
    groups = {}
    for mesh in build_meshes(scene):  # noqa: F821
        group = groups.setdefault(mesh['material'], [])
        if not group or len(group[-1][0]) // 3 + len(mesh['vertices']) > 65000:
            group.append(([], []))
        points, indices = group[-1]
        offset = len(points) // 3
        for x, y, z in mesh['vertices']:
            points += (x, z, -y)
        for a, b, c in _triangles(mesh['faces']):
            indices += (offset + a, offset + b, offset + c)
    batches = []
    for material, group in sorted(groups.items()):
        spec = scene['materials'][material]
        for points, indices in group:
            batches.append({'c': [round(part, 4) for part in spec['color']], 'g': spec['glow'],
                            'p': base64.b64encode(struct.pack('<%df' % len(points), *points)).decode('ascii'),
                            'i': base64.b64encode(struct.pack('<%dH' % len(indices), *indices)).decode('ascii')})
    return batches


_VIEWER = r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>html,body{margin:0;height:100%;overflow:hidden;background:__CSS__;font-family:Segoe UI,system-ui,sans-serif}
canvas{display:block;width:100%;height:100%;touch-action:none;cursor:grab}
#tag{position:fixed;left:16px;bottom:14px;color:#fff;text-shadow:0 1px 3px #000;font-size:13px;line-height:1.5;opacity:.86}
#tag b{font-size:16px}</style></head><body><canvas id="view"></canvas>
<div id="tag"><b>__TITLE__</b><br>Drag to look around · scroll or pinch to zoom<br><span id="note">__NOTE__</span></div>
<script>
const SCENE=__DATA__;
const canvas=document.getElementById('view');
const gl=canvas.getContext('webgl',{antialias:true});
if(!gl){document.getElementById('note').textContent='This browser cannot show WebGL. Open scene.glb in another viewer.';}
else{
const flat=gl.getExtension('OES_standard_derivatives');
const vs='attribute vec3 p;uniform mat4 m;uniform mat4 v;varying vec3 w;varying float d;void main(){w=p;vec4 e=v*vec4(p,1.0);d=-e.z;gl_Position=m*vec4(p,1.0);}';
const fs=(flat?'#extension GL_OES_standard_derivatives : enable\n':'')+'precision mediump float;uniform vec3 c;uniform float g;uniform vec3 sky;uniform vec3 sun;uniform float far;varying vec3 w;varying float d;void main(){'+
(flat?'vec3 n=normalize(cross(dFdx(w),dFdy(w)));':'vec3 n=vec3(0.0,1.0,0.0);')+
'float k=max(dot(n,sun),0.0);float h=0.5+0.5*n.y;float e=clamp(g/5.0,0.0,0.9);vec3 lit=mix(c*(0.34+0.22*h+0.7*k),min(c*1.12,vec3(1.0)),e);float f=clamp(d/far,0.0,1.0);f=f*f*0.6;gl_FragColor=vec4(mix(lit,sky,f),1.0);}';
function shader(type,text){const s=gl.createShader(type);gl.shaderSource(s,text);gl.compileShader(s);return s;}
const prog=gl.createProgram();gl.attachShader(prog,shader(gl.VERTEX_SHADER,vs));gl.attachShader(prog,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(prog);gl.useProgram(prog);
const loc=n=>gl.getUniformLocation(prog,n);
function bytes(t){const b=atob(t),a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a.buffer;}
const parts=SCENE.batches.map(b=>{const p=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,p);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(bytes(b.p)),gl.STATIC_DRAW);
const i=gl.createBuffer();const idx=new Uint16Array(bytes(b.i));gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,i);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,idx,gl.STATIC_DRAW);return{p,i,n:idx.length,c:b.c,g:b.g};});
let yaw=SCENE.yaw,pitch=SCENE.pitch,dist=SCENE.dist,spin=true,drag=null;
function perspective(f,a,n,r){const t=1/Math.tan(f/2);return[t/a,0,0,0,0,t,0,0,0,0,(r+n)/(n-r),-1,0,0,2*r*n/(n-r),0];}
function look(e,c){let z=[e[0]-c[0],e[1]-c[1],e[2]-c[2]];let l=Math.hypot(...z);z=z.map(x=>x/l);let x=[z[2],0,-z[0]];l=Math.hypot(...x)||1;x=x.map(q=>q/l);
const y=[z[1]*x[2]-z[2]*x[1],z[2]*x[0]-z[0]*x[2],z[0]*x[1]-z[1]*x[0]];
return[x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-(x[0]*e[0]+x[1]*e[1]+x[2]*e[2]),-(y[0]*e[0]+y[1]*e[1]+y[2]*e[2]),-(z[0]*e[0]+z[1]*e[1]+z[2]*e[2]),1];}
function mul(a,b){const o=new Array(16);for(let i=0;i<4;i++)for(let j=0;j<4;j++)o[j*4+i]=a[i]*b[j*4]+a[4+i]*b[j*4+1]+a[8+i]*b[j*4+2]+a[12+i]*b[j*4+3];return o;}
function draw(){const w=canvas.clientWidth*devicePixelRatio|0,h=canvas.clientHeight*devicePixelRatio|0;if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
gl.viewport(0,0,w,h);gl.clearColor(SCENE.sky[0],SCENE.sky[1],SCENE.sky[2],1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);
if(spin)yaw+=0.0025;const c=SCENE.centre;const e=[c[0]+dist*Math.cos(pitch)*Math.sin(yaw),c[1]+dist*Math.sin(pitch),c[2]+dist*Math.cos(pitch)*Math.cos(yaw)];
const view=look(e,c);const far=dist*4+SCENE.reach*4;gl.uniformMatrix4fv(loc('m'),false,mul(perspective(0.8,w/h,Math.max(0.05,dist/200),far),view));gl.uniformMatrix4fv(loc('v'),false,view);
gl.uniform3fv(loc('sky'),SCENE.sky);gl.uniform3fv(loc('sun'),SCENE.sun);gl.uniform1f(loc('far'),far);
for(const b of parts){gl.bindBuffer(gl.ARRAY_BUFFER,b.p);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,b.i);
gl.uniform3fv(loc('c'),b.c);gl.uniform1f(loc('g'),b.g);gl.drawElements(gl.TRIANGLES,b.n,gl.UNSIGNED_SHORT,0);}
requestAnimationFrame(draw);}
gl.bindAttribLocation(prog,0,'p');gl.linkProgram(prog);gl.useProgram(prog);
canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];spin=false;canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing';});
canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw-=(e.clientX-drag[0])*0.006;pitch=Math.max(-1.45,Math.min(1.45,pitch+(e.clientY-drag[1])*0.006));drag=[e.clientX,e.clientY];});
canvas.addEventListener('pointerup',()=>{drag=null;canvas.style.cursor='grab';});
canvas.addEventListener('wheel',e=>{e.preventDefault();spin=false;dist=Math.max(SCENE.reach*0.15,Math.min(SCENE.reach*30,dist*Math.exp(e.deltaY*0.001)));},{passive:false});
draw();}
</script></body></html>
'''


def to_viewer_html(scene, note=''):
    """One HTML file with the geometry embedded. It makes no network request."""
    eye, target = scene['camera']['at'], scene['camera']['look_at']
    # Viewer space is Y-up like glTF.
    offset = (eye[0] - target[0], eye[2] - target[2], -(eye[1] - target[1]))
    distance = math.sqrt(sum(part * part for part in offset)) or 1.0
    sun = next((light for light in scene['lights'] if light['kind'] == 'sun'), None)
    direction = (0.4, 0.8, 0.45)
    if sun:
        raw = (sun['at'][0] - scene['centre'][0], sun['at'][2] - scene['centre'][2], -(sun['at'][1] - scene['centre'][1]))
        length = math.sqrt(sum(part * part for part in raw)) or 1.0
        direction = tuple(part / length for part in raw)
    data = {'batches': _viewer_batches(scene), 'sky': [round(part, 4) for part in scene['background']],
            'centre': [round(target[0], 4), round(target[2], 4), round(-target[1], 4)], 'reach': round(scene['reach'], 4),
            'dist': round(distance, 4), 'yaw': round(math.atan2(offset[0], offset[2]), 5),
            'pitch': round(math.asin(max(-1.0, min(1.0, offset[1] / distance))), 5),
            'sun': [round(part, 4) for part in direction]}
    sky = '#%02x%02x%02x' % tuple(int(round(part * 255)) for part in scene['background'])
    escape = lambda text: text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')  # noqa: E731
    return (_VIEWER.replace('__TITLE__', escape(scene['title'])).replace('__NOTE__', escape(note)[:300])
            .replace('__CSS__', sky).replace('__DATA__', json.dumps(data, separators=(',', ':'))))


# ------------------------------------------------------------------ preview

def _clip_near(points, near):
    """Keep the part of a camera-space polygon in front of the near plane."""
    result = []
    for index, current in enumerate(points):
        previous = points[index - 1]
        inside, was_inside = current[2] >= near, previous[2] >= near
        if inside != was_inside:
            ratio = (near - previous[2]) / (current[2] - previous[2])
            result.append(tuple(previous[axis] + (current[axis] - previous[axis]) * ratio for axis in range(3)))
        if inside:
            result.append(current)
    return result


def render_preview(scene, size=(960, 600)):
    """Draw the scene with Pillow (already an app dependency) and return a PIL image.

    A painter's-order sketch for the chat and Evidence panel, not a render:
    faces are flat shaded and sorted by distance.
    """
    from PIL import Image, ImageDraw
    width, height = size
    eye, target = scene['camera']['at'], scene['camera']['look_at']
    forward = [target[axis] - eye[axis] for axis in range(3)]
    length = math.sqrt(sum(part * part for part in forward)) or 1.0
    forward = [part / length for part in forward]
    right = [forward[1], -forward[0], 0.0]
    length = math.sqrt(sum(part * part for part in right)) or 1.0
    right = [part / length for part in right]
    up = [right[1] * forward[2] - right[2] * forward[1], right[2] * forward[0] - right[0] * forward[2],
          right[0] * forward[1] - right[1] * forward[0]]
    sun = next((light for light in scene['lights'] if light['kind'] == 'sun'), None)
    source = sun['at'] if sun else [scene['centre'][0] + 1.0, scene['centre'][1] - 1.0, scene['centre'][2] + 2.0]
    toward = [source[axis] - scene['centre'][axis] for axis in range(3)]
    length = math.sqrt(sum(part * part for part in toward)) or 1.0
    toward = [part / length for part in toward]
    focal = 0.5 * height / math.tan(0.4)
    near = max(0.05, scene['reach'] / 400.0)
    sky = scene['background']
    drawn = []
    for mesh in build_meshes(scene):  # noqa: F821
        material = scene['materials'][mesh['material']]
        glow = min(0.9, material['glow'] / 5.0)
        faces = []
        for face in mesh['faces']:
            corners = [mesh['vertices'][index] for index in face]
            span = max(max(corner[axis] for corner in corners) - min(corner[axis] for corner in corners) for axis in range(3))
            cuts = min(14, int(span / max(1.0, scene['reach'] * 0.3))) if len(corners) == 4 else 0
            if cuts < 2:
                faces.append(corners)
                continue
            a, b, c, d = corners
            def at(u, v):
                return tuple((a[axis] * (1 - u) + b[axis] * u) * (1 - v) + (d[axis] * (1 - u) + c[axis] * u) * v
                             for axis in range(3))
            for i in range(cuts):
                for j in range(cuts):
                    faces.append([at(i / cuts, j / cuts), at((i + 1) / cuts, j / cuts),
                                  at((i + 1) / cuts, (j + 1) / cuts), at(i / cuts, (j + 1) / cuts)])
        for corners in faces:
            normal = _normal(corners[0], corners[1], corners[2])
            centre = [sum(corner[axis] for corner in corners) / len(corners) for axis in range(3)]
            if sum(normal[axis] * (eye[axis] - centre[axis]) for axis in range(3)) < 0:
                normal = (-normal[0], -normal[1], -normal[2])  # a plane seen from below still shows
            view = []
            for corner in corners:
                relative = [corner[axis] - eye[axis] for axis in range(3)]
                view.append((sum(relative[axis] * right[axis] for axis in range(3)),
                             sum(relative[axis] * up[axis] for axis in range(3)),
                             sum(relative[axis] * forward[axis] for axis in range(3))))
            view = _clip_near(view, near)
            if len(view) < 3:
                continue
            direct = max(0.0, sum(normal[axis] * toward[axis] for axis in range(3)))
            shade = 0.34 + 0.22 * (0.5 + 0.5 * normal[2]) + 0.7 * direct
            depth = max(point[2] for point in view)
            haze = min(1.0, depth / (scene['reach'] * 9.0 + 1.0)) ** 2 * 0.6
            colour = []
            for channel in range(3):
                value = material['color'][channel] * shade * (1.0 - glow) + min(1.0, material['color'][channel] * 1.12) * glow
                value = value * (1.0 - haze) + sky[channel] * haze
                colour.append(int(max(0, min(255, round(value * 255)))))
            drawn.append((depth, [(width / 2 + focal * x / z, height / 2 - focal * y / z) for x, y, z in view], tuple(colour)))
    image = Image.new('RGB', size, tuple(int(round(part * 255)) for part in sky))
    canvas = ImageDraw.Draw(image)
    for _, points, colour in sorted(drawn, key=lambda item: -item[0]):
        canvas.polygon(points, fill=colour)
    return image


# ---------------------------------------------------------------- templates

SCENE_FORMAT = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'background': {'type': 'string'},
        'objects': {'type': 'array', 'minItems': 4, 'maxItems': 80, 'items': {
            'type': 'object',
            'properties': {
                'shape': {'type': 'string', 'enum': list(SHAPES)},
                'at': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                'size': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                'radius': {'type': 'number'}, 'height': {'type': 'number'}, 'tube': {'type': 'number'},
                'color': {'type': 'string'}, 'glow': {'type': 'number'},
                'rotate': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                'ring': {'type': 'object', 'properties': {'count': {'type': 'integer'}, 'radius': {'type': 'number'}},
                         'required': ['count', 'radius']},
                'scatter': {'type': 'object', 'properties': {
                    'count': {'type': 'integer'}, 'area': {'type': 'array', 'items': {'type': 'number'},
                                                           'minItems': 2, 'maxItems': 2}},
                    'required': ['count', 'area']},
            },
            'required': ['shape', 'at', 'color'],
        }},
    },
    'required': ['title', 'background', 'objects'],
}

SCENE_PROMPT = (
    'Design the requested 3D scene as data instead of code. Return one JSON object with title, background '
    '(a hex colour) and objects: a list of 15 to 60 simple shapes. Each object has shape (box, sphere, cylinder, '
    'cone, torus, pyramid, crystal, gem or plane), at [x, y, z] for its centre in metres (z is up, the ground is '
    'z = 0), and color (hex). Give box, pyramid and plane a size [x, y, z]; give round shapes a radius and, for '
    'cylinder, cone and crystal, a height; give torus a radius and tube. Optional: rotate [x, y, z] in degrees, '
    'glow (1 to 10) for lamps and lights, ring {"count", "radius"} to copy an object around a circle, and scatter '
    '{"count", "area": [x, y]} to copy it at random places. Compose a recognisable scene with a ground, a clear '
    'subject, supporting detail and strong colours. The app builds the geometry, lighting and camera from this.')


def _shape(shape, at, colour, **fields):
    item = {'shape': shape, 'at': list(at), 'color': colour}
    item.update(fields)
    return item


def _pine(objects, x, y, height, colour='#1f6b3a', snow=True):
    objects.append(_shape('cylinder', (x, y, height * 0.12), '#5b3a21', radius=height * 0.05, height=height * 0.24))
    for level in range(3):
        radius = height * (0.34 - level * 0.08)
        z = height * (0.36 + level * 0.22)
        objects.append(_shape('cone', (x, y, z), colour, radius=radius, height=height * 0.34))
        if snow:
            objects.append(_shape('cone', (x, y, z + height * 0.1), '#f4f9ff', radius=radius * 0.62, height=height * 0.16))


def _house(objects, x, y, width, depth, height, wall, roof, turn=0.0, snow=True):
    objects.append(_shape('box', (x, y, height / 2), wall, size=[width, depth, height], rotate=[0, 0, turn]))
    objects.append(_shape('pyramid', (x, y, height + height * 0.3), roof, size=[width * 1.18, depth * 1.18, height * 0.6],
                          rotate=[0, 0, turn]))
    if snow:
        objects.append(_shape('pyramid', (x, y, height + height * 0.42), '#f6fbff',
                              size=[width * 0.86, depth * 0.86, height * 0.4], rotate=[0, 0, turn]))
    angle = math.radians(turn)
    front = (x + math.sin(angle) * depth * 0.5, y - math.cos(angle) * depth * 0.5)
    objects.append(_shape('box', (front[0], front[1], height * 0.42), '#ffd27a', size=[width * 0.24, 0.08, height * 0.26],
                          rotate=[0, 0, turn], glow=3.0, name='Lit window'))
    objects.append(_shape('box', (x + width * 0.28, y, height * 1.45), '#6d4c41', size=[width * 0.14, width * 0.14, height * 0.5],
                          name='Chimney'))


def _winter(text, source):
    objects = [_shape('plane', (0, 0, 0), '#eef4fb', size=[70, 70, 0], name='Snow field'),
               _shape('sphere', (-16, 22, 17), '#fff6d6', radius=2.4, glow=4.0, name='Moon')]
    walls = ['#b5553c', '#c98b4b', '#8d6e63', '#a94f45', '#d9a05b']
    roofs = ['#5d4037', '#4e342e', '#37474f']
    for index, (x, y) in enumerate([(-7, 2), (-1.5, 5.5), (4.5, 3), (8.5, -2.5), (-9.5, -4.5)]):
        _house(objects, x, y, 3.0 + source.random(), 2.6 + source.random() * 0.8, 2.2 + source.random() * 0.9,
               walls[index % len(walls)], roofs[index % len(roofs)], turn=source.uniform(-25, 25))
    for _ in range(16):
        angle, reach = source.uniform(0, 2 * math.pi), source.uniform(11, 24)
        _pine(objects, math.cos(angle) * reach, math.sin(angle) * reach, source.uniform(3.2, 6.0))
    for x, y in [(-3.5, -1.5), (2.2, -1.2), (6.0, -6.0), (-6.0, -8.0)]:
        objects.append(_shape('cylinder', (x, y, 1.3), '#263238', radius=0.07, height=2.6, name='Lamp post'))
        objects.append(_shape('gem', (x, y, 2.75), '#ffcf70', radius=0.26, glow=6.0, name='Lamp'))
    for index, (radius, z) in enumerate([(0.75, 0.7), (0.55, 1.65), (0.38, 2.38)]):
        objects.append(_shape('sphere', (0.6, -4.2, z), '#ffffff', radius=radius, name='Snowman ' + str(index + 1)))
    objects.append(_shape('cone', (0.6, -4.62, 2.4), '#ff7a1a', radius=0.07, height=0.45, rotate=[90, 0, 0], name='Carrot nose'))
    objects.append(_shape('cylinder', (0.6, -4.2, 2.82), '#222222', radius=0.3, height=0.32, name='Hat'))
    objects.append(_shape('gem', (0, 0, 1.0), '#ffffff', radius=0.06, glow=1.5, name='Snowflake',
                          scatter={'count': 170, 'area': [46, 46], 'lift': 13, 'seed': source.randrange(1000), 'vary': 0.5}))
    if re.search(r'castle|nottingham|fort|tower', text, re.I):
        objects.append(_shape('cylinder', (0, 17, 2.0), '#8a8f98', radius=8.5, height=4.0, top=6.5, name='Castle rock'))
        objects.append(_shape('box', (0, 17, 5.6), '#a8a39a', size=[8.5, 5.5, 3.2], name='Castle keep'))
        for x in (-4.6, 4.6):
            objects.append(_shape('cylinder', (x, 17, 6.4), '#9c978e', radius=1.1, height=4.8, name='Castle tower'))
            objects.append(_shape('cone', (x, 17, 9.6), '#5a2e2e', radius=1.35, height=1.7, name='Tower roof'))
        for step in range(-3, 4):
            objects.append(_shape('box', (step * 1.15, 14.3, 7.45), '#a8a39a', size=[0.55, 0.35, 0.5], name='Battlement'))
        objects.append(_shape('box', (0, 14.2, 5.0), '#ffd27a', size=[0.9, 0.1, 1.2], glow=3.0, name='Castle gate light'))
    place_name = 'Nottingham' if re.search(r'nottingham', text, re.I) else ''
    return {'title': ('Winter evening in ' + place_name) if place_name else 'Winter village at dusk',
            'background': '#141d3a', 'objects': objects,
            'camera': {'at': [17, -25, 10.5], 'look_at': [0, 2, 3.2]},
            'lights': [{'type': 'sun', 'at': [-16, 22, 17], 'color': '#cfdcff', 'power': 2.2},
                       {'type': 'point', 'at': [0, -3, 5], 'color': '#ffc98a', 'power': 900}]}


def _portal(text, source):
    hue = source.random()
    def tone(shift, light=0.6, sat=0.85):
        red, green, blue = colorsys.hls_to_rgb((hue + shift) % 1.0, light, sat)
        return '#%02x%02x%02x' % (int(red * 255), int(green * 255), int(blue * 255))
    objects = [_shape('cylinder', (0, 0, -0.25), '#12141f', radius=16, height=0.5, metallic=0.6, roughness=0.3, name='Platform'),
               _shape('torus', (0, 0, 0.05), tone(0.0), radius=15.2, tube=0.12, glow=4.0, name='Rim light')]
    for index in range(4):
        objects.append(_shape('torus', (0, 0, 6.2), tone(index * 0.07, 0.62), radius=5.2 - index * 0.9, tube=0.16,
                              rotate=[90, 0, index * 24.0], glow=5.0 + index, metallic=0.4, name='Portal ring'))
    objects.append(_shape('gem', (0, 0, 6.2), '#ffffff', radius=1.1, glow=9.0, name='Core'))
    objects.append(_shape('crystal', (0, 0, 1.9), tone(0.5, 0.7), radius=0.7, height=3.4, glow=1.5, metallic=0.2, roughness=0.15,
                          ring={'count': 9, 'radius': 9.2}, rotate=[0, 12, 0], name='Crystal'))
    objects.append(_shape('crystal', (0, 0, 1.0), tone(0.42, 0.55), radius=0.38, height=1.9, glow=1.0,
                          ring={'count': 18, 'radius': 12.4, 'start': 10}, rotate=[0, -18, 0], name='Shard'))
    objects.append(_shape('box', (0, 0, 2.6), '#1c2033', size=[1.3, 1.3, 5.2], metallic=0.7, roughness=0.25,
                          ring={'count': 6, 'radius': 6.6, 'start': 30}, name='Pylon'))
    objects.append(_shape('gem', (0, 0, 5.5), tone(0.12, 0.7), radius=0.34, glow=7.0,
                          ring={'count': 6, 'radius': 6.6, 'start': 30}, name='Pylon light'))
    for level in range(3):
        objects.append(_shape('gem', (0, 0, 9.5 + level * 1.5), tone(0.2 + level * 0.1, 0.72), radius=0.3 + level * 0.06, glow=5.0,
                              ring={'count': 7 + level * 3, 'radius': 3.4 + level * 1.7, 'start': level * 17.0}, name='Orbiter'))
    objects.append(_shape('gem', (0, 0, 1.0), '#ffffff', radius=0.05, glow=6.0, name='Spark',
                          scatter={'count': 150, 'area': [30, 30], 'lift': 16, 'seed': source.randrange(1000), 'vary': 0.6}))
    return {'title': 'Cosmic nexus portal', 'background': '#05060f', 'objects': objects,
            'camera': {'at': [20, -28, 13.5], 'look_at': [0, 0, 5.0]},
            'lights': [{'type': 'sun', 'at': [9, -12, 22], 'color': '#b9c8ff', 'power': 1.6},
                       {'type': 'point', 'at': [0, 0, 6.2], 'color': tone(0.0, 0.7), 'power': 2500}]}


def _city(text, source):
    objects = [_shape('plane', (0, 0, 0), '#0d0f18', size=[80, 80, 0], name='Street level')]
    neon = ['#ff2e88', '#00e5ff', '#b388ff', '#ffe14d', '#3dff9a']
    for gx in range(-4, 5):
        for gy in range(-3, 4):
            if gx == 0 or gy == 0:
                continue
            height = source.uniform(3.5, 9.0) + max(0.0, 9.0 - (abs(gx) + abs(gy)) * 1.9) * source.uniform(0.9, 2.1)
            width, depth = source.uniform(2.3, 3.4), source.uniform(2.3, 3.4)
            x, y = gx * 5.0 + source.uniform(-0.5, 0.5), gy * 5.0 + source.uniform(-0.5, 0.5)
            shade = source.choice(['#1b2238', '#232b45', '#161b2c', '#2a2f4a'])
            objects.append(_shape('box', (x, y, height / 2), shade, size=[width, depth, height], metallic=0.5, roughness=0.35,
                                  name='Tower'))
            colour = source.choice(neon)
            for band in range(1, int(height // 2.2) + 1):
                objects.append(_shape('box', (x, y, band * 2.0), colour, size=[width + 0.06, depth + 0.06, 0.12], glow=4.0,
                                      name='Neon band'))
            if height > 14:
                objects.append(_shape('cylinder', (x, y, height + 1.6), '#cfd8dc', radius=0.07, height=3.2, name='Antenna'))
                objects.append(_shape('gem', (x, y, height + 3.3), '#ff3b3b', radius=0.18, glow=8.0, name='Beacon'))
    for lane, colour in ((0.9, '#ff5252'), (-0.9, '#fff59d')):
        objects.append(_shape('box', (-34, lane, 0.2), colour, size=[1.4, 0.5, 0.3], glow=5.0, name='Traffic',
                              repeat={'count': 18, 'step': [4.0, 0, 0]}))
        objects.append(_shape('box', (lane, -30, 0.2), colour, size=[0.5, 1.4, 0.3], glow=5.0, name='Traffic',
                              repeat={'count': 16, 'step': [0, 4.0, 0]}))
    objects.append(_shape('sphere', (20, 34, 26), '#ffe9c4', radius=4.2, glow=3.0, name='Moon'))
    return {'title': 'Neon city at night', 'background': '#090b1a', 'objects': objects,
            'camera': {'at': [30, -38, 23], 'look_at': [0, 0, 6.5]},
            'lights': [{'type': 'sun', 'at': [20, 34, 26], 'color': '#9fb4ff', 'power': 1.4},
                       {'type': 'point', 'at': [0, 0, 14], 'color': '#ff5fb0', 'power': 5000}]}


def _island(text, source):
    objects = [_shape('plane', (0, 0, -0.6), '#1d6fa5', size=[90, 90, 0], roughness=0.15, metallic=0.2, name='Sea'),
               _shape('cylinder', (0, 0, -0.1), '#e3cf9a', radius=13, height=1.4, top=10.5, name='Beach'),
               _shape('cylinder', (0, 0, 0.9), '#5fa052', radius=9.6, height=1.2, top=7.4, name='Meadow'),
               _shape('cone', (-2.2, 2.6, 4.6), '#7c8a72', radius=4.6, height=6.4, name='Mountain'),
               _shape('cone', (-2.2, 2.6, 7.0), '#f4f8fb', radius=1.7, height=2.3, name='Snow cap'),
               _shape('cone', (2.8, 3.4, 3.2), '#8b9680', radius=3.0, height=3.8, name='Hill')]
    for _ in range(14):
        angle, reach = source.uniform(0, 2 * math.pi), source.uniform(3.5, 7.8)
        _pine(objects, math.cos(angle) * reach, math.sin(angle) * reach - 1.2, source.uniform(1.6, 3.2), snow=False)
    for x, y in ((6.6, -6.2), (-7.6, -5.2), (4.2, -8.4)):
        objects.append(_shape('cylinder', (x, y, 2.3), '#8d6e4a', radius=0.16, height=3.6, rotate=[8, 6, 0], name='Palm trunk'))
        objects.append(_shape('cone', (x + 0.35, y + 0.25, 4.2), '#3f9b4b', radius=1.5, height=0.5, name='Palm crown'))
    _house(objects, 3.4, -3.6, 2.4, 2.0, 1.7, '#f1e2c4', '#b5553c', turn=20, snow=False)
    objects.append(_shape('sphere', (0, 0, -0.45), '#6f7a6a', radius=0.7, name='Rock',
                          ring={'count': 9, 'radius': 14.6, 'start': 12}, scale=[1.3, 1.0, 0.7]))
    objects.append(_shape('sphere', (-20, 26, 19), '#ffd76a', radius=3.2, glow=5.0, name='Sun'))
    objects.append(_shape('sphere', (0, 0, 13), '#ffffff', radius=1.5, name='Cloud', scale=[2.2, 1.2, 0.6], glow=2.2,
                          scatter={'count': 9, 'area': [48, 40], 'lift': 4, 'seed': source.randrange(1000), 'vary': 0.4, 'spin': False}))
    objects.append(_shape('box', (17, -9, -0.15), '#f5f5f5', size=[3.0, 1.0, 0.9], name='Boat hull'))
    objects.append(_shape('pyramid', (17, -9, 1.6), '#ff7043', size=[0.12, 1.9, 2.6], name='Sail'))
    return {'title': 'Sunlit island', 'background': '#8fd0ff', 'objects': objects,
            'camera': {'at': [24, -31, 14], 'look_at': [0, 0, 2.6]},
            'lights': [{'type': 'sun', 'at': [-20, 26, 19], 'color': '#fff3d6', 'power': 4.0}]}


def _sculpture(text, source):
    hue = source.random()
    def tone(shift, light=0.56, sat=0.9):
        red, green, blue = colorsys.hls_to_rgb((hue + shift) % 1.0, light, sat)
        return '#%02x%02x%02x' % (int(red * 255), int(green * 255), int(blue * 255))
    objects = [_shape('cylinder', (0, 0, -0.2), '#15171f', radius=13, height=0.4, metallic=0.7, roughness=0.25, name='Plinth'),
               _shape('torus', (0, 0, 0.1), tone(0.0, 0.6), radius=12.2, tube=0.1, glow=4.0, name='Plinth light')]
    steps = 34
    for index in range(steps):
        progress = index / (steps - 1)
        angle = progress * math.pi * 5.0
        reach = 1.2 + 3.6 * math.sin(progress * math.pi)
        objects.append(_shape('box', (math.cos(angle) * reach, math.sin(angle) * reach, 0.9 + progress * 12.5),
                              tone(progress * 0.55), size=[2.3 - progress * 1.1, 0.7, 0.34],
                              rotate=[progress * 30.0, 0, math.degrees(angle) + 90.0], metallic=0.55, roughness=0.25,
                              glow=0.6 if index % 3 else 2.5, name='Ribbon step'))
    objects.append(_shape('cylinder', (0, 0, 7.0), '#e8eaf6', radius=0.16, height=14.0, metallic=0.9, roughness=0.15, name='Spine'))
    objects.append(_shape('gem', (0, 0, 14.9), '#ffffff', radius=1.0, glow=9.0, name='Crown'))
    for index in range(3):
        objects.append(_shape('torus', (0, 0, 4.0 + index * 3.6), tone(0.18 + index * 0.16, 0.66), radius=6.4 - index * 1.3,
                              tube=0.09, rotate=[18.0 + index * 14.0, 10.0 * index, index * 40.0], glow=5.0, name='Orbit'))
    objects.append(_shape('gem', (0, 0, 2.4), tone(0.33, 0.7), radius=0.42, glow=4.0, name='Satellite',
                          ring={'count': 10, 'radius': 9.4}))
    objects.append(_shape('crystal', (0, 0, 1.3), tone(0.6, 0.66), radius=0.45, height=2.4, metallic=0.2, roughness=0.12, glow=1.2,
                          ring={'count': 14, 'radius': 11.0, 'start': 13}, name='Crystal'))
    objects.append(_shape('gem', (0, 0, 1.0), '#ffffff', radius=0.045, glow=6.0, name='Spark',
                          scatter={'count': 130, 'area': [26, 26], 'lift': 17, 'seed': source.randrange(1000), 'vary': 0.6}))
    return {'title': 'Helix of light', 'background': '#070812', 'objects': objects,
            'camera': {'at': [18, -25, 13], 'look_at': [0, 0, 6.6]},
            'lights': [{'type': 'sun', 'at': [10, -12, 24], 'color': '#dfe6ff', 'power': 2.0},
                       {'type': 'point', 'at': [0, 0, 15], 'color': tone(0.0, 0.72), 'power': 3500}]}


_TEMPLATES = (
    ('winter', r'\b(?:winter|snow\w*|christmas|xmas|frost\w*|ic[ey]|nottingham|village|cabin|chalet)\b', _winter),
    ('portal', r'\b(?:portal|crystals?|sci-?fi|futuris\w*|space|cosmic|galax\w*|nexus|alien|magic\w*|fantasy)\b', _portal),
    ('city', r'\b(?:city|cities|skyline|neon|cyber\w*|downtown|streets?|buildings?|skyscrapers?|metropolis|urban)\b', _city),
    ('island', r'\b(?:islands?|forest|trees?|nature|garden|mountains?|landscape|lake|beach|summer|sea|ocean|park)\b', _island),
    ('sculpture', r'\b(?:sculptures?|abstract|artwork|statue|installation)\b', _sculpture),
)
_OPEN_CHOICE = ('portal', 'sculpture', 'city', 'island', 'winter')


def template_scene(request=''):
    """Choose a built-in design from the words of the request. Returns (key, scene)."""
    text = request if isinstance(request, str) else ''
    seed = int.from_bytes(hashlib.sha256(text.strip().lower().encode('utf-8')).digest()[:4], 'big')
    builders = {key: builder for key, _, builder in _TEMPLATES}
    key = next((name for name, pattern, _ in _TEMPLATES if re.search(pattern, text, re.I)), None)
    if key is None:
        key = _OPEN_CHOICE[seed % len(_OPEN_CHOICE)]
    return key, normalize_scene(builders[key](text, random.Random(seed)), origin='template')
