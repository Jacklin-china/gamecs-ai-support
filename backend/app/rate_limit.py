"""Bound expensive operations; Redis coordinates limits outside local mode."""
from collections import OrderedDict
from hashlib import sha256
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError
from app.config import settings

_lock = Lock()
_windows = OrderedDict()
_redis = None
_lua = """local n = redis.call('INCR', KEYS[1]); if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; return n"""


def check_limit(kind: str, identity: str, limit: int, window: int = 60) -> None:
    cfg = settings()
    if not cfg.rate_limit_enabled:
        return
    key = f"gamecs:rate:{kind}:{sha256(identity.encode()).hexdigest()}"
    if cfg.local_mode:
        now = monotonic()
        with _lock:
            for old_key, (_, expires) in list(_windows.items()):
                if expires <= now:
                    del _windows[old_key]
            count, expires = _windows.get(key, (0, now + window))
            _windows[key] = (count + 1, expires)
            _windows.move_to_end(key)
            if len(_windows) > 10000:
                _windows.popitem(last=False)
            count += 1
    else:
        global _redis
        try:
            if _redis is None:
                _redis = Redis.from_url(cfg.redis_url, socket_connect_timeout=1, socket_timeout=1)
            count = int(_redis.eval(_lua, 1, key, window))
        except RedisError as exc:
            raise HTTPException(503, "请求限制服务暂不可用") from exc
    if count > limit:
        raise HTTPException(429, "请求过于频繁，请稍后再试", headers={"Retry-After": str(window)})
