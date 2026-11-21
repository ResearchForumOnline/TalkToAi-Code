"""Plan and bound sequential, read-only reviews on the selected local model."""
from dataclasses import dataclass
import http.client
import json
from pathlib import PurePosixPath
import re
import time
from urllib.parse import urlsplit

from agent_focus import normalize_focus

MAX_WORKERS = 3
WORKER_SECONDS = 180
COLD_LOAD_SECONDS = 120
TEAM_SECONDS = 360
READ_TOOLS = frozenset(('list_files', 'read_file', 'read_project_files', 'file_fingerprint',
                        'search_code', 'project_map', 'triage_failures'))
ROLE_FOCUS = {
    'investigator': 'Trace the relevant implementation and identify the files and symbols involved.',
    'reviewer': 'Review the relevant behavior for correctness and regressions. Cite specific source locations.',
    'test_planner': 'Inspect existing project tests and report coverage and gaps. Suggest specific checks.',
    'documenter': 'Check the project documentation and examples against the requested behavior. Cite the relevant files.',
    'gameplay_reviewer': 'Trace the requested gameplay through the actual scenes, scripts or input settings. Name the relevant mechanics and source paths.',
    'playtest_reviewer': 'Inspect existing game tests or checks. Identify a reproducible playtest, its expected result, and any missing coverage.',
    'asset_reviewer': 'Inspect asset references, engine compatibility and available license notices. Identify what needs verification before import.',
    'evidence_reviewer': 'Find the project evidence behind the claim. Cite exact local paths and observations, separating cited material from inference.',
    'methods_reviewer': 'Review the method, assumptions and comparisons in the actual project. Name the evidence that a second reader can check.',
    'fact_checker': 'Verify key claims using the available project evidence. Cite specific sources and flag statements that still need support.',
    'draft_reviewer': 'Review the local draft for audience, structure and clarity. Quote the relevant passage and explain a concrete improvement.',
    'consistency_reviewer': 'Check wording, terminology and examples across the relevant project documents. Point to specific inconsistencies.',
}
_GENERAL_ROLES = ('reviewer', 'investigator', 'test_planner', 'documenter')
_WORKER = r'(?:local\s+|read-only\s+)?(?:sub[- ]?agents?|agents?|workers?|reviewers?)\b'
_COUNT = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3}
_NUMBER = r'\d{1,3}|one|two|three|a|an'
_REQUEST = re.compile(r'\b(?:use|using|with|spawn|assign|delegate(?:\s+to)?|ask|start|launch|get)\s+'
                      r'(?:up\s+to\s+)?(?:(' + _NUMBER + r')(?:\s*(?:to|or|[-–/])\s*(' + _NUMBER + r'))?\s+)?' + _WORKER, re.I)
_OPT_OUT = re.compile(r"\b(?:without|no|avoid|skip|(?:do not|don.t|never)\s+(?:use|spawn|delegate to|assign|start))\s+(?:any\s+)?"
                      + _WORKER + r"|\b(?:work|do (?:this|it)) (?:alone|solo|yourself)\b", re.I)
_QUESTION = re.compile(r'^(?:(?:please\s+)?(?:explain|describe|teach|tell me|show me)\s+(?:how|what|why)\b|'
                       r'(?:what|why|when|how|which)\b|(?:can|could|should|do)\s+(?:i|we|agents?|subagents?)\b)', re.I)
_PROJECT = re.compile(r'\b(?:project|repo(?:sitory)?|code(?:base)?|implementation|source|modules?|functions?|'
                      r'patch|changes?|tests?|documentation|readme|docs|files?|bugs?|app|software|game)\b|'
                      r'\b[\w/-]+\.(?:py|js|tsx?|go|rs|cs|md)\b', re.I)
