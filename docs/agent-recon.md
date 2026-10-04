# Разведка для модуля предложений (фаза 0)

Дата: 2026-10-04. Источники: код репозитория (`README.md`, `ads_api/`, `bot/`, `tests/`, `data/`) и
локальная копия официальной документации `docs.html` (снимок страницы ads.telegram.org/docs/api,
последняя запись в «Recent changes» — September 18, 2026).

Живых вызовов Ads API не было: токена у исполнителя нет. Для вопросов, которые документация не
закрывает, есть read-only скрипт `scripts/agent_recon.py` (см. раздел 8). Он не делает ни одного
изменяющего вызова.

Обозначения статуса:
- **подтверждено (docs)** — прямо написано в `docs.html`;
- **подтверждено (код)** — так реализовано в репозитории, документация не противоречит;
- **открыто** — нужна проверка read-only вызовом или ответ владельца.

---

## 1. Как бот хранит данные

| Файл | Где создаётся | Что лежит |
|---|---|---|
| `data/fsm.sqlite` (`BOT_FSM_PATH`) | `bot/storage.py`, таблица `fsm(key, state, data)` | состояние мастеров aiogram и id текущей панели (`ui_message_id`, `ui_chat_id`, выбранный `account_id`) |
| `data/rules.sqlite` | `bot/rules.py`, таблица `ad_rules` | правила автостопа: ad_id, метрика, порог, статус, последнее значение |
| `data/library.sqlite` | `bot/library.py`, таблицы `library_groups`, `library_ads` | «Сохранённые»: группы и **полный JSON объявления на момент сохранения** (`payload`) |
| `data/ab.sqlite` | `bot/ab.py`, таблица `ab_tests` | A/B-сравнения: снимок метрик «до» (`baseline`) и «после» (`followup`) |

Все файлы — соседи `BOT_FSM_PATH` (`Path(fsm_path).with_name(...)`), создаются автоматически, в git
не попадают (`.gitignore`: `*.sqlite`, `data/*`). README упоминает только `fsm.sqlite`; остальные три
базы есть только в коде.

Вывод для агента:
- истории объявлений и статистики бот **не хранит** — только снимки, которые пользователь сохранил
  вручную (`library_ads.payload`) и пары снимков A/B. Синхронизацию с нуля делает `agent/sync`.
- `library_ads.payload` и `ab_tests.baseline` можно в будущем использовать как дополнительный источник
  истории удалённых объявлений (не реализовано, см. вопросы).
- агент пишет в отдельный файл `data/agent.sqlite`, базы бота не трогает (раздел 5 плана).

## 2. Семантика `Account.remaining_budget` / `ads_budget` — **открыто**

Что сказано в `docs.html` (тип `Account`):
- `spent_budget` — «Spent amount in the account's currency»;
- `remaining_budget` — «Remaining amount in the account's currency»;
- `ads_budget` — «Remaining amount in the account's currency on advertisement budgets».

Косвенные указания:
- `createAd.initial_budget` и `increaseAdBudget.amount`: «will be added to the ad budget **from the
  account budget**. The account budget must have enough funds». `decreaseAdBudget`: «withdrawn from the
  ad budget **to the account budget**». То есть деньги перемещаются между кабинетом и объявлением.
- `AccountTransaction.amount`: «A negative amount represents a withdrawal from the account budget»;
  у транзакции есть peer `ad` — пополнение объявления видно в ленте кабинета как списание.
- Шапка `docs.html` показывает «Budget: 💎 87.65» — то же число, что `remaining_budget` в фикстуре
  `tests/conftest.py` (87.65 при `ads_budget` 10.0). Это совпадение фикстуры с UI, а не доказательство.
- Бот (`bot/formatters.py::account_rows`) подписывает поля как «Остаток» и «В объявлениях», ничего не
  вычитая.

Две гипотезы:
- **H1 (`excludes_ads`)**: `remaining_budget` — свободные деньги кабинета, бюджеты объявлений уже
  вычтены; `ads_budget ≈ Σ Ad.remaining_budget`. Тогда `free = remaining_budget − Σ резервов`.
  Формулировки «from the account budget» и отрицательные транзакции с peer `ad` говорят в пользу H1.
- **H2 (`includes_ads`)**: `remaining_budget` включает деньги на бюджетах объявлений. Тогда
  `free = remaining_budget − Σ remaining_budget живых объявлений − Σ резервов` (формула раздела 8 плана).

Решение в коде (фаза 1): формула изолирована в одной функции
`agent/planner/balance.py::compute_free_balance` с пометкой `[VERIFY]`. Режим задаётся
`AGENT_BALANCE_SEMANTICS`, по умолчанию **`includes_ads` (H2)** — она консервативнее: при любой
истинной семантике свободный баланс не будет завышен, в худшем случае занижен. Переключить на
`excludes_ads` владелец может после запуска `scripts/agent_recon.py`.

