"""Per-request retention for the local Ollama service."""

from datetime import datetime, timezone
import math
import re
from urllib.parse import urlsplit


DEFAULT_KEEP_ALIVE = '60m'
KEEP_ALIVE_CHOICES = ('5m', '15m', DEFAULT_KEEP_ALIVE, '4h', '24h', 'until_released')
_SECONDS = {'5m': 300, '15m': 900, '60m': 3600, '4h': 14400, '24h': 86400}


def normalize_keep_alive(value):
    return value if isinstance(value, str) and value in KEEP_ALIVE_CHOICES else DEFAULT_KEEP_ALIVE


def local_port(url):
    """Use the native loopback route, apart from the reserved server tunnel."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
                and parsed.port and parsed.port != 11435 and not parsed.username and not parsed.password):
            return parsed.port
    except (TypeError, ValueError):
        pass
    return None


def keep_alive_for(url, setting=None, *, remote_default='15m'):
    if url is not None and local_port(url) is None:
        return remote_default
    requested = normalize_keep_alive(setting)
    return -1 if requested == 'until_released' else requested


def diagnostic_keep_alive(setting, resident, *, now=None):
    """Keep a diagnostic generation from shortening an existing local lease."""
    value = keep_alive_for(None, setting)
    if resident is None or value == -1:
        return value
    expiry = resident.get('expires_at')
    try:
        # Ollama sends RFC3339 timestamps with up to nanosecond precision.
        expiry = re.sub(r'(\.\d{6})\d+', r'\1', expiry).replace('Z', '+00:00')
        expiry = datetime.fromisoformat(expiry)
        if expiry.tzinfo is None or expiry.year < 1970:
            raise ValueError
        remaining = math.ceil((expiry - (now or datetime.now(timezone.utc))).total_seconds())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Ollama has not reported when this loaded model expires. Wait for its active work to finish, then retry the check.') from exc
    # Ollama encodes indefinite retention as an expiry centuries in the future.
    if remaining >= 365 * 86400:
        return -1
    return remaining if remaining > _SECONDS[value] else value