_FOCUSES = (
    ('investigator', re.compile(r'\b(?:implementation|source|modules?|functions?|code(?:base)?)\b', re.I)),
    ('test_planner', re.compile(r'\b(?:tests?|test(?:ing)? coverage|coverage|regression tests?)\b', re.I)),
    ('documenter', re.compile(r'\b(?:documentation|examples|readme|docs)\b', re.I)),
    ('reviewer', re.compile(r'\b(?:correctness|risks?|regressions?|contracts?|edge cases?)\b', re.I)),
)
_CHANGE_VERBS = r'build|implement|fix|repair|refactor|extend|update|upgrade|add|create|patch|optimi[sz]e'
_REVIEW_VERBS = (r'review|inspect|audit|investigate|assess|compare|triage|evaluate|trace|diagnose|check|examine|'
                 r'analy[sz]e|look through|perform (?:a|an) (?:project |code )?(?:review|audit)')
_POLITE = r'(?:(?:please|(?:can|could|would|will) you|I (?:want|need) you to|I.d like you to)\s+)*'
_TASK_START = re.compile(r'^\s*' + _POLITE + r'(' + _CHANGE_VERBS + '|' + _REVIEW_VERBS + r')\b', re.I)
_CHANGE_START = re.compile(r'^\s*' + _POLITE + r'(?:' + _CHANGE_VERBS + r')\b', re.I)
_SECONDARY_REVIEW = re.compile(r'^(?:(?:then|also|separately|please)\s+)*(?:' + _REVIEW_VERBS +
                               r'|identify|find|determine|look for|look into)\b', re.I)
_CLAUSES = re.compile(r'(?<=[.!?])\s+|[,;\n]+|\s+(?:and(?:\s+then)?|then|also|plus|separately|as well as)\s+', re.I)
_SOFTWARE = re.compile(r'\b(?:repo(?:sitory)?|(?:this|my|our|the|software|game) project|project files?|code(?:base)?|source|modules?|functions?|program|application|app|game|'
                       r'feature|bugs?|crash(?:es)?|parser|renderer|plugin|handler|endpoint|backend|frontend|component|'
                       r'dialog|autosave|workflow|loader|build system|regression)\b|'
                       r'[\w/-]+\.(?:py|jsx?|tsx?|cs|cpp|go|rs|gd|java|kt|swift|rb|vue|c|h)\b', re.I)
_TEST_MATERIAL = re.compile(r'\b(?:tests?|coverage|test suite|test cases?)\b|\btest_[\w.-]+\.\w+|[\w.-]+[._-](?:test|spec)s?\.\w+', re.I)
_DOC_MATERIAL = re.compile(r'\b(?:docs|documentation|readme|examples|(?:setup|installation|developer|migration|user|usage) guides?)\b', re.I)
_NEGATED = re.compile(r"^(?:do not|don.t|never|avoid|skip|without|no need to|do not need to)\b", re.I)
_TEST_EXECUTION = re.compile(r'\b(?:tests?|suite)\s+(?:(?:all|still|already|currently|now|will|can|successfully)\s+)*'
                             r'(?:run|pass(?:es|ed|ing)?|succeed(?:ed|s)?)\b|\b(?:is|are|went)\s+(?:still\s+)?(?:green|passing)\b', re.I)
_NONREVIEW_CLAUSE = re.compile(r'^(?:also\s+)?(?:' + _CHANGE_VERBS + r'|run|execute|write|edit|delete|remove|send)\b', re.I)
_NONLOCAL = re.compile(r'https?://|\b(?:ssh|inbox|mailbox|send an? email|publish|deploy|search the web|'
                       r'(?:on|from) (?:the )?(?:web|internet|github)|online documentation|remote server|'
                       r'pasted (?:below|here)|in this (?:message|snippet)|following (?:passage|excerpt|snippet))\b', re.I)
_SPECIALTY_SCOPE = re.compile(r'\b(?:projects?|repo(?:sitory)?|workspace|local files?|checked[- ]in|godot|unity|unreal engine|'
                              r'(?:my|our|this|existing) game)\b|\b[\w./-]+\.(?:md|txt|gd|tscn|cs|yaml|json)\b', re.I)
