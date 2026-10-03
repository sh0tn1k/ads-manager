from __future__ import annotations

from pathlib import Path

import pytest
from aiogram.fsm.storage.base import StorageKey

from ads_api.catalog import ADS_API_METHOD_NAMES
from ads_api.errors import AdsApiError
from bot.coverage import BOT_METHOD_COVERAGE
from bot.formatters import ads_table_rows, error_needs_user_fix, format_ads_error, status_ru
from bot.handlers.ads import _parse_target
from bot.keyboards import nav_blocks, wizard_blocks
from bot.rich import button_row, heading, rich, table
from bot.states import CreateAdSG
from bot.storage import SqliteStorage
from bot.ui import UI_CHAT_KEY, UI_MESSAGE_KEY, event_text, keep_ui_data, resolve_screen_id
from ads_api.models import Ad
from aiogram.types import CallbackQuery, Message


def test_no_inline_or_reply_keyboard_in_bot_sources():
    root = Path(__file__).resolve().parents[1] / "bot"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("\"\"\""):
                continue
            if "InlineKeyboardButton" in line or "inline_keyboard=" in line:
                offenders.append(f"{path}:{i}:{stripped}")
            if "ReplyKeyboardMarkup" in line or "ReplyKeyboardRemove" in line:
                offenders.append(f"{path}:{i}:{stripped}")
            if "KeyboardButton" in line and "RichMessageButton" not in line:
                offenders.append(f"{path}:{i}:{stripped}")
    assert offenders == []


def test_menu_is_in_message_buttons():
    blocks = nav_blocks()
    assert len(blocks) >= 2
    buttons = [btn for block in blocks for btn in block.buttons]
    labels = [btn.text for btn in buttons]
    assert "Объявления" in labels
    assert "Создать" in labels
    styles = {btn.text: btn.style for btn in buttons}
    assert styles["Объявления"] == "primary"
    assert styles["Создать"] == "success"
    assert styles["Помощь"] == "link"
    wiz = wizard_blocks(skip=True)
    wiz_by_label = {btn.text: btn.style for block in wiz for btn in block.buttons}
    assert wiz_by_label["Отмена"] == "danger"
    assert wiz_by_label["Пропустить"] == "link"


def test_event_text_maps_skip_and_confirm():
    skip = CallbackQuery.model_construct(data="wiz:skip")
    yes = CallbackQuery.model_construct(data="cad:ok")
    assert event_text(skip) == "Пропустить"
    assert event_text(yes) == "да"


def test_resolve_screen_uses_tapped_message_for_buttons():
    cb = CallbackQuery.model_construct(
        data="nav:ads",
        message=type("Msg", (), {"message_id": 42, "chat": type("C", (), {"id": 1})()})(),
    )
    assert resolve_screen_id(cb, stored_id=7) == 42
    assert resolve_screen_id(cb, stored_id=None) == 42
    msg = type("UserMsg", (), {"text": "hi"})()
    assert resolve_screen_id(msg, stored_id=7) is None
    assert resolve_screen_id(msg, stored_id=None) is None


def test_keep_ui_data_preserves_screen_keys():
    kept = keep_ui_data({"account_id": "acc", "ui_message_id": 9, "draft": {"x": 1}})
    assert kept["account_id"] == "acc"
    assert kept["ui_message_id"] == 9
    assert "draft" not in kept


def test_rich_message_has_in_text_buttons():
    msg = rich(
        heading("Test"),
        table(["A", "B"], [["1", "2"]]),
        button_row([("Открыть", "ads:v:1")]),
    )
    assert msg.blocks is not None
    types = [getattr(block, "type", None) or block.__class__.__name__ for block in msg.blocks]
    assert any("button" in str(t).lower() or "Buttons" in str(t) for t in types)


