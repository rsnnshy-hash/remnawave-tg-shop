"""Admin audit service for tracking administrative actions."""
import logging
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal.message_log_dal import create_message_log_no_commit


class AdminAuditService:
    """
    Service for logging administrative actions for audit purposes.

    Usage:
        audit = AdminAuditService()
        await audit.log_user_ban(session, admin_id, target_user_id, reason="Violation")
    """

    # Event type constants
    EVENT_USER_BAN = "admin_user_ban"
    EVENT_USER_UNBAN = "admin_user_unban"
    EVENT_PROMO_CREATE = "admin_promo_create"
    EVENT_PROMO_DELETE = "admin_promo_delete"
    EVENT_PROMO_DEACTIVATE = "admin_promo_deactivate"
    EVENT_SUBSCRIPTION_EXTEND = "admin_subscription_extend"
    EVENT_SUBSCRIPTION_REVOKE = "admin_subscription_revoke"
    EVENT_BROADCAST = "admin_broadcast"
    EVENT_PAYMENT_REFUND = "admin_payment_refund"
    EVENT_USER_SYNC = "admin_user_sync"
    EVENT_SETTINGS_CHANGE = "admin_settings_change"
    EVENT_AD_CAMPAIGN_CREATE = "admin_ad_campaign_create"
    EVENT_AD_CAMPAIGN_DELETE = "admin_ad_campaign_delete"
    EVENT_TRIAL_GRANT = "admin_trial_grant"
    EVENT_TRIAL_REVOKE = "admin_trial_revoke"

    async def _log_action(
        self,
        session: AsyncSession,
        admin_id: int,
        event_type: str,
        content: str,
        target_user_id: Optional[int] = None,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Internal method to log an admin action."""
        try:
            log_data = {
                "user_id": admin_id,
                "telegram_username": admin_username,
                "event_type": event_type,
                "content": content,
                "is_admin_event": True,
                "target_user_id": target_user_id,
            }
            await create_message_log_no_commit(session, log_data)
            logging.debug(f"Admin audit: {event_type} by {admin_id} - {content}")
            return True
        except Exception as e:
            logging.error(f"Failed to log admin action: {e}")
            return False

    async def log_user_ban(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        reason: Optional[str] = None,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log user ban action."""
        content = f"Banned user {target_user_id}"
        if reason:
            content += f". Reason: {reason}"
        return await self._log_action(
            session, admin_id, self.EVENT_USER_BAN, content,
            target_user_id, admin_username
        )

    async def log_user_unban(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log user unban action."""
        content = f"Unbanned user {target_user_id}"
        return await self._log_action(
            session, admin_id, self.EVENT_USER_UNBAN, content,
            target_user_id, admin_username
        )

    async def log_promo_create(
        self,
        session: AsyncSession,
        admin_id: int,
        promo_code: str,
        bonus_days: int,
        max_activations: int,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log promo code creation."""
        content = f"Created promo code '{promo_code}': {bonus_days} days, max {max_activations} uses"
        return await self._log_action(
            session, admin_id, self.EVENT_PROMO_CREATE, content,
            admin_username=admin_username
        )

    async def log_promo_delete(
        self,
        session: AsyncSession,
        admin_id: int,
        promo_code: str,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log promo code deletion."""
        content = f"Deleted promo code '{promo_code}'"
        return await self._log_action(
            session, admin_id, self.EVENT_PROMO_DELETE, content,
            admin_username=admin_username
        )

    async def log_promo_deactivate(
        self,
        session: AsyncSession,
        admin_id: int,
        promo_code: str,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log promo code deactivation."""
        content = f"Deactivated promo code '{promo_code}'"
        return await self._log_action(
            session, admin_id, self.EVENT_PROMO_DEACTIVATE, content,
            admin_username=admin_username
        )

    async def log_subscription_extend(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        days: int,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log subscription extension."""
        content = f"Extended subscription for user {target_user_id} by {days} days"
        return await self._log_action(
            session, admin_id, self.EVENT_SUBSCRIPTION_EXTEND, content,
            target_user_id, admin_username
        )

    async def log_subscription_revoke(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        reason: Optional[str] = None,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log subscription revocation."""
        content = f"Revoked subscription for user {target_user_id}"
        if reason:
            content += f". Reason: {reason}"
        return await self._log_action(
            session, admin_id, self.EVENT_SUBSCRIPTION_REVOKE, content,
            target_user_id, admin_username
        )

    async def log_broadcast(
        self,
        session: AsyncSession,
        admin_id: int,
        recipients_count: int,
        message_preview: str,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log broadcast message."""
        content = f"Sent broadcast to {recipients_count} users. Preview: {message_preview[:100]}..."
        return await self._log_action(
            session, admin_id, self.EVENT_BROADCAST, content,
            admin_username=admin_username
        )

    async def log_payment_refund(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        payment_id: str,
        amount: float,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log payment refund."""
        content = f"Refunded payment {payment_id} ({amount}) for user {target_user_id}"
        return await self._log_action(
            session, admin_id, self.EVENT_PAYMENT_REFUND, content,
            target_user_id, admin_username
        )

    async def log_user_sync(
        self,
        session: AsyncSession,
        admin_id: int,
        sync_result: str,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log user sync operation."""
        content = f"Performed user sync: {sync_result}"
        return await self._log_action(
            session, admin_id, self.EVENT_USER_SYNC, content,
            admin_username=admin_username
        )

    async def log_ad_campaign_create(
        self,
        session: AsyncSession,
        admin_id: int,
        campaign_source: str,
        start_param: str,
        cost: float,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log ad campaign creation."""
        content = f"Created ad campaign: source='{campaign_source}', param='{start_param}', cost={cost}"
        return await self._log_action(
            session, admin_id, self.EVENT_AD_CAMPAIGN_CREATE, content,
            admin_username=admin_username
        )

    async def log_ad_campaign_delete(
        self,
        session: AsyncSession,
        admin_id: int,
        campaign_source: str,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log ad campaign deletion."""
        content = f"Deleted ad campaign: source='{campaign_source}'"
        return await self._log_action(
            session, admin_id, self.EVENT_AD_CAMPAIGN_DELETE, content,
            admin_username=admin_username
        )

    async def log_trial_grant(
        self,
        session: AsyncSession,
        admin_id: int,
        target_user_id: int,
        days: int,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log trial grant action."""
        content = f"Granted {days}-day trial to user {target_user_id}"
        return await self._log_action(
            session, admin_id, self.EVENT_TRIAL_GRANT, content,
            target_user_id, admin_username
        )

    async def log_custom_action(
        self,
        session: AsyncSession,
        admin_id: int,
        event_type: str,
        content: str,
        target_user_id: Optional[int] = None,
        admin_username: Optional[str] = None,
    ) -> bool:
        """Log a custom admin action."""
        return await self._log_action(
            session, admin_id, event_type, content,
            target_user_id, admin_username
        )


# Global instance
_audit_service: Optional[AdminAuditService] = None


def get_audit_service() -> AdminAuditService:
    """Get global audit service instance."""
    global _audit_service
    if _audit_service is None:
        _audit_service = AdminAuditService()
    return _audit_service
