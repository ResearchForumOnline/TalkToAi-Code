"""Explicit provider failover before output; never replays a partially emitted turn."""
from __future__ import annotations
import datetime
import email.utils
import threading
import time
from providers import key_identity, http_error, key_ready

class ProviderHTTPError(RuntimeError):
    def __init__(self, status, retry_after=None):
        self.status=int(status); self.retry_after=retry_after
        super().__init__(http_error(self.status))

class ProviderPartialResponseError(RuntimeError):
    def __init__(self):
        super().__init__('Provider disconnected or interrupted a partial response. No alternate provider was called; retry this turn explicitly.')

def retry_delay(value, now=None):
    if value is None:return 60.0
    try:return max(1.0,min(86400.0,float(value)))
    except (TypeError,ValueError):
        try:
            stamp=email.utils.parsedate_to_datetime(str(value))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=datetime.timezone.utc)
            return max(1.0,min(86400.0,stamp.timestamp()-(time.time() if now is None else now)))
        except (TypeError,ValueError,OverflowError):return 60.0

def profile_id(profile):
    return key_identity(profile)+':'+profile.model

class ApiRouter:
    def __init__(self, clock=time.monotonic):
        self.clock=clock;self.cooldowns={};self.lock=threading.Lock()

    def remaining(self, profile):
        with self.lock:return max(0.0,self.cooldowns.get(profile.base_url,0)-self.clock())

    def stream(self, primary, profiles, payload, cancel, stream_fn, notify=None):
        """Selected primary always runs first unless cooling. Alternatives require consent."""
        requires_vision=any(m.get('images') or (isinstance(m.get('content'),list) and any(isinstance(part,dict) and part.get('type')=='image_url' for part in m['content'])) for m in payload.get('messages',[]) if isinstance(m,dict))
        if requires_vision and not primary.supports_vision:raise ValueError('Enable screenshot/vision support for the selected API model in the vault.')
        candidates=[primary]
        if primary.fallback_enabled:
            candidates += sorted((p for p in profiles if profile_id(p)!=profile_id(primary) and p.fallback_enabled and p.cost_tier in {'free','self_hosted'} and (not requires_vision or p.supports_vision) and (p.base_url!='https://openrouter.ai/api/v1' or p.cost_tier!='free' or p.model.endswith(':free'))),key=lambda p:p.priority)
        seen=set();last=None
        for profile in candidates:
            identity=profile_id(profile)
            if identity in seen:continue
            seen.add(identity)
            if cancel.is_set():raise InterruptedError('Task stopped.')
            if identity!=profile_id(primary) and not key_ready(profile):continue
            remaining=self.remaining(profile)
            if remaining:
                last=RuntimeError('Configured API is cooling down for '+str(int(remaining)+1)+' seconds.')
                continue
            yield {'_provider_route':{'label':profile.label,'model':profile.model,'fallback':identity!=profile_id(primary)}}
            emitted=False
            try:
                for event in stream_fn(profile,payload,cancel):
                    if cancel.is_set():raise InterruptedError('Task stopped.')
                    message=event.get('message') or {}
                    if message.get('content') or message.get('tool_calls') or event.get('done'):emitted=True
                    if event.get('done'):
                        event=dict(event);event['api_provider']=profile.label;event['api_model']=profile.model
                        event['api_usage_source']='provider_reported' if event.get('api_usage') else 'unavailable'
                    yield event
                return
            except ProviderPartialResponseError:raise
            except (ProviderHTTPError,OSError) as exc:
                if cancel.is_set():raise InterruptedError('Task stopped.') from None
                if emitted:raise ProviderPartialResponseError() from None
                if isinstance(exc,ProviderHTTPError) and exc.status not in {402,429,502,503,504}:raise
                delay=retry_delay(exc.retry_after) if isinstance(exc,ProviderHTTPError) else 15.0
                with self.lock:self.cooldowns[profile.base_url]=self.clock()+delay
                last=exc
                if not primary.fallback_enabled:raise
                reason=('quota' if exc.status==402 else 'rate_limit') if isinstance(exc,ProviderHTTPError) and exc.status in {402,429} else 'temporarily_unavailable'
                yield {'_provider_switch':{'label':profile.label,'reason':reason,'cooldown_seconds':int(delay)}}
                if notify:notify('API unavailable or limited; checking your enabled fallback profiles.')
        raise last or RuntimeError('No eligible API profiles are available. Add an enabled free or self-hosted fallback in the private vault.')

DEFAULT_ROUTER=ApiRouter()
