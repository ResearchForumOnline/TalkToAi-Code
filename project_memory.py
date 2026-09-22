"""Small, editable project notes shared by tasks in the same workspace."""
from pathlib import Path

LIMIT=12000
def memory_path(project):
    root=Path(project).resolve()
    target=root/'.talktoai-code'/'PROJECT_MEMORY.md'
    if not target.resolve().is_relative_to(root):
        raise ValueError('Project memory must stay inside the selected project')
    return target

def read_memory(project):
    path=memory_path(project)
    try:
        with path.open('r',encoding='utf-8') as stream:return stream.read(LIMIT)
    except FileNotFoundError:return ''

def save_memory(project,text,previous=None):
    if len(text)>LIMIT:raise ValueError(f'Keep project memory under {LIMIT:,} characters')
    path=memory_path(project)
    if previous is not None and read_memory(project)!=previous:
        raise ValueError('Notes changed since opening. Reopen Project memory to keep the latest edits.')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(path)

TEMPLATE='''# Project memory

## What we are building

## Decisions and conventions

## Build and test commands

## Known issues and next steps

'''
