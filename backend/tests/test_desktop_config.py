"""Desktop configuration profile tests."""

import os
from unittest import mock

import pytest


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MODE", "1")
    monkeypatch.setenv("DESKTOP_USE_SQLITE", "1")
    monkeypatch.setenv("TITLESEARCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)


def test_desktop_settings_profile(desktop_env, monkeypatch):
    # Reload settings with desktop env
    from importlib import reload

    import app.core.config as config_mod

    reload(config_mod)
    s = config_mod.Settings()
    assert s.DESKTOP_MODE is True
    assert s.JOB_RUNNER_ENABLED is True
    assert s.USE_REDIS is False
    assert s.CELERY_ENABLED is False
    assert s.DATABASE_URL.startswith("sqlite+aiosqlite:///")
    assert len(s.SECRET_KEY) >= 32
    assert s.WEBSHARE_PROXY_SERVER == ""