"""User-owned, OpenAI-compatible provider profiles without stored API keys."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit


class ProviderProfile:
    def __init__(self, label: str, base_url: str, model: str, api_key_env: str = "", kind='compatible', engine='groq'):
        if kind not in ('compatible','zerothink'):raise ValueError('Unknown provider kind')
        self.kind=kind;self.engine=engine
        self.label = str(label or "Provider").strip()[:80]
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.model = str(model or "").strip()[:200]
        self.api_key_env = str(api_key_env or "").strip()[:80]
        parsed = urlsplit(self.base_url)
        if kind=='zerothink' and (self.base_url!='https://zerothink.talktoai.org' or engine not in ('groq','nvidia','openai','xai','gemini')):raise ValueError('Invalid ZeroThink account route')
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Provider URL must be an http:// or https:// OpenAI-compatible endpoint.")
        if not self.model:
            raise ValueError("Provider model cannot be empty.")
        if self.api_key_env and not self.api_key_env.replace("_", "A").isalnum():
            raise ValueError("API key reference must be an environment variable name, not a key value.")

    def as_dict(self):
        return {"label": self.label, "base_url": self.base_url, "model": self.model, "api_key_env": self.api_key_env, 'kind':self.kind,'engine':self.engine}


def load_profiles(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [ProviderProfile(x.get("label", ""), x["base_url"], x["model"], x.get("api_key_env", ""),x.get('kind','compatible'),x.get('engine','groq')) for x in data if isinstance(x, dict)]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_profiles(path: Path, profiles):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([p.as_dict() for p in profiles], indent=2), encoding="utf-8")
    tmp.replace(path)
