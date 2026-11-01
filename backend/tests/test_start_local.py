from scripts import start_local


def test_frontend_port_skips_an_unrelated_listener_and_reserved_port(monkeypatch):
    monkeypatch.setattr(start_local, "listening", lambda port: port == 5173)
    assert start_local.frontend_port(5173, {5174}) == 5175
    assert start_local.frontend_port(5174, {5175}) == 5174
