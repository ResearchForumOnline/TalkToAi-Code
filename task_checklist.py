"""Local conversation checklists. Checking an item never runs the item."""
import re
from pathlib import Path

from agent_workflow import check_evidence, normalize_plan
from chat_intent import request_body, requests_tool_continuation


_NOUN = r'(?:to[\s-]?do(?:\s+list)?|check\s?list|list of (?:things (?:i |we )?(?:need )?)?to[\s-]?do)'
_ACTION = r'(?:build|create|make|write|implement|update|fix|edit|design|draw|render|inspect|investigate|research|review|compare|test|verify|check|run|open|send|publish|deploy|save|export|download|install|add|remove|clean|document|plan|pack(?:age)?|collect|buy|book|call|email|pick|finish|prepare)'
_SPLIT = re.compile(r'(?:[,;]\s*|\s+)(?:and\s+)?then\s+|;\s*|\n+|'
                    r',?\s+(?:and\s+)(?=' + _ACTION + r'\b)|,\s*(?=' + _ACTION + r'\b)', re.I)


def command(text):
    if not isinstance(text, str) or len(text) > 4000:
        return None
    body = request_body(text).strip().rstrip(' .!?')
    body = re.sub(r'[ ,]+(?:please|sir)$', '', body, flags=re.I).strip()
    prefix = r'(?:(?:a|an|the|my|our|current|task|new)\s+)*'
    match = re.fullmatch(r'(?:make|create|write|give|build|draft)(?:\s+me)?\s+' + prefix + _NOUN
                         + r'(?:\s+for\s+(?:this|the current) (?:task|project))?'
                         + r'(?:(?:\s*[:\n]\s*|\s+(?:with|for)\s+)(.+))?', body, re.I | re.S)
    if match:
        return 'create', (match[1] or '').strip()
    if re.fullmatch(r'(?:(?:show|display|open|list|read)(?:\s+me)?\s+)?' + prefix + _NOUN
                    + r'(?:\s+(?:for\s+)?(?:this|the current) (?:task|project))?', body, re.I):
        return 'show', ''
    match = re.fullmatch(r'(?:mark|set)\s+(?:(?:to[\s-]?do|item|number|step|task)\s+)*#?(\d{1,2})'
                         r'\s+(?:as\s+)?(done|complete[d]?|pending|incomplete|undone|to[\s-]?do)', body, re.I)
    if match:
        return ('done' if match[2].lower() in ('done','complete','completed') else 'undo'), int(match[1])
    match = re.fullmatch(r'(check off|tick off|uncheck|untick|unmark)\s+(?:(?:item|step|task|number)\s+)?#?(\d{1,2})', body, re.I)
    if match:
        return ('undo' if match[1].lower().startswith('un') else 'done'), int(match[2])
    return None


def _items(text, explicit=False):
    raw = re.split(r'[,\n;]+|\s+and\s+(?=' + _ACTION + r'\b)', text, flags=re.I) if explicit else _SPLIT.split(text)
    items = []
    for item in raw:
        title = re.sub(r'^(?:(?:[-*•]|\d{1,2}[.)])\s*|(?:and|then)\s+)', '', item.strip(), flags=re.I)
        if title and title.casefold() not in {entry.casefold() for entry in items}:
            items.append(title)
    return items


def _project(task):
    return str(Path(task['project']).resolve())


def _bucket(task):
    return task.get('checklists', {}).get(_project(task), {})


def saved(task):
    bucket = _bucket(task)
    return bucket.get('manual') or bucket.get('work')


def _save(task, slot, plan):
    task.setdefault('checklists', {}).setdefault(_project(task), {})[slot] = plan
    return plan


def _objective(task):
    messages = task.get('messages', [])
    start = task.get('checklist_history_start', 0)
    for index in range(len(messages)-1, start-1, -1):
        message = messages[index]
        if message.get('role') != 'user' or message.get('source') == 'app' or message.get('_automation_nudge'):
            continue
        content = message.get('typed_content', message.get('content'))
        if not isinstance(content, str):
            continue
        text = content.strip()
        body = request_body(text)
        if (not text or command(text) or requests_tool_continuation(text)
                or re.fullmatch(r'(?:ok(?:ay)?|thanks?|thank you|sir|hello|hi|nice|great|yes|please|good|work|amazing|of course|you)[\s,!.]*(?:(?:sir|work|thanks?|you)[\s,!.]*)*', body, re.I)):
            continue
        if (re.match(_ACTION + r'\b', body, re.I) or len(_items(body)) > 1
                or re.match(r'(?:i (?:need|want)|help me|organize|organise|organizing|organising)\b', body, re.I)):
            return index, body
        # A substantive change of topic ends the search for a previous objective.
        return index, ''
    return -1, ''


def _step(text, watch=''):
    return {'step': text, 'status': 'pending', 'watch': watch}


def _steps(text, explicit=False):
    titles = _items(text, explicit)
    if len(titles) > 10 or any(len(title) > 180 for title in titles):
        raise ValueError('Use up to ten items, each under 181 characters. Split longer tasks into a second checklist.')
    return [_step(title) for title in titles]


