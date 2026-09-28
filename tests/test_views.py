"""Тести меню ринку в особистих повідомленнях: екрани, пагінація, пошук, маршрутизація."""
import pytest

from app import views
from app.artifacts import Artifact
from app.constants import MARKET_MIN_LOTS, UI_PAGE_SIZE
from app.formatting import telegram_len
from app.telegram_bot import region_from_payload, to_markup

SOLID = MARKET_MIN_LOTS + 5

PRISM = Artifact(id="prism", name="Призма", category="other_arts")
HEDGEHOG = Artifact(id="hog", name="Ежик", category="biochemical")
STEEL_HOG = Artifact(id="shog", name="Стальной Ежик", category="gravity")
ICE_HOG = Artifact(id="ihog", name="Ледяной ежик", category="thermal")
EVIL = Artifact(id="evil", name="<b>Злий</b> & Ко", category="gravity")
CATALOG = (PRISM, HEDGEHOG, STEEL_HOG, ICE_HOG, EVIL)


def day(date, **prices):
    return {
        "taken_at": f"2026-09-{date}T17:00:00Z",
        "prices": {key: {"median": m, "count": c} for key, (m, c) in prices.items()},
    }


def state_with(current, base=None, base_days=7):
    base = base if base is not None else {}
    history = [day(f"{20 + i:02d}", **base) for i in range(base_days)]
    history.append(day("28", **current))
    return {"market_history": history}


STATE = state_with(
    current={"prism:2": (900_000, SOLID), "hog:5": (47_000_000, SOLID), "shog:1": (100, 2)},
    base={"prism:2": (1_200_000, SOLID), "hog:5": (40_000_000, SOLID), "shog:1": (100, SOLID)},
)


def data(state=STATE, region="EU"):
    return views.MarketData.from_state(region, state)


def buttons(view):
    return [data for row in view.keyboard for _, data in row]


# --- дані ------------------------------------------------------------------

def test_market_data_uses_last_snapshot_and_week_before_it():
    d = data()
    assert d.baseline_days == 7
    assert d.taken_at.isoformat() == "2026-09-28T17:00:00+00:00"
    by_id = {q.artifact_id: q for q in d.quotes}
    assert by_id["prism"].baseline == 1_200_000


def test_no_history_means_no_data():
    assert views.MarketData.from_state("EU", {}) is None


def test_same_signals_as_channel_post():
    buys, sells = data().signals()
    assert [q.artifact_id for q in buys] == ["prism"]
    assert [q.artifact_id for q in sells] == ["hog"]


# --- меню ------------------------------------------------------------------

def test_menu_shows_signal_counts_and_sections():
    view = views.menu(data(), "EU", ["EU"])
    assert "💰 Вигідно купити: <b>1</b>" in view.text
    assert "📤 Вигідно продати: <b>1</b>" in view.text
    assert buttons(view) == ["b:EU:0", "s:EU:0", "p:EU:0", "r:EU", "f:EU"]


def test_menu_has_region_switch_only_with_several_regions():
    view = views.menu(data(), "EU", ["EU", "RU"])
    assert view.keyboard[-1] == [("EU ✓", "m:EU"), ("RU", "m:RU")]
    assert all("m:" not in b for b in buttons(views.menu(data(), "EU", ["EU"])))


def test_menu_without_data():
    view = views.menu(None, "EU", ["EU"])
    assert "Даних ще немає" in view.text
    assert view.keyboard == []


def test_first_day_menu_explains_signals_come_later():
    view = views.menu(data(state_with({"prism:2": (1, SOLID)}, base_days=0)), "EU", ["EU"])
    assert "перший зріз" in view.text


# --- сигнали ---------------------------------------------------------------

def test_buy_list_shows_discount_usual_price_and_lots():
    view = views.signal_list(data(), "b", 0, CATALOG)
    assert "🔵 <b>Призма</b> · Особый  <b>−25%</b>" in view.text
    assert "900 тис · зазвичай 1,2 млн · лотів: 10" in view.text
    assert buttons(view) == ["m:EU"]


