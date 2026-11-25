"""The built-in scene kit: checked scene descriptions, GLB files, a viewer page and a Blender script."""
import contextlib
import io
import json
import math
import struct
import sys
import types
import unittest

import scene_kit as kit


def stand_in_bpy():
    """Just enough of Blender's API to run the generated script and inspect what it built."""
    built = {'meshes': [], 'linked': []}

    class Mesh:
        def __init__(self, name):
            self.name, self.materials = name, []

        def from_pydata(self, vertices, edges, faces):
            self.vertices, self.faces = vertices, faces

        def update(self):
            pass

    def new_mesh(name):
        mesh = Mesh(name)
        built['meshes'].append(mesh)
        return mesh

    def new_object(name, data):
        return types.SimpleNamespace(name=name, data=data, location=(0, 0, 0))

    data = types.SimpleNamespace(
        meshes=types.SimpleNamespace(new=new_mesh),
        materials=types.SimpleNamespace(new=lambda name: types.SimpleNamespace(name=name)),
        objects=types.SimpleNamespace(new=new_object),
        lights=types.SimpleNamespace(new=lambda name, kind: types.SimpleNamespace(name=name)),
        cameras=types.SimpleNamespace(new=lambda name: types.SimpleNamespace(name=name)),
        worlds=types.SimpleNamespace(new=lambda name: types.SimpleNamespace(name=name)))
    collection = types.SimpleNamespace(objects=types.SimpleNamespace(link=built['linked'].append))
    module = types.ModuleType('bpy')
    module.data = data
    module.context = types.SimpleNamespace(scene=types.SimpleNamespace(collection=collection))
    return module, built


SIMPLE = {'title': 'Test square', 'background': '#101830', 'objects': [
    {'shape': 'plane', 'at': [0, 0, 0], 'size': [20, 20, 0], 'color': '#eeeeee'},
    {'shape': 'box', 'at': [0, 0, 1], 'size': [2, 2, 2], 'color': 'red'},
    {'shape': 'sphere', 'at': [4, 0, 1], 'radius': 1, 'color': '#3366ff', 'glow': 3, 'ring': {'count': 5, 'radius': 6}},
    {'shape': 'torus', 'at': [0, 0, 4], 'radius': 2, 'tube': .3, 'color': '#00e5ff', 'rotate': [90, 0, 0]}]}


class SceneKitTests(unittest.TestCase):
    def test_descriptions_are_checked_and_completed(self):
        scene = kit.normalize_scene(SIMPLE)
        self.assertEqual(scene['title'], 'Test square')
        self.assertTrue(scene['camera'] and scene['lights'])
        self.assertEqual(kit.statistics(scene)['objects'], 8)
        for broken, words in (({'objects': []}, 'non-empty list called objects'),
                              ({'objects': [{'shape': 'teapot'}]}, 'teapot'),
                              ([1, 2], 'JSON object')):
            with self.subTest(words=words), self.assertRaisesRegex(ValueError, words):
                kit.normalize_scene(broken)
        crowded = {'objects': [{'shape': 'box', 'at': [i, 0, 0]} for i in range(kit.MAX_SOURCE_OBJECTS + 1)]}
        with self.assertRaises(ValueError):
            kit.normalize_scene(crowded)

    def test_glb_is_valid_and_reads_back(self):
        scene = kit.normalize_scene(SIMPLE)
        data = kit.to_glb(scene)
        magic, version, length = struct.unpack_from('<4sII', data)
        self.assertEqual((magic, version, length), (b'glTF', 2, len(data)))
        report = kit.check_glb(data)
        self.assertEqual(report['objects'], 8)
        self.assertGreater(report['triangles'], 100)
        document = json.loads(data[20:20 + struct.unpack_from('<I', data, 12)[0]])
        self.assertEqual(document['asset']['version'], '2.0')

    def test_every_face_points_outwards(self):
        scene = kit.normalize_scene({'objects': [{'shape': shape, 'at': [0, 0, 0]} for shape in kit.SHAPES]})
        for item in kit.build_meshes(scene):
            vertices, faces = item['vertices'], item['faces']
            centre = [sum(v[axis] for v in vertices) / len(vertices) for axis in range(3)]
            ring = sum(math.hypot(v[0] - centre[0], v[1] - centre[1]) for v in vertices) / len(vertices)
            inward = 0
            for face in faces:
                a, b, c = (vertices[index] for index in face[:3])
                normal = kit._normal(a, b, c)
                middle = [sum(vertices[index][axis] for index in face) / len(face) for axis in range(3)]
                inside = centre
                if item['name'].startswith('Torus'):
                    # A torus surface faces away from the circle through the middle of its tube.
                    across = math.hypot(middle[0] - centre[0], middle[1] - centre[1]) or 1.0
                    inside = [centre[0] + (middle[0] - centre[0]) * ring / across,
                              centre[1] + (middle[1] - centre[1]) * ring / across, centre[2]]
                if sum(normal[axis] * (middle[axis] - inside[axis]) for axis in range(3)) < -1e-9:
                    inward += 1
            with self.subTest(shape=item['name']):
                self.assertEqual(inward, 0)

    def test_the_blender_script_builds_the_same_meshes(self):
        scene = kit.normalize_scene(SIMPLE)
        script = kit.to_blender_script(scene)
        compile(script, 'scene.py', 'exec')
        bpy, built = stand_in_bpy()
        saved = sys.modules.get('bpy')
        sys.modules['bpy'] = bpy
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(script, 'scene.py', 'exec'), {'__name__': '__main__'})
        finally:
            if saved is None:
                sys.modules.pop('bpy', None)
            else:
                sys.modules['bpy'] = saved
        self.assertEqual(len(built['meshes']), 8)
        for mesh in built['meshes']:
            self.assertTrue(all(0 <= index < len(mesh.vertices) for face in mesh.faces for index in face))
            self.assertEqual(len(mesh.materials), 1)
        self.assertGreaterEqual(len(built['linked']), 8)

    def test_viewer_page_is_self_contained(self):
        page = kit.to_viewer_html(kit.normalize_scene(SIMPLE), note='Fixture note')
        self.assertTrue(page.startswith('<!DOCTYPE html>'))
        self.assertIn('Fixture note', page)
        for remote in ('http://', 'https://', '<script src', '<link'):
            self.assertNotIn(remote, page)

    def test_preview_image_is_drawn(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed')
        image = kit.render_preview(kit.normalize_scene(SIMPLE), size=(320, 200))
        self.assertEqual(image.size, (320, 200))
        self.assertGreater(len(image.convert('RGB').getcolors(1 << 20) or ()), 20)

    def test_built_in_designs_fit_the_request_and_their_limits(self):
        cases = {'winter scene in nottingham with snow': 'winter', 'a glowing magic portal': 'portal',
                 'neon cyberpunk city at night': 'city', 'tropical island with palm trees': 'island',
                 'abstract sculpture': 'sculpture'}
        for request, expected in cases.items():
            with self.subTest(request=request):
                key, scene = kit.template_scene(request)
                self.assertEqual(key, expected)
                self.assertLessEqual(kit.statistics(scene)['triangles'], kit.MAX_TRIANGLES)
                self.assertEqual(kit.check_glb(kit.to_glb(scene))['objects'], kit.statistics(scene)['objects'])
        self.assertIn(kit.template_scene('make anything you like, your choice')[0], ('portal', 'sculpture', 'city', 'island', 'winter'))
        self.assertNotEqual(kit.template_scene('your choice')[0], 'winter')


if __name__ == '__main__':
    unittest.main()
