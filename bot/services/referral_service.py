import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any
from aiogram import Bot
from datetime import datetime, timezone, timedelta

from config.settings import Settings
from db.dal import user_dal
from db.dal import payment_dal
from db.models import User
from db.dal import subscription_dal
from bot.middlewares.i18n import JsonI18n
from .subscription_service import SubscriptionService


class ReferralService:

    def __init__(self, settings: Settings,
                 subscription_service: SubscriptionService, bot: Bot,
                 i18n: JsonI18n):
        self.settings = settings
        self.subscription_service = subscription_service
        self.bot = bot
        self.i18n = i18n

    async def apply_referral_bonuses_for_payment(
            self,
            session: AsyncSession,
            referee_user_id: int,
            purchased_subscription_months: int,
            current_payment_db_id: Optional[int] = None,
            skip_if_active_before_payment: bool = True) -> Dict[str, Any]:

        referee_final_end_date: Optional[datetime] = None
        referee_bonus_applied_days: Optional[int] = None
        inviter_bonus_successfully_applied = False

        try:
            referee_user_model = await user_dal.get_user_by_id(
                session, referee_user_id)
            if not referee_user_model or referee_user_model.referred_by_id is None:
                logging.debug(
                    f"User {referee_user_id} not referred or inviter ID missing. No referral bonuses."
                )
                return {
                    "referee_bonus_applied_days": None,
                    "referee_new_end_date": None
                }

            # Note: First purchase check moved to bonus calculation section below
            # to allow inviter bonuses on subsequent purchases

            # Note: skip_if_active_before_payment check removed
            # Inviter should still get bonus even if referee has active subscription
            # Referee bonus is controlled by is_first_purchase check below

            inviter_user_id = referee_user_model.referred_by_id
            inviter_user_model = await user_dal.get_user_by_id(
                session, inviter_user_id)

            referee_name_for_msg = referee_user_model.first_name or f"User {referee_user_id}"

            default_lang_for_placeholder = self.settings.DEFAULT_LANGUAGE
            inviter_name_for_referee_msg = (
                inviter_user_model.first_name if inviter_user_model
                and inviter_user_model.first_name else self.i18n.gettext(
                    default_lang_for_placeholder, "friend_placeholder"))

            # Check if this is the referee's first purchase
            is_first_purchase = True
            try:
                succeeded_count = await payment_dal.count_user_succeeded_payments(
                    session, referee_user_id, exclude_payment_id=current_payment_db_id
                )
                is_first_purchase = (succeeded_count is None or succeeded_count == 0)
            except Exception as e_cnt:
                logging.error(f"Failed counting succeeded payments for user {referee_user_id}: {e_cnt}")
            
            # Determine bonuses based on first purchase or not
            if is_first_purchase:
                # First purchase: inviter gets one-time bonus (default 15 days)
                inviter_bonus_days = getattr(self.settings, 'REFERRAL_FIRST_PURCHASE_BONUS', 15)
                # Referee already got bonus at registration, so no bonus here
                referee_bonus_days = None
                logging.info(f"First purchase by referee {referee_user_id}: inviter gets {inviter_bonus_days} days (referee got bonus at registration)")
            else:
                # Subsequent purchases: inviter gets bonus based on subscription period
                inviter_bonus_days = self.settings.referral_bonus_inviter.get(
                    purchased_subscription_months)
                # Referee gets nothing on subsequent purchases
                referee_bonus_days = None
                logging.info(f"Subsequent purchase by referee {referee_user_id}: inviter gets {inviter_bonus_days} days, referee gets nothing")

            if inviter_bonus_days and inviter_bonus_days > 0:
                if not inviter_user_model:

                    logging.warning(
                        f"Inviter user {inviter_user_id} not found in local DB. Cannot apply inviter bonus."
                    )
                else:

                    inviter_panel_uuid, inviter_panel_sub_link_id, _, _ = await self.subscription_service._get_or_create_panel_user_link_details(
                        session, inviter_user_id, inviter_user_model)

                    if not inviter_panel_uuid:
                        logging.warning(
                            f"Failed to get/create panel link for inviter {inviter_user_id}. Cannot apply inviter bonus directly to panel."
                        )

                    else:
                        new_end_date_inviter = await self.subscription_service.extend_active_subscription_days(
                            session=session,
                            user_id=inviter_user_id,
                            bonus_days=inviter_bonus_days,
                            reason=f"referral bonus from {referee_name_for_msg}"
                        )

                        if new_end_date_inviter:
                            inviter_bonus_successfully_applied = True
                            logging.info(
                                f"Bonus of {inviter_bonus_days} days successfully applied/extended for inviter {inviter_user_id}."
                            )

                            try:
                                inviter_lang = inviter_user_model.language_code or default_lang_for_placeholder
                                _i = lambda k, **kw: self.i18n.gettext(
                                    inviter_lang, k, **kw)
                                from bot.keyboards.inline.user_keyboards import get_referral_bonus_keyboard
                                keyboard = get_referral_bonus_keyboard(inviter_lang, self.i18n)
                                await self.bot.send_message(
                                    inviter_user_id,
                                    _i("referral_bonus_inviter_notification_extended",
                                       days=inviter_bonus_days,
                                       referee_name=referee_name_for_msg,
                                       new_end_date=new_end_date_inviter.
                                       strftime('%Y-%m-%d')),
                                    reply_markup=keyboard)
                            except Exception as e_notify_inviter:
                                logging.error(
                                    f"Failed to send bonus notification to inviter {inviter_user_id}: {e_notify_inviter}"
                                )
                        else:

                            logging.info(
                                f"Inviter {inviter_user_id} has no active sub to extend. Creating new bonus subscription for {inviter_bonus_days} days."
                            )

                            bonus_start_date = datetime.now(timezone.utc)
                            bonus_end_date = bonus_start_date + timedelta(
                                days=inviter_bonus_days)

                            if not inviter_panel_sub_link_id:
                                logging.error(
                                    f"Cannot create bonus subscription for inviter {inviter_user_id}: panel_sub_link_id is missing even after link detail fetch."
                                )
                            else:
                                bonus_sub_payload = {
                                    "user_id":
                                    inviter_user_id,
                                    "panel_user_uuid":
                                    inviter_panel_uuid,
                                    "panel_subscription_uuid":
                                    inviter_panel_sub_link_id,
                                    "start_date":
                                    bonus_start_date,
                                    "end_date":
                                    bonus_end_date,
                                    "duration_months":
                                    0,
                                    "is_active":
                                    True,
                                    "status_from_panel":
                                    "ACTIVE_BONUS",
                                    "traffic_limit_bytes":
                                    self.settings.user_traffic_limit_bytes,
                                    "auto_renew_enabled":
                                    False,
                                }
                                try:
                                    await subscription_dal.deactivate_other_active_subscriptions(
                                        session, inviter_panel_uuid,
                                        inviter_panel_sub_link_id)
                                    bonus_sub = await subscription_dal.upsert_subscription(
                                        session, bonus_sub_payload)

                                    panel_update_success = await self.subscription_service.panel_service.update_user_details_on_panel(
                                        inviter_panel_uuid, {
                                            "expireAt":
                                            bonus_end_date.isoformat(
                                                timespec='milliseconds').
                                            replace('+00:00', 'Z'),
                                            "status":
                                            "ACTIVE",
                                        })
                                    if panel_update_success:
                                        inviter_bonus_successfully_applied = True
                                        logging.info(
                                            f"New bonus subscription for {inviter_bonus_days} days created for inviter {inviter_user_id}."
                                        )

                                        inviter_lang = inviter_user_model.language_code or default_lang_for_placeholder
                                        _i = lambda k, **kw: self.i18n.gettext(
                                            inviter_lang, k, **kw)
                                        from bot.keyboards.inline.user_keyboards import get_referral_bonus_keyboard
                                        keyboard = get_referral_bonus_keyboard(inviter_lang, self.i18n)
                                        await self.bot.send_message(
                                            inviter_user_id,
                                            _i("referral_bonus_inviter_notification_new_sub",
                                               days=inviter_bonus_days,
                                               referee_name=
                                               referee_name_for_msg,
                                               new_end_date=bonus_end_date.
                                               strftime('%Y-%m-%d')),
                                            reply_markup=keyboard)
                                    else:
                                        logging.warning(
                                            f"Failed to update panel for new bonus subscription for inviter {inviter_user_id}. Local bonus sub created (ID: {bonus_sub.subscription_id}) but may not be active on panel."
                                        )

                                except Exception as e_create_bonus_sub:
                                    logging.error(
                                        f"Failed to create new bonus subscription for inviter {inviter_user_id}: {e_create_bonus_sub}",
                                        exc_info=True)

            if referee_bonus_days and referee_bonus_days > 0:

                new_end_date_referee = await self.subscription_service.extend_active_subscription_days(
                    session=session,
                    user_id=referee_user_id,
                    bonus_days=referee_bonus_days,
                    reason=
                    f"referee bonus (invited by {inviter_name_for_referee_msg})"
                )
                if new_end_date_referee:
                    referee_final_end_date = new_end_date_referee
                    referee_bonus_applied_days = referee_bonus_days
                    logging.info(
                        f"Bonus of {referee_bonus_days} days successfully applied to referee {referee_user_id}."
                    )
                else:

                    logging.warning(
                        f"Failed to apply referee bonus for {referee_user_id} (could not extend their new subscription)."
                    )

            return {
                "referee_bonus_applied_days": referee_bonus_applied_days,
                "referee_new_end_date": referee_final_end_date,
                "inviter_bonus_applied_flag":
                inviter_bonus_successfully_applied
            }
        except Exception as e:
            logging.error(
                f"Error in apply_referral_bonuses_for_payment for referee {referee_user_id}: {e}",
                exc_info=True)

            raise

    async def generate_referral_link(self, session: AsyncSession,
                                     bot_username: str,
                                     inviter_user_id: int) -> Optional[str]:
        try:
            user = await user_dal.get_user_by_id(session, inviter_user_id)
            if not user:
                logging.warning(
                    "Unable to generate referral link: user %s not found.",
                    inviter_user_id,
                )
                return None

            referral_code = await user_dal.ensure_referral_code(session, user)
            if not referral_code:
                logging.warning(
                    "User %s has no referral code even after regeneration attempt.",
                    inviter_user_id,
                )
                return None

            return f"https://t.me/{bot_username}?start=ref_u{referral_code}"
        except Exception as exc:
            logging.error(
                "Failed to generate referral link for user %s: %s",
                inviter_user_id,
                exc,
                exc_info=True,
            )
            return None

    async def get_referral_stats(self, session: AsyncSession, user_id: int) -> dict:
        """Get referral statistics for a user"""
        from db.dal import user_dal, payment_dal
        
        try:
            # Count total invited users (referrals)
            invited_count_result = await session.execute(
                text("SELECT COUNT(*) FROM users WHERE referred_by_id = :user_id"),
                {"user_id": user_id}
            )
            invited_count = invited_count_result.scalar() or 0
            
            # Count users who made successful payments (purchased subscription)
            purchased_count_result = await session.execute(
                text("""
                    SELECT COUNT(DISTINCT u.user_id) 
                    FROM users u 
                    JOIN payments p ON u.user_id = p.user_id 
                    WHERE u.referred_by_id = :user_id 
                    AND p.status = 'succeeded'
                """),
                {"user_id": user_id}
            )
            purchased_count = purchased_count_result.scalar() or 0
            
            return {
                "invited_count": invited_count,
                "purchased_count": purchased_count
            }
        except Exception as e:
            logging.error(f"Error getting referral stats for user {user_id}: {e}")
            return {
                "invited_count": 0,
                "purchased_count": 0
            }