Как скрипт закрывает вопрос: печатает `getCurrentAccount`, `Σ Ad.remaining_budget` по всем
страницам `getAdsList`, разницу с `ads_budget` и сумму `getAccountTransactionsList` по типам peer.
- если `ads_budget ≈ Σ Ad.remaining_budget` и `Σ транзакций (external + ad + …) ≈ remaining_budget` →
  H1;
- если `remaining_budget ≈ Σ external − spent_budget` (т.е. включает деньги на объявлениях) → H2.

## 3. `initial_budget` в `createAd` — **подтверждено (docs)**, серверное поведение не проверено

`docs.html`, `createAd`: `initial_budget` Float, Optional — «Amount in the account's currency that will
be added to the ad budget from the account budget. The account budget must have enough funds. The value
will be rounded to the cpm & budget precision… Defaults to 0.» Клиент (`AdsClient.create_ad`) передаёт
его как есть, бот-мастер тоже (`bot/handlers/ads.py::create_confirm`).

Следствия:
- нехватка средств кабинета — ошибка API на `createAd` (точный код в документации не назван; клиент
  отдаст его в `AdsApiError.error`);
- `submitAdForReview`: «Ads are submitted for review automatically when they are created or edited,
  provided the ad's budget is not empty. If an ad has the “ready_for_review” status, it will not be
  submitted automatically». Значит при `initial_budget > 0` ревью обычно стартует само, а
  `submit_ad_for_review` нужен только для статуса `ready_for_review` — так и сделано в исполнителе.

## 4. Связь `placement` ↔ `target.type` — **открыто**

Что сказано в `docs.html`:
- `placement` — одно из `channel_post`, `bot_banner`, `search_result`, `video_banner`; формально
  Required, но временно опционален: «if not specified, the value will be inferred from target.type».
  Таблицы соответствия в документации **нет**.
- `photo_id`/`video_id`: «Applicable for targeting types channels or users only».
- `use_viewer_timezone` в расписании: «Applicable for targeting type users only».

Что в коде:
- `ads_api/enums.py::PLACEMENT_DEFAULT_TARGET`: `channel_post→channels`, `bot_banner→bots`,
  `search_result→search`, `video_banner→users`;
- мастер бота (`bot/create_ad.py`) выбирает размещение, а таргет строит по нему же:
  `video_banner` подписан «Юзеры», синонимы «users», «пользователи», «видео».

Вывод: в репозитории пара для таргета `users` — `video_banner`, но это код, а не документация.
Владелец ранее запускал объявления с таргетом `users` — их `placement` покажет `scripts/agent_recon.py`
(таблица пар `placement × target.type` по всем объявлениям). До ответа валидатор (фаза 2) пропускает
только пары из `PLACEMENT_DEFAULT_TARGET`; расширение — правкой одной константы
`agent/validator/rules.py::ALLOWED_PLACEMENT_TARGETS`.

## 5. Лимиты текста и полей — **подтверждено (docs)**

| Поле | Ограничение |
|---|---|
| `title` | 1–128 **байт**, видно только в кабинете |
| `text` | 1–160 **символов**; обязателен для всех типов таргета, кроме `search` |
| `promote_url` | 1–256 символов |
| `website_name` | 1–40 символов, обязателен для внешней ссылки |
| `button` | «Applicable only if the URL you want to promote is an external link» — для `t.me` не применяется |
| `additional_info` | 0–64 символа |
| `impression_frequency` | 1–4, по умолчанию 1 |
| `cpm`, бюджеты | округляются до 2 знаков (TON/EUR), 0 (XTR); суммы переводов — 5 знаков (TON/EUR) |
| `users` | до 8 стран, до 20 локаций (одна страна), до 8 языков, темы вкл.+искл. ≤ 20, каналы вкл.+искл. ≤ 100, аудитории ≤ 4 (+искл. ≤ 10 всего) |
| `channels` | до 8 языков; при `topic_ids` — ровно один язык; темы ≤ 20; каналы ≤ 100; при `channel_ids` темы и языки пустые |
| `search` | до 10 запросов |
| фото | JPEG/PNG ≤ 5 MB, ширина ≥ 640, 16:9 |
| `activate_date`/`deactivate_date` | в будущем, не дальше 365 дней |
| `getAdStats`/`getAccountStats` | `interval ∈ {300, 86400}`, период ≤ 1000·interval |
| `decreaseAdBudget`, `deleteAd` | объявление неактивно ≥ 10 минут |

Важное для продукта: продвигаемая ссылка `https://t.me/premierleaguedaily` — не внешняя, поэтому
`button` и `website_name` к ней неприменимы. Фактор «кнопка» из раздела 9 плана для канала владельца
не работает; валидатор отклоняет `button` при `t.me`-ссылке (см. вопросы).

## 6. Идемпотентность — **подтверждено (docs + код)**

