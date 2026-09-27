"""Bounded project experiment records; recording never executes an experiment."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 200
MAX_ENTRY_BYTES = 12000
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_VERIFY_BYTES = 32 * 1024 * 1024
PRIVATE_PARTS = {'.git', '.ssh', '.aws', '.azure', 'private', 'secrets', 'credentials'}
PRIVATE_NAMES = {'credentials.json', 'token.json', 'tokens.json', 'config.json', 'providers.json', 'studio.json', 'connections.json', 'id_rsa', 'id_ed25519'}
PROTOCOL_FIELDS = {'dataset', 'split', 'seed', 'environment', 'controls', 'budget',
                   'metric_definition', 'sample_size', 'limitations', 'source_urls', 'source_claims'}
COMPARISON_FIELDS = ('dataset', 'split', 'seed', 'environment', 'controls', 'budget',
                     'metric_definition', 'sample_size')


def _journal(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing project folder.')
    directory = root / '.talktoai-code'
    path = directory / 'EXPERIMENTS.jsonl'
    if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Experiment journal must stay inside the selected project without links.')
    return root, path


def _entries(path):
    if not path.exists():
        return []
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Experiment journal exceeds 1 MiB. Archive it manually before continuing.')
    try:
        entries = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Experiment journal is damaged; preserve and repair it before appending.') from exc
    if len(entries) > MAX_ENTRIES or any(not isinstance(e, dict) or e.get('schema') != 'talktoai.experiment.v1' for e in entries):
        raise ValueError('Experiment journal has an unsupported format or too many entries.')
    seen_ids = set()
    for index, entry in enumerate(entries, 1):
        identifier = entry.get('id')
        if (not isinstance(identifier, str) or len(identifier) != 32
                or any(char not in '0123456789abcdef' for char in identifier)
                or identifier in seen_ids
                or not isinstance(entry.get('hypothesis'), str) or not entry['hypothesis'].strip()
                or type(entry.get('sequence')) is not int or entry['sequence'] != index
                or not isinstance(entry.get('reported_metrics'), dict)
                or not isinstance(entry.get('evidence'), list)):
            raise ValueError(f'Experiment journal entry {index} is damaged; preserve and repair it before continuing.')
        seen_ids.add(identifier)
        protocol = entry.get('reported_protocol', {})
        try:
            _metrics(entry['reported_metrics'])
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Experiment journal entry {index} has damaged metrics; preserve and repair it before continuing.') from exc
        try:
            _protocol(protocol)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f'Experiment journal entry {index} has damaged protocol metadata; preserve and repair it before continuing.') from exc
    return entries


def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name} must be text of at most {limit} characters' + (' and nonempty.' if required else '.'))
    return value.strip()


def _metrics(value):
    if isinstance(value, str) and len(value) > 4000:
        raise ValueError('Metric JSON exceeds 4,000 characters.')
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict) or len(value) > 12:
        raise ValueError('metrics must be an object with at most 12 numeric measurements.')
    result = {}
    for name, number in value.items():
        _text(name, 'Metric name', 80, True)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError('Metric values must be finite numbers. Describe units in the metric name.')
        result[name] = number
    return result


def _protocol(value):
    """Describe a method; metadata is user-reported, never executed or fetched."""
    if isinstance(value, str) and len(value) > 4000:
        raise ValueError('Protocol JSON exceeds 4,000 characters.')
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise ValueError('Protocol must be a JSON object.') from exc
    if not isinstance(value, dict) or set(value) - PROTOCOL_FIELDS:
        raise ValueError('Protocol must contain only supported method fields.')
    result = {}
    for key in sorted(set(value) - {'source_urls', 'source_claims'}):
        result[key] = _text(value[key], key, 500, True)
    def reference(url):
        url = _text(url, 'Source URL', 500, True)
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Source URLs must be HTTP(S) references without credentials, query strings or fragments.')
        return url
    if 'source_urls' in value:
        urls = value['source_urls']
        if not isinstance(urls, list) or len(urls) > 4:
            raise ValueError('source_urls must contain at most four HTTP(S) references.')
        result['source_urls'] = [reference(url) for url in urls]
    if 'source_claims' in value:
        claims = value['source_claims']
        if not isinstance(claims, list) or len(claims) > 4:
            raise ValueError('source_claims must contain at most four claim-to-source notes.')
        result['source_claims'] = []
        for item in claims:
            if not isinstance(item, dict) or set(item) != {'claim', 'url', 'relationship'}:
                raise ValueError('Each source claim needs claim, URL and relationship.')
            relationship = item['relationship']
            if relationship not in ('supports', 'contradicts', 'background', 'unverified'):
                raise ValueError('Source relationship must be supports, contradicts, background or unverified.')
            result['source_claims'].append({'claim': _text(item['claim'], 'Source claim', 300, True),
                                            'url': reference(item['url']),
                                            'relationship': relationship})
    return result


def _evidence_path(root, name):
    _text(name, 'Evidence path', 300, True)
    relative = Path(name)
    parts = {p.casefold() for p in relative.parts}
    base = relative.name.casefold()
    if relative.is_absolute() or '..' in relative.parts or parts & PRIVATE_PARTS or base in PRIVATE_NAMES or base.startswith('.env') or any(word in base for word in ('password', 'credential', 'private_key', 'secret')) or relative.suffix.casefold() in {'.pem', '.key', '.pfx', '.dpapi'}:
        raise ValueError('Use a non-sensitive evidence file inside the project.')
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *list(path.parents)[:len(relative.parts)])):
        raise ValueError('Evidence files must stay in the project without links.')
    return relative, path


def _hash_evidence(path, budget=None):
    before = path.stat()
    if not path.is_file() or before.st_size > MAX_EVIDENCE_BYTES:
        raise ValueError('Evidence must be an existing file of at most 4 MiB.')
    if budget is not None and before.st_size > budget[0]:
        raise ValueError('Verification byte budget exhausted.')
    digest = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        while True:
            remaining=min(65536, MAX_EVIDENCE_BYTES-size, budget[0] if budget is not None else 65536)
            if remaining <= 0:break
            chunk=stream.read(remaining)
            if not chunk:break
            size += len(chunk)
            if budget is not None:
                budget[0] -= len(chunk)
            digest.update(chunk)
    after = path.stat()
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if signature(before) != signature(after) or size != after.st_size:
        raise ValueError('Evidence changed while being read; retry with a stable file.')
    return size, digest.hexdigest()


def _evidence(root, names):
    if isinstance(names, str) and len(names) > 4000:
        raise ValueError('Evidence path JSON exceeds 4,000 characters.')
    names = json.loads(names) if isinstance(names, str) else names
    if not isinstance(names, list) or len(names) > 8:
        raise ValueError('Provide at most 8 project-relative evidence file paths.')
    evidence = []
    for name in names:
        relative, path = _evidence_path(root, name)
        if not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError('Evidence must be an existing file of at most 4 MiB.')
        size, digest = _hash_evidence(path)
        evidence.append({'path': relative.as_posix(), 'bytes': size, 'sha256': digest, 'status': 'file_bytes_hashed'})
    return evidence


def record_experiment(project, hypothesis, command, result, metrics='{}', evidence_paths='[]', next_step='', protocol='{}'):
    root, path = _journal(project)
    entry = {'schema': 'talktoai.experiment.v1', 'id': uuid.uuid4().hex, 'created_utc': datetime.now(timezone.utc).isoformat(),
             'hypothesis': _text(hypothesis, 'Hypothesis', 1600, True),
             'reported_command': _text(command, 'Command', 1600),
             'reported_result': _text(result, 'Result', 2400, True),
             'reported_metrics': _metrics(metrics), 'reported_protocol': _protocol(protocol),
             'evidence': _evidence(root, evidence_paths),
             'next_step': _text(next_step, 'Next step', 1000),
             'verification': 'Claims and metrics are reported, not independently verified. Evidence hashes establish file bytes only. No command was executed by this tool.'}
    encoded = (json.dumps(entry, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_ENTRY_BYTES:
        raise ValueError('Experiment entry exceeds the 12 KB limit.')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Experiment journal is busy. Retry later; recover a stale lock only after confirming no writer is active.') from exc
    temporary = None
    try:
        os.close(descriptor)
        previous = _entries(path)
        if len(previous) >= MAX_ENTRIES:
            raise ValueError('Experiment journal reached 200 entries. Archive it manually before continuing.')
        entry['sequence'] = len(previous) + 1
        if len(json.dumps(entry, ensure_ascii=False).encode('utf-8')) > MAX_ENTRY_BYTES:
            raise ValueError('Experiment entry exceeds the 12 KB limit.')
        data = ''.join(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n' for item in previous + [entry]).encode('utf-8')
        if len(data) > MAX_BYTES:
            raise ValueError('Experiment journal reached 1 MiB. Archive it manually before continuing.')
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='experiments-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        return json.dumps({'id': entry['id'], 'sequence': entry['sequence'], 'journal': '.talktoai-code/EXPERIMENTS.jsonl', 'evidence_files': len(entry['evidence']), 'verification': entry['verification']})
    finally:
        if temporary and temporary.exists(): temporary.unlink()
        lock.unlink(missing_ok=True)


def _verify_record(root, entry, budget):
    records = entry.get('evidence', [])
    if not isinstance(records, list) or len(records) > 8:
        return {'status': 'invalid_record', 'files': []}
    results = []
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get('path'), str)
                or not isinstance(record.get('sha256'), str) or len(record['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in record['sha256'])
                or type(record.get('bytes')) is not int or not 0 <= record['bytes'] <= MAX_EVIDENCE_BYTES):
            results.append({'status':'invalid_record'});continue
        item = {'path':record['path'][:300]};results.append(item)
        try:
            _, target = _evidence_path(root, record['path'])
        except (ValueError, OSError):
            item['status']='blocked';continue
        try:
            size, digest = _hash_evidence(target, budget)
            item.update(status='match' if (size == record['bytes'] and digest == record['sha256']) else 'changed',
                        current_bytes=size, current_sha256=digest)
        except FileNotFoundError:item['status']='missing'
        except ValueError as exc:
            item['status']='budget_exceeded' if 'budget' in str(exc) else 'unverifiable'
            item['reason']=str(exc)
        except OSError:item['status']='unreadable'
    return {'status': 'no_evidence' if not results else 'all_match' if all(item['status']=='match' for item in results) else 'needs_attention', 'files':results}


def read_experiments(project, limit='5', verify_evidence=False):
    root, path = _journal(project)
    if isinstance(verify_evidence,str):
        if verify_evidence.lower() not in ('true','false'):raise ValueError('verify_evidence must be true or false.')
        verify_evidence=verify_evidence.lower()=='true'
    if not isinstance(verify_evidence,bool):raise ValueError('verify_evidence must be true or false.')
    limit = int(limit)
    if not 1 <= limit <= 20:
        raise ValueError('Read 1–20 recent experiment entries.')
    entries = _entries(path)
    selected = []; size = 0
    for entry in reversed(entries[-limit:]):
        item = json.dumps(entry, ensure_ascii=False)
        if size + len(item) > 16000:
            break
        selected.insert(0, entry); size += len(item)
    budget=[MAX_VERIFY_BYTES]
    if verify_evidence:
        for entry in reversed(selected):entry['evidence_check']=_verify_record(root,entry,budget)
        while selected and len(json.dumps(selected,ensure_ascii=False))>32000:selected.pop(0)
    response={'total_entries': len(entries), 'entries': selected, 'omitted_entries': len(entries) - len(selected),
              'note': 'Historical reported claims; matching current file hashes establish byte consistency only, not scientific correctness, journal authenticity, or successful command execution.'}
    if verify_evidence:
        response.update(evidence_checked_utc=datetime.now(timezone.utc).isoformat(),evidence_bytes_read=MAX_VERIFY_BYTES-budget[0])
    return json.dumps(response, ensure_ascii=False)


def compare_experiments(project, baseline_id, candidate_id, metric, direction='minimize', verify_evidence=True):
    """Compare reported measurements without upgrading them to verified facts.

    Protocol fields and evidence hashes are checked for reproducibility. Equal
    hashes establish byte identity only; the method and metric remain reported.
    The journal is read-only throughout this operation.
    """
    root, path = _journal(project)
    baseline_id = _text(baseline_id, 'Baseline ID', 32, True)
    candidate_id = _text(candidate_id, 'Candidate ID', 32, True)
    metric = _text(metric, 'Metric name', 80, True)
    if baseline_id == candidate_id:
        raise ValueError('Choose different baseline and candidate records.')
    if direction not in ('minimize', 'maximize'):
        raise ValueError('direction must be minimize or maximize.')
    if isinstance(verify_evidence, str):
        if verify_evidence.lower() not in ('true', 'false'):
            raise ValueError('verify_evidence must be true or false.')
        verify_evidence = verify_evidence.lower() == 'true'
    if not isinstance(verify_evidence, bool):
        raise ValueError('verify_evidence must be true or false.')
    records = {entry['id']: entry for entry in _entries(path)
               if isinstance(entry.get('id'), str) and len(entry['id']) == 32}
    if baseline_id not in records or candidate_id not in records:
        raise ValueError('Both experiment IDs must be present in this project journal.')
    baseline, candidate = records[baseline_id], records[candidate_id]
    for name, entry in (('baseline', baseline), ('candidate', candidate)):
        measurements = entry.get('reported_metrics')
        if not isinstance(measurements, dict) or metric not in measurements:
            raise ValueError(f'{name} lacks the requested metric.')
        value = measurements[metric]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{name} metric is not a finite number.')
    before, after = baseline['reported_metrics'][metric], candidate['reported_metrics'][metric]
    delta = after - before
    if not math.isfinite(delta):
        raise ValueError('Reported metric difference is outside the finite numeric range.')
    baseline_protocol = baseline.get('reported_protocol') or {}
    candidate_protocol = candidate.get('reported_protocol') or {}
    if not isinstance(baseline_protocol, dict) or not isinstance(candidate_protocol, dict):
        raise ValueError('One experiment has invalid reported protocol metadata.')
    warnings = []
    for field in COMPARISON_FIELDS:
        old, new = baseline_protocol.get(field), candidate_protocol.get(field)
        if old is None or new is None:
            warnings.append(f'{field} missing from one or both reported protocols')
        elif old != new:
            warnings.append(f'{field} differs between reported protocols')
    baseline_sequence, candidate_sequence = baseline.get('sequence'), candidate.get('sequence')
    if (type(baseline_sequence) is not int or type(candidate_sequence) is not int
            or baseline_sequence >= candidate_sequence):
        warnings.append('candidate is not later than baseline in this journal')
    for name, protocol in (('baseline', baseline_protocol), ('candidate', candidate_protocol)):
        if protocol.get('limitations'):
            warnings.append(f'{name} reports limitations; inspect the original entry')
        if any(item.get('relationship') in ('contradicts', 'unverified')
               for item in protocol.get('source_claims', []) if isinstance(item, dict)):
            warnings.append(f'{name} has contradicting or unverified source claims')
    budget = [MAX_VERIFY_BYTES]
    checks = {}
    if verify_evidence:
        checks = {'baseline': _verify_record(root, baseline, budget),
                  'candidate': _verify_record(root, candidate, budget)}
        for name, check in checks.items():
            if check['status'] != 'all_match':
                warnings.append(f'{name} evidence status: {check["status"]}')
    else:
        warnings.append('current evidence file bytes were not rechecked')
    numeric_direction = 'lower' if delta < 0 else 'higher' if delta > 0 else 'unchanged'
    reported_improvement = delta < 0 if direction == 'minimize' else delta > 0
    response = {'schema': 'talktoai.experiment-comparison.v1',
                'baseline_id': baseline_id, 'candidate_id': candidate_id,
                'metric': metric, 'desired_direction': direction,
                'baseline_reported': before, 'candidate_reported': after,
                'candidate_minus_baseline': delta,
                'reported_numeric_direction': numeric_direction,
                'reported_improvement': reported_improvement,
                'comparison_status': 'comparable_as_reported' if not warnings else 'limitations_found',
                'warnings': warnings,
                'evidence_checks': checks,
                'verification': 'Arithmetic uses reported metrics. Matching file hashes check current bytes only; they do not prove measurement validity, causal improvement or a scientific discovery.'}
    return json.dumps(response, ensure_ascii=False, allow_nan=False)
