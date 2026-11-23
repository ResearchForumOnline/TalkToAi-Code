"""Select labelled passages from the full text of a user-selected document."""
import gzip
import json
import math
import os
import re
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

MAX_EXTRACTED = 3_000_000
PASSAGE_SIZE = 1150
_STOP = set("a an the and or is are as at by for from in into of on to with this that its it their they be was were do does did what who when where which how why tell me give show please report filing form document company annual attached information about according summarize summarise summary review explain".split())
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_PAGE = re.compile(r"\b(?:pages?|p{1,2}\.)\s*(\d{1,3})(?:\s*(?:[–-]|to|through)\s*(\d{1,3}))?", re.I)

class VisibleHTML(HTMLParser):
    """Keep visible text and table cell boundaries in EDGAR and ordinary HTML."""
    blocks = {'article','section','p','div','br','hr','tr','li','h1','h2','h3','h4','h5','h6'}
    hidden = {'script','style','head','noscript','ix:header','ix:hidden','template'}
    void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_tag = ''
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.hidden_tag:
            if tag == self.hidden_tag:
                self.depth += 1
            return
        attrs = dict(attrs)
        if tag not in self.void and (tag in self.hidden or 'hidden' in attrs or
                re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',attrs.get('style') or '',re.I)):
            self.hidden_tag, self.depth = tag, 1
        elif tag in self.blocks:
            self.parts.append('\n')
        elif tag in {'td','th'}:
            self.parts.append(' | ')

    def handle_endtag(self, tag):
        if self.hidden_tag:
            if tag == self.hidden_tag:
                self.depth -= 1
                if not self.depth:
                    self.hidden_tag = ''
        elif tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden_tag:
            self.parts.append(data)

    def visible(self):
        return '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())

def passages(text, page=None):
    text = text.replace('\x00','').strip()
    start = 0
    while start < len(text):
        end = min(len(text), start + PASSAGE_SIZE)
        if end < len(text):
            cut = max(text.rfind('\n',start+PASSAGE_SIZE//2,end), text.rfind(' ',start+PASSAGE_SIZE//2,end))
            if cut > start:
                end = cut
        yield {'page':page, 'text':text[start:end]}
        if end == len(text):
            break
        start = max(start+1, end-90)

def _tokens(text):
    return [w.casefold() for w in _WORD.findall(text) if w.casefold() not in _STOP]

def _even(indices, count):
    if len(indices) <= count:
        return list(indices)
    if count <= 1:
        return [indices[0]]
    return [indices[round(i*(len(indices)-1)/(count-1))] for i in range(count)]

def select_passages(entries, query, allowance):
    """Rank over every passage; keep document order and space for distant evidence."""
    if not entries or allowance <= 0:
        return []
    n = len(entries)
    indices = list(range(n))
    page_match = _PAGE.search(query)
    if page_match and any(e['page'] is not None for e in entries):
        first, last = int(page_match[1]), int(page_match[2] or page_match[1])
        indices = [i for i in indices if entries[i]['page'] is not None and first <= entries[i]['page'] <= last]
    elif re.search(r'\b(?:back|second|latter) half\b',query,re.I):
        indices = indices[n//2:]
    elif re.search(r'\bfirst half\b',query,re.I):
        indices = indices[:(n+1)//2]
    if not indices:
        return []
    count = min(len(indices), max(1, allowance // (PASSAGE_SIZE+100)))
    # Scores use document frequency so a rare note or officer's title outranks boilerplate.
    terms = set(_tokens(query))
    item_query = re.search(r'\bitem\s+(\d{1,2}[a-z]?)\b',query,re.I)
    counts = {i:Counter(t for t in _tokens(entries[i]['text']) if t in terms) for i in indices} if terms else {}
    df = Counter(t for words in counts.values() for t in words)
    scores = {}
    for i in indices:
        words = counts.get(i, {})
        score = sum(math.log1p((len(indices)-df[t]+0.5)/(df[t]+0.5))*min(freq,3) for t,freq in words.items())
        if item_query and re.search(r'\bitem\s+'+re.escape(item_query[1])+r'\b',entries[i]['text'],re.I):
            score += 9
        if score:
            scores[i] = score
    if scores:
        ranked = sorted(scores,key=lambda i:(-scores[i],i))
        # Overview requests span the document even when the query contains keywords.
        overview = bool(re.search(r'\b(?:overview|throughout|summari[sz]e|summary|entire|whole|across|front and back)\b',query,re.I))
        top = count//2 if overview and count>=3 else count
        selected = ranked[:max(1,top)]
        for i in _even(indices,count):
            if i not in selected and len(selected)<count:
                selected.append(i)
    else:
        selected = _even(indices,count)
    per = max(1, (allowance // max(1,len(selected)))-65)
    result = []
    for i in sorted(selected):
        snippet = entries[i]['text']
        if len(snippet)>per:
            # Match the user's terms even on small-context models.
            positions = [(df.get(t,len(indices)),m.start()) for m in _WORD.finditer(snippet)
                         if (t:=m[0].casefold()) in terms]
            pos = min(positions)[1] if positions else 0
            offset = min(len(snippet)-per,max(0,pos-per//3))
            snippet = snippet[offset:offset+per]
        label = f"PDF p. {entries[i]['page']}, passage {i+1}" if entries[i]['page'] is not None else f"passage {i+1}"
        result.append((label,snippet))
    return result

def save_document(folder, item):
    """Keep a single immutable local index. History stores only its digest and name."""
    ref = {k:item[k] for k in ('name','sha256','kind','bytes','pages','text_pages')}
    key = ref['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}',key):
        raise ValueError('Invalid document fingerprint.')
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    path = folder / (key+'.json.gz')
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as tmp:
            temporary = Path(tmp.name)
        try:
            with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=3) as stream:
                json.dump(item['passages'],stream,ensure_ascii=False)
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
    return ref

def load_documents(folder, refs):
    items, missing = [], []
    for ref in refs[:5]:
        if not isinstance(ref,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(ref.get('sha256',''))):
            continue
        name = str(ref.get('name','document'))[:180]
        try:
            with gzip.open(Path(folder)/(ref['sha256']+'.json.gz'),'rt',encoding='utf-8') as stream:
                raw = stream.read(MAX_EXTRACTED*3+1)
            if len(raw)>MAX_EXTRACTED*3:
                raise ValueError('index too large')
            entries = json.loads(raw)
            if not isinstance(entries,list) or not entries:
                raise ValueError('no passages')
            items.append(dict(ref,passages=entries))
        except (OSError, EOFError, ValueError):
            missing.append(name)
    return items, missing

def last_document_refs(messages):
    for item in reversed(messages):
        if item.get('role')=='user' and item.get('document_refs'):
            return item['document_refs']
    return []

def model_excerpt_budget(context_tokens, question):
    # Reserve room for template, the rest of the transcript and a useful answer.
    return min(14_000,max(350,(max(2048,int(context_tokens))-min(1536,max(2048,int(context_tokens))//2)-530)*2-len(question.encode('utf-8'))))
