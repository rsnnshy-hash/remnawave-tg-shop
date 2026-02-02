"""
Data Access Layer for webhook idempotency tracking.
Prevents duplicate processing of the same webhook event.
"""
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from db.models import ProcessedWebhook


async def is_webhook_processed(
    session: AsyncSession,
    provider: str,
    provider_event_id: str,
) -> bool:
    """
    Check if a webhook event has already been processed.

    Args:
        session: Database session
        provider: Payment provider name (yookassa, freekassa, etc.)
        provider_event_id: Unique event/payment ID from provider

    Returns:
        True if already processed, False otherwise
    """
    stmt = select(ProcessedWebhook).where(
        ProcessedWebhook.provider == provider,
        ProcessedWebhook.provider_event_id == provider_event_id,
    ).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def mark_webhook_processed(
    session: AsyncSession,
    provider: str,
    provider_event_id: str,
    event_type: Optional[str] = None,
) -> bool:
    """
    Mark a webhook event as processed. Uses INSERT ON CONFLICT DO NOTHING
    for atomic idempotency check and insert.

    Args:
        session: Database session
        provider: Payment provider name
        provider_event_id: Unique event/payment ID from provider
        event_type: Optional event type (payment.succeeded, etc.)

    Returns:
        True if successfully marked (first time), False if already existed
    """
    try:
        # Use PostgreSQL upsert for atomic operation
        stmt = pg_insert(ProcessedWebhook).values(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
        ).on_conflict_do_nothing(
            index_elements=['provider', 'provider_event_id']
        ).returning(ProcessedWebhook.id)

        result = await session.execute(stmt)
        row = result.fetchone()

        # If row is None, the record already existed (conflict)
        if row is None:
            logging.info(
                f"Webhook already processed: provider={provider}, event_id={provider_event_id}"
            )
            return False

        logging.info(
            f"Webhook marked as processed: provider={provider}, event_id={provider_event_id}"
        )
        return True

    except IntegrityError:
        # Fallback for race condition (shouldn't happen with ON CONFLICT DO NOTHING)
        logging.info(
            f"Webhook already processed (IntegrityError): provider={provider}, event_id={provider_event_id}"
        )
        return False
