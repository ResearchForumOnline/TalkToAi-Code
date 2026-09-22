"""Bounded agent workflow helpers. No model calls or permission changes here."""
import json
import re


def check_evidence(result):
    """Conservative status from the check runner, not from model prose."""
    codes=[int(c) for c in re.findall(r'^Exit (-?\d+)\s*$',result,re.M)]
    if any(c!=0 for c in codes) or re.search(r'^(?:SCRIPT ERROR|ERROR):',result,re.M):
        return {'status':'failed','summary':'A detected check reported a failure. Inspect the tool output.'}
    if not codes or 'UNVERIFIED:' in result or 'Ran 0 tests' in result:
        return {'status':'unverified','summary':'No passing check established. Inspect the output for missing checks, zero tests or errors.'}
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


def validate_calls(calls):
    """Validate the entire batch before *any* tool runs; do not repair guesses."""
    if not isinstance(calls,list) or len(calls)>16:
        raise ValueError('Return at most 16 structured tool calls per batch')
    result=[]
    for call in calls:
        if not isinstance(call,dict) or not isinstance(call.get('function'),dict):
            raise ValueError('Each tool call needs a function object')
        fn=call['function'];name=fn.get('name');args=fn.get('arguments',{})
        if not isinstance(name,str) or not name or len(name)>80:
            raise ValueError('Each tool call needs a valid function name')
        if isinstance(args,str):args=json.loads(args)
        if not isinstance(args,dict):raise ValueError('Tool arguments must be a JSON object')
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