- Ключ — заголовок `Idempotency-Key` или параметр `idempotency_key`, до 128 символов (UUID v4 = 36).
- Ответ сохраняется и повторяется 24 часа; повтор помечен заголовком `Idempotent-Replayed: true`.
- Параметры повторного запроса обязаны совпадать, иначе `IDEMPOTENT_PARAM_MISMATCH`; параллельный
  повтор — `IDEMPOTENT_REQUEST_IN_PROGRESS`; если запрос не прошёл валидацию, ответ не сохраняется.
- Клиент (`ads_api/client.py::request`) переносит `idempotency_key` из параметров в заголовок,
  ретраит транспортные ошибки, 429 (с `Retry-After`) и `IDEMPOTENT_REQUEST_IN_PROGRESS`.
- Идемпотентны по документации: `createAd`, `increaseAdBudget`, `decreaseAdBudget`,
  `increase/decreaseAccountBudget`, `createAccount`, `createAudience`, `createPixel`,
  `createPixelEvent`. У `submitAdForReview` и `editAd` параметра нет — повтор безопасен по смыслу
  (`submit` допустим только из `ready_for_review`, пауза — идемпотентна по значению).

Следствия для исполнителя: `idempotency_key = proposal_id`; payload должен сериализоваться
детерминированно (иначе `IDEMPOTENT_PARAM_MISMATCH` на ретрае); ретраи после 24 часов не защищены
ключом, поэтому `created_ad_id` записывается в БД и проверяется до любого повторного вызова.

## 7. Прочие наблюдения

- **Мультиаккаунт.** Почти все методы принимают `account_id`; бот хранит выбранный кабинет в FSM.
  Агент работает с одним кабинетом (`TELEGRAM_ADS_ACCOUNT_ID` или текущий токен) и сам `account_id`
  в payload не кладёт.
- **Существующие пути изменения Ads в боте.** Мастер `/create_ad` (ручное создание), кнопки
  «Пауза/Бюджет/Удалить» в карточке объявления и `bot/watcher.py` (автостоп по правилам пользователя,
  вызывает `edit_ad(is_paused=True)`). Это ручные инструменты владельца, они были до агента.
  Инварианты I1/I7 относятся к агенту: в `agent/` вызовы мутаций есть только в `agent/executor`, это
  проверяет тест. Нужно ли ограничивать старые пути — вопрос владельцу.
- **Allow-list бота.** `AllowlistMiddleware`: пустой `BOT_ALLOWED_USER_IDS` = доступ всем. Для I1
  этого мало: `ProposalService.accept` при пустом списке отказывает всем.
- **Конверсии.** `Ad.actions` считается по `action_type`; «If action_type is not set, this value will
  be 0». Для канала ожидается `action_type=join`; синхронизация сохраняет `action_type`, чтобы модель
  не приняла 0 за «нет вступлений» у объявлений без трекинга.
- **История.** `getAccountReport` — только активные объявления; `AccountTransactionPeerAd.ad_is_deleted`
  показывает, что удалённые объявления остаются в ленте. Снапшоты в `ad_snapshots` защищают от потери.
- **Тесты.** 57 тестов, HTTP мокается `respx` с `base_url=https://promoteapi.telegram.org`, ответы
  `{"ok": true, "result": ...}`; бот тестируется через `AsyncMock` бота и `model_construct` апдейтов.

## 8. Read-only скрипт `scripts/agent_recon.py`

```bash
python scripts/agent_recon.py            # берёт TELEGRAM_ADS_ACCESS_TOKEN из .env
python scripts/agent_recon.py --json     # машинно-читаемый вывод
```

Вызывает только `getCurrentAccount`, `getAdsList` (с `return_target=True`, все страницы) и
`getAccountTransactionsList` (все страницы). Методы берутся из белого списка внутри скрипта; любой
другой метод вызывает исключение до отправки запроса. Токен не печатается.

Печатает:
1. поля `Account`;
2. `Σ Ad.remaining_budget` (всего и по живым статусам), сравнение с `ads_budget` и `remaining_budget`;
3. суммы транзакций кабинета по типам peer;
4. оценку гипотез H1/H2;
5. таблицу пар `placement × target.type` по существующим объявлениям и максимальную длину текста;
6. распределение `action_type`.

## 9. Сводка статусов [VERIFY]

| Вопрос | Статус |
|---|---|
| Семантика `remaining_budget`/`ads_budget` | **открыто**, консервативная H2 по умолчанию, проверка — `scripts/agent_recon.py` |
| `initial_budget` в `createAd` | подтверждено (docs); фактическое списание видно после первого живого объявления |
| Пары `placement` ↔ `target.type` (особенно `users`) | **открыто**; в коде `users ↔ video_banner`; проверка — таблица пар в скрипте |
| Лимит текста | подтверждено (docs): 1–160 символов, не нужен для `search` |
| Что хранит бот | подтверждено (код): 4 SQLite-файла, истории статистики нет |
| Правила идемпотентности | подтверждено (docs): 128 символов, 24 часа, совпадение параметров |