_WRITING_START = re.compile(r'^\s*' + _POLITE + r'(?:edit|revise|rewrite|polish|proofread|fact[- ]check)\b', re.I)
_SPECIALTY_TOPICS = {
    'game': (
        ('gameplay_reviewer', r'\b(?:gameplay|mechanics|controls?|movement|collisions?|scenes?|levels?|platformer)\b'),
        ('playtest_reviewer', r'\b(?:playtest(?:s|ing)?|tests?|test coverage|regression checks?)\b'),
        ('asset_reviewer', r'\b(?:assets?|sprites?|textures?|licenses?|licences?|asset imports?)\b'),
    ),
    'research': (
        ('evidence_reviewer', r'\b(?:sources?|evidence|citations?|references?|datasets?|data)\b'),
        ('methods_reviewer', r'\b(?:methods?|methodology|assumptions?|benchmarks?|experiments?|controls?)\b'),
        ('fact_checker', r'\b(?:findings?|claims?|conclusions?|interpretation|limitations?|uncertainty)\b'),
    ),
    'writing': (
        ('draft_reviewer', r'\b(?:draft|structure|audience|rewrite|introduction|headings?|argument)\b'),
        ('fact_checker', r'\b(?:facts?|sources?|evidence|citations?|claims?)\b'),
        ('consistency_reviewer', r'\b(?:terminology|consistency|style guide|wording|glossary)\b'),
    ),
}


def worker_limit(value):
    return max(0, min(MAX_WORKERS, value)) if type(value) is int else MAX_WORKERS


def requested_count(text):
    """None means no explicit request. Zero also represents an explicit opt-out."""
    if _OPT_OUT.search(text):
        return 0
    if _QUESTION.search(text.strip()):
        return None
    match = _REQUEST.search(text)
    if not match:
        return None
    if re.match(r'\s+zero\b', text[match.end():], re.I):
        return None
    if (re.search(r'\bagents?\b', match[0], re.I) and not _PROJECT.search(text) and
            not re.match(r'^(?:(?:please|can you|could you|would you)\s+)*(?:use|spawn|assign|delegate|ask|start|launch)\b', text.strip(), re.I)):
        return None
    count = match[2] or match[1]
    if count:
        return int(count) if count.isdigit() else _COUNT[count.lower()]
    return 2 if match[0].lower().endswith('s') else 1


def requests_project_workers(text, *, focus=None):
    """Whether Chat has a request for project work that can use local reviewers."""
    count = requested_count(text)
    if count is not None:
        return bool(count and _PROJECT.search(text))
    return bool(_specialty_roles(text, focus) or _implicit_roles(text))


def review_question(text):
    return re.sub(r'^(?:please\s+)?to\s+', '', _REQUEST.sub('', text, count=1).strip(' ,:;'), flags=re.I)


@dataclass(frozen=True)
class Assignment:
    role: str
    task: str


def _implicit_roles(text):
    if not _TASK_START.match(text) or _NONLOCAL.search(text):
        return []
    clauses = [part.strip() for part in _CLAUSES.split(text) if part.strip()]
    if len(clauses) < 2:
        return []
    if not _CHANGE_START.match(clauses[0]):
        positive = ' '.join(part for part in clauses if not (_NEGATED.match(part) or _NONREVIEW_CLAUSE.match(part)))
        roles = [role for role, pattern in _FOCUSES if pattern.search(positive)]
        return roles if _PROJECT.search(positive) and len(roles) >= 2 else []
    if not _SOFTWARE.search(clauses[0]):
        return []
    # Editing the implementation is the parent's job. Workers can independently
    # inspect its current behavior and a specifically requested second body of
    # evidence. A routine "run tests" or "update the docs" adds no review.
    roles = ['investigator']
    reading = False
    for clause in clauses[1:]:
        if _NEGATED.match(clause):
            reading = False
            continue
        noun = re.sub(r'^(?:(?:the|its|our|existing|current|unit|integration|regression|project)\s+)+', '', clause, flags=re.I)
        reading = bool(_SECONDARY_REVIEW.match(clause) or
                       (reading and (_TEST_MATERIAL.match(noun) or _DOC_MATERIAL.match(noun))))
        if not reading:
            continue
        if _TEST_MATERIAL.search(clause) and not _TEST_EXECUTION.search(clause) and 'test_planner' not in roles:
            roles.append('test_planner')
        if _DOC_MATERIAL.search(clause) and 'documenter' not in roles:
            roles.append('documenter')
    return roles if len(roles) > 1 else []


