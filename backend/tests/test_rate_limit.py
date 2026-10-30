import pytest
from fastapi import HTTPException
from app import rate_limit
from app.config import settings


def test_local_limit_is_bounded_and_isolated(monkeypatch):
    monkeypatch.setattr(settings(), 'rate_limit_enabled', True)
    monkeypatch.setattr(settings(), 'local_mode', True)
    rate_limit._windows.clear()
    rate_limit.check_limit('login', 'one', 2)
    rate_limit.check_limit('login', 'one', 2)
    with pytest.raises(HTTPException) as exc:
        rate_limit.check_limit('login', 'one', 2)
    assert exc.value.status_code == 429
    rate_limit.check_limit('login', 'two', 2)
    rate_limit.check_limit('agent', 'one', 2)
    rate_limit._windows.clear()