def test_sell_list():
    view = views.signal_list(data(), "s", 0, CATALOG)
    assert "🟡 <b>Ежик</b> · Легендарный  <b>+18%</b>" in view.text


def test_empty_signals_on_first_day():
    view = views.signal_list(data(state_with({"prism:2": (1, SOLID)}, base_days=0)), "b", 0, CATALOG)
    assert "з'явиться з наступного дня" in view.text


def test_signal_list_paginates():
    n = UI_PAGE_SIZE * 2 + 3
    catalog = tuple(Artifact(id=f"a{i}", name=f"Арт{i:02d}", category="gravity") for i in range(n))
    state = state_with(
        current={f"a{i}:1": (500, SOLID) for i in range(n)},
        base={f"a{i}:1": (1000, SOLID) for i in range(n)},
    )
    first = views.signal_list(data(state), "b", 0, catalog)
    assert first.keyboard[0] == [("1/3", "noop"), ("▶", "b:EU:1")]
    last = views.signal_list(data(state), "b", 99, catalog)  # за межами — остання сторінка
    assert last.keyboard[0] == [("◀", "b:EU:1"), ("3/3", "noop")]
    assert last.text.count("· зазвичай") == 3  # рядки сигналів, без заголовка


# --- прайс і рідкості ---------------------------------------------------------

def test_price_list_marks_thin_prices():
    view = views.price_list(data(), 0, CATALOG)
    assert "<b>Стальной Ежик</b> — 🟢 100*" in view.text
    assert "<b>Призма</b> — 🔵 900 тис\n" in view.text
    assert "* менше" in view.text


def test_rarity_picker_counts_artifacts_per_rarity():
    view = views.rarity_picker(data())
    labels = [label for row in view.keyboard for label, _ in row]
    assert "🔵 Особый (1)" in labels
    assert "🔴 Исключительный (0)" in labels
    assert "q:EU:5:0" in buttons(view)


def test_rarity_list_sorted_by_price_with_trend():
    state = state_with(
        current={"prism:5": (900, SOLID), "hog:5": (47_000, SOLID)},
        base={"prism:5": (1000, SOLID), "hog:5": (40_000, SOLID)},
    )
    view = views.rarity_list(data(state), 5, 0, CATALOG)
    lines = view.text.split("\n\n")[1].splitlines()  # тіло між заголовком і приміткою
    assert lines == ["<b>Ежик</b> — 47 тис  🟢▲ +18%", "<b>Призма</b> — 900  🔴▼ −10%"]
    assert buttons(view) == ["r:EU", "m:EU"]


# --- картка й пошук ---------------------------------------------------------

def test_artifact_card_says_what_to_do():
    view = views.artifact_card(data(), PRISM, "EU")
    assert "🔵 <b>Особый</b> — 900 тис  🔴▼ −25%" in view.text
    assert "зазвичай 1,2 млн · лотів: 10 · 💰 вигідно купити" in view.text


def test_card_thin_price_is_flagged_not_signalled():
    view = views.artifact_card(data(), STEEL_HOG, "EU")
    assert "мало лотів — ціна орієнтовна" in view.text
    assert "▼" not in view.text and "▲" not in view.text


def test_card_for_artifact_absent_from_market():
    view = views.artifact_card(data(), ICE_HOG, "EU")
    assert "не було" in view.text


def test_search_exact_name_opens_card_even_if_substring_of_others():
    view = views.search(data(), "ежик", "EU", CATALOG)
    assert view.text.startswith("<b>🔎 Ежик</b>")


def test_search_is_case_and_yo_insensitive():
    view = views.search(data(), "ПРИЗМ", "EU", CATALOG)
    assert view.text.startswith("<b>🔎 Призма</b>")
    view = views.search(data(), "ёжик", "EU", (HEDGEHOG,))
    assert view.text.startswith("<b>🔎 Ежик</b>")


def test_search_several_matches_offers_choice():
    view = views.search(data(), "жик", "EU", CATALOG)
    assert "Знайдено 3" in view.text
    assert buttons(view)[:3] == ["a:EU:hog", "a:EU:ihog", "a:EU:shog"]


