"""Process-level runtime helpers for the desktop sidecar: port selection and single-instance locking."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import IO


class PortInUse(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str, port: int) -> int:
    """``port == 0`` → a free ephemeral port chosen by the OS. Otherwise the port must be free."""
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            return int(s.getsockname()[1])
    if not port_available(host, port):
        raise PortInUse(f"포트 {port}가 이미 사용 중입니다(다른 MarketLens 또는 프로그램). --port 0 으로 빈 포트를 자동 선택할 수 있습니다.")
    return port


class InstanceLock:
    """Exclusive lock file in the data directory so two backends never share one database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            raise AlreadyRunning(f"MarketLens 백엔드가 이미 실행 중입니다(잠금 파일: {self.path}).") from e
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
