"""Захист від другого екземпляра: один бот на один STATE_PATH.

Два процеси з тим самим станом шлють у канал по два однакові сповіщення на
кожну подію і затирають `state.json` один одному. Ловимо це на старті.

Беремо ексклюзивне блокування файлу, а не PID-файл: ОС знімає лок сама, коли
процес помирає, тож після падіння чи kill -9 не лишається "застряглого" локу,
який довелося б чистити руками. PID усередині файлу — суто щоб у повідомленні
про помилку було видно, хто саме тримає лок.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType

# Лочимо один байт далеко за текстом PID: на Windows заблокована ділянка
# заважає писати в неї, а так текст і лок не перетинаються.
_LOCK_OFFSET = 1 << 20
_PID_WIDTH = 32


class AlreadyRunning(RuntimeError):
    """Інший екземпляр уже тримає цей лок."""


def _acquire(handle) -> None:
    """Взяти ексклюзивний лок без очікування. OSError, якщо вже зайнято."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle) -> None:
    """Зняти лок. ОС зробила б це й сама, але при штатному виході — явно."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SingleInstance:
    """Контекст-менеджер: тримає лок, поки бот працює.

    Usage:
        with SingleInstance(path):
            ...  # тут ми єдиний екземпляр
    """

    def __init__(self, path: str | Path) -> None:
        """Ініціалізація.

        Args:
            path: Шлях до лок-файлу
        """
        self._path = Path(path)
        self._handle = None

    def __enter__(self) -> SingleInstance:
        """Взяти лок або впасти з AlreadyRunning."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("r+" if self._path.exists() else "w+")
        try:
            _acquire(handle)
        except OSError as exc:
            holder = self._read_pid(handle)
            handle.close()
            raise AlreadyRunning(
                f"Бот уже працює{f' (PID {holder})' if holder else ''} з тим самим станом "
                f"({self._path}). Другий екземпляр слав би в канал по два сповіщення."
            ) from exc
        self._handle = handle
        self._write_pid(handle)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Зняти лок і закрити файл."""
        if self._handle is None:
            return
        try:
            _release(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _write_pid(handle) -> None:
        """Записати свій PID на початок файлу (фіксованої ширини, без truncate)."""
        handle.seek(0)
        handle.write(str(os.getpid()).ljust(_PID_WIDTH))
        handle.flush()

    @staticmethod
    def _read_pid(handle) -> str:
        """Прочитати PID власника локу. Порожньо, якщо не вдалося."""
        try:
            handle.seek(0)
            return handle.read(_PID_WIDTH).strip()
        except OSError:
            return ""
