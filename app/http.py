"""Загальний SSL-контекст для httpx-клієнтів.

httpx за замовчуванням довіряє тільки пакету certifi (публічний список кореневих
сертифікатів), а не системному сховищу Windows. Якщо в системі стоїть антивірус
або корпоративна мережа з HTTPS-інспекцією (свій кореневий сертифікат, якому
довіряє Windows, але не certifi) — httpx падає з CERTIFICATE_VERIFY_FAILED,
хоча curl через нативний Schannel все бачить.
Заводимо контекст, який знає і про certifi, і про системне сховилище.
"""
from __future__ import annotations

import ssl

import certifi


def default_ssl_context() -> ssl.SSLContext:
    """Створити SSL-контекст, що довіряє certifi та системному сховищу сертифікатів.

    Returns:
        SSL контекст готовий для використання з httpx
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_default_certs()  # на Windows підтягує системні кореневі сертифікати
    return ctx
