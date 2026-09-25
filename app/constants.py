"""Константи застосунку."""

# Тип подій трекера
EVENT_WARNING = "warning"  # викид оголошений або вже йде
EVENT_ENDED = "ended"      # викид завершився

# Трекер
HISTORY_LIMIT = 10  # скільки останніх циклів тримаємо для середніх
DEFAULT_ANNOUNCE_MAX_AGE = 900  # секунди: старіші події не анонсуємо (тихий старт)

# Час у повідомленнях: МСК = UTC+3 (без переходу на літній час з 2014)
MSK_OFFSET_HOURS = 3
MSK_LABEL = "МСК"

# Сітьові константи
DEFAULT_TIMEOUT = 15.0
RETRY_ATTEMPTS = 2
RETRY_BASE_DELAY = 2  # секунди (експоненціальна затримка: 2^0, 2^1, ...)

# Rate limit
RATE_LIMIT_SAFETY_MARGIN = 0.8  # використовуємо не більше цієї частки ліміту

# Telegram
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_SEND_METHOD = "sendMessage"
TELEGRAM_RETRY_AFTER_DEFAULT = 3  # секунди
