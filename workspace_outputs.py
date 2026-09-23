"""Budgeted project reads and explicit output registration; no remote upload."""
import hashlib
import json
from pathlib import Path


def read_batch(tools, requests):
    requests = json.loads(requests) if isinstance(requests, str) else requests
    if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
        raise ValueError('Provide 1-8 objects with path, optional offset and optional expected_sha256.')
    if any(not isinstance(r, dict) or not isinstance(r.get('path'), str) or len(r['path'])>1024 for r in requests):
        raise ValueError('Each request needs a project-relative path.')
    results = []
    for request in requests:
        result = {'path': request['path']}
        try:
            path = tools.path(request['path'])
            if path.stat().st_size > 1024*1024:
                raise ValueError('File exceeds 1 MiB; choose a smaller source file.')
            data = path.read_bytes()
            if len(data) > 1024*1024:
                raise ValueError('File grew beyond the read limit.')
            if b'\x00' in data:
                raise ValueError('Binary file is not supported by text reads.')
            digest = hashlib.sha256(data).hexdigest()
            if request.get('expected_sha256') and request['expected_sha256'] != digest:
                raise ValueError('File changed since the previous page. Restart at offset 0.')
            text = data.decode('utf-8-sig')
            offset = int(request.get('offset', 0))
            if not 0 <= offset <= len(text):
                raise ValueError('offset is outside the file.')
            # Share the response budget across all files; offsets are Unicode characters.
            chunk = text[offset:offset+max(256, 12000//len(requests))]
            result.update(sha256=digest, bytes=len(data), offset=offset, content=chunk,
                          next_offset=offset+len(chunk) if offset+len(chunk)<len(text) else None,
                          complete=offset+len(chunk)>=len(text))
            while len(json.dumps(result, ensure_ascii=False)) > 18000//len(requests) and chunk:
                chunk = chunk[:len(chunk)//2]
                result.update(content=chunk, next_offset=offset+len(chunk), complete=False)
        except (OSError, ValueError, TypeError, PermissionError) as exc:
            result['error'] = str(exc)[:300]
        results.append(result)
    return json.dumps({'files': results, 'note': 'Independent file observations, not an atomic snapshot. Use sha256 when continuing a page or making a checked write.'}, ensure_ascii=False)


def register_output(tools, path, title=''):
    source = tools.path(path)
    if not source.is_file():
        raise ValueError('Register an existing project file, not a folder.')
    if source.stat().st_size > 100*1024*1024:
        raise ValueError('Output exceeds 100 MiB; provide its path in the response instead.')
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as stream:
        while chunk := stream.read(65536):
            if tools.cancel.is_set(): raise InterruptedError('Task stopped.')
            size += len(chunk)
            if size > 100*1024*1024: raise ValueError('Output grew beyond 100 MiB.')
            digest.update(chunk)
    return json.dumps({'artifact': str(source), 'type': 'file', 'title': str(title or source.name)[:120],
        'bytes': size, 'sha256': digest.hexdigest(),
        'note': 'Registered local output only; not uploaded, executed, tested or certified. Hash describes bytes read at registration.'})
