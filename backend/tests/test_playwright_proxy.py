from app.browser.playwright_proxy import build_playwright_proxy


def test_build_playwright_proxy_empty(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "WEBSHARE_PROXY_SERVER", "")
    assert build_playwright_proxy() is None


def test_build_playwright_proxy_full(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "WEBSHARE_PROXY_SERVER", "http://p.webshare.io:80")
    monkeypatch.setattr(config.settings, "WEBSHARE_PROXY_USERNAME", "user-1")
    monkeypatch.setattr(config.settings, "WEBSHARE_PROXY_PASSWORD", "secret")
    proxy = build_playwright_proxy()
    assert proxy == {
        "server": "http://p.webshare.io:80",
        "username": "user-1",
        "password": "secret",
    }