import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Set
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_, or_

from config.settings import Settings
from bot.middlewares.i18n import JsonI18n
from db.models import User, Subscription
from bot.keyboards.inline.user_keyboards import get_referral_link_keyboard


class PeriodicNotificationsService:
    """Service for sending periodic promotional notifications to users."""

    # Days before expiry to send reminders
    RENEWAL_REMINDER_DAYS = [7, 3, 1, 0]

    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        i18n: JsonI18n,
        async_session_factory: sessionmaker,
    ):
        self.bot = bot
        self.settings = settings
        self.i18n = i18n
        self.async_session_factory = async_session_factory
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Start the periodic notification loop."""
        if self._running:
            logging.warning("PeriodicNotificationsService is already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._notification_loop())
        logging.info("PeriodicNotificationsService started")

    def stop(self):
        """Stop the periodic notification loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logging.info("PeriodicNotificationsService stopped")

    async def _notification_loop(self):
        """Main loop that sends notifications periodically."""
        # Wait a bit after startup before sending first notifications
        await asyncio.sleep(60)

        while self._running:
            try:
                await self._send_scheduled_notifications()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Error in periodic notifications loop: {e}", exc_info=True)

            # Sleep until next check (every 1 hour to catch optimal time windows)
            await asyncio.sleep(3600)  # 1 hour

    async def _send_scheduled_notifications(self):
        """Send all scheduled notifications."""
        now = datetime.now(timezone.utc)

        # Use Moscow time (UTC+3) for determining optimal hours
        moscow_hour = (now.hour + 3) % 24

        # Only send notifications during peak engagement hours (Moscow time):
        # 10-11 (morning), 13-14 (lunch), 19-20 (evening)
        optimal_hours = [10, 11, 13, 14, 19, 20]
        if moscow_hour not in optimal_hours:
            logging.debug(f"Skipping periodic notifications - not optimal hour (Moscow: {moscow_hour}:00)")
            return

        logging.info(f"Sending periodic notifications at optimal hour (Moscow: {moscow_hour}:00)")

        async with self.async_session_factory() as session:
            # Send referral reminders (once per day)
            if self.settings.PERIODIC_REFERRAL_NOTIFICATIONS_ENABLED:
                users_for_referral = await self._get_users_for_referral_reminder(session)
                await self._send_referral_reminders(session, users_for_referral)

            # Send renewal reminders (multiple stages: 7, 3, 1, 0 days before expiry)
            if self.settings.PERIODIC_RENEWAL_NOTIFICATIONS_ENABLED:
                await self._send_renewal_reminders_multi_stage(session)

    async def _get_users_for_referral_reminder(self, session: AsyncSession) -> List[User]:
        """Get active users who haven't been sent a referral reminder in the last day."""
        if not self.settings.PERIODIC_REFERRAL_NOTIFICATIONS_ENABLED:
            return []

        # Send referral reminders once per day
        cutoff_date = datetime.now(timezone.utc) - timedelta(hours=20)

        # Get users with active subscriptions who haven't received referral reminder recently
        query = select(User).where(
            and_(
                User.is_banned == False,
                or_(
                    User.last_referral_reminder_sent.is_(None),
                    User.last_referral_reminder_sent < cutoff_date
                )
            )
        ).limit(50)  # Limit to avoid rate limiting

        result = await session.execute(query)
        users = result.scalars().all()

        # Filter to only users with active subscriptions
        active_users = []
        for user in users:
            sub_query = select(Subscription).where(
                and_(
                    Subscription.user_id == user.user_id,
                    Subscription.is_active == True,
                    Subscription.end_date > datetime.now(timezone.utc)
                )
            )
            sub_result = await session.execute(sub_query)
            if sub_result.scalar():
                active_users.append(user)

        return active_users

    async def _send_renewal_reminders_multi_stage(self, session: AsyncSession):
        """Send renewal reminders at multiple stages: 7, 3, 1, 0 days before expiry."""
        now = datetime.now(timezone.utc)
        sent_count = 0

        # Get all active subscriptions without auto-renewal expiring within 8 days
        query = select(Subscription, User).join(User).where(
            and_(
                Subscription.is_active == True,
                Subscription.end_date > now,
                Subscription.end_date <= now + timedelta(days=8),
                Subscription.auto_renew_enabled == False,
                User.is_banned == False,
            )
        ).limit(100)

        result = await session.execute(query)
        subscriptions = result.all()

        for sub, user in subscriptions:
            try:
                # Calculate days left (rounded down)
                time_left = sub.end_date - now
                days_left = time_left.days

                # Check if this is a reminder day (7, 3, 1, 0)
                if days_left not in self.RENEWAL_REMINDER_DAYS:
                    continue

                # Check if we already sent a reminder for this stage
                # We use a 20-hour window to avoid duplicate sends within same day
                if user.last_renewal_reminder_sent:
                    hours_since_last = (now - user.last_renewal_reminder_sent).total_seconds() / 3600
                    if hours_since_last < 20:
                        continue

                lang = user.language_code or self.settings.DEFAULT_LANGUAGE
                _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw)

                end_date_str = sub.end_date.strftime("%d.%m.%Y")

                # Select message based on urgency
                if days_left == 0:
                    message = _("renewal_reminder_today", end_date=end_date_str)
                elif days_left == 1:
                    message = _("renewal_reminder_tomorrow", end_date=end_date_str)
                elif days_left == 3:
                    message = _("renewal_reminder_3_days", days_left=days_left, end_date=end_date_str)
                else:  # 7 days
                    message = _("renewal_reminder_7_days", days_left=days_left, end_date=end_date_str)

                from bot.keyboards.inline.user_keyboards import get_subscribe_only_markup
                keyboard = get_subscribe_only_markup(lang, self.i18n)

                await self.bot.send_message(
                    chat_id=user.user_id,
                    text=message,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

                # Update last reminder sent
                user.last_renewal_reminder_sent = now
                sent_count += 1

                await asyncio.sleep(0.5)

            except TelegramForbiddenError:
                logging.debug(f"User {user.user_id} blocked the bot, skipping renewal reminder")
            except TelegramBadRequest as e:
                logging.debug(f"Failed to send renewal reminder to {user.user_id}: {e}")
            except Exception as e:
                logging.error(f"Error sending renewal reminder to {user.user_id}: {e}")

        await session.commit()
        if sent_count > 0:
            logging.info(f"Sent {sent_count} renewal reminders")

    async def _send_referral_reminders(self, session: AsyncSession, users: List[User]):
        """Send referral program reminders to users."""
        sent_count = 0
        for user in users:
            try:
                lang = user.language_code or self.settings.DEFAULT_LANGUAGE
                _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw)

                # Generate referral link
                ref_code = user.referral_code
                if not ref_code:
                    continue

                bot_info = await self.bot.get_me()
                referral_link = f"https://t.me/{bot_info.username}?start=ref_u{ref_code}"

                message = _("periodic_referral_reminder", referral_link=referral_link)
                keyboard = get_referral_link_keyboard(lang, self.i18n)

                await self.bot.send_message(
                    chat_id=user.user_id,
                    text=message,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

                # Update last reminder sent
                user.last_referral_reminder_sent = datetime.now(timezone.utc)
                sent_count += 1

                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)

            except TelegramForbiddenError:
                logging.debug(f"User {user.user_id} blocked the bot, skipping referral reminder")
            except TelegramBadRequest as e:
                logging.debug(f"Failed to send referral reminder to {user.user_id}: {e}")
            except Exception as e:
                logging.error(f"Error sending referral reminder to {user.user_id}: {e}")

        await session.commit()
        if sent_count > 0:
            logging.info(f"Sent {sent_count} referral reminders")
