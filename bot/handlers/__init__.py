from bot.handlers.ab import router as ab_router
from bot.handlers.accounts import router as accounts_router
from bot.handlers.ads import router as ads_router
from bot.handlers.audiences import router as audiences_router
from bot.handlers.billing import router as billing_router
from bot.handlers.budget import router as budget_router
from bot.handlers.library import router as library_router
from bot.handlers.media import router as media_router
from bot.handlers.menu import router as menu_router
from bot.handlers.pixels import router as pixels_router
from bot.handlers.rules import router as rules_router
from bot.handlers.stats import router as stats_router
from bot.handlers.targeting import router as targeting_router

routers = (
    menu_router,
    accounts_router,
    ads_router,
    library_router,
    ab_router,
    billing_router,
    budget_router,
    audiences_router,
    pixels_router,
    rules_router,
    media_router,
    stats_router,
    targeting_router,
)
