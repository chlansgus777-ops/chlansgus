"""Portable Windows entry: bundled server and UI, with a small launch/exit window."""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from marketlens.workers.runtime import AlreadyRunning, InstanceLock, process_alive


def initial_settings(env_path: Path) -> None:
    """Only seed a new installation. Never replace the owner's settings or database."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with env_path.open('x', encoding='utf-8') as f:
            f.write('MARKETLENS_MODE=LIVE\nLLM_PROVIDER=none\nMARKETLENS_SCHEDULER=0\n')
    except FileExistsError:
        return  # Existing installation settings belong to the user.


def listener(preferred: int = 8765) -> socket.socket:
    """Reserve the port until Uvicorn takes the same socket; no check/bind race."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.bind(('127.0.0.1', preferred))
        except OSError:
            sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def existing_url(data_dir: Path) -> str | None:
    """A duplicate opens only the current local instance, never a stale/arbitrary address."""
    import re
    try:
        state = json.loads((data_dir / 'desktop.json').read_text(encoding='utf-8'))
        # Windows denies even reads of a byte protected by msvcrt.locking.
        # The state is written only while holding desktop.lock; don't reread that locked file.
        pid = state['pid']
        url = state['url']
        if type(pid) is not int or not process_alive(pid) or not isinstance(url, str) or not re.fullmatch(r'http://127\.0\.0\.1:[1-9]\d{0,4}/', url):
            return None
        if int(url.split(':')[-1].rstrip('/')) > 65535:
            return None
        with urllib.request.urlopen(url + 'api/health/ready', timeout=0.5) as response:
            if not json.load(response).get('ready'):
                return None
        return url
    except (OSError, ValueError, KeyError, TypeError):
        return None


class DesktopServer:
    def __init__(self):
        self.url: str | None = None
        self.error: str | None = None
        self.duplicate = False
        self.server = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='marketlens-desktop', daemon=True)

    def start(self) -> None:
        self.thread.start()

    @property
    def ready(self) -> bool:
        return bool(self.url and (self.duplicate or self.server and self.server.started))

    def stop(self) -> None:
        self._stop.set()
        if self.server and self.server.started:
            self.server.should_exit = True

    def _run(self) -> None:
        sock = None
        locks: list[InstanceLock] = []
        try:
            from marketlens.config import env_file_path, load_settings
            initial_settings(env_file_path())
            settings = load_settings()
            data = settings.data_dir
            data.mkdir(parents=True, exist_ok=True)
            from marketlens.infrastructure.logging import configure_logging
            configure_logging(settings.log_level, settings.secrets(), data / 'logs')
            desktop_lock = InstanceLock(data / 'desktop.lock')
            try:
                desktop_lock.acquire()
            except AlreadyRunning:
                for _ in range(100):
                    if self._stop.is_set():
                        return
                    url = existing_url(data)
                    if url:
                        self.url, self.duplicate = url, True
                        return
                    time.sleep(0.1)
                raise AlreadyRunning('기존 실행이 준비 중이거나 응답하지 않습니다.')
            locks.append(desktop_lock)
            # Keep the existing CLI/Tauri database protection as well.
            locks.append(InstanceLock(data / f'marketlens-{settings.mode.value.lower()}.lock').acquire())
            from marketlens.application.services import assert_db_environment
            from marketlens.api.app import create_app
            import uvicorn
            assert_db_environment(settings)
            if self._stop.is_set():
                return
            sock = listener()
            self.url = f'http://127.0.0.1:{sock.getsockname()[1]}/'
            self.server = uvicorn.Server(uvicorn.Config(create_app(settings), loop='asyncio', http='h11', ws='websockets', log_config=None, access_log=False))
            state_path = data / 'desktop.json'
            state_path.write_text(json.dumps({'pid': os.getpid(), 'url': self.url}), encoding='utf-8')
            self.server.run(sockets=[sock])
        except BaseException as exc:
            logging.getLogger('marketlens.desktop').exception('desktop startup failed')
            self.error = ('MarketLens가 이미 실행 중입니다. 기존 창을 확인해주세요.' if isinstance(exc, AlreadyRunning)
                          else f'시작하지 못했습니다 ({type(exc).__name__}). 데이터 폴더의 logs/marketlens.log를 확인해주세요.')
        finally:
            if sock is not None:
                sock.close()
            if locks:
                try:
                    (locks[0].path.parent / 'desktop.json').unlink(missing_ok=True)
                except OSError as exc:
                    logging.getLogger('marketlens.desktop').warning('Could not remove desktop runtime state: %s', exc)
            for lock in reversed(locks):
                lock.release()


def main() -> int:
    import tkinter as tk
    from tkinter import ttk
    window = tk.Tk()
    window.title('MarketLens')
    icon = (Path(getattr(sys, '_MEIPASS')) / 'MarketLens.ico' if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2] / 'packaging/MarketLens.ico')
    if icon.exists():
        window.iconbitmap(str(icon))
    window.geometry('430x240')
    window.resizable(False, False)
    window.configure(bg='#0d1220')
    frame = tk.Frame(window, bg='#0d1220', padx=28, pady=24)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='MarketLens', font=('Segoe UI', 24, 'bold'), fg='#ddd6ff', bg='#0d1220').pack(anchor='w')
    status = tk.StringVar(value='앱을 준비하고 있습니다…')
    tk.Label(frame, textvariable=status, font=('맑은 고딕', 10), fg='#a9b3c9', bg='#0d1220', wraplength=365, justify='left').pack(anchor='w', pady=(8, 16))
    progress = ttk.Progressbar(frame, mode='indeterminate', length=365)
    progress.pack(fill='x')
    progress.start(12)
    buttons = tk.Frame(frame, bg='#0d1220')
    buttons.pack(fill='x', pady=(16, 0))
    controller = DesktopServer()
    opened = False
    closing = False

    def open_app():
        if controller.ready:
            webbrowser.open(controller.url)

    open_button = tk.Button(buttons, text='앱 열기', command=open_app, state='disabled', bg='#7460df', fg='white', relief='flat', padx=18, pady=6)
    open_button.pack(side='left')

    def close():
        nonlocal closing
        closing = True
        status.set('안전하게 종료하고 있습니다…')
        open_button.configure(state='disabled')
        exit_button.configure(state='disabled')
        controller.stop()

    exit_button = tk.Button(buttons, text='종료', command=close, bg='#20283b', fg='#e0e6f2', relief='flat', padx=18, pady=6)
    exit_button.pack(side='right')
    window.protocol('WM_DELETE_WINDOW', close)

    def poll():
        nonlocal opened
        if closing:
            controller.stop()
            if not controller.thread.is_alive():
                window.destroy()
                return
        elif controller.error:
            progress.stop()
            progress.pack_forget()
            status.set(controller.error)
        elif controller.ready and not opened:
            opened = True
            progress.stop()
            progress.pack_forget()
            status.set('실행 중입니다. 화면은 브라우저에서 열립니다.\n이 창에서 종료하면 데이터도 안전하게 닫습니다.')
            open_button.configure(state='normal')
            if os.environ.get('MARKETLENS_NO_BROWSER') != '1':
                open_app()
            if controller.duplicate:
                window.destroy()
                return
        elif opened and not controller.thread.is_alive():
            status.set('앱이 종료되었습니다. 다시 실행해주세요.')
            open_button.configure(state='disabled')
        window.after(100, poll)

    controller.start()
    window.after(100, poll)
    window.mainloop()
    return 1 if controller.error else 0
