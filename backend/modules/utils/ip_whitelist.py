import asyncio, logging, os
from functools import wraps
import aiohttp
from quart import jsonify, request

TRMNL_IPS_API  = 'https://trmnl.com/api/ips'
ACCESS_MODE    = os.getenv('ACCESS_MODE', 'whitelist_only').lower()
IP_REFRESH_HOURS = int(os.getenv('IP_REFRESH_HOURS', '24'))
LOCALHOST_IPS  = {'127.0.0.1', '::1'}

log = logging.getLogger(__name__)
_ips: set[str] = set(LOCALHOST_IPS)
_lock = asyncio.Lock()


async def _fetch_ips() -> set[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TRMNL_IPS_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                addrs = data.get('data', {})
                ips = set(addrs.get('ipv4', []) + addrs.get('ipv6', [])) | LOCALHOST_IPS
                log.info('Loaded %d TRMNL IPs', len(ips))
                return ips
    except Exception as exc:
        log.warning('Failed to fetch TRMNL IPs: %s', exc)
        return set()


async def _refresh_loop():
    while True:
        await asyncio.sleep(IP_REFRESH_HOURS * 3600)
        fresh = await _fetch_ips()
        if fresh:
            async with _lock:
                global _ips
                _ips = fresh


async def init_ip_whitelist():
    global _ips
    if ACCESS_MODE == 'open':
        log.info('ACCESS_MODE=open — IP whitelist disabled')
        return
    fresh = await _fetch_ips()
    if fresh:
        async with _lock:
            _ips = fresh
    asyncio.create_task(_refresh_loop())
    log.info('ACCESS_MODE=%s — IP refresh every %dh', ACCESS_MODE, IP_REFRESH_HOURS)


def _client_ip() -> str:
    for header in ('CF-Connecting-IP', 'X-Forwarded-For', 'X-Real-IP'):
        value = request.headers.get(header)
        if value:
            return value.split(',')[0].strip()
    return request.remote_addr


async def _is_trmnl_ip() -> bool:
    ip = _client_ip()
    async with _lock:
        return ip in _ips


async def check_access(redis=None, prefix: str = 'ratelimit') -> str | None:
    """
    Returns None if the request is allowed, or a denial reason:
      'blocked'      — ACCESS_MODE=whitelist_only and caller is not a TRMNL IP
      'rate_limited' — ACCESS_MODE=rate_limited and caller has exceeded their quota
    """
    if ACCESS_MODE == 'open':
        return None
    if await _is_trmnl_ip():
        return None
    if ACCESS_MODE == 'whitelist_only':
        log.warning('Blocked request from %s', _client_ip())
        return 'blocked'
    # rate_limited — Redis required; if unavailable, fails open (all public requests allowed)
    if not redis:
        log.warning(
            'ACCESS_MODE=rate_limited but Redis is unavailable — '
            'rate limit NOT enforced for %s (prefix=%s)', _client_ip(), prefix
        )
        return None
    from modules.utils.rate_limiter import is_rate_limited, PUBLIC_RATE_LIMIT_WINDOW_SECONDS
    key = f'{prefix}:{_client_ip()}'
    if await is_rate_limited(redis, key, PUBLIC_RATE_LIMIT_WINDOW_SECONDS):
        log.info('Rate limited: %s (key=%s)', _client_ip(), key)
        return 'rate_limited'
    return None


def require_tiered_access(redis_getter, prefix: str = 'ratelimit'):
    """Decorator for JSON endpoints. Returns 403 if blocked, 429 if rate limited."""
    def decorator(f):
        @wraps(f)
        async def decorated(*args, **kwargs):
            reason = await check_access(redis_getter(), prefix)
            if reason == 'blocked':
                return jsonify({'error': 'Access denied'}), 403
            if reason == 'rate_limited':
                return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429
            return await f(*args, **kwargs)
        return decorated
    return decorator