def delegation_roles(focus):
    topics = _SPECIALTY_TOPICS.get(normalize_focus(focus))
    return tuple(role for role, _ in topics) if topics else _GENERAL_ROLES


def _specialty_roles(text, focus):
    topics = _SPECIALTY_TOPICS.get(normalize_focus(focus))
    if not topics or not (_TASK_START.match(text) or _WRITING_START.match(text)):
        return []
    if _NONLOCAL.search(text) or not _SPECIALTY_SCOPE.search(text):
        return []
    clauses = [part.strip() for part in _CLAUSES.split(text) if part.strip()]
    independent = [part for part in clauses[1:] if not (_NEGATED.match(part) or _NONREVIEW_CLAUSE.match(part))]
    if not independent:
        return []
    positive = ' '.join([clauses[0], *independent])
    roles = [role for role, pattern in topics if re.search(pattern, positive, re.I)]
    return roles if len(roles) >= 2 else []


def plan_reviews(text, limit=MAX_WORKERS, *, focus=None):
    """Preselect independent project questions. Simple turns stay on the parent."""
    limit = worker_limit(limit)
    focus = normalize_focus(focus)
    count = requested_count(text)
    if not limit or count == 0 or _QUESTION.search(text.strip()):
        return []
    if count is None:
        roles = _specialty_roles(text, focus) or _implicit_roles(text)
        count = len(roles)
    elif focus in _SPECIALTY_TOPICS:
        roles = [role for role, pattern in _SPECIALTY_TOPICS[focus] if re.search(pattern, text, re.I)]
        roles.extend(role for role in delegation_roles(focus) if role not in roles)
    else:
        roles = [role for role, pattern in _FOCUSES if pattern.search(text)]
        roles.extend(role for role in ('reviewer', 'investigator', 'test_planner') if role not in roles)
    scope = review_question(text)
    return [Assignment(role, ROLE_FOCUS[role] + '\nProject request:\n' + scope[:3500])
            for role in roles[:min(limit, count)]]


def starting_files(paths, task, role):
    """Choose two relevant text excerpts before a small model's first review step."""
    from work_packet import SOURCE_SUFFIXES
    task = task.casefold()
    mentioned = set(re.findall(r'[\w./\\-]+', task))
    ranked = []
    for name in paths:
        path = PurePosixPath(name.replace('\\', '/').casefold())
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        test = any(word in ('test', 'tests', '__tests__') for word in path.parts) or bool(
            re.search(r'(^test[_-]|[_\-.](?:tests?|spec)\.)', path.name))
        doc = path.suffix in ('.md', '.rst', '.txt')
        if role in ('test_planner', 'playtest_reviewer'):
            related = test
        elif role in ('documenter', 'evidence_reviewer', 'fact_checker', 'draft_reviewer', 'consistency_reviewer'):
            related = doc
        elif role == 'gameplay_reviewer':
            related = not test and path.suffix in ('.gd', '.gdshader', '.tscn', '.godot', '.cs', '.cpp', '.lua')
        elif role == 'asset_reviewer':
            related = any(word in path.as_posix() for word in ('asset', 'sprite', 'licen', 'credit'))
        elif role == 'methods_reviewer':
            related = test or any(word in path.name for word in ('bench', 'method', 'experiment', 'protocol'))
        else:
            related = not (test or doc)
        named = path.as_posix() in mentioned or path.name in mentioned or (' ' in path.name and path.name in task)
        if named or related:
            ranked.append((not related, not named, len(path.parts), len(name), name))
    ranked.sort()
    if ranked and not ranked[0][0]:
        ranked = [entry for entry in ranked if not entry[0]]
    return [entry[-1] for entry in ranked[:2]]


