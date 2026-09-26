"""Константи застосунку."""

# Тип подій трекера
EVENT_WARNING = "warning"  # викид оголошений або вже йде
EVENT_ENDED = "ended"      # викид завершився

# Трекер
HISTORY_LIMIT = 10  # скільки останніх циклів тримаємо для середніх
DEFAULT_ANNOUNCE_MAX_AGE = 900  # секунди: старіші події не анонсуємо (тихий старт)

# Час у повідомленнях. Саме зона, а не фіксований офсет: Київ узимку UTC+2,
# улітку UTC+3, тож офсет "+3" з кінця жовтня показував би на годину більше.
LOCAL_TZ_NAME = "Europe/Kyiv"
LOCAL_TZ_LABEL = "за Києвом"

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

# Топ продажів артефактів після викиду
DEFAULT_TOP_SALES_DELAY = 1800  # секунди після ENDED: 30 хв на активний збір і продаж
TOP_SALES_COUNT = 10
AUCTION_PAGE_SIZE = 200  # максимум за раз, як задокументовано в API
AUCTION_MAX_PAGES = 3    # запобіжник: не більше 600 записів на один артефакт за прогін

# category з бази EXBO-Studio/stalzone-database -> емодзі й підпис для повідомлень.
# Немає власної "рідкості" в історії продажів (API її не віддає) — категорія артефакту
# єдине, чим можна візуально розрізнити повідомлення, тому забарвлюємо саме нею.
ARTIFACT_CATEGORY_EMOJI = {
    "gravity": "🌀",
    "thermal": "🔥",
    "biochemical": "🧬",
    "electrophysical": "⚡",
    "other_arts": "💠",
}
ARTIFACT_CATEGORY_LABEL = {
    "gravity": "гравітаційний",
    "thermal": "термальний",
    "biochemical": "біохімічний",
    "electrophysical": "електрофізичний",
    "other_arts": "інший",
}
