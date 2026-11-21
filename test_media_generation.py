"""Loopback-only acceptance for native ComfyUI APIs and media validation."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zlib

from PIL import Image

import media_generation as media


# Original 16x16, two-frame red video fixtures encoded locally with FFmpeg.
_VIDEOS = {
    '.mp4': (
        'eNp9VEFvHDUU9m4oahESAVIphxwM5MAhmcxMttsk0khpo4o9gEBCrUBCGry2JzNde+zY3k22HODQA+IHcODeSvwB4ILUAwckJM4U9cIF'
        'wQXUC9flvdmNMg2l3rHfe/b33rO/Zy8hhBZhaitvNCFdghJ6yiY80baXELL0pjZmQghRelIK8kRb+qPpHYLfWes8iTpv75Nnti587wfHR'
        'qB/FEZNzqX/RsPcz87z1LzLi07IuhTBg1yTyoczj0XcxrfzkxYVA4Vqcf7s+02/+UNjbJZCudOVSSVkG3kLbDNgtVASMZ2JruoClJWJ'
        'boK2t7ku5mtrwsmidYwXx07Rud7p+zBUoH/jgxctzH0s2v9QgYdeJgOQg1PE5bcB30+iZDdK4piqaniS9nstj9XZDMYNQB10rj94Yf'
        'YbuXwI8tdPOstYkWbYf/z67Q55rvz5pR8xh2XeLrJjXxkGt6D21e/mnaz60NB+uksoNx6MrMBp2vPY12COn5vrttbunLs2NchL81jc'
        'tEr6AUg2FgFL+aGWjcT2WrtsUGrHrFVt0jaq+d1Y/zoY05SFNc4NvcBfAfxdRfrAvFg4CXXvfqoBhHf4+Quz2buPbvz5/e8PB99+'
        'ufELffjGX38jx3STcuMkTUB128luSnnak3Gfw8IgAsDWO+/deGuzR6/dOgCkkLhwYOxUySLQNI63N9M4xTBlCHZva+v4+DjCW2cUq'
        'yPjDrcwS1QGrQBjbKhM7fcoZ0PGs5jCzcoSKuRQGT7K4j34UVYzNfUSLaplBree+vEQtJhaPwU4jLkTWRLFAIGB6upEihxjoUfuW'
        'H0os6RPeemMZjm4JjQ4qVTlAbFzsiN4AIUfaRiFZOKOqWWWJhtJQgvmQ279qLLgtAhwZHNTFF6iUygdOHhYVMaMWAlGfjbnVcXl'
        '2URMa9ek4JVmAbdR1UE6xQAE80M1dmyac6MtazYE3MC/TVVDCAA6hpjCMS0x1LGsDstgQRvJKSxn6ZVTNYdnnKXUc1lLPsZQjTtS'
        '4qQvkWiecVdQDa9AIpNgZOl2FNMjzJ7FUR9Ui2EayU6y/i4oPkib9WhlgVQoHXDegwIdZTH5DB+S/Pzu3stfkO7q4wdk/58dcnH6'
        'MUxfuPYVfeXev2vxQYg='
    ),
    '.webm': (
        'eNqTcr2/eL5TWyOj03cg/tTI4vS5kcOpqaU8NSnXqb2Ryam1kUkiuCGdkQEEmDgEfWeX7PLd3R28ukXUc2Va8JrGhRCeWMi6bCDvm'
        'u/uHiBPKORwevCaJkZlCFcmeHc2iPvpDcQchkgGWgGQqzZoXd/YzO/k4NvQ45NYlmZmqGeuZ2hgEO6IwnXp7HDodwBrAjn+xDq'
        'o2+yvNzIWH+2YvTJvwQun2yFzGhmUts5pLs1L6WhkaGsNiw8LsGxuZFR+3NwiezaV4cGEDY0CuxoFZjUyhW5oCd3ZyAjyu0N9cf'
        'H85AMN6Sdmui5ud/Vz9ndxDXJpR7G/uPhW8oHuZIRN6SeWIiueAVScDFRsaAlSrZCTmVRWUKFbVmCZfmKh6+IOl9AgxxBPfz+X'
        '9skGBlYgZKhnAAMM8s7bSo8+b2RYvLqRgaGhybPZiYHhA8M3BgsVGQkvBgaDBAYGgf3/v8uu////f/x9IPFJ6wDD4smNjF8Y2'
        'hgcJs1hCGBgYFZgYHByAMXdxN39mxsZtncB08jHJsYVHxqZAfeFtx4='
    ),
}


def _video(extension):
    return zlib.decompress(base64.b64decode(_VIDEOS[extension]))


def _image(format='PNG', animated=False):
    stream = io.BytesIO()
    frame = Image.new('RGB', (24, 16), 'red')
    args = {'save_all': True, 'append_images': [Image.new('RGB', frame.size, 'blue')], 'duration': 100} if animated else {}
    frame.save(stream, format=format, **args)
    return stream.getvalue()


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def as_json(self, body, status=200):
        self.respond(json.dumps(body).encode(), status)

    def do_GET(self):
        server = self.server
        url = urlsplit(self.path)
        server.gets.append(self.path)
        if server.redirect and url.path.startswith('/object_info/'):
            self.send_response(302)
            self.send_header('Location', '/unexpected-redirect')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if url.path.startswith('/object_info/'):
            node = url.path.rsplit('/', 1)[1]
            info = {'python_module': server.modules.get(node, 'nodes')}
            if node == 'CheckpointLoaderSimple':
                info['input'] = {'required': {'ckpt_name': [server.checkpoints]}}
            self.as_json({node: info})
            return
        if url.path.startswith('/history/'):
            active = server.submitted[-1]
            if server.cancel_when_polled:
                server.cancel_when_polled.set()
                self.as_json({})
                return
            if server.job_error:
                self.as_json({active['prompt_id']: {'status': server.job_error}})
                return
            save_id = next(key for key, value in active['prompt'].items() if value['class_type'].startswith('Save'))
            prefix = active['prompt'][save_id]['inputs']['filename_prefix']
            name = server.output_filename or prefix + '_00001_' + server.extension
            returned = {save_id: {'images': [{'filename': name, 'subfolder': '', 'type': 'output'}]}}
            self.as_json({active['prompt_id']: {'status': {'status_str': 'success', 'completed': True}, 'outputs': returned}})
            return
        if url.path == '/view':
            server.views.append(parse_qs(url.query, keep_blank_values=True))
            if server.cancel_when_downloading:
                server.cancel_when_downloading.set()
            self.respond(server.media_bytes, content_type='application/octet-stream')
            return
        self.as_json({}, status=404)

    def do_POST(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        server.posts.append((self.path, body))
        if self.path == '/prompt':
            if server.reject_prompt:
                self.as_json({'error': {'type': 'prompt_outputs_failed_validation'}}, status=400)
                return
            server.submitted.append(body)
            server.pending.append(body['prompt_id'])
            self.as_json({'prompt_id': body['prompt_id'], 'number': 1, 'node_errors': {}})
            return
        if self.path == '/queue':
            server.pending[:] = [job for job in server.pending if job not in body.get('delete', [])]
            self.respond(b'')
            return
        self.as_json({}, status=404)


class MediaGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.directory = self.state / 'requested-output'
        self.events = []
        self.cancel = threading.Event()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        self.server.gets = []
        self.server.posts = []
        self.server.submitted = []
        self.server.views = []
        self.server.pending = ['unrelated-pending']
        self.server.checkpoints = ['not-compatible.safetensors', 'base/v1-5-pruned-emaonly.safetensors']
        self.server.modules = {}
        self.server.redirect = False
        self.server.reject_prompt = False
        self.server.cancel_when_polled = None
        self.server.cancel_when_downloading = None
        self.server.job_error = None
        self.server.output_filename = None
        self.server.extension = '.png'
        self.server.media_bytes = _image()
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        self.addCleanup(self.stop_server, thread)
        patcher = patch.object(media, '_BASE', f'http://127.0.0.1:{self.server.server_port}')
        patcher.start()
        self.addCleanup(patcher.stop)

    def stop_server(self, thread):
        self.server.shutdown()
        self.server.server_close()
        thread.join(2)

    def generate(self, kind='image', wording='A fixture in warm light'):
        return media.generate_media(kind, wording, self.state, self.cancel,
                                    lambda event, value: self.events.append((event, value)), self.directory)

    def workflow(self, kind='image', save='SaveImage'):
        graph = {
            '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'custom-style.safetensors'}},
            '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'Landscape: {{prompt}}', 'clip': ['1', 1]}},
            '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '', 'clip': ['1', 1]}},
            '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 2}},
            '5': {'class_type': 'KSampler', 'inputs': {
                'model': ['1', 0], 'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0],
                'seed': 17, 'steps': 20, 'cfg': 7, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1}},
            '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
            '7': {'class_type': save, 'inputs': {'images': ['6', 0], 'filename_prefix': 'user-prefix'}},
        }
        if save == 'SaveVideo':
            graph['8'] = {'class_type': 'CreateVideo', 'inputs': {'images': ['6', 0], 'fps': 2}}
            graph['7']['inputs'] = {'video': ['8', 0], 'filename_prefix': 'user-prefix',
                                    'format': self.server.extension[1:], 'codec': 'auto'}
        return self.write_workflow(kind, graph)

    def write_workflow(self, kind, graph):
        path = self.state / 'media' / f'{kind}.workflow.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(graph), encoding='utf-8')
        return graph

    def test_default_image_submits_native_graph_and_returns_verified_png(self):
        result = self.generate()
        artifact = Path(result['artifact'])
        self.assertEqual(artifact.parent, self.directory.resolve())
        self.assertEqual(artifact.read_bytes(), self.server.media_bytes)
        self.assertEqual((result['type'], result['format'], result['width']), ('image', 'PNG', 24))
        posted = self.server.submitted[0]
        self.assertEqual(posted['prompt_id'], result['prompt_id'])
        self.assertEqual(posted['prompt']['1']['inputs']['ckpt_name'], 'base/v1-5-pruned-emaonly.safetensors')
        self.assertEqual(posted['prompt']['2']['inputs']['text'], 'A fixture in warm light')
        self.assertEqual(posted['prompt']['4']['inputs']['width'], 512)
        self.assertEqual(self.server.views[0]['type'], ['output'])
        self.assertTrue(all(name == 'status' for name, _ in self.events))
        self.assertNotEqual(self.generate()['artifact'], result['artifact'])

    def test_default_sdxl_selects_native_xl_resolution(self):
        self.server.checkpoints = ['sd_xl_base_1.0.safetensors']
        self.generate()
        inputs = self.server.submitted[0]['prompt']['4']['inputs']
        self.assertEqual((inputs['width'], inputs['height']), (1024, 1024))

    def test_configured_local_workflow_substitutes_literal_text_and_unique_prefix(self):
        self.workflow()
        original = (self.state / 'media/image.workflow.json').read_bytes()
        self.generate(wording='A quote "here" and {{prompt}} as text')
        graph = self.server.submitted[0]['prompt']
        self.assertEqual(graph['2']['inputs']['text'], 'Landscape: A quote "here" and {{prompt}} as text')
        self.assertTrue(graph['7']['inputs']['filename_prefix'].startswith('TalkToAi_'))
        self.assertEqual((self.state / 'media/image.workflow.json').read_bytes(), original)

    def test_video_records_savevideo_output_as_valid_mp4_and_webm(self):
        for extension in ('.mp4', '.webm'):
            with self.subTest(extension=extension):
                self.server.extension = extension
                self.server.media_bytes = _video(extension)
                self.workflow('video', 'SaveVideo')
                result = self.generate('video')
                self.assertEqual(result['type'], 'video')
                self.assertEqual(result['format'], extension[1:].upper())
                self.assertEqual(Path(result['artifact']).read_bytes(), self.server.media_bytes)

    def test_jpeg_and_gif_real_format_checks(self):
        for extension, format, kind, animated in (('.jpeg', 'JPEG', 'image', False), ('.gif', 'GIF', 'video', True)):
            with self.subTest(extension=extension):
                contents = _image(format, animated)
                path = self.state / 'download.partial'
                path.write_bytes(contents)
                self.assertEqual(media._media_format(path, extension, kind, len(contents))['format'], format)

    def test_incorrect_image_signature_does_not_publish_partial_file(self):
        self.server.media_bytes = b'Not an image.'
        with self.assertRaisesRegex(ValueError, 'unreadable image'):
            self.generate()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_size_cap_rejects_response_before_publishing(self):
        with patch.dict(media._FILE_LIMIT, {'image': 16}):
            with self.assertRaisesRegex(ValueError, 'allowed size'):
                self.generate()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_mp4_without_video_track_does_not_publish_partial_file(self):
        self.server.extension = '.mp4'
        self.server.media_bytes = _video('.mp4').replace(b'vide', b'soun', 1)
        self.workflow('video', 'SaveVideo')
        with self.assertRaisesRegex(ValueError, 'without a video track'):
            self.generate('video')
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_template_rejects_api_nodes_and_replaced_native_registration(self):
        graph = self.workflow()
        graph['9'] = {'class_type': 'OpenAIDalle3', 'inputs': {}}
        self.write_workflow('image', graph)
        with self.assertRaisesRegex(ValueError, 'custom nodes are excluded'):
            self.generate()
        self.assertFalse(self.server.posts)
        self.workflow()
        self.server.modules['SaveImage'] = 'custom_nodes.fake'
        with self.assertRaisesRegex(ValueError, 'not registered as a supported native'):
            self.generate()
        self.assertFalse(self.server.posts)

    def test_template_rejects_placeholder_outside_connected_prompt_and_local_unsafe_paths(self):
        graph = self.workflow()
        graph['5']['inputs']['positive'] = ['3', 0]
        self.write_workflow('image', graph)
        with self.assertRaisesRegex(ValueError, 'Connect the text input'):
            self.generate()
        graph['1']['inputs']['ckpt_name'] = '../escape.safetensors'
        self.write_workflow('image', graph)
        with self.assertRaisesRegex(ValueError, 'local .safetensors'):
            self.generate()
        self.assertFalse(self.server.posts)

    def test_template_rejects_wrong_saver_and_multiple_markers(self):
        graph = self.workflow()
        graph['3']['inputs']['text'] = '{{prompt}}'
        self.write_workflow('image', graph)
        with self.assertRaisesRegex(ValueError, 'one literal'):
            self.generate()
        graph['3']['inputs']['text'] = ''
        self.write_workflow('video', graph)
        with self.assertRaisesRegex(ValueError, 'native video save node'):
            self.generate('video')
        self.assertFalse(self.server.posts)

    def test_history_cannot_download_others_or_path_traversal(self):
        self.server.output_filename = '../other.png'
        with self.assertRaisesRegex(ValueError, 'from this job'):
            self.generate()
        self.assertFalse(self.server.views)
        self.assertFalse(self.directory.exists())

    def test_stop_deletes_only_own_pending_job_without_interrupt(self):
        self.server.cancel_when_polled = self.cancel
        with self.assertRaises(InterruptedError):
            self.generate()
        self.assertEqual([route for route, _ in self.server.posts], ['/prompt', '/queue'])
        self.assertEqual(self.server.posts[-1][1], {'delete': [self.server.submitted[0]['prompt_id']]})
        self.assertEqual(self.server.pending, ['unrelated-pending'])
        self.assertFalse(self.directory.exists())

    def test_stop_during_download_removes_partial_and_leaves_queue_alone(self):
        self.server.cancel_when_downloading = self.cancel
        with self.assertRaises(InterruptedError):
            self.generate()
        self.assertEqual([route for route, _ in self.server.posts], ['/prompt'])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_backend_errors_are_actionable(self):
        self.server.reject_prompt = True
        with self.assertRaisesRegex(ValueError, 'Open it in ComfyUI'):
            self.generate()
        self.server.reject_prompt = False
        self.server.job_error = {'status_str': 'error', 'messages': [
            ['execution_error', {'exception_type': 'torch.OutOfMemoryError'}]]}
        with self.assertRaisesRegex(RuntimeError, 'ran out of memory'):
            self.generate()
        self.assertFalse(self.server.views)

    def test_no_backend_requires_explicit_local_setup_and_video_workflow(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            with patch.object(media, '_BASE', f'http://127.0.0.1:{reserved.getsockname()[1]}'):
                with self.assertRaisesRegex(ConnectionError, 'docs.comfy.org'):
                    self.generate()
        with self.assertRaisesRegex(FileNotFoundError, 'video.workflow.json'):
            self.generate('video')

    def test_redirect_is_blocked_and_proxy_environment_is_ignored(self):
        self.server.redirect = True
        with self.assertRaisesRegex(RuntimeError, 'redirect'):
            self.generate()
        self.assertNotIn('/unexpected-redirect', self.server.gets)
        self.server.redirect = False
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1',
                                     'NO_PROXY': '', 'http_proxy': 'http://127.0.0.1:1', 'no_proxy': ''}):
            self.assertEqual(self.generate()['format'], 'PNG')


if __name__ == '__main__':
    unittest.main()
