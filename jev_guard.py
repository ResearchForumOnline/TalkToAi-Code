"""Local, dependency-free Jev-inspired judgment checks for TalkToAi Code.

This is deliberately advisory: it never edits files, executes commands, or
pretends that an empty report means a change is approved.
"""
import re
import subprocess
from pathlib import Path

def _git(root, *args):
    p=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,timeout=15)
    return p.stdout, p.stderr, p.returncode

def check_changes(root, task=''):
    out,err,code=_git(root,'diff','--unified=3','HEAD')
    if code!=0:return {'findings':[],'notChecked':['Git diff unavailable: '+(err.strip() or 'not a repository')]}
    findings=[]; lower=out.lower()
    patterns=[('test weakened','test.skip','A test was skipped.'),('test weakened','pytest.skip','A test was skipped.'),('assertion removed','-assert ','An assertion appears to have been removed.'),('credential risk','+.*api[_-]?key|+.*password|+.*secret','A possible secret-bearing addition needs review.')]
    for kind,pat,note in patterns:
        if re.search(pat,out,re.I|re.M):findings.append({'kind':kind,'note':note})
    if task and out and not any(word in lower for word in re.findall(r'[a-zA-Z]{4,}',task.lower())[:8]):
        findings.append({'kind':'scope','note':'The diff may not contain obvious terms from the requested task; inspect for unrelated changes.'})
    return {'findings':findings,'changed_bytes':len(out),'notChecked':[] if out else ['No working-tree diff was found. This is not an approval.']}

def triage_failures(text):
    findings=[]
    for line in text.splitlines():
        if re.search(r'error|failed|failure|exception|traceback',line,re.I):
            kind='environment' if re.search(r'not found|missing|module|dependency|permission|timeout',line,re.I) else 'test-or-code'
            findings.append({'kind':kind,'line':line.strip()[:500]})
    return {'findings':findings[:100],'notChecked':[] if findings else ['No failure-like lines recognized; inspect the complete log.']}