def test_formatters_and_errors():
    ad = Ad.model_validate(
        {
            "ad_id": 5,
            "title": "Hello world this is a long title",
            "currency": "TON",
            "promote_url": "https://t.me/x",
            "cpm": 1,
            "spent_budget": 0,
            "remaining_budget": 3,
            "views": 9,
            "actions": 0,
            "status": "in_review",
        }
    )
    rows = ads_table_rows([ad])
    assert rows[0][0] == "5"
    assert status_ru("in_review") == "на модерации"
    text = format_ads_error(AdsApiError("CHANNEL_ID_UNKNOWN"))
    assert "username" in text.lower() or "CHANNEL" in text
    assert "основного кабинета" in format_ads_error(AdsApiError("MAIN_ACCOUNT_REQUIRED"))
    assert "account_id" in format_ads_error(AdsApiError("ACCOUNT_ID_INVALID")).lower()
    parse = format_ads_error(
        AdsApiError(
            "RESPONSE_INVALID",
            method="getAccountTransactionsList",
            payload={"detail": [{"loc": ["next_offset"], "msg": "Input should be a valid string"}]},
        )
    )
    assert "getAccountTransactionsList" in parse
    assert "next_offset" in parse
    retarget = format_ads_error(AdsApiError("RETARGETING_DISABLED"))
    assert "ретаргет" in retarget.lower()
    assert error_needs_user_fix(AdsApiError("AD_TITLE_REQUIRED"))
    assert not error_needs_user_fix(AdsApiError("RETARGETING_DISABLED"))


def test_ad_cpc_cpa_and_full_rows():
    from bot.formatters import ad_cpa, ad_cpc, ad_ctr, ad_ecpm, ad_rows, ads_table_rows

    ad = Ad.model_validate(
        {
            "ad_id": 156,
            "title": "AI Posted",
            "currency": "TON",
            "promote_url": "https://t.me/ai_posted",
            "cpm": 0.1,
            "placement": "search_result",
            "spent_budget": 10,
            "remaining_budget": 1,
            "views": 1000,
            "clicks": 5,
            "actions": 2,
            "status": "active",
            "is_paused": False,
        }
    )
    assert ad_cpc(ad) == "2 TON"
    assert ad_cpa(ad) == "5 TON"
    assert ad_ctr(ad) == "0.50%"
    assert ad_ecpm(ad) == "10 TON"
    row = ads_table_rows([ad])[0]
    assert row[0] == "156"
    assert row[1] == "AI Posted"
    assert row[2] == "активно"
    assert row[4] == "1000"
    assert row[5] == "5"
    assert row[7] == "2 TON"
    assert row[9] == "5 TON"
    assert row[11] == "10 TON"
    assert row[12] == "1 TON"
    labels = [row[0] for row in ad_rows(ad)]
    assert "CPC" in labels
    assert "CPA" in labels
    assert "CTR" in labels
    assert "CVR" in labels
    assert "eCPM факт" in labels
    assert "Текст" in labels
    assert "Кнопка" in labels
    assert "Частота / день" in labels
    assert "Расписание" in labels
    assert "Тип таргетинга" in labels
    assert "Бюджет" in labels
    empty = Ad.model_validate(
        {
            "ad_id": 1,
            "title": "x",
            "currency": "TON",
            "promote_url": "https://t.me/x",
            "cpm": 1,
            "spent_budget": 0,
            "remaining_budget": 0,
            "views": 0,
            "actions": 0,
        }
    )
    assert ad_cpc(empty) == "—"
    assert ad_cpa(empty) == "—"


def test_parse_target_variants():
    search = _parse_target("search_result", "vpn, proxy")
    assert search.search_queries == ["vpn", "proxy"]
    bots = _parse_target("bot_banner", "@one,@two")
    assert bots.bot_ids == ["@one", "@two"]
    channels = _parse_target("channel_post", "en,ru")
    assert channels.language_codes == ["en", "ru"]
    users = _parse_target("video_banner", "RU, lang:en, device:ios")
    assert users.country_codes == ["RU"]
    assert users.language_codes == ["en"]
    assert users.device == "ios"


