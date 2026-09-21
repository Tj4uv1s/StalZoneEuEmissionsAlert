# stalzone-bot

Telegram-бот для моніторингу викидів (вибухів) в STALCRAFT (STALZONE) через офіційний API
та відправлення сповіщень у канал/групу/особисту чат.

## Структура

```
app/
  config.py         — параметри з .env
  http.py           — загальний SSL-контекст для httpx (Windows + перехоплювальний TLS антивірус/проксі)
  stalcraft.py      — клієнт API гри, адаптивний до rate-limit
  tracker.py        — чистої логіка "що змінилося" -> список подій
  storage.py        — зберігання стану (JSON, пізніше SQLite)
  notify.py         — відправлення повідомлень у Telegram (або в консоль, якщо TG не налаштований)
  main.py           — цикл опитування
  constants.py      — константи застосунку
tests/              — pytest
```

## Швидкий старт (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

copy .env.example .env      # демо-креденшали для API вже в шаблоні, Telegram можна поки не заповнювати

pytest                      # запустити тести
python -m app.main          # запустити бота — без TG_TOKEN просто друкує статус у консоль
```

## Що далі

1. Демо-креденшали для API гри вже в `.env.example`, реєстрація не потрібна — можна одразу
   дивитися статус викиду в консолі.
2. Отримати токен бота у @BotFather; дізнатися, куди слати — id особистого чату, групи
   або каналу.
3. Заповнити `TG_TOKEN`/`TG_CHAT_IDS` в `.env`, перезапустити — сповіщення пійдуть у Telegram.
4. На боєвий сервер (`https://eapi.stalcraft.net`) — зареєструвати застосунок,
   отримати свої `client_id`/`client_secret`, замінити демо-креденшали.
5. Задеплоїти на завжди-живий хост (Oracle Always Free / VPS + systemd `Restart=always`,
   або Docker `restart: unless-stopped`) — не GitHub Actions cron, там мінімум 5 хвилин
   між запусками й короткий викид можна прозевати.

## Windows: SSL-помилка при запуску

Якщо бачиш `CERTIFICATE_VERIFY_FAILED` — це антивірус або мережа з HTTPS-інспекцією
підсовує свій кореневий сертифікат, якому довіряє Windows, але не список certifi,
яким за замовчуванням користується httpx. Вже починено в `app/http.py`
(додає системне сховилище сертифікатів Windows до списку довірених).
