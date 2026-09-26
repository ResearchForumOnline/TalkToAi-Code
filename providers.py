"""User-owned, OpenAI-compatible provider profiles without stored API keys."""
from __future__ import annotations

import json
import os
import re
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit


class ProviderProfile:
    def __init__(self, label: str, base_url: str, model: str, api_key_env: str = "", kind='compatible', engine='groq', max_output_tokens=2048):
        if kind not in ('compatible','zerothink'):raise ValueError('Unknown provider kind')
        self.kind=kind;self.engine=engine
        self.label = str(label or "Provider").strip()[:80]
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.model = str(model or "").strip()[:200]
        self.api_key_env = str(api_key_env or "").strip()[:80]
        parsed = urlsplit(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Provider URL must not contain credentials, query parameters or fragments.')
        if kind=='zerothink' and (self.base_url!='https://zerothink.talktoai.org' or engine not in ('groq','nvidia','openai','xai','gemini')):raise ValueError('Invalid ZeroThink account route')
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Provider URL must be an http:// or https:// OpenAI-compatible endpoint.")
        if not self.model:
            raise ValueError("Provider model cannot be empty.")
        if self.api_key_env and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',self.api_key_env):
            raise ValueError("API key reference must be an environment variable name, not a key value.")
        self.max_output_tokens=int(max_output_tokens)
        if not 256<=self.max_output_tokens<=8192:raise ValueError('Output-token limit must be between 256 and 8192.')
        if parsed.hostname=='api.openai.com' and self.base_url!='https://api.openai.com/v1':
            raise ValueError('Use https://api.openai.com/v1 for the direct OpenAI API.')
        if parsed.hostname=='api.groq.com' and self.base_url!='https://api.groq.com/openai/v1':
            raise ValueError('Use https://api.groq.com/openai/v1 for the direct Groq API.')

    @property
    def is_openai(self):return self.kind=='compatible' and self.base_url=='https://api.openai.com/v1'

    @property
    def is_groq(self):return self.kind=='compatible' and self.base_url=='https://api.groq.com/openai/v1'

    def as_dict(self):
        return {"label": self.label, "base_url": self.base_url, "model": self.model, "api_key_env": self.api_key_env, 'kind':self.kind,'engine':self.engine,'max_output_tokens':self.max_output_tokens}


def load_profiles(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [ProviderProfile(x.get("label", ""), x["base_url"], x["model"], x.get("api_key_env", ""),x.get('kind','compatible'),x.get('engine','groq'),x.get('max_output_tokens',2048)) for x in data if isinstance(x, dict)]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_profiles(path: Path, profiles):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([p.as_dict() for p in profiles], indent=2), encoding="utf-8")
    tmp.replace(path)


_SESSION_KEYS={}


def key_identity(profile):
    return hashlib.sha256((profile.base_url+'\0'+profile.api_key_env).encode()).hexdigest()


def key_path(profile):
    return Path(os.environ.get('LOCALAPPDATA') or str(Path.home()))/'TalkToAiCode'/'provider-keys'/(key_identity(profile)+'.dpapi')


def store_api_key(profile, key, remember=False):
    if not isinstance(key,str) or not key.strip() or len(key)>4096 or any(c.isspace() for c in key.strip()):
        raise ValueError('Enter a nonempty API key without whitespace.')
    key=key.strip()
    parsed=urlsplit(profile.base_url)
    if parsed.scheme!='https' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('Use HTTPS before storing a key for a remote provider.')
    if remember:
        if os.name!='nt':raise ValueError('Remembered keys use Windows encryption. Use a session key or environment variable on this platform.')
        import win32crypt
        encrypted=win32crypt.CryptProtectData(key.encode(),'TalkToAi Code provider',None,None,None,0)
        path=key_path(profile);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp');temporary.write_bytes(encrypted);temporary.replace(path)
    _SESSION_KEYS[key_identity(profile)]=key


def forget_api_key(profile):
    _SESSION_KEYS.pop(key_identity(profile),None)
    key_path(profile).unlink(missing_ok=True)


def api_key(profile):
    key=_SESSION_KEYS.get(key_identity(profile),'') or os.environ.get(profile.api_key_env,'')
    path=key_path(profile)
    if not key and path.is_file():
        try:
            import win32crypt
            key=win32crypt.CryptUnprotectData(path.read_bytes(),None,None,None,0)[1].decode()
        except Exception:raise ValueError('Saved provider key could not be decrypted. Enter it again in API providers.') from None
    if profile.is_openai and not key:
        raise ValueError('OpenAI API key is missing. Enter one in API providers or set OPENAI_API_KEY, then restart the app.')
    if profile.is_groq and not key:
        raise ValueError('Groq API key is missing. Enter one in API providers or set GROQ_API_KEY, then restart the app.')
    if key and urlsplit(profile.base_url).scheme!='https' and urlsplit(profile.base_url).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('API keys require HTTPS for remote providers.')
    return key


def http_error(status):
    advice={400:'Check the model and its support for Chat Completions and function tools.',401:'Check your API key.',403:'Check account/model access.',404:'Check the endpoint and model name.',429:'Check provider quota, billing or rate limits.'}
    return f'Provider HTTP {status}. '+advice.get(status,'Check provider availability and settings. No automatic provider fallback was attempted.')


def list_models(profile):
    """Metadata only. No inference, workspace content, or redirect with credentials."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    key=api_key(profile)
    headers={'Authorization':'Bearer '+key} if key else {}
    request=urllib.request.Request(profile.base_url+'/models',headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=12) as response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Model listing exceeds the response limit.')
        data=json.loads(raw)
        return sorted({m['id'] for m in data.get('data',[]) if isinstance(m,dict) and isinstance(m.get('id'),str)})
    except urllib.error.HTTPError as exc:raise RuntimeError(http_error(exc.code)) from None


def configure_request(profile, request, options):
    """Keep compatible endpoints unchanged; use current first-party parameters."""
    if profile.is_openai:
        request['max_completion_tokens']=profile.max_output_tokens
        request['store']=False
        request['stream_options']={'include_usage':True}
        # Leave sampling defaults alone: some reasoning models reject temperature.
    elif profile.is_groq:
        request['max_completion_tokens']=profile.max_output_tokens
        request['temperature']=options.get('temperature',.1)
    else:
        request['max_tokens']=profile.max_output_tokens
        request['temperature']=options.get('temperature',.1)
    return request