def test_create_steps_depend_on_placement():
    from bot.create_ad import next_step, parse_media, steps_for

    search_tg = steps_for("search_result", {"promote_url": "https://t.me/ai_posted"})
    assert "text" not in search_tg
    assert "media" not in search_tg
    assert "userpic" not in search_tg
    assert "website" not in search_tg
    assert "promote_url" in search_tg
    assert next_step("title", {"placement": "search_result", "promote_url": "https://t.me/x"}) == "promote_url"

    search_web = steps_for("search_result", {"promote_url": "https://example.com"})
    assert "website" in search_web

    channel = steps_for("channel_post", {"promote_url": "https://t.me/x"})
    assert "text" in channel
    assert "media" in channel
    assert "userpic" in channel

    bot = steps_for("bot_banner", {"promote_url": "https://t.me/mybot"})
    assert "text" in bot
    assert "media" not in bot
    assert "userpic" in bot

    video = steps_for("video_banner", {"promote_url": "https://t.me/x"})
    assert "media" in video
    assert "userpic" not in video
    assert parse_media("video_banner", "vid123") == {"photo_id": "vid123"}
    assert parse_media("video_banner", "video:vid9") == {"video_id": "vid9"}
    assert parse_media("channel_post", "ph123") == {"photo_id": "ph123"}
    assert parse_media("channel_post", "video:vid9") == {"video_id": "vid9"}
    assert next_step("budget", {"placement": "search_result"}) == "freq"
    assert next_step("budget", {"placement": "channel_post"}) == "media"
    from bot.create_ad import PLACEMENT_LABELS
    from bot.formatters import placement_ru

    assert PLACEMENT_LABELS["video_banner"] == "Юзеры"
    assert placement_ru("video_banner") == "юзеры"
    assert set(PLACEMENT_LABELS.values()) == {"Юзеры", "Каналы", "Поиск", "Боты"}



def test_every_ads_method_has_bot_entry():
    assert set(BOT_METHOD_COVERAGE) == ADS_API_METHOD_NAMES
    assert len(BOT_METHOD_COVERAGE) == 45


def test_create_ad_states_exist():
    assert CreateAdSG.placement
    assert CreateAdSG.confirm


@pytest.mark.asyncio
async def test_sqlite_fsm():
    path = Path("data") / "test_fsm.sqlite"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    storage = SqliteStorage(path)
    key = StorageKey(bot_id=1, chat_id=2, user_id=3)
    await storage.set_state(key, "CreateAdSG:title")
    await storage.set_data(key, {"draft": {"title": "x"}})
    assert await storage.get_state(key) == "CreateAdSG:title"
    data = await storage.get_data(key)
    assert data["draft"]["title"] == "x"
    await storage.set_state(key, None)
    assert await storage.get_state(key) is None
    await storage.close()


class _FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)


def _user_message(message_id: int, text: str = "/start"):
    from aiogram.types import Chat, User

    return Message.model_construct(
        message_id=message_id,
        date=0,
        chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=200, is_bot=False, first_name="A"),
        text=text,
    )


@pytest.mark.asyncio
async def test_commands_send_reply_and_never_delete_user_messages():
    from unittest.mock import AsyncMock

    from bot.rich import heading
    from bot.ui import _screens, current_bot, current_state, reply_rich

    _screens.clear()
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=type("M", (), {"message_id": 50})())
    bot.delete_message = AsyncMock()
    state = _FakeState()
    t_bot = current_bot.set(bot)
    t_state = current_state.set(state)
    try:
        await reply_rich(_user_message(1, "/start"), heading("One"), section="menu")
        assert bot.send_rich_message.await_count == 1
        send_kwargs = bot.send_rich_message.await_args.kwargs
        assert send_kwargs.get("reply_markup") is None
        assert send_kwargs.get("reply_parameters") is None
        bot.delete_message.assert_not_awaited()

        bot.send_rich_message.return_value = type("M", (), {"message_id": 51})()
        await reply_rich(_user_message(2, "/ads"), heading("Two"), section="ads")
        assert bot.send_rich_message.await_count == 2
        bot.delete_message.assert_not_awaited()
        assert state.data[UI_MESSAGE_KEY] == 51
    finally:
        current_bot.reset(t_bot)
        current_state.reset(t_state)
        _screens.clear()


