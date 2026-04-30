import logging, os

log = logging.getLogger(__name__)

PUBLIC_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('PUBLIC_RATE_LIMIT_WINDOW_SECONDS', '300'))


async def is_rate_limited(redis, key: str, window: int = PUBLIC_RATE_LIMIT_WINDOW_SECONDS) -> bool:
    """Returns True if the key has been hit more than once within the window. Fails open."""
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count > 1
    except Exception:
        log.warning('Rate limit check failed for %s — allowing', key)
        return False
