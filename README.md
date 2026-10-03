# Telegram Ads Manager

Bot UI for Telegram Ads, plus a standalone **Telegram Ads API** client that an agent can import later. There is **no ad-creating agent** in this repo.

## Architecture

```
ads_api/     Telegram Ads API only (https://promoteapi.telegram.org)
             No Bot API imports. Typed client covering every documented method.

bot/         Telegram Bot API UI (aiogram, Bot API 10.3 rich messages)
             Depends on ads_api, never the reverse.
```

Official Ads API methods live in `ads_api/catalog.py` (parsed from `docs.html` / [ads.telegram.org/docs/api](https://ads.telegram.org/docs/api)). The public facade is `AdsClient`.

Telegram Ads has **accounts + ads** (no campaign object). Targeting, pixels, audiences, budgets, stats, and dictionaries are first-class.

## Setup

Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
copy .env.example .env
```

Fill `.env`:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ADS_ACCESS_TOKEN` | Ads API token from [ads.telegram.org/account/api](https://ads.telegram.org/account/api) |
| `TELEGRAM_ADS_ACCOUNT_ID` | Optional default advertiser account |
| `BOT_ALLOWED_USER_IDS` | Optional comma-separated Telegram user ids |

Never commit `.env`.

## Run the bot

```bash
python -m bot
```

or `python -m bot.main` / `ads-bot` after install.

The bot uses long polling. While it talks to Ads API or builds tables it sends `sendChatAction` (`typing` / upload). Conversation state is stored in `data/fsm.sqlite`.

## Use the Ads client from code (for a future agent)

```python
import asyncio
from ads_api import AdsClient
from ads_api.models import InputAdTargetSearch

async def main():
    async with AdsClient.from_env() as ads:
        account = await ads.get_current_account()
        ad = await ads.create_ad(
            title="Search VPN",
            promote_url="https://t.me/example",
            cpm=1.5,
            placement="search_result",
            target=InputAdTargetSearch(search_queries=["vpn"]),
        )
        print(account.title, ad.ad_id)

asyncio.run(main())
```

CamelCase aliases from the docs also work: `await ads.createAd(...)`, `await ads.getAdsList(...)`.

## Bot UX

- Commands (`/start`, `/ads`, …) send a new panel message (not as a reply). Buttons update the panel you tapped.
- User messages are never deleted.
- Primary actions are blue, create/confirm are green, delete/cancel are red.
- Buttons inside the bot message update that same message.
- There is no reply keyboard and no inline keyboard under the message.
- Rich tables, blockquotes, `<pre>`, spoilers/details, copy-id buttons.
- Russian UI copy; Python identifiers in English.
- In wizards: «Отмена» / «Пропустить» / «Меню» are in-message buttons. `/cancel` also works.

`editAdStatus` is deprecated in the Ads docs; the bot pauses/resumes with `editAd(is_paused=...)`. The client still exposes `edit_ad_status` for completeness.

## Tests

```bash
pytest
```

HTTP to Ads API is mocked (`respx`). No live Ads credentials required.

## Ads API notes

- Base URL: `https://promoteapi.telegram.org/<method>`
- Auth: `Authorization: Bearer <token>`
- Idempotency: header `Idempotency-Key` or parameter `idempotency_key`
- Uploads: `multipart/form-data`
- Channel/bot numeric ids work only after the same account resolved them via `@username`
