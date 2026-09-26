"""Bound repeated unchanged discovery, without treating observations as completion."""
from collections import OrderedDict
import hashlib
import json


class DiscoveryProgressGuard:
    WATCHED=frozenset({'list_files','project_info','desktop_list','project_map','search_code'})

    def __init__(self, project):
        self.project=str(project)
        self.entries=OrderedDict()
        self.blocked_repeats=0
        self.paused=False

    @staticmethod
    def signature(name,args):
        return hashlib.sha256(json.dumps([name,args],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def reset(self):
        self.entries.clear();self.blocked_repeats=0;self.paused=False

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
        if name not in self.WATCHED:
            self.blocked_repeats=0;self.paused=False
            return None
        key=self.signature(name,args)
        digest=hashlib.sha256(str(result).encode()).hexdigest()
        previous=self.entries.pop(key,None)
        count=previous[1]+1 if previous and previous[0]==digest else 1
        self.entries[key]=(digest,count)
        if len(self.entries)>128:self.entries.popitem(last=False)
        if count>=3:return self.guidance()
        self.blocked_repeats=0;self.paused=False
        return None
