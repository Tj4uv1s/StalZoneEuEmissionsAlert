# stalzone-bot

Телеграм-бот, который следит за выбросами в STALCRAFT: X (STALZONE) через официальный API
и постит уведомления в канал.

## Структура

```
app/
  config.py     — настройки из .env
  stalcraft.py  — клиент API игры (эндпоинт emission)
  tracker.py    — чистая логика "что изменилось" -> список событий
  storage.py    — хранение состояния (JSON, позже SQLite)
  notify.py     — отправка сообщений в Telegram
  main.py       — цикл опроса
tests/          — тесты (начинаем с tracker)
```

## Быстрый старт (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

copy .env.example .env      # затем заполнить токены

pytest                      # прогнать тесты
python -m app.main          # запустить бота
```

## Что дальше

1. Получить токен бота у @BotFather, добавить бота админом в канал.
2. Взять демо-токен API из https://eapi.stalcraft.net/auth.html (или зарегистрировать боевое приложение).
3. Заполнить `.env`.
4. Проверить коды регионов: `GET /regions`.
5. Задеплоить на бесплатный хост (Oracle Always Free + systemd, либо GitHub Actions по cron).
