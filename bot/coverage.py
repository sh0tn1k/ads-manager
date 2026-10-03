"""Map every Ads API method to a bot command, menu or FSM flow."""

from __future__ import annotations

from ads_api.catalog import ADS_API_METHOD_NAMES

# Human-readable bot entry for each Ads method (used in tests + /help).
BOT_METHOD_COVERAGE: dict[str, str] = {
    "createAccount": "Аккаунт → Создать аккаунт (мастер)",
    "editAccountInfo": "Аккаунт → Изменить реквизиты",
    "getAccountInfo": "Аккаунт → Реквизиты",
    "getRelatedAccountsList": "Аккаунт → Связанные аккаунты",
    "increaseAccountBudget": "Биллинг → Пополнить связанный",
    "decreaseAccountBudget": "Биллинг → Вывести со связанного",
    "getTransactionStatus": "Биллинг → Статус перевода",
    "getCurrentAccount": "Аккаунт → Текущий аккаунт / /account",
    "getAccountsById": "Аккаунт → Выбрать аккаунт (загрузка по id)",
    "getAccountTransactionsList": "Биллинг → Лента аккаунта",
    "getAccountStats": "Статистика → Статистика аккаунта",
    "getAccountReport": "Статистика → Отчёт за месяц",
    "uploadAdPhoto": "Медиа → Загрузить фото объявления",
    "uploadAdVideo": "Медиа → Загрузить видео",
    "uploadWebsitePhoto": "Медиа → Загрузить фото сайта",
    "createAd": "➕ Создать объявление / /create_ad",
    "editAd": "Объявления → редактирование / пауза / возобновить",
    "editAdStatus": "Deprecated: то же, что пауза через editAd(is_paused)",
    "increaseAdBudget": "Объявления → Бюджет объявления (+)",
    "decreaseAdBudget": "Объявления → Бюджет объявления (-)",
    "submitAdForReview": "Объявления → Отправить на модерацию",
    "deleteAd": "Объявления → Удалить объявление",
    "getAdsById": "Объявления → Найти объявление / карточка",
    "getAdsList": "/ads / Список объявлений",
    "getAdTransactionsList": "Объявления → Транзакции объявления",
    "getAdStats": "Статистика → Статистика объявления",
    "createAudience": "Аудитории → Создать аудиторию",
    "editAudience": "Аудитории → Изменить аудиторию",
    "deleteAudience": "Аудитории → Удалить аудиторию",
    "getAudiencesById": "Карточка аудитории (кнопка в сообщении)",
    "getAudiencesList": "Аудитории → Список аудиторий",
    "createPixel": "Пиксель → Создать пиксель",
    "getPixel": "Пиксель → Показать пиксель",
    "createPixelEvent": "Пиксель → Создать событие",
    "editPixelEvent": "Пиксель → Переименовать событие",
    "deletePixelEvent": "Пиксель → Удалить событие",
    "getPixelEventsById": "Карточка события пикселя",
    "getPixelEventsList": "Пиксель → События",
    "getTargetLanguagesList": "Таргетинг → Языки",
    "getTargetTopicsList": "Таргетинг → Темы",
    "getTargetCountriesList": "Таргетинг → Страны",
    "getTargetLocationsById": "Таргетинг → Локации (ids 1,2,3)",
    "getTargetLocationsList": "Таргетинг → Локации (ISO + запрос)",
    "getTargetChannel": "Таргетинг → Канал",
    "getTargetBot": "Таргетинг → Бот",
}

assert set(BOT_METHOD_COVERAGE) == ADS_API_METHOD_NAMES
