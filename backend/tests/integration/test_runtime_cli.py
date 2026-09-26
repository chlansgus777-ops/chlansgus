"""Desktop sidecar runtime: port selection / collision, single-instance lock, CLI commands end-to-end."""

from __future__ import annotations

import json
import socket

import pytest

from marketlens.workers.runtime import AlreadyRunning, InstanceLock, PortInUse, choose_port, port_available


def test_port_zero_picks_a_free_port_and_collisions_are_reported():
    p = choose_port("127.0.0.1", 0)
    assert 1024 < p < 65536 and port_available("127.0.0.1", p)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert not port_available("127.0.0.1", busy)
        with pytest.raises(PortInUse) as e:
            choose_port("127.0.0.1", busy)
        assert "--port 0" in str(e.value)


def test_second_instance_on_the_same_data_dir_is_refused(tmp_path):
    first = InstanceLock(tmp_path / "marketlens-mock.lock").acquire()
    try:
        with pytest.raises(AlreadyRunning):
            InstanceLock(tmp_path / "marketlens-mock.lock").acquire()
    finally:
        first.release()
    again = InstanceLock(tmp_path / "marketlens-mock.lock").acquire()  # released → can start again
    again.release()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETLENS_MODE", "MOCK")
    monkeypatch.setenv("MARKETLENS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARKETLENS_DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("MOCK_UNIVERSE_SIZE", "60")
    monkeypatch.setenv("AI_COMMITTEE_TOP_N", "2")
    yield tmp_path
    import logging

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_marketlens", False):  # detach the file handler that points into tmp_path
            root.removeHandler(h)
            h.close()


def test_serve_refuses_non_local_host_busy_port_and_second_instance(cli_env, monkeypatch):
    from marketlens.config import load_settings
    from marketlens.workers import cli

    st = load_settings()
    assert cli._serve(st, "0.0.0.0", 8765) == 2  # never exposed on the network
    lock = InstanceLock(st.data_dir / "marketlens-mock.lock").acquire()
    try:
        assert cli._serve(st, "127.0.0.1", 0) == 4
    finally:
        lock.release()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        assert cli._serve(st, "127.0.0.1", s.getsockname()[1]) == 3


def test_cli_commands_end_to_end(cli_env, capsys):
    from marketlens.workers.cli import main

    assert main(["migrate"]) == 0
    assert main(["sync"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "SKIPPED"  # MOCK needs no store sync
    assert main(["analyze", "NVDA"]) == 0
    printed = capsys.readouterr().out
    analysed = json.loads(printed[printed.find("{"):])
    assert analysed["ticker"] == "NVDA" and analysed["mode"] == "MOCK" and analysed["action"] and "breakdown" in analysed
    assert main(["scan", "--no-committee"]) == 0
    assert main(["evaluate"]) == 0
    assert main(["calibrate"]) == 0
    captured = capsys.readouterr().out
    assert "INSUFFICIENT_SAMPLES" in captured
    assert main(["replay", "1"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["matches"] is True
    assert (cli_env / "logs" / "marketlens.log").exists()  # file log in the data directory


def test_serve_refuses_a_database_of_the_other_mode(cli_env):
    """Mock/Live separation: a MOCK database must never be opened in LIVE mode (or vice versa)."""
    from dataclasses import replace

    from marketlens.application.services import MixedEnvironmentError, assert_db_environment
    from marketlens.config import load_settings
    from marketlens.domain.enums import DataMode
    from marketlens.infrastructure.db import repository as repo
    from marketlens.infrastructure.db.session import make_engine, make_session_factory, migrate
    from marketlens.workers import cli

    st = load_settings()
    migrate(st.database_url)
    with make_session_factory(make_engine(st.database_url))() as s:
        repo.set_setting(s, "db_environment", "MOCK")
        s.commit()
    live = replace(st, mode=DataMode.LIVE)
    with pytest.raises(MixedEnvironmentError):
        assert_db_environment(live)
    assert cli._serve(live, "127.0.0.1", 0) == 5
    assert_db_environment(st)  # same mode is fine
