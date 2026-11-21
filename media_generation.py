"""Local ComfyUI media jobs. Workflow protocol follows ComfyUI's official API examples."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import secrets
import struct
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


SETUP_URL = 'https://docs.comfy.org/installation/system_requirements'
_BASE = 'http://127.0.0.1:8188'
_LIMIT_JSON = 2 * 1024 * 1024
_LIMIT_WORKFLOW = 256 * 1024
_POLL_SECONDS = 1
_JOB_LIMIT = {'image': 600, 'video': 1200}
_FILE_LIMIT = {'image': 64 * 1024 ** 2, 'video': 512 * 1024 ** 2}
_MARKER = '{{prompt}}'
_IMAGE_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF', '.webp': 'WEBP'}
_FORMATS = {'image': set(_IMAGE_FORMATS), 'video': {'.mp4', '.webm', '.gif'}}
_SAVERS = {'image': {'SaveImage', 'SaveAnimatedPNG', 'SaveAnimatedWEBP'}, 'video': {'SaveVideo', 'SaveWEBM'}}
_TEXT = {'CLIPTextEncode': {'text'}, 'CLIPTextEncodeSDXL': {'text_g', 'text_l'}, 'CLIPTextEncodeSDXLRefiner': {'text'}}
_WEIGHTS = {
    'CheckpointLoaderSimple': ('ckpt_name',), 'UNETLoader': ('unet_name',),
    'VAELoader': ('vae_name',), 'CLIPLoader': ('clip_name',),
    'DualCLIPLoader': ('clip_name1', 'clip_name2'), 'TripleCLIPLoader': ('clip_name1', 'clip_name2', 'clip_name3'),
    'LoraLoader': ('lora_name',), 'LoraLoaderModelOnly': ('lora_name',),
}
_NATIVE = set(_TEXT) | set(_WEIGHTS) | set.union(*_SAVERS.values()) | {
    'EmptyLatentImage', 'EmptySD3LatentImage', 'EmptyFlux2LatentImage',
    'KSampler', 'KSamplerAdvanced', 'VAEDecode', 'VAEDecodeTiled', 'VAEEncode', 'VAEEncodeTiled',
    'CLIPSetLastLayer', 'ConditioningZeroOut', 'ConditioningCombine', 'ConditioningConcat',
    'ModelSamplingDiscrete', 'ModelSamplingContinuousEDM', 'ModelSamplingSD3', 'ModelSamplingFlux',
    'FluxGuidance', 'FluxDisableGuidance', 'CFGGuider', 'BasicGuider', 'RandomNoise', 'DisableNoise',
    'BasicScheduler', 'KSamplerSelect', 'SamplerCustom', 'SamplerCustomAdvanced',
    'ImageScale', 'ImageScaleBy', 'ImageBatch', 'RepeatImageBatch', 'ImageFromBatch', 'LoadImage',
    'EmptyHunyuanLatentVideo', 'EmptyMochiLatentVideo', 'EmptyLTXVLatentVideo',
    'LTXVConditioning', 'LTXVScheduler', 'LTXVImgToVideo', 'WanImageToVideo',
    'WanFirstLastFrameToVideo', 'Wan22ImageToVideoLatent', 'HunyuanImageToVideo',
    'CreateVideo', 'VideoLinearCFGGuidance', 'VideoTriangleCFGGuidance',
}
_CHECKPOINTS = {
    'sd_xl_base_1.0.safetensors': 1024,
    'sdxl_base_1.0.safetensors': 1024,
    'v1-5-pruned-emaonly.safetensors': 512,
    'v1-5-pruned.safetensors': 512,
    'sd-v1-5-pruned-emaonly.safetensors': 512,
}
_NODE_ID = re.compile(r'[a-zA-Z0-9_.:-]{1,64}\Z')
_SAFE_FILE = re.compile(r'[\w][\w .()+,=@-]{0,180}\Z')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_LOCAL_HTTP = build_opener(ProxyHandler({}), _NoRedirect())


def _check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Local media generation stopped.')


def _open(endpoint, data=None, *, timeout=3):
    # Callers supply fixed routes and encode their own narrowly scoped parameters.
    body = None if data is None else json.dumps(data, allow_nan=False).encode('utf-8')
    headers = {'Accept': 'application/json' if not endpoint.startswith('/view?') else '*/*'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    return _LOCAL_HTTP.open(Request(_BASE + endpoint, data=body, headers=headers), timeout=timeout)


def _request_json(endpoint, data=None):
    try:
        with _open(endpoint, data) as response:
            deadline = time.monotonic() + 5
            payload = bytearray()
            while len(payload) <= _LIMIT_JSON:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Local API metadata timed out.')
                chunk = response.read1(min(65536, _LIMIT_JSON + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
    except HTTPError as exc:
        exc.close()
        if 300 <= exc.code < 400:
            raise RuntimeError('The local ComfyUI endpoint tried to redirect the request.') from None
        if endpoint == '/prompt' and exc.code in (400, 422):
            raise ValueError('ComfyUI rejected the workflow. Open it in ComfyUI and check its nodes and installed model names.') from None
        raise RuntimeError(f'ComfyUI returned HTTP {exc.code}. Check that its local API is available.') from None
    except (OSError, URLError, http.client.HTTPException):
        raise ConnectionError(f'Start ComfyUI on {_BASE} and retry. Setup: {SETUP_URL}') from None
    if len(payload) > _LIMIT_JSON:
        raise ValueError('ComfyUI returned too much job or node metadata.')
    try:
        value = json.loads(payload)
    except (ValueError, RecursionError):
        raise ValueError('The server on port 8188 did not return ComfyUI JSON.') from None
    if not isinstance(value, dict):
        raise ValueError('ComfyUI returned an unexpected API response.')
    return value


def _node_info(node, cache, cancel):
    if node not in cache:
        _check_cancel(cancel)
        info = _request_json('/object_info/' + node).get(node)
        if not isinstance(info, dict):
            raise ValueError(f'ComfyUI does not expose the native {node} node. Update ComfyUI or change the workflow.')
        module = info.get('python_module')
        if (info.get('api_node') or not isinstance(module, str)
                or not (module == 'nodes' or module.startswith('comfy_extras.nodes_'))):
            raise ValueError(f'The local {node} node is not registered as a supported native ComfyUI node.')
        cache[node] = info
    return cache[node]


def _relative_name(value, suffixes):
    if not isinstance(value, str) or len(value) > 400:
        return False
    parts = value.replace('\\', '/').split('/')
    return (all(_SAFE_FILE.fullmatch(part) and not part.endswith(('.', ' ')) for part in parts)
            and Path(parts[-1]).suffix.lower() in suffixes)


def _default_image(cache, cancel, path):
    info = _node_info('CheckpointLoaderSimple', cache, cancel)
    try:
        inventory = info['input']['required']['ckpt_name'][0]
    except (KeyError, TypeError, IndexError):
        inventory = []
    candidates = [
        (name, _CHECKPOINTS[name.replace('\\', '/').rsplit('/', 1)[-1].lower()])
        for name in inventory if _relative_name(name, {'.safetensors'})
        and name.replace('\\', '/').rsplit('/', 1)[-1].lower() in _CHECKPOINTS
    ] if isinstance(inventory, list) else []
    if not candidates:
        raise ValueError(
            f'Install a recognized SDXL or SD 1.5 base safetensors checkpoint in ComfyUI, or save a native image workflow at {path}.'
        )
    # Prefer an available SD 1.5 base preset for a lighter default on local PCs.
    checkpoint, size = min(candidates, key=lambda item: (item[1], item[0].casefold()))
    return {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': _MARKER}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': ''}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': size, 'height': size, 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {
            'model': ['1', 0], 'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0],
            'seed': secrets.randbits(53), 'steps': 24, 'cfg': 7, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'TalkToAi'}},
    }


def _load_workflow(kind, path, cache, cancel):
    try:
        with path.open('rb') as handle:
            payload = handle.read(_LIMIT_WORKFLOW + 1)
    except FileNotFoundError:
        if kind == 'image':
            return _default_image(cache, cancel, path)
        raise FileNotFoundError(
            f'Save a native ComfyUI video workflow in API format at {path}, then start ComfyUI on {_BASE}. Setup: {SETUP_URL}'
        ) from None
    if len(payload) > _LIMIT_WORKFLOW:
        raise ValueError('The local workflow exceeds the 256 KiB limit.')
    try:
        return json.loads(payload.decode('utf-8-sig'))
    except (ValueError, RecursionError):
        raise ValueError(f'The local workflow is not valid JSON: {path}') from None


def _prepare_workflow(graph, kind, prompt, prefix):
    if not isinstance(graph, dict) or not 2 <= len(graph) <= 128:
        raise ValueError('Use a ComfyUI API-format workflow with 2–128 native nodes.')
    workflow, prompt_inputs, saves = {}, [], []
    for key, row in graph.items():
        if not _NODE_ID.fullmatch(key) or not isinstance(row, dict) or not isinstance(row.get('inputs'), dict):
            raise ValueError('Use a ComfyUI API-format workflow containing named node inputs.')
        node = row.get('class_type')
        if not isinstance(node, str) or node not in _NATIVE:
            raise ValueError('The workflow contains an unsupported node. Use supported native nodes; API and custom nodes are excluded.')
        inputs = dict(row['inputs'])
        for field in _WEIGHTS.get(node, ()):
            if not _relative_name(inputs.get(field), {'.safetensors'}):
                raise ValueError(f'{node} must select a local .safetensors file.')
        if node == 'LoadImage' and not _relative_name(inputs.get('image'), set(_IMAGE_FORMATS)):
            raise ValueError('LoadImage must select a relative ComfyUI input image.')
        if node in _TEXT:
            for field in _TEXT[node]:
                text = inputs.get(field)
                if isinstance(text, str) and _MARKER in text:
                    prompt_inputs.append((key, field))
        if node in _SAVERS['image'] or node in _SAVERS['video']:
            if node not in _SAVERS[kind]:
                raise ValueError(f'Use a native {kind} save node for this workflow.')
            if node == 'SaveVideo':
                container = inputs.get('format', 'auto')
                codec = inputs.get('codec')
                if isinstance(container, dict):
                    codec = container.get('codec') or codec
                    container = container.get('format')
                if isinstance(codec, dict):
                    codec = codec.get('codec')
                if container not in ('auto', 'mp4', 'webm') or codec == 'prores':
                    raise ValueError('Choose an MP4 or WebM format and compatible codec in SaveVideo.')
            saves.append(key)
        workflow[key] = {'class_type': node, 'inputs': inputs}
    try:
        count = json.dumps(workflow, allow_nan=False).count(_MARKER)
    except (ValueError, RecursionError):
        raise ValueError('Workflow inputs must contain bounded, valid JSON values.') from None
    if len(saves) != 1 or len(prompt_inputs) != 1 or count != 1:
        raise ValueError(f'Use one native {kind} save node and one literal {_MARKER} in a supported text input.')
    save_id, (prompt_id, field) = saves[0], prompt_inputs[0]
    pending, visited = [save_id], set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        pending.extend(value[0] for value in workflow[key]['inputs'].values()
                       if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
                       and value[0] in workflow and type(value[1]) is int)
    if prompt_id not in visited:
        raise ValueError('Connect the text input containing {{prompt}} to the requested save node.')
    workflow[prompt_id]['inputs'][field] = workflow[prompt_id]['inputs'][field].replace(_MARKER, prompt)
    workflow[save_id]['inputs']['filename_prefix'] = prefix
    return workflow, save_id


def _job_error(status):
    messages = status.get('messages')
    for message in messages if isinstance(messages, list) else ():
        if not isinstance(message, list) or len(message) != 2 or not isinstance(message[1], dict):
            continue
        event, detail = message
        if event == 'execution_interrupted':
            return 'ComfyUI interrupted this job.'
        if event == 'execution_error':
            category = detail.get('exception_type', '')
            if isinstance(category, str) and any(text in category.lower() for text in ('outofmemory', 'oom')):
                return 'ComfyUI ran out of memory. Reduce the workflow dimensions or frames, or use a smaller compatible model.'
    return 'ComfyUI failed this job. Its local console shows the failing node and error.'


def _wait_for_job(job_id, kind, cancel, emit):
    started = time.monotonic()
    deadline = started + _JOB_LIMIT[kind]
    next_status = started + 20
    transient_failures = 0
    while time.monotonic() < deadline:
        _check_cancel(cancel)
        try:
            history = _request_json('/history/' + job_id)
            transient_failures = 0
        except ConnectionError:
            transient_failures += 1
            if transient_failures >= 3:
                raise
            history = {}
        if job_id in history:
            job = history[job_id]
            if not isinstance(job, dict):
                raise ValueError('ComfyUI returned invalid history for this job.')
            status = job.get('status', {})
            if isinstance(status, dict) and status.get('status_str') in ('error', 'failed'):
                raise RuntimeError(_job_error(status))
            return job
        if time.monotonic() >= next_status:
            emit('status', f'Waiting for local ComfyUI ({int(time.monotonic()-started)} seconds)…')
            next_status += 20
        if cancel.wait(_POLL_SECONDS):
            _check_cancel(cancel)
    raise TimeoutError('The local ComfyUI job reached its time limit. Check its queue or reduce the workflow size.')


def _media_result(job, save_id, prefix, kind):
    outputs = job.get('outputs')
    node = outputs.get(save_id) if isinstance(outputs, dict) else None
    if isinstance(node, dict):
        for field in ('images', 'videos', 'gifs'):
            items = node.get(field, [])
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict) or entry.get('type') != 'output' or entry.get('subfolder', '') != '':
                    continue
                name = entry.get('filename')
                if (isinstance(name, str) and name.startswith(prefix + '_')
                        and _SAFE_FILE.fullmatch(name) and Path(name).suffix.lower() in _FORMATS[kind]):
                    return name
    raise ValueError(f'ComfyUI completed without a supported {kind} file from this job’s save node.')


def _mp4_boxes(handle, start, end):
    position = start
    for _ in range(4096):
        if position == end:
            return
        handle.seek(position)
        header = handle.read(8)
        if len(header) != 8:
            break
        size, tag = struct.unpack('>I4s', header)
        length = 8
        if size == 1:
            wide = handle.read(8)
            if len(wide) != 8:
                break
            size = int.from_bytes(wide, 'big')
            length = 16
        elif size == 0:
            size = end - position
        if size < length or position + size > end:
            break
        yield tag, position + length, position + size
        position += size
    raise ValueError('Invalid MP4 box structure.')


def _valid_mp4(handle, size):
    has_brand = has_samples = has_video = False
    for tag, start, end in _mp4_boxes(handle, 0, size):
        if tag == b'ftyp' and end - start >= 8:
            handle.seek(start)
            has_brand = handle.read(4) in {
                b'isom', b'iso2', b'iso3', b'iso4', b'iso5', b'iso6', b'iso8', b'iso9',
                b'mp41', b'mp42', b'avc1', b'M4V ', b'dash', b'msdh', b'msix'}
        elif tag == b'mdat' and end > start:
            has_samples = True
        elif tag == b'moov':
            for name, first, last in _mp4_boxes(handle, start, end):
                if name != b'trak':
                    continue
                for name, left, right in _mp4_boxes(handle, first, last):
                    if name != b'mdia':
                        continue
                    for name, substart, subend in _mp4_boxes(handle, left, right):
                        if name == b'hdlr' and subend - substart >= 12:
                            handle.seek(substart + 8)
                            if handle.read(4) == b'vide':
                                has_video = True
    return has_brand and has_samples and has_video


def _vint(handle, element_id=False):
    first = handle.read(1)
    if not first or first[0] == 0:
        raise ValueError('Invalid WebM element.')
    length = 9 - first[0].bit_length()
    if element_id and length > 4:
        raise ValueError('Invalid WebM element ID.')
    tail = handle.read(length - 1)
    if len(tail) != length - 1:
        raise ValueError('Truncated WebM element.')
    value = int.from_bytes(first + tail, 'big')
    if element_id:
        return value
    value ^= 1 << (7 * length)
    return None if value == (1 << (7 * length)) - 1 else value


def _webm_elements(handle, start, end, unknown=()):
    position = start
    for _ in range(4096):
        if position == end:
            return
        handle.seek(position)
        element_id, length = _vint(handle, True), _vint(handle)
        content = handle.tell()
        if length is None and element_id in unknown:
            length = end - content
        if length is None or content + length > end:
            break
        yield element_id, content, content + length
        position = content + length
    raise ValueError('Invalid WebM element structure.')


def _valid_webm(handle, size):
    has_doctype = has_video = has_clusters = False
    for tag, start, end in _webm_elements(handle, 0, size, (0x18538067,)):
        if tag == 0x1A45DFA3:
            for item, left, right in _webm_elements(handle, start, end):
                if item == 0x4282 and right - left == 4:
                    handle.seek(left)
                    has_doctype = handle.read(4) == b'webm'
        elif tag == 0x18538067:
            for item, left, right in _webm_elements(handle, start, end, (0x1F43B675,)):
                if item == 0x1F43B675 and right > left:
                    has_clusters = True
                elif item == 0x1654AE6B:
                    for entry, first, last in _webm_elements(handle, left, right):
                        if entry == 0xAE:
                            for field, begin, stop in _webm_elements(handle, first, last):
                                if field == 0x83 and stop - begin == 1:
                                    handle.seek(begin)
                                    if handle.read(1) == b'\x01':
                                        has_video = True
    return has_doctype and has_video and has_clusters


def _media_format(path, extension, kind, size):
    if extension in _IMAGE_FORMATS:
        from PIL import Image

        try:
            with Image.open(path) as image:
                if image.format != _IMAGE_FORMATS[extension] or image.width * image.height > 32_000_000:
                    raise ValueError('The saved image format or dimensions do not match the result.')
                if kind == 'video' and not image.is_animated:
                    raise ValueError('The returned GIF is not animated.')
                dimensions = {'width': image.width, 'height': image.height}
                image.verify()
            with Image.open(path) as image:
                image.load()
                if kind == 'video':
                    image.seek(1)
                    image.load()
            return {'format': _IMAGE_FORMATS[extension], **dimensions}
        except (OSError, SyntaxError, EOFError, Image.DecompressionBombError):
            raise ValueError('ComfyUI returned an unreadable image or GIF.') from None
    with path.open('rb') as handle:
        valid = _valid_mp4(handle, size) if extension == '.mp4' else _valid_webm(handle, size)
    if not valid:
        raise ValueError('ComfyUI returned an invalid video container or a file without a video track.')
    return {'format': extension[1:].upper()}


def _download(name, kind, directory, cancel):
    extension = Path(name).suffix.lower()
    final = directory / ('generated-' + kind + '-' + uuid4().hex[:16] + extension)
    endpoint = '/view?' + urlencode({'filename': name, 'subfolder': '', 'type': 'output'})
    directory.mkdir(parents=True, exist_ok=True)
    part = None
    size = 0
    try:
        with _open(endpoint) as response, tempfile.NamedTemporaryFile(
            dir=directory, prefix='.media-', suffix='.partial', delete=False,
        ) as handle:
            part = Path(handle.name)
            announced = response.headers.get('Content-Length')
            if announced is not None and (not announced.isdecimal() or not 0 < int(announced) <= _FILE_LIMIT[kind]):
                raise ValueError('ComfyUI returned a media file outside the allowed size.')
            deadline = time.monotonic() + 120
            while True:
                _check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise TimeoutError('The local media download reached its time limit.')
                chunk = response.read1(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _FILE_LIMIT[kind]:
                    raise ValueError('The media file exceeds its allowed size.')
                handle.write(chunk)
        if not size or (announced is not None and size != int(announced)):
            raise ValueError('ComfyUI returned an empty or incomplete media file.')
        metadata = _media_format(part, extension, kind, size)
        _check_cancel(cancel)
        part.replace(final)
        return final, {'bytes': size, **metadata}
    except HTTPError as exc:
        exc.close()
        raise RuntimeError('ComfyUI could not supply the saved result over its local API.') from None
    except (URLError, http.client.HTTPException):
        raise ConnectionError('ComfyUI disconnected while downloading the saved media file.') from None
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


def _delete_pending(job_id):
    # This operation removes an ID from the pending queue only. Running jobs,
    # including this one if it already began, are left alone.
    try:
        with _open('/queue', {'delete': [job_id]}, timeout=1):
            pass
    except HTTPError as exc:
        exc.close()
    except (OSError, http.client.HTTPException):
        pass


def generate_media(kind, prompt, state, cancel, emit, output_dir=None):
    """Run an owner-configured native graph, then verify and save its actual media."""
    if kind not in _JOB_LIMIT:
        raise ValueError('Choose image or video generation.')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000:
        raise ValueError('Enter a media prompt between 1 and 6,000 characters.')
    _check_cancel(cancel)
    state = Path(state)
    path = state / 'media' / f'{kind}.workflow.json'
    directory = Path(output_dir if output_dir is not None else state / 'media' / 'generated').resolve()
    cache = {}
    prefix = 'TalkToAi_' + uuid4().hex
    graph, save_id = _prepare_workflow(_load_workflow(kind, path, cache, cancel), kind, prompt.strip(), prefix)
    for node in sorted({row['class_type'] for row in graph.values()}):
        _node_info(node, cache, cancel)
    _check_cancel(cancel)
    emit('status', 'Sending the workflow to local ComfyUI…')
    _check_cancel(cancel)
    requested = str(uuid4())
    job_id = requested
    queued = False
    try:
        try:
            submitted = _request_json('/prompt', {'prompt': graph, 'client_id': requested, 'prompt_id': requested})
        except ConnectionError:
            _delete_pending(requested)
            raise ConnectionError('ComfyUI did not acknowledge submission. Check its local queue before retrying.') from None
        # Current implementations accept a client ID for the job. Older builds
        # assign one and return it. Only this submitted response may set it.
        remote_id = submitted.get('prompt_id')
        if not isinstance(remote_id, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', remote_id, re.I):
            _delete_pending(requested)
            raise ValueError('ComfyUI did not return a valid job ID. Check its local queue before retrying.')
        job_id = remote_id
        queued = True
        emit('status', 'Local ComfyUI job queued. Stop can remove it while it is pending.')
        result = _wait_for_job(job_id, kind, cancel, emit)
        queued = False
        _check_cancel(cancel)
        filename = _media_result(result, save_id, prefix, kind)
        emit('status', f'Checking and saving the generated {kind}…')
        artifact, metadata = _download(filename, kind, directory, cancel)
    except (InterruptedError, TimeoutError):
        if queued:
            _delete_pending(job_id)
        raise
    return {
        'artifact': str(artifact),
        'type': kind,
        'title': f'Generated {kind}',
        'backend': 'local-comfyui',
        'prompt_id': job_id,
        **metadata,
    }