@pytest.mark.asyncio
async def test_callback_edits_tapped_message():
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, User

    from bot.rich import heading
    from bot.ui import _screens, current_bot, current_state, reply_rich

    _screens.clear()
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock()
    bot.delete_message = AsyncMock()
    state = _FakeState({UI_MESSAGE_KEY: 50, UI_CHAT_KEY: 100})
    msg = Message.model_construct(
        message_id=50,
        date=0,
        chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=1, is_bot=True, first_name="Bot"),
    )
    cb = CallbackQuery.model_construct(
        id="1",
        data="nav:ads",
        from_user=User.model_construct(id=200, is_bot=False, first_name="A"),
        chat_instance="x",
        message=msg,
    )
    t_bot = current_bot.set(bot)
    t_state = current_state.set(state)
    try:
        await reply_rich(cb, heading("Ads"), section="ads")
        bot.send_rich_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()
        method = bot.await_args.args[0]
        assert method.__api_method__ == "editMessageText"
        assert method.message_id == 50
    finally:
        current_bot.reset(t_bot)
        current_state.reset(t_state)
        _screens.clear()


@pytest.mark.asyncio
async def test_reply_rich_sends_new_if_callback_edit_fails():
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import Chat, User

    from bot.rich import heading
    from bot.ui import _screens, current_bot, current_state, reply_rich

    _screens.clear()
    bot = AsyncMock()

    async def fail_edit(method):
        if getattr(method, "__api_method__", None) == "editMessageText":
            raise TelegramBadRequest(method=method, message="message can't be edited")
        return True

    bot.side_effect = fail_edit
    bot.send_rich_message = AsyncMock(return_value=type("M", (), {"message_id": 99})())
    bot.delete_message = AsyncMock()
    state = _FakeState({UI_MESSAGE_KEY: 50, UI_CHAT_KEY: 100})
    msg = Message.model_construct(
        message_id=50,
        date=0,
        chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=1, is_bot=True, first_name="Bot"),
    )
    cb = CallbackQuery.model_construct(
        id="1",
        data="nav:ads",
        from_user=User.model_construct(id=200, is_bot=False, first_name="A"),
        chat_instance="x",
        message=msg,
    )
    t_bot = current_bot.set(bot)
    t_state = current_state.set(state)
    try:
        await reply_rich(cb, heading("Ads"), section="ads")
        bot.send_rich_message.assert_awaited()
        bot.delete_message.assert_not_awaited()
        assert state.data[UI_MESSAGE_KEY] == 99
    finally:
        current_bot.reset(t_bot)
        current_state.reset(t_state)
        _screens.clear()


def test_paragraph_coerces_code_dict():
    from bot.rich import paragraph

    block = paragraph(["acc ", {"type": "code", "text": "abc"}])
    parts = block.text
    assert isinstance(parts, list)
    assert parts[1].type == "code"
    assert parts[1].text == "abc"


def test_button_row_rejects_long_callback():
    from bot.rich import button_row

    with pytest.raises(ValueError, match="64 bytes"):
        button_row([("x", "n" * 70)])


def test_money_appends_currency_once():
    from bot.formatters import money

    assert money(0.1, "TON") == "0.1 TON"
    assert money(0.1, "TON TON") == "0.1 TON"
    assert money(0.0022, "TON") == "0.0022 TON"
    assert money(1500, "TON") == "1 500.00 TON"
    assert money(None, "TON") == "—"


def _button_labels(blocks) -> list[str]:
    return [btn.text for block in blocks for btn in block.buttons]


def test_ad_card_hides_irrelevant_actions():
    from bot.keyboards import ad_card_blocks, ads_blocks

    active = _button_labels(ad_card_blocks(ad_id=156, status="active", is_paused=False))
    assert active.count("Пауза") == 1
    assert "Старт" not in active
    assert "На модерацию" not in active
    assert "Удалить" not in active
    assert "Править" in active
    assert "Бюджет" in active
    assert "Лента" in active
    assert "Правила" in active
    assert "JSON" in active
    assert "Сохранить" in active
    assert "A/B" in active
    assert "К списку" in active
    assert "Список" not in active
    assert "Создать" not in active

    paused = _button_labels(ad_card_blocks(ad_id=156, status="active", is_paused=True))
    assert "Пауза" not in paused
    assert "Старт" in paused
    assert "Удалить" not in paused

    review = _button_labels(ad_card_blocks(ad_id=3, status="ready_for_review", is_paused=False))
    assert "На модерацию" in review
    assert "Пауза" not in review
    assert "Удалить" in review

    hub = _button_labels(ads_blocks())
    assert hub == ["Список", "Создать", "Найти", "Сохранённые", "A/B", "Меню"]