def test_search_nothing_found_and_too_short():
    assert "Не знайшов" in views.search(data(), "бублик", "EU", CATALOG).text
    assert "хоча б 2 літери" in views.search(data(), "а", "EU", CATALOG).text


def test_search_too_many_results_asks_to_refine():
    catalog = tuple(Artifact(id=f"a{i}", name=f"Кристал {i}", category="gravity") for i in range(15))
    assert "уточніть" in views.search(None, "крист", "EU", catalog).text


def test_names_are_html_escaped():
    """Назва з < > & не повинна ламати HTML-розмітку повідомлення."""
    view = views.search(None, "злий", "EU", CATALOG)
    assert "&lt;b&gt;Злий&lt;/b&gt; &amp; Ко" in view.text
    assert "<b>Злий</b>" not in view.text


# --- маршрутизація натискань --------------------------------------------------

def get_state(region):
    return STATE if region == "EU" else {}


@pytest.mark.parametrize(
    "callback,starts_with",
    [
        ("m:EU", "<b>📊 Ринок артефактів EU</b>"),
        ("b:EU:0", "<b>💰 Вигідно купити"),
        ("s:EU:0", "<b>📤 Вигідно продати"),
        ("p:EU:0", "<b>📋 Прайс</b>"),
        ("r:EU", "<b>🎨 За рідкістю</b>"),
        ("q:EU:2:0", "<b>🔵 Особый</b>"),
        ("f:EU", "<b>🔍 Пошук артефакту</b>"),
        ("a:EU:prism", "<b>🔎 Призма</b>"),
    ],
)
def test_every_button_leads_somewhere(callback, starts_with):
    view = views.render_callback(callback, get_state, ["EU"], CATALOG)
    assert view is not None and view.text.startswith(starts_with)


@pytest.mark.parametrize(
    "callback",
    ["noop", "", "m", "m:XX", "b:EU:не-число", "q:EU:2", "a:EU:немає-такого", "zzz:EU:0"],
)
def test_garbage_or_foreign_callbacks_are_ignored(callback):
    assert views.render_callback(callback, get_state, ["EU"], CATALOG) is None


def test_region_without_data_shows_no_data_instead_of_crash():
    view = views.render_callback("p:RU:0", get_state, ["EU", "RU"], CATALOG)
    assert "Даних ще немає" in view.text


def test_all_callback_data_fits_telegram_64_bytes():
    screens = [
        views.menu(data(), "NEA", ["EU", "RU", "NA", "SEA", "NEA"]),
        views.rarity_picker(data(region="NEA")),
        views.search(data(), "жик", "NEA", CATALOG),
    ]
    for view in screens:
        assert all(len(b.encode()) <= 64 for b in buttons(view))


def test_pages_fit_telegram_message_limit():
    n = 103
    catalog = tuple(Artifact(id=f"a{i}", name=f"Дуже довга назва артефакту {i}", category="gravity") for i in range(n))
    current = {f"a{i}:{q}": (123_456_789, SOLID) for i in range(n) for q in range(6)}
    state = state_with(current=current, base=current)
    for page in range(8):
        assert telegram_len(views.price_list(data(state), page, catalog).text) <= 4096


# --- обгортка Telegram ------------------------------------------------------

def test_deep_link_payload_to_region():
    assert region_from_payload("market_EU", ["EU", "RU"]) == "EU"
    assert region_from_payload("market_ru", ["EU", "RU"]) == "RU"
    assert region_from_payload("market_XX", ["EU"]) is None
    assert region_from_payload("щось інше", ["EU"]) is None
    assert region_from_payload(None, ["EU"]) is None


def test_keyboard_to_markup():
    markup = to_markup([[("💰 Купити", "b:EU:0")], [("⬅ Меню", "m:EU")]])
    assert [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard] == [
        [("💰 Купити", "b:EU:0")],
        [("⬅ Меню", "m:EU")],
    ]
    assert to_markup([]) is None
