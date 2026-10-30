"""Minimal request observability without logging credentials, bodies or query strings."""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger('gamecs.requests')


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            route = request.scope.get('route')
            log.error('request_id=%s method=%s route=%s status=500 duration_ms=%d', request_id, request.method, getattr(route, 'path', 'unmatched'), int((time.monotonic()-started)*1000))
            raise
        route = request.scope.get('route')
        log.info('request_id=%s method=%s route=%s status=%d duration_ms=%d', request_id, request.method, getattr(route, 'path', 'unmatched'), response.status_code, int((time.monotonic()-started)*1000))
        response.headers['X-Request-ID'] = request_id
        return response
