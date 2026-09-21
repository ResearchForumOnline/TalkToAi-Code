"""Bounded source navigation, inspired by public repository-map workflows."""
import ast
from pathlib import Path
import re
import subprocess
import os

SOURCE_EXTENSIONS={'.py','.gd','.cs','.js','.ts','.tsx','.jsx','.cpp','.c','.h','.hpp','.rs','.go','.java','.lua','.md','.toml','.json','.godot','.tscn','.yaml','.yml','.txt','.shader','.gdshader'}

def source_text(tools,relative):
    path=tools.path(relative)
    if path.suffix.lower() not in SOURCE_EXTENSIONS or path.stat().st_size>200000:return None
    try:return path.read_text(encoding='utf-8')
    except (UnicodeError,OSError):return None

def search_code(tools,query):
    if not query or len(query)>300:raise ValueError('Use a nonempty literal search of at most 300 characters.')
    hits=[];scanned=0
    for relative in tools.files():
        if tools.cancel.is_set():raise InterruptedError('Stopped.')
        try:text=source_text(tools,relative)
        except (ValueError,PermissionError,OSError):continue
        if text is None:continue
        scanned+=len(text)
        for number,line in enumerate(text.splitlines(),1):
            if query.casefold() in line.casefold():
                hits.append(f'{relative}:{number}: {line.strip()[:240]}')
                if len(hits)>=60:return '\n'.join(hits)+'\n[60-hit limit; narrow the query.]'
        if scanned>8000000:break
    return '\n'.join(hits) if hits else 'No matches within the bounded source scan.'

def project_map(tools,query=''):
    words=re.findall(r'[a-zA-Z_]{3,}',query.lower())[:12]
    files=tools.files()
    files.sort(key=lambda p:(-sum(w in p.lower() for w in words),len(Path(p).parts),p))
    output=[];size=0;seen=0
    for relative in files:
        if tools.cancel.is_set():raise InterruptedError('Stopped.')
        try:text=source_text(tools,relative)
        except (ValueError,PermissionError,OSError):continue
        if text is None:continue
        seen+=1;symbols=[]
        if relative.endswith('.py'):
            try:
                tree=ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                        symbols.append(f'  {node.lineno}: {text.splitlines()[node.lineno-1].strip()[:140]}')
            except SyntaxError:pass
        else:
            for number,line in enumerate(text.splitlines(),1):
                if re.match(r'\s*(?:class(?:_name)? |(?:async )?(?:func|function) |(?:export )?(?:class|function|const) |(?:public|private|protected|internal) .*(?:\(|class ))',line):
                    symbols.append(f'  {number}: {line.strip()[:140]}')
        entry=relative+'\n'+'\n'.join(symbols[:10])
        output.append(entry);size+=len(entry)
        if size>=6500 or seen>=60:break
    return '\n\n'.join(output)+'\n[Bounded overview; use search_code/read_file for exact code.]'

def git_changes(tools):
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    base=['git','-c','core.fsmonitor=false','--no-pager','-C',str(tools.root)]
    try:
        check=subprocess.run(base+['rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=10,creationflags=flags)
    except FileNotFoundError:return 'Git is not installed.'
    if check.returncode:return 'Selected project is not inside a Git repository.'
    commands=[['status','--short','--','.'],['diff','--no-ext-diff','--no-textconv','--stat','--','.'],['diff','--cached','--no-ext-diff','--no-textconv','--stat','--','.']]
    output=[]
    for args in commands:
        result=subprocess.run(base+args,capture_output=True,text=True,timeout=15,creationflags=flags)
        output.append('git '+' '.join(args)+'\n'+(result.stdout+result.stderr)[:10000])
    return '\n'.join(output)
