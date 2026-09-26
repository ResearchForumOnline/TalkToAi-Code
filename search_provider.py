"""Optional user-key Serper search. Keys use the existing Windows provider vault."""
import json
import urllib.error
import urllib.request

from providers import ProviderProfile, api_key, forget_api_key, store_api_key

SERPER_URL = 'https://google.serper.dev/search'


def _profile():
    return ProviderProfile('Serper search', 'https://google.serper.dev', 'search', 'SERPER_API_KEY')


def configured():
    return bool(api_key(_profile()))


def save_key(key):
    store_api_key(_profile(), key, remember=True)


def forget_key():
    forget_api_key(_profile())


def search(query):
    key=api_key(_profile())
    if not key:
        raise ValueError('Add your Serper API key in Settings or set SERPER_API_KEY.')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request=urllib.request.Request(SERPER_URL, data=json.dumps({'q':query,'num':10}).encode('utf-8'),
        headers={'X-API-KEY':key,'Content-Type':'application/json','User-Agent':'TalkToAi-Code/0.4'},method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw=response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Serper HTTP '+str(exc.code)+'; check the key, account credits or rate limit.') from None
    if len(raw)>1_000_000:
        raise ValueError('Serper response exceeds the size limit.')
    data=json.loads(raw)
    if not isinstance(data,dict):
        raise ValueError('Unexpected Serper response.')
    results=[]
    for item in data.get('organic',[])[:20]:
        if not isinstance(item,dict):continue
        url=item.get('link','')
        if not isinstance(url,str) or not url.startswith(('https://','http://')):continue
        results.append({'title':str(item.get('title',''))[:200], 'url':url,
                        'snippet':str(item.get('snippet',''))[:600]})
    if not results:
        raise ValueError('Serper returned no organic source links.')
    return results
