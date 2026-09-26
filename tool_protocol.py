"""Strict adapter for complete Qwen tool output; never parse user/tool messages."""
import json
import re
import uuid
import math

MARKERS=('<tool_call', '</tool_call', '<function=', '</function', '<parameter=', '</parameter')


def has_tool_markup(text):
    return isinstance(text,str) and any(marker in text for marker in MARKERS)


class VisibleTextStream:
    """Hold partial protocol prefixes; stop displaying at the first tool tag."""
    def __init__(self):self.pending='';self.hidden=False
    def feed(self,delta):
        if self.hidden:return ''
        self.pending+=delta
        positions=[self.pending.find(marker) for marker in MARKERS if marker in self.pending]
        if positions:
            index=min(positions);visible=self.pending[:index]
            self.pending='';self.hidden=True
            return visible
        hold=0
        for marker in MARKERS:
            for count in range(1,len(marker)):
                if self.pending.endswith(marker[:count]):hold=max(hold,count)
        if hold:
            visible=self.pending[:-hold];self.pending=self.pending[-hold:]
        else:visible=self.pending;self.pending=''
        return visible
    def finish(self):
        visible='' if self.hidden or any(marker.startswith(self.pending) for marker in MARKERS) else self.pending
        self.pending=''
        return visible


def _validate(value,schema):
    kind=schema.get('type')
    if kind=='string' and not isinstance(value,str):raise ValueError('Expected string argument')
    if kind=='object':
        if not isinstance(value,dict):raise ValueError('Expected object argument')
        properties=schema.get('properties',{})
        if any(key not in value for key in schema.get('required',[])):raise ValueError('Missing required argument')
        if schema.get('additionalProperties') is not True and any(key not in properties for key in value):raise ValueError('Unknown argument')
        for key,item in value.items():
            if key in properties:_validate(item,properties[key])
    if kind=='array':
        if not isinstance(value,list):raise ValueError('Expected array argument')
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',10000):raise ValueError('Array length outside schema')
        for item in value:_validate(item,schema.get('items',{}))
    if kind=='boolean' and not isinstance(value,bool):raise ValueError('Expected boolean argument')
    if kind in ('integer','number') and (isinstance(value,bool) or not isinstance(value,(int,float)) or (kind=='integer' and not isinstance(value,int))):raise ValueError('Expected numeric argument')
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('Numeric arguments must be finite')
    if isinstance(value,str) and (len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',65536)):raise ValueError('String length outside schema')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('Argument outside enum')


def parse_qwen_tool_calls(content,enabled_tools,truncated=False):
    """All-or-nothing parsing of a completed assistant-only Qwen XML batch.

    No prose, fences, partial tags, duplicate parameters, unknown functions or
    extra arguments are tolerated. Native tools remain the preferred route.
    """
    if truncated:raise ValueError('Truncated tool output cannot execute')
    if not isinstance(content,str) or len(content)>65536:raise ValueError('Tool output is too large')
    remaining=content.strip();calls=[]
    available={tool['function']['name']:tool['function'].get('parameters',{}) for tool in enabled_tools}
    while remaining:
        if len(calls)>=16:raise ValueError('Too many tool calls')
        wrapped=remaining.startswith('<tool_call>')
        if wrapped:
            closing=remaining.find('</tool_call>')
            if closing<0:raise ValueError('Unclosed tool_call tag')
            block=remaining[len('<tool_call>'):closing].strip()
            remaining=remaining[closing+len('</tool_call>'):].strip()
        else:
            closing=remaining.find('</function>')
            if not remaining.startswith('<function=') or closing<0:raise ValueError('Expected a complete function block without prose')
            block=remaining[:closing+len('</function>')]
            remaining=remaining[closing+len('</function>'):].strip()
            if remaining.startswith('</tool_call>'):
                remaining=remaining[len('</tool_call>'):].strip()
        match=re.fullmatch(r'<function=([A-Za-z_][A-Za-z0-9_]*)>\s*(.*?)\s*</function>',block,re.S)
        if not match:raise ValueError('Malformed function block')
        name,body=match.groups()
        if name not in available:raise ValueError('Function is not enabled: '+name)
        parameters=available[name];properties=parameters.get('properties',{});args={}
        body=body.strip()
        while body:
            parameter=re.match(r'<parameter=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</parameter>',body,re.S)
            if not parameter:raise ValueError('Malformed or unclosed parameter')
            key,raw=parameter.groups()
            if key in args:raise ValueError('Duplicate parameter: '+key)
            if key not in properties:raise ValueError('Unknown parameter: '+key)
            if has_tool_markup(raw):raise ValueError('Nested tool markup is ambiguous')
            # Remove one framing line break on each side, preserving literal
            # indentation and additional blank lines inside string arguments.
            if raw.startswith('\r\n'):raw=raw[2:]
            elif raw.startswith('\n'):raw=raw[1:]
            if raw.endswith('\r\n'):raw=raw[:-2]
            elif raw.endswith('\n'):raw=raw[:-1]
            if properties[key].get('type')=='string':value=raw
            else:
                try:value=json.loads(raw)
                except (ValueError,TypeError) as exc:raise ValueError('Non-string argument must be valid JSON') from exc
            args[key]=value;body=body[parameter.end():].strip()
        _validate(args,parameters)
        calls.append({'id':'qwen_'+uuid.uuid4().hex,'function':{'name':name,'arguments':args}})
    if not calls:raise ValueError('No complete tool calls found')
    return calls
