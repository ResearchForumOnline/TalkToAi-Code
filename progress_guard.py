"""Bound repeated unchanged discovery, without treating observations as completion."""
from collections import OrderedDict
import hashlib
import json
import os
import re


def source_excerpt(source, number):
    lines = source.splitlines()
    if not 1 <= number <= len(lines):
        raise ValueError(f'The requested line is outside this file ({len(lines)} lines).')
    excerpt = []
    for i in range(max(1, number - 4), min(len(lines), number + 4) + 1):
        line = lines[i - 1]
        excerpt.append(f'{i}: {line[:600]}' + (' …' if len(line) > 600 else ''))
    return '\n'.join(excerpt)


class DiscoveryProgressGuard:
    WATCHED=frozenset({'list_files','project_info','desktop_list','project_map','search_code'})

    def __init__(self, project):
        self.project=str(project)
        self.entries=OrderedDict()
        self.blocked_repeats=0
        self.paused=False
        self.repeated_source=False
        self.traceback_lines={}

    def _path_key(self, path):
        return os.path.normcase(os.path.normpath(os.path.join(self.project, path)))

    def record_command(self, result, failed):
        self.traceback_lines.clear()
        if failed:
            _, marker, traceback = result.rpartition('Traceback (most recent call last):')
            if marker:
                frames = re.findall(r'^\s*File "([^"\n]+)", line (\d{1,8})\b', traceback, re.M)
                for path, number in frames[-12:]:
                    self.traceback_lines[self._path_key(path)] = int(number)

    def traceback_excerpt(self, name, args, source):
        # Only use source the requested read actually returned. A filename printed
        # by a process never grants permission to open that file.
        if name != 'read_file' or args.get('line'):
            return ''
        number = self.traceback_lines.get(self._path_key(args['path']))
        if number:
            try:
                excerpt = source_excerpt(source, number)
            except ValueError:
                return ''
            return f'The last command traceback named line {number}. Current project source:\n' + excerpt
        return ''

    @staticmethod
    def signature(name,args):
        return hashlib.sha256(json.dumps([name,args],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def reset(self):
        self.entries.clear();self.blocked_repeats=0;self.paused=False;self.repeated_source=False

    def guidance(self):
        return ('[Agent progress guard] The same discovery request returned unchanged output three times. '+
                'Selected project: '+self.project+'. Use the observations already obtained: inspect a specific relevant '+
                'source file, choose a different focused search, or perform the next authorized task step. '+
                'Do not browse unrelated folders or repeat this unchanged discovery. If the project or requirement '+
                'is genuinely unresolved, report the precise blocker. This is not task completion.')

    def before(self,name,args):
        if name not in self.WATCHED:return None
        previous=self.entries.get(self.signature(name,args))
        if previous and previous[1]>=3:
            self.blocked_repeats+=1
            self.paused=self.blocked_repeats>=2
            return self.guidance()+' The redundant call was not executed.'
        return None

    def observe(self,name,args,result):
        self.repeated_source=False
        source_read = (name == 'read_file' and args.get('path')) or (name == 'read_project_files' and args.get('requests'))
        if name not in self.WATCHED and not source_read:
            self.blocked_repeats=0;self.paused=False
            return None
        key=self.signature(name,args)
        digest=hashlib.sha256(str(result).encode()).hexdigest()
        previous=self.entries.pop(key,None)
        count=previous[1]+1 if previous and previous[0]==digest else 1
        self.entries[key]=(digest,count)
        if len(self.entries)>128:self.entries.popitem(last=False)
        if source_read:
            # Always read the real file first. Another process or the user may have edited it.
            self.blocked_repeats=0;self.paused=False
            self.repeated_source=count>=4
            if count>=3:
                return ('This source was read again and matches the earlier result. Use the existing excerpt. '
                        'For a traceback, call read_file with the file path and its 1-based line number. '
                        'For the next section, use read_project_files with next_offset and expected_sha256. '
                        'If you have the failing line, apply the relevant repair and rerun the check.')
            return None
        if count>=3:return self.guidance()
        self.blocked_repeats=0;self.paused=False
        return None
