import logging
from typing import Dict

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from bot.middlewares.db_session import DBSessionMiddleware
from bot.middlewares.i18n import I18nMiddleware, get_i18n_instance, JsonI18n
from bot.middlewares.ban_check_middleware import BanCheckMiddleware
from bot.middlewares.action_logger_middleware import ActionLoggerMiddleware
from bot.middlewares.profile_sync import ProfileSyncMiddleware
from bot.middlewares.channel_subscription import ChannelSubscriptionMiddleware
from bot.middlewares.rate_limit import TelegramRateLimitMiddleware


def build_dispatcher(settings: Settings, async_session_factory: sessionmaker) -> tuple[Dispatcher, Bot, Dict]:
    storage = MemoryStorage()
    default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=settings.BOT_TOKEN, default=default_props)

    dp = Dispatcher(storage=storage, settings=settings, bot_instance=bot)

    i18n_instance = get_i18n_instance(path="locales", default=settings.DEFAULT_LANGUAGE)

    dp["i18n_instance"] = i18n_instance
    dp["async_session_factory"] = async_session_factory

    dp.update.outer_middleware(DBSessionMiddleware(async_session_factory))
    dp.update.outer_middleware(I18nMiddleware(i18n=i18n_instance, settings=settings))
    dp.update.outer_middleware(ProfileSyncMiddleware())
    dp.update.outer_middleware(BanCheckMiddleware(settings=settings, i18n_instance=i18n_instance))
    dp.update.outer_middleware(ChannelSubscriptionMiddleware(settings=settings, i18n_instance=i18n_instance))
    dp.update.outer_middleware(ActionLoggerMiddleware(settings=settings))

    # Rate limiting middleware (protects against spam/flood)
    if settings.RATE_LIMIT_ENABLED:
        rate_limit_mw = TelegramRateLimitMiddleware(
            messages_per_minute=settings.RATE_LIMIT_MESSAGES_PER_MINUTE,
            callbacks_per_minute=settings.RATE_LIMIT_CALLBACKS_PER_MINUTE,
            admin_ids=set(settings.ADMIN_IDS),
            enabled=True,
        )
        dp.message.middleware(rate_limit_mw)
        dp.callback_query.middleware(rate_limit_mw)
        logging.info("Rate limiting middleware enabled")

    return dp, bot, {"i18n_instance": i18n_instance}

