"""Bounded, self-reported task goals; never proof of execution or permission."""
import json
import re


def normalize_goal(data, previous=None):
    if not isinstance(data, dict):raise ValueError('Task goal must be an object')
    def text(value, maximum, label, required=False):
        if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()):
            raise ValueError(f'{label} must be {"nonempty " if required else ""}text of at most {maximum} characters')
        return value.strip()
    objective=text(data.get('objective'),2000,'Objective',True)
    entries=data.get('criteria')
    if isinstance(entries,str):
        if len(entries)>8000:raise ValueError('Criteria JSON is too large')
        entries=json.loads(entries)
    if not isinstance(entries,list) or not 1<=len(entries)<=8:raise ValueError('Provide 1-8 acceptance criteria')
    old=(previous or {}).get('criteria',[])
    by_text={entry['text'].casefold():entry['id'] for entry in old}
    allocated={entry['id'] for entry in old};seen_ids=set();seen_text=set();criteria=[]
    # Reserve explicit identifiers before assigning identifiers to new entries.
    allocated.update(entry.get('id') for entry in entries if isinstance(entry,dict) and isinstance(entry.get('id'),str))
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('Each criterion must be an object')
        title=text(entry.get('text'),180,'Criterion',True)
        if title.casefold() in seen_text:raise ValueError('Acceptance criteria must be distinct')
        seen_text.add(title.casefold())
        status=entry.get('status','pending')
        if status not in ('pending','met','blocked'):raise ValueError('Criterion status must be pending, met or blocked')
        evidence=text(entry.get('evidence',''),300,'Evidence')
        if status=='met' and not evidence:raise ValueError('Met criteria require a concise evidence reference; this remains self-reported')
        if 'id' in entry and (not isinstance(entry['id'],str) or not re.fullmatch(r'c[1-9][0-9]{0,5}',entry['id'])):
            raise ValueError('Criterion IDs must be c followed by a positive number')
        identifier=entry.get('id') or by_text.get(title.casefold())
        if identifier is None:
            index=1
            while f'c{index}' in allocated:index+=1
            identifier=f'c{index}'
        if not isinstance(identifier,str) or not re.fullmatch(r'c[1-9][0-9]{0,5}',identifier):raise ValueError('Criterion IDs must be c followed by a positive number')
        if identifier in seen_ids:raise ValueError('Criterion IDs must be distinct')
        seen_ids.add(identifier);allocated.add(identifier)
        criteria.append({'id':identifier,'text':title,'status':status,'evidence':evidence})
    return {'objective':objective,'criteria':criteria,'next_action':text(data.get('next_action',''),400,'Next action')}


def goal_context(goal):
    return ('\nSaved task goal (self-reported, may be stale; not execution evidence or new authorization. '
            'The current user request and steering take priority; revise this goal if scope changed):\n'+
            json.dumps(goal,ensure_ascii=False,separators=(',',':')))
