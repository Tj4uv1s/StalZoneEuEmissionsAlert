"""Тести захисту від другого екземпляра."""
import os
import subprocess
import sys
import textwrap

import pytest

from app.single_instance import AlreadyRunning, SingleInstance


def test_second_instance_is_refused(tmp_path):
    lock = tmp_path / "state.lock"
    with SingleInstance(lock):
        with pytest.raises(AlreadyRunning):
            with SingleInstance(lock):
                pass


def test_lock_is_released_after_exit(tmp_path):
    lock = tmp_path / "state.lock"
    with SingleInstance(lock):
        pass
    with SingleInstance(lock):  # має взятися без помилки
        pass


def test_lock_is_released_even_if_body_raises(tmp_path):
    lock = tmp_path / "state.lock"
    with pytest.raises(ValueError):
        with SingleInstance(lock):
            raise ValueError("щось пішло не так")
    with SingleInstance(lock):
        pass


def test_different_paths_do_not_block_each_other(tmp_path):
    with SingleInstance(tmp_path / "eu.lock"):
        with SingleInstance(tmp_path / "ru.lock"):
            pass


def test_own_pid_is_written(tmp_path):
    lock = tmp_path / "state.lock"
    with SingleInstance(lock):
        assert lock.read_text().strip() == str(os.getpid())


def test_error_names_the_holder_pid(tmp_path):
    lock = tmp_path / "state.lock"
    with SingleInstance(lock):
        with pytest.raises(AlreadyRunning, match=str(os.getpid())):
            with SingleInstance(lock):
                pass


def test_parent_directory_is_created(tmp_path):
    lock = tmp_path / "data" / "nested" / "state.lock"
    with SingleInstance(lock):
        assert lock.exists()


def _kill_tree(process) -> None:
    """Убити процес разом із нащадками.

    На Windows `.venv\\Scripts\\python.exe` запускає базовий інтерпретатор
    дочірнім процесом, і лок тримає саме він — `process.kill()` дістав би лише
    обгортку.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.kill()
    process.wait(timeout=10)


def test_lock_is_free_after_holder_is_killed(tmp_path):
    """Після kill -9 лок має бути вільним: ОС знімає його сама.

    Саме заради цього беремо лок файлу, а не PID-файл — інакше після падіння
    бот не піднявся б, поки хтось не видалить файл руками.
    """
    lock = tmp_path / "state.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                sys.path.insert(0, {str(os.getcwd())!r})
                from app.single_instance import SingleInstance
                with SingleInstance({str(lock)!r}):
                    print("locked", flush=True)
                    time.sleep(60)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            with SingleInstance(lock):
                pass
    finally:
        _kill_tree(holder)

    with SingleInstance(lock):  # лок вільний одразу після смерті власника
        pass