def begin(task, brief, *, scene=False):
    """Start a fresh observation window for this request. User-created lists stay saved."""
    try:
        steps = _steps(request_body(brief))
    except ValueError:
        steps = []
    if scene:
        steps = [_step('Save the requested 3D scene', 'scene'), _step('Verify the saved scene geometry', 'geometry')] + steps[1:9]
    elif len(steps) < 2:
        return None
    for item in steps:
        target = re.fullmatch(r'(?:run|execute)\s+(?:(?:the|project|automated)\s+)*(tests?|checks?)[.! ]*',
                              item['step'], re.I)
        if target:
            item['watch'] = 'tests' if target[1].lower().startswith('test') else 'checks'
    index, _ = _objective(task)
    return _save(task, 'work', {'objective': brief[:400], 'anchor': index, 'steps': steps})


def _from_current(task, goal):
    index, objective = _objective(task)
    work = _bucket(task).get('work')
    goal_position = task.get('checklist_goal_position', -1)
    if goal and (goal_position > index or index < 0 or goal['objective'].casefold() == objective.casefold()):
        return {'objective': goal['objective'], 'steps': [
            {'step': entry['text'], 'status': {'met':'completed','blocked':'blocked'}.get(entry['status'],'pending'),
             'basis': 'reported'} for entry in goal['criteria']]}
    if work and (work.get('anchor') == index or not objective):
        return work
    # A model plan belongs to the request that produced it. Keep its attribution.
    if task.get('checklist_plan_context') == [_project(task), index]:
        plan = normalize_plan(task['plan']['steps'], task['plan'].get('explanation',''))
        return {'objective': objective, 'steps': [dict(item, basis='reported') for item in plan['steps']]}
    if objective:
        return {'objective': objective, 'steps': _steps(objective)}
    return None


def note_model_plan(task):
    task['checklist_plan_context'] = [_project(task), _objective(task)[0]]


def selected(task, goal=None):
    return _bucket(task).get('manual') or _from_current(task, goal)


def apply(task, request, goal=None):
    operation, argument = request
    if operation == 'create' and argument:
        entries = _steps(argument, explicit=True)
        if not entries:
            raise ValueError('Give me the items you want on the list.')
        return _save(task, 'manual', {'objective': 'My to-do list', 'steps': entries})
    plan = (_from_current(task, goal) or selected(task, goal)) if operation == 'create' else selected(task, goal)
    if not plan or not plan['steps']:
        raise ValueError('What would you like to get done? For example, “Make a to-do list: sketch the scene, add lighting, export the model”.')
    if operation in ('done','undo'):
        if not 1 <= argument <= len(plan['steps']):
            raise ValueError(f'Choose an item from 1 to {len(plan["steps"])}.')
        item = plan['steps'][argument - 1]
        item.update(status='completed' if operation == 'done' else 'pending', basis='user')
        item.pop('evidence', None)
    if plan is not _bucket(task).get('work') and plan is not _bucket(task).get('manual'):
        _save(task, 'manual' if operation in ('done','undo') else 'work', dict(plan, anchor=_objective(task)[0]))
    return plan


def observe(plan, verification, artifacts, checked=False):
    """Earn scene checks only when this run registered the precise file named by the verifier."""
    if not plan:
        return
    status = verification.get('status')
    primary = verification.get('primary_output') or {}
    digest, name = primary.get('sha256'), primary.get('name')
    scene = bool(name and digest and (name, digest) in artifacts)
    for item in plan['steps']:
        watch = item.get('watch')
        if not watch or item.get('basis') == 'user':
            continue
        if status == 'passed' and ((watch in ('scene','geometry') and scene)
                                  or (watch == 'checks' and checked and not primary)):
            item.update(status='completed', basis='observed', evidence=name or verification.get('summary','')[:160])
        elif watch in ('checks','tests') and (checked or status == 'stale') and status in ('stale','failed','unverified'):
            item.update(status='blocked' if status == 'failed' else 'pending', basis='observed',
                        evidence=verification.get('summary','')[:160])


def observe_test_output(plan, output):
    """A syntax check alone cannot satisfy a request to run tests."""
    count = re.search(r'(?m)^(?:Ran ([1-9]\d*) tests?\b|.*?\b([1-9]\d*) passed(?:,| in\b)|# pass ([1-9]\d*)\s*$)', output)
    if count and check_evidence(output)['status'] == 'passed':
        number = next(value for value in count.groups() if value)
        for item in plan['steps']:
            if item.get('watch') == 'tests' and item.get('basis') != 'user':
                item.update(status='completed', basis='observed', evidence=f'{number} test(s) reported by the project check.')


def line(item):
    label = {'pending':'To do', 'completed':'Done', 'in_progress':'Working', 'blocked':'Blocked'}[item['status']]
    basis = {'reported':'agent-reported', 'observed':'app check', 'user':'marked by you'}.get(item.get('basis'))
    return f'{label} ({basis}) · {item["step"]}' if basis else f'{label} · {item["step"]}'


def readout(plan):
    # Literal text prevents filenames or user-written items from becoming external hyperlinks.
    rows = [plan['objective']] if plan.get('objective') else []
    rows.extend(f'{n}. {line(item)}' + (f' — {item["evidence"]}' if item.get('evidence') else '')
                for n, item in enumerate(plan['steps'], 1))
    body = '\n'.join(rows)
    fence = chr(96) * max(3, 1 + max((len(m[0]) for m in re.finditer(chr(96) + '+', body)), default=0))
    return '**To-do list**\n\n' + fence + 'text\n' + body + '\n' + fence