def test_audience_and_pixel_cards_hide_delete_when_in_use():
    from bot.keyboards import audience_card_blocks, pixel_event_card_blocks

    free = _button_labels(audience_card_blocks(audience_id=1, ads_count=0))
    busy = _button_labels(audience_card_blocks(audience_id=1, ads_count=2))
    assert "Удалить" in free
    assert "Удалить" not in busy
    auto = _button_labels(pixel_event_card_blocks(event_id="ev1", auto_created=True, ads_count=0))
    assert "Удалить" not in auto


@pytest.mark.asyncio
async def test_reply_rich_back_does_not_append_section_hub():
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, User

    from bot.rich import heading
    from bot.ui import _screens, current_bot, current_state, reply_rich

    _screens.clear()
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock()
    state = _FakeState({UI_MESSAGE_KEY: 50, UI_CHAT_KEY: 100})
    msg = Message.model_construct(
        message_id=50,
        date=0,
        chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=1, is_bot=True, first_name="Bot"),
    )
    cb = CallbackQuery.model_construct(
        id="1",
        data="ads:view:156",
        from_user=User.model_construct(id=200, is_bot=False, first_name="A"),
        chat_instance="x",
        message=msg,
    )
    t_bot = current_bot.set(bot)
    t_state = current_state.set(state)
    try:
        await reply_rich(cb, heading("Ad"), back="ads")
        method = bot.await_args.args[0]
        labels = [
            btn.text
            for block in method.rich_message.blocks
            if getattr(block, "buttons", None)
            for btn in block.buttons
        ]
        assert labels == ["К списку", "Меню"]
        assert "Пауза" not in labels
        assert "Удалить" not in labels
    finally:
        current_bot.reset(t_bot)
        current_state.reset(t_state)
        _screens.clear()


def test_ad_json_export_includes_settings_and_computed():
    from bot.formatters import ad_json, ads_json

    ad = Ad.model_validate(
        {
            "ad_id": 156,
            "title": "AI Posted",
            "currency": "TON",
            "promote_url": "https://t.me/ai_posted",
            "cpm": 0.1,
            "placement": "search_result",
            "spent_budget": 10,
            "remaining_budget": 1,
            "views": 1000,
            "clicks": 5,
            "actions": 2,
            "status": "active",
            "is_paused": False,
            "text": "Hello",
            "button": "start_bot",
        }
    )
    payload = ad_json(ad)
    assert payload["ad_id"] == 156
    assert payload["title"] == "AI Posted"
    assert payload["text"] == "Hello"
    assert payload["promote_url"] == "https://t.me/ai_posted"
    assert payload["computed"]["cpc"] == 2.0
    assert payload["computed"]["cpa"] == 5.0
    assert payload["computed"]["ctr"] == 0.005
    assert payload["computed"]["cvr"] == 0.4
    assert payload["computed"]["ecpm"] == 10.0
    assert payload["computed"]["budget"] == 11.0
    bundle = ads_json([ad], total_count=3)
    assert bundle["total_count"] == 3
    assert len(bundle["ads"]) == 1
    assert bundle["ads"][0]["ad_id"] == 156


def test_ad_card_json_callback():
    from bot.keyboards import ad_card_blocks

    callbacks = [
        btn.callback_data
        for block in ad_card_blocks(ad_id=156, status="active", is_paused=False)
        for btn in block.buttons
    ]
    assert "ads:json:156" in callbacks
    assert "sv:ad:156" in callbacks
    assert "ab:ad:156" in callbacks