def complete_named_files(inventory, examined, task):
    from work_packet import _explicit_file_refs
    refs = [ref.casefold().replace('\\','/') for ref in _explicit_file_refs(task)]
    names = [name.casefold().replace('\\','/') for name in inventory]
    unique = set()
    for ref in refs:
        matches = [name for name in names if name == ref or ('/' not in ref and PurePosixPath(name).name == ref)]
        if len(matches) == 1:
            unique.add(matches[0])
    return bool(examined and all(f.get('complete') and not f.get('redacted')
                and f['path'].casefold().replace('\\','/') in unique for f in examined))


def local_route(url):
    from routing import LOCAL_PORT
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost') and parsed.port == LOCAL_PORT
                and parsed.path in ('', '/') and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
    except (TypeError, ValueError):
        return False


def verify_local_model(url, model):
    """Local Ollama can proxy cloud models, including under a local alias."""
    if not local_route(url) or not model or re.search(r'(?:^|[-:])cloud(?:$|:)', model, re.I):
        raise ValueError('This action requires an Ollama model installed on this PC. Choose Local or Local large.')
    parsed = urlsplit(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=4)
    try:
        conn.request('POST', '/api/show', json.dumps({'model': model, 'name': model}),
                     {'Content-Type': 'application/json'})
        with conn.getresponse() as response:
            if response.status != 200:
                raise ValueError('Ollama could not verify the selected local model.')
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('Ollama returned too much metadata to verify the selected model.')
            info = json.loads(raw)
        detail = info.get('details') if isinstance(info, dict) else None
        if (not isinstance(info, dict) or info.get('remote_model') or info.get('remote_host')
                or not ((isinstance(info.get('model_info'), dict) and info['model_info']) or
                        (isinstance(detail, dict) and detail.get('quantization_level')))):
            raise ValueError('This action requires local weights. This model is cloud-backed or could not be verified.')
        return detail if isinstance(detail, dict) else {}
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Ollama could not verify the selected local model.') from exc
    finally:
        conn.close()


def cold_load_allowance(url, model, details):
    """Allow a cold large model extra startup time only when local inventory confirms it."""
    size = re.fullmatch(r'(\d+(?:\.\d+)?)\s*B', str((details or {}).get('parameter_size','')).strip(), re.I)
    if not size or float(size[1]) < 20:
        return 0
    from routing import _json_request, matching_model
    try:
        running = _json_request(urlsplit(url).port, '/api/ps', timeout=2).get('models')
        if isinstance(running, list):
            names = [item['name'] for item in running if isinstance(item, dict) and isinstance(item.get('name'),str)]
            if len(names) == len(running) and not matching_model(names, model):
                return COLD_LOAD_SECONDS
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return 0


class WorkerStop:
    """Event-compatible view of the parent stop signal and the worker's deadline."""
    def __init__(self, parent, deadline):
        self.parent, self.deadline = parent, deadline

    def is_set(self):
        return self.parent.is_set() or time.monotonic() >= self.deadline

    def wait(self, timeout=None):
        remaining = max(0, self.deadline - time.monotonic())
        self.parent.wait(remaining if timeout is None else min(remaining, timeout))
        return self.is_set()


class ReviewBudget:
    def __init__(self, limit):
        self.limit = worker_limit(limit)
        self.used = 0
        self.deadline = None
        self.assignments = set()

    def reserve(self, role, task, cancel):
        if cancel.is_set():
            raise InterruptedError('Task stopped.')
        if role not in ROLE_FOCUS or not isinstance(task, str) or not task.strip() or len(task) > 6000:
            raise ValueError('Give the worker a valid role and a focused question of up to 6000 characters.')
        if self.used >= self.limit:
            raise ValueError('Worker budget reached. Use the available reports to finish the task.')
        key = (role, ' '.join(task.casefold().split()))
        if key in self.assignments:
            raise ValueError('This worker question was already assigned. Use the existing report.')
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + TEAM_SECONDS
        if now >= self.deadline:
            raise TimeoutError('The project worker time limit was reached.')
        self.assignments.add(key)
        self.used += 1
        return WorkerStop(cancel, self.deadline)
