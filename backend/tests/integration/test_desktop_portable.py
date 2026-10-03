"""Launcher lifecycle uses isolated data and real local HTTP, never paid providers."""
import json
import socket
import sqlite3
import time
import urllib.request

import pytest

from marketlens.workers.desktop import DesktopServer, existing_url, initial_settings, listener


def wait_for(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError('Desktop lifecycle timed out')


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import marketlens.config as config
    monkeypatch.setattr(config, '_secret', lambda _: None)
    monkeypatch.setenv('MARKETLENS_ENV_FILE', str(tmp_path / '.env'))
    monkeypatch.setenv('MARKETLENS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MARKETLENS_MODE', 'MOCK')
    monkeypatch.setenv('MARKETLENS_SCHEDULER', '0')
    monkeypatch.setenv('MARKETLENS_LIVE_QUOTES', '0')
    monkeypatch.setenv('LLM_PROVIDER', 'none')
    monkeypatch.delenv('MARKETLENS_DATABASE_URL', raising=False)
    monkeypatch.delenv('MARKETLENS_API_TOKEN', raising=False)
    return tmp_path


def test_new_install_live_without_paid_ai_and_existing_settings_preserved(tmp_path):
    path = tmp_path / '.env'
    initial_settings(path)
    assert 'MARKETLENS_MODE=LIVE' in path.read_text()
    assert 'LLM_PROVIDER=none' in path.read_text()
    # automatic analysis on: with no scan button, a new install that had it off never showed a candidate
    assert 'MARKETLENS_SCHEDULER=1' in path.read_text() and 'MARKETLENS_SCHEDULER=0' not in path.read_text()
    path.write_text('MARKETLENS_MODE=MOCK\nLLM_PROVIDER=none\n# owner settings\n')
    before = path.read_bytes()
    initial_settings(path)
    assert path.read_bytes() == before


def test_port_collision_reserves_another_loopback_socket():
    with listener(0) as first, listener(first.getsockname()[1]) as second:
        assert second.getsockname()[0] == '127.0.0.1'
        assert second.getsockname()[1] != first.getsockname()[1]


def test_actual_start_duplicate_clean_exit_and_restart_preserve_db(isolated):
    first = DesktopServer()
    first.start()
    try:
        wait_for(lambda: first.ready or first.error)
        assert first.error is None
        with urllib.request.urlopen(first.url + 'api/health/ready') as response:
            assert json.load(response)['ready']
        assert existing_url(isolated) == first.url
        duplicate = DesktopServer()
        duplicate.start()
        duplicate.thread.join(10)
        assert duplicate.duplicate and duplicate.url == first.url
        assert first.thread.is_alive()
        with sqlite3.connect(isolated / 'marketlens_mock.db') as db:
            db.execute('CREATE TABLE launcher_sentinel(value TEXT)')
            db.execute("INSERT INTO launcher_sentinel VALUES ('preserved')")
    finally:
        first.stop()
        first.thread.join(10)
    assert not first.thread.is_alive()
    assert not (isolated / 'desktop.json').exists()
    restarted = DesktopServer()
    restarted.start()
    try:
        wait_for(lambda: restarted.ready or restarted.error)
        assert restarted.error is None
        with sqlite3.connect(isolated / 'marketlens_mock.db') as db:
            assert db.execute('SELECT value FROM launcher_sentinel').fetchone() == ('preserved',)
    finally:
        restarted.stop()
        restarted.thread.join(10)
    assert not restarted.thread.is_alive()


def test_arbitrary_or_stale_instance_url_never_opened(tmp_path):
    (tmp_path / 'desktop.lock').write_text('123')
    (tmp_path / 'desktop.json').write_text(json.dumps({'pid': 123, 'url': 'https://example.com/'}))
    assert existing_url(tmp_path) is None


def test_invalid_configuration_has_error_instead_of_infinite_loading(isolated, monkeypatch):
    monkeypatch.setenv('MARKETLENS_MODE', 'INVALID')
    failed = DesktopServer()
    failed.start()
    failed.thread.join(10)
    assert not failed.thread.is_alive()
    assert failed.error and not failed.ready
