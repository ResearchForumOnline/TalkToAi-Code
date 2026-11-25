"""Bounded agent workflow helpers. No model calls or permission changes here."""
import json
import math
import re
import ntpath
from pathlib import PureWindowsPath

# pathlib's is_reserved() is removed in Python 3.15; ntpath.isreserved() exists from 3.13.
_reserved_name = getattr(ntpath, 'isreserved', None) or (lambda part: PureWindowsPath(part).is_reserved())


NEW_FILE_FORMAT = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
    'required': ['path', 'content'],
}
_FILE_PROMISE = re.compile(
    r"\b(?:let me|let['’]s|i(?:['’]ll| will| can| am going to)|i['’]m going to)\s+"
    r"(?:now\s+)?(?:make|build|create|write|generate|design|implement|save|put together)\b|"
    r"\bi(?: have|['’]ve)? (?:created|written|built|saved|generated)\b|"
    r"^[ \t]*```(?:html|python|javascript|js|css|svg|json)\s*$", re.I | re.M)


def promises_project_file(reply):
    return bool(_FILE_PROMISE.search(reply))


def missing_python_probe(command, result):
    """Only an optional interpreter discovery failure can be superseded by a verified file."""
    probe = re.fullmatch(r'\s*(?:(?:python(?:3(?:\.\d+)?)?|py(?:\s+-3)?)\s+(?:--version|-V)|'
                         r'(?:where(?:\.exe)?|which|command\s+-v|Get-Command)\s+(?:py|python3?))\s*', command, re.I)
    return bool(probe or (not re.search(r'[\n\r&|;<>`$()]', command)
                          and re.match(r'^\s*(?:py|python3?)\s', command, re.I)
                          and re.search(r'Python was not found|(?:python3?|py): (?:command )?not found|'
                                        r"(?:term|command) ['\x22](?:python3?|py)['\x22] is not recognized", result, re.I)))


def new_file_call(reply):
    """Convert only an explicitly requested, complete new-file response to an ordinary checked write."""
    def unique_fields(pairs):
        fields = dict(pairs)
        if len(pairs) != len(fields):
            raise ValueError('The file response contains a duplicate field.')
        return fields
    data = json.loads(reply, object_pairs_hook=unique_fields)
    if not isinstance(data, dict) or set(data) != {'path', 'content'}:
        raise ValueError('Return one JSON object with path and content.')
    return checked_file_call(data['path'], data['content'])


def checked_file_call(path, content):
    """Validate a new project file and express it as an ordinary checked write."""
    if not isinstance(path, str) or not path or len(path) > 240:
        raise ValueError('Provide a short project-relative file path.')
    path = path.replace('\\', '/')
    parts = path.split('/')
    if (any(not part or part.startswith('.') or part.endswith((' ', '.')) or _reserved_name(part)
            for part in parts) or re.search(r'[<>:"|?*\x00-\x1f]', path)):
        raise ValueError('Use a portable project-relative filename with no hidden or parent folders.')
    if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > 500_000:
        raise ValueError('Supply the complete nonempty file contents within 500 KB.')
    return {'function': {'name': 'write_file_checked', 'arguments': {
        'path': path, 'content': content, 'expected_sha256': '__absent__'}}}


def check_evidence(result):
    """Conservative status from the check runner, not from model prose."""
    codes=[int(c) for c in re.findall(r'^Exit (-?\d+)\s*$',result,re.M)]
    if any(c!=0 for c in codes) or re.search(r'^(?:SCRIPT ERROR|ERROR):',result,re.M):
        return {'status':'failed','summary':'A detected check reported a failure. Inspect the tool output.'}
    if not codes or 'UNVERIFIED:' in result or 'Ran 0 tests' in result:
        return {'status':'unverified','summary':'No passing check established. Inspect the output for missing checks, zero tests or errors.'}
    if result.startswith('Exit 0\nStandalone file checks\n'):
        return {'status':'passed','summary':result.split('\n',2)[2][:800]}
    return {'status':'passed','summary':f'{len(codes)} detected check command(s) exited successfully. This is not complete gameplay or product verification.'}

PLAN_TOOL = {'type':'function','function':{
    'name':'update_plan',
    'description':'Maintain a short user-visible checklist for a multi-step task. Update it as work progresses. A completed step is your report, not an independent test result. Mark genuinely blocked work blocked and explain why. Do not use for trivial requests.',
    'parameters':{'type':'object','properties':{
        'steps':{'type':'array','minItems':1,'maxItems':10,'items':{'type':'object','properties':{
            'step':{'type':'string','description':'A short verifiable outcome'},
            'status':{'type':'string','enum':['pending','in_progress','completed','blocked']}},'required':['step','status']}},
        'explanation':{'type':'string','description':'Brief user-facing progress or blocker, not private reasoning'}},
        'required':['steps','explanation']}}}


def normalize_plan(steps, explanation=''):
    if not isinstance(steps,list) or not 1<=len(steps)<=10:
        raise ValueError('Provide 1–10 task steps')
    if not isinstance(explanation,str) or len(explanation)>1000:
        raise ValueError('Keep the progress explanation under 1,000 characters')
    result=[];seen=set()
    for entry in steps:
        if not isinstance(entry,dict):raise ValueError('Each step must have step and status fields')
        title=entry.get('step');status=entry.get('status')
        if not isinstance(title,str) or not title.strip() or len(title)>180:
            raise ValueError('Each step needs a nonempty title of at most 180 characters')
        if status not in ('pending','in_progress','completed','blocked'):
            raise ValueError('Step status must be pending, in_progress, completed or blocked')
        title=title.strip()
        if title.casefold() in seen:raise ValueError('Step titles must be distinct')
        seen.add(title.casefold());result.append({'step':title,'status':status})
    if sum(s['status']=='in_progress' for s in result)>1:
        raise ValueError('Keep only one step in progress at a time')
    return {'steps':result,'explanation':explanation.strip()}


def previous_plan(history):
    for message in reversed(history):
        if message.get('role')=='tool' and message.get('tool_name')=='update_plan':
            try:
                data=json.loads(message.get('content',''))
                return normalize_plan(data['steps'],data.get('explanation',''))
            except (ValueError,KeyError,TypeError):continue
    return None


def _validate_tool_argument(value, schema):
    """Validate a native tool argument against the schema shown to the model."""
    kind=schema.get('type')
    if isinstance(kind,list):
        for allowed in kind:
            try:
                _validate_tool_argument(value,dict(schema,type=allowed))
                break
            except ValueError:
                continue
        else:raise ValueError('Argument does not match any allowed type')
        return
    if kind=='object':
        if not isinstance(value,dict):raise ValueError('Expected object argument')
        properties=schema.get('properties',{})
        if any(key not in value for key in schema.get('required',[])):
            raise ValueError('Missing required argument')
        if schema.get('additionalProperties') is not True and any(key not in properties for key in value):
            raise ValueError('Unknown argument')
        for key,item in value.items():
            if key in properties:_validate_tool_argument(item,properties[key])
    elif kind=='array':
        if not isinstance(value,list):raise ValueError('Expected array argument')
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',float('inf')):
            raise ValueError('Array length outside schema')
        for item in value:_validate_tool_argument(item,schema.get('items',{}))
    elif kind=='string':
        if not isinstance(value,str):raise ValueError('Expected string argument')
        if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',float('inf')):
            raise ValueError('String length outside schema')
    elif kind=='boolean':
        if not isinstance(value,bool):raise ValueError('Expected boolean argument')
    elif kind in ('integer','number'):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or (kind=='integer' and not isinstance(value,int)):
            raise ValueError('Expected numeric argument')
        if not math.isfinite(value):raise ValueError('Numeric arguments must be finite')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Argument outside enum')


def validate_calls(calls, enabled_tools=None):
    """Validate a complete native batch before *any* tool runs."""
    if not isinstance(calls,list) or len(calls)>16:
        raise ValueError('Return at most 16 structured tool calls per batch')
    schemas=({tool['function']['name']:tool['function'].get('parameters',{}) for tool in enabled_tools}
             if enabled_tools is not None else None)
    result=[]
    for call in calls:
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):
            raise ValueError('Each tool call needs a function object')
        fn=call['function'];name=fn.get('name');args=fn.get('arguments',{})
        if not isinstance(name,str) or not name or len(name)>80:
            raise ValueError('Each tool call needs a valid function name')
        if isinstance(args,str):args=json.loads(args)
        if not isinstance(args,dict):raise ValueError('Tool arguments must be a JSON object')
        if schemas is not None:
            if name not in schemas:
                raise ValueError('Function is not enabled: '+name+'. Enable a tool set in a separate response first.')
            try:_validate_tool_argument(args,schemas[name])
            except ValueError as exc:raise ValueError(f'Invalid {name} arguments: {exc}') from exc
        item=dict(call);item['function']={'name':name,'arguments':args};result.append(item)
    return result


class ToolCatalog:
    """Load only pre-authorized tool schemas. Enabling a set never executes it."""
    def __init__(self, packs, blocked=None):
        self.packs=packs;self.blocked=blocked or {}

    def describe(self):
        return {'available':{name:[t['function']['name'] for t in tools] for name,tools in self.packs.items()},
                'unavailable':self.blocked}

    def enable(self, group, active):
        if not isinstance(group,str):raise ValueError('Tool group must be a string')
        if not group:return json.dumps(self.describe())
        if group not in self.packs:
            raise PermissionError(self.blocked.get(group,'Unknown tool group. Use enable_tools with an empty group to list available sets.'))
        known={t['function']['name'] for t in active};added=[]
        for tool in self.packs[group]:
            name=tool['function']['name']
            if name not in known:active.append(tool);known.add(name);added.append(name)
        return json.dumps({'group':group,'added':added,'enabled':[t['function']['name'] for t in self.packs[group]],
                           'note':'Tools enabled for this turn; no operation has been executed. Use them only for the current user request.'})
