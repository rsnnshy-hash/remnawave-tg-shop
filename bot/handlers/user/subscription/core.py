import hashlib
import logging
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from typing import Optional, Union, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from config.settings import Settings
from bot.keyboards.inline.user_keyboards import (
    get_subscription_options_keyboard,
    get_back_to_main_menu_markup,
    get_autorenew_confirm_keyboard,
    get_subscription_choice_keyboard,
    get_my_subscriptions_keyboard,
    get_subscription_details_keyboard,
)
from bot.services.subscription_service import SubscriptionService
from bot.services.panel_api_service import PanelApiService
from bot.middlewares.i18n import JsonI18n
from db.dal import subscription_dal, user_billing_dal
from db.models import Subscription

router = Router(name="user_subscription_core_router")


def _shorten_hwid_for_display(hwid: Optional[str], max_length: int = 24) -> str:
    """Trim HWID for button text to keep within Telegram limits."""
    if not hwid:
        return "-"
    hwid_str = str(hwid)
    if len(hwid_str) <= max_length:
        return hwid_str
    return f"{hwid_str[:8]}...{hwid_str[-6:]}"


def _hwid_callback_token(hwid: Optional[str]) -> str:
    """Stable short token for callback_data; avoids 64b limit with raw HWID."""
    hwid_str = str(hwid or "")
    return hashlib.sha256(hwid_str.encode()).hexdigest()[:32]


async def display_subscription_options(
    event: Union[types.Message, types.CallbackQuery],
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
    subscription_service: Optional[SubscriptionService] = None,
    extend_subscription_id: Optional[int] = None,
    is_new_subscription: bool = False,
):
    """Display subscription options. If user has active subscriptions, show choice first."""
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n:
        err_msg = "Language service error."
        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer(err_msg, show_alert=True)
            except Exception:
                pass
        elif isinstance(event, types.Message):
            await event.answer(err_msg)
        return

    user_id = event.from_user.id
    
    # Check if user has active subscriptions and should see choice screen
    if subscription_service and not extend_subscription_id and not is_new_subscription:
        try:
            active_subs = await subscription_service.get_all_active_subscriptions_details(session, user_id)
            if active_subs and len(active_subs) > 0:
                # Show subscription choice screen
                text_content = get_text("subscription_choice_title")
                reply_markup = get_subscription_choice_keyboard(active_subs, current_lang, i18n, settings)
                
                target_message_obj = event.message if isinstance(event, types.CallbackQuery) else event
                if target_message_obj:
                    if isinstance(event, types.CallbackQuery):
                        try:
                            await target_message_obj.edit_text(text_content, reply_markup=reply_markup)
                        except Exception:
                            await target_message_obj.answer(text_content, reply_markup=reply_markup)
                        try:
                            await event.answer()
                        except Exception:
                            pass
                    else:
                        await target_message_obj.answer(text_content, reply_markup=reply_markup)
                return
        except Exception as e:
            logging.warning(f"Error checking active subscriptions: {e}")

    # Show standard subscription options
    currency_symbol_val = settings.DEFAULT_CURRENCY_SYMBOL
    traffic_packages = getattr(settings, "traffic_packages", {}) or {}
    stars_traffic_packages = getattr(settings, "stars_traffic_packages", {}) or {}
    traffic_mode = bool(getattr(settings, "traffic_sale_mode", False) or stars_traffic_packages)

    if traffic_mode:
        if traffic_packages:
            options = traffic_packages
        elif stars_traffic_packages:
            options = stars_traffic_packages
            currency_symbol_val = "⭐"
        else:
            options = {}
    else:
        options = settings.subscription_options

    if options:
        text_content = get_text("select_traffic_package") if traffic_mode else get_text("select_subscription_period")
        reply_markup = get_subscription_options_keyboard(
            options, currency_symbol_val, current_lang, i18n, traffic_mode=traffic_mode
        )
    else:
        text_content = get_text("no_subscription_options_available")
        reply_markup = get_back_to_main_menu_markup(current_lang, i18n)

    target_message_obj = event.message if isinstance(event, types.CallbackQuery) else event
    if not target_message_obj:
        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer(get_text("error_occurred_try_again"), show_alert=True)
            except Exception:
                pass
        return

    if isinstance(event, types.CallbackQuery):
        try:
            await target_message_obj.edit_text(text_content, reply_markup=reply_markup)
        except Exception:
            await target_message_obj.answer(text_content, reply_markup=reply_markup)
        try:
            await event.answer()
        except Exception:
            pass
    else:
        await target_message_obj.answer(text_content, reply_markup=reply_markup)


@router.callback_query(F.data == "main_action:subscribe")
async def reshow_subscription_options_callback(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
    subscription_service: SubscriptionService,
):
    await display_subscription_options(callback, i18n_data, settings, session, subscription_service)


@router.callback_query(F.data == "buy_new_subscription")
async def buy_new_subscription_callback(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
):
    """Handle buying a completely new subscription."""
    await display_subscription_options(callback, i18n_data, settings, session, is_new_subscription=True)


@router.callback_query(F.data.startswith("extend_sub:"))
async def extend_subscription_callback(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
):
    """Handle extending an existing subscription."""
    try:
        sub_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Error", show_alert=True)
        return
    
    # Store the subscription ID for later use in payment flow
    # We'll pass it through the payment process
    await display_subscription_options(
        callback, i18n_data, settings, session, 
        extend_subscription_id=sub_id, 
        is_new_subscription=False
    )


@router.callback_query(F.data.startswith("view_sub:"))
async def view_subscription_details_callback(
    callback: types.CallbackQuery,
    i18n_data: dict,
    settings: Settings,
    panel_service: PanelApiService,
    subscription_service: SubscriptionService,
    session: AsyncSession,
    bot: Bot,
):
    """View details of a specific subscription."""
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: JsonI18n = i18n_data.get("i18n_instance")
    get_text = lambda key, **kw: i18n.gettext(current_lang, key, **kw)

    try:
        sub_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return

    # Get subscription from DB
    sub = await subscription_dal.get_subscription_by_id(session, sub_id)
    if not sub or sub.user_id != callback.from_user.id:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return

    # Get panel data
    panel_user_data = await panel_service.get_user_by_uuid(sub.panel_user_uuid)
    if not panel_user_data:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return

    end_date = sub.end_date
    days_left = (end_date.date() - datetime.now().date()).days if end_date else 0
    
    config_link_raw = panel_user_data.get("subscriptionUrl")
    from bot.utils.config_link import prepare_config_links
    display_link, connect_button_url = await prepare_config_links(settings, config_link_raw)
    
    def _fmt_gb(val: Optional[float]) -> str:
        if val is None:
            return get_text("traffic_na")
        try:
            if isinstance(val, (int, float)):
                val_gb = float(val) / (2**30)
                return f"{val_gb:.2f} GB"
        except Exception:
            pass
        return str(val)

    traffic_limit = panel_user_data.get("trafficLimitBytes")
    traffic_used = (panel_user_data.get("userTraffic") or {}).get("usedTrafficBytes")

    text = get_text(
        "subscription_details_title",
        sub_name=sub.subscription_name or f"tg_{callback.from_user.id}",
        status=panel_user_data.get("status", "UNKNOWN").upper(),
        end_date=end_date.strftime("%Y-%m-%d") if end_date else "N/A",
        days_left=max(0, days_left),
        config_link=display_link or get_text("config_link_not_available"),
        traffic_limit=_fmt_gb(traffic_limit) if traffic_limit else get_text("traffic_unlimited"),
        traffic_used=_fmt_gb(traffic_used),
    )

    reply_markup = get_subscription_details_keyboard(
        sub.subscription_id,
        display_link or "",
        connect_button_url or "",
        current_lang,
        i18n,
        settings,
    )

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    
    try:
        await callback.answer()
    except Exception:
        pass


async def my_subscription_command_handler(
    event: Union[types.Message, types.CallbackQuery],
    i18n_data: dict,
    settings: Settings,
    panel_service: PanelApiService,
    subscription_service: SubscriptionService,
    session: AsyncSession,
    bot: Bot,
):
    target = event.message if isinstance(event, types.CallbackQuery) else event
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: JsonI18n = i18n_data.get("i18n_instance")
    get_text = lambda key, **kw: i18n.gettext(current_lang, key, **kw)

    if not i18n or not target:
        if isinstance(event, types.Message):
            await event.answer(get_text("error_occurred_try_again"))
        return

    if not panel_service or not subscription_service:
        await target.answer(get_text("error_service_unavailable"))
        return

    user_id = event.from_user.id
    user_name = event.from_user.full_name or f"User {user_id}"

    # Get all active subscriptions
    try:
        all_subs = await subscription_service.get_all_active_subscriptions_details(session, user_id)
    except Exception as e:
        logging.error(f"Error getting subscriptions for user {user_id}: {e}")
        all_subs = []

    if not all_subs:
        # No active subscriptions - show old behavior
        text = get_text("no_active_subscriptions")
        buy_button = InlineKeyboardButton(
            text=get_text("menu_subscribe_inline"), callback_data="main_action:subscribe"
        )
        back_markup = get_back_to_main_menu_markup(current_lang, i18n)
        kb = InlineKeyboardMarkup(inline_keyboard=[[buy_button], *back_markup.inline_keyboard])

        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer()
            except Exception:
                pass
            try:
                await event.message.edit_text(text, reply_markup=kb)
            except Exception:
                await event.message.answer(text, reply_markup=kb)
        else:
            await event.answer(text, reply_markup=kb)
        return

    if len(all_subs) == 1:
        # Single subscription - show detailed view directly
        sub = all_subs[0]
        end_date = sub.get("end_date")
        days_left = (end_date.date() - datetime.now().date()).days if end_date else 0
        
        def _fmt_gb(val: Optional[float]) -> str:
            if val is None:
                return get_text("traffic_na")
            try:
                if isinstance(val, (int, float)):
                    val_gb = float(val) / (2**30)
                    return f"{val_gb:.2f} GB"
            except Exception:
                pass
            return str(val)

        text = get_text(
            "subscription_details_title",
            sub_name=sub.get("subscription_name", f"tg_{user_id}"),
            status=sub.get("status_from_panel", "ACTIVE"),
            end_date=end_date.strftime("%Y-%m-%d") if end_date else "N/A",
            days_left=max(0, days_left),
            config_link=sub.get("config_link") or get_text("config_link_not_available"),
            traffic_limit=_fmt_gb(sub.get("traffic_limit_bytes")) if sub.get("traffic_limit_bytes") else get_text("traffic_unlimited"),
            traffic_used=_fmt_gb(sub.get("traffic_used_bytes")),
        )

        reply_markup = get_subscription_details_keyboard(
            sub.get("subscription_id"),
            sub.get("config_link") or "",
            sub.get("connect_button_url") or "",
            current_lang,
            i18n,
            settings,
        )
    else:
        # Multiple subscriptions - show list
        text = get_text(
            "my_subscriptions_title",
            user_name=user_name,
            user_id=user_id,
        )
        reply_markup = get_my_subscriptions_keyboard(all_subs, current_lang, i18n)

    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await event.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await bot.send_message(
                chat_id=target.chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    else:
        await target.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)


async def my_devices_command_handler(
    event: Union[types.Message, types.CallbackQuery],
    i18n_data: dict,
    settings: Settings,
    panel_service: PanelApiService,
    subscription_service: SubscriptionService,
    session: AsyncSession,
    bot: Bot,
):
    target = event.message if isinstance(event, types.CallbackQuery) else event
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: JsonI18n = i18n_data.get("i18n_instance")
    get_text = lambda key, **kw: i18n.gettext(current_lang, key, **kw)

    if not i18n or not target:
        if isinstance(event, types.Message):
            await event.answer(get_text("error_occurred_try_again"))
        return

    if not settings.MY_DEVICES_SECTION_ENABLED:
        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer(get_text("my_devices_feature_disabled"), show_alert=True)
            except Exception:
                pass
        else:
            await target.answer(get_text("my_devices_feature_disabled"))
        return

    active = await subscription_service.get_active_subscription_details(session, event.from_user.id)
    if not active or not active.get("user_id"):
        message = get_text("subscription_not_active")
        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer(message, show_alert=True)
            except Exception:
                pass
        else:
            await target.answer(message)
        return

    devices = await panel_service.get_user_devices(active.get("user_id")) if active else None
    if not devices:
        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer(get_text("no_devices_found"), show_alert=True)
            except Exception:
                pass
        else:
            await target.answer(get_text("no_devices_found"))
        return

    devices_list_raw = []
    if isinstance(devices, dict):
        devices_list_raw = devices.get("devices") or []
    elif isinstance(devices, list):
        devices_list_raw = devices

    max_devices_value = active.get("max_devices")
    max_devices_display = get_text("devices_unlimited_label")
    if max_devices_value not in (None, 0):
        try:
            max_devices_int = int(max_devices_value)
            if max_devices_int >= 0:
                max_devices_display = str(max_devices_int)
        except (TypeError, ValueError):
            max_devices_display = str(max_devices_value)

    if not devices_list_raw:
        text = get_text("no_devices_details_found_message", max_devices=max_devices_display)
    else:
        devices_list = []
        current_devices = len(devices_list_raw)
        for index, device in enumerate(devices_list_raw, start=1):
            device_model = device.get('deviceModel') or None
            platform = device.get('platform') or None
            user_agent = device.get('userAgent') or None
            os_version = device.get('osVersion') or None
            created_at = device.get('createdAt')
            hwid = device.get('hwid')
            try:
                created_at_str = datetime.fromisoformat(created_at).strftime("%d.%m.%Y %H:%M") if created_at else "-"
            except Exception:
                created_at_str = str(created_at)

            device_details = get_text("device_details", index=index, device_model=device_model, platform=platform, os_version=os_version, created_at_str=created_at_str, user_agent=user_agent, hwid=hwid)
            devices_list.append(device_details)

        text = get_text("my_devices_details", devices="\n\n".join(devices_list), current_devices=current_devices, max_devices=max_devices_display)

    base_markup = get_back_to_main_menu_markup(current_lang, i18n, callback_data="main_action:my_subscription")
    kb = base_markup.inline_keyboard

    devices_kb = []
    for index, device in enumerate(devices_list_raw, start=1):
        hwid = device.get('hwid')
        if not hwid:
            continue
        device_button_text = get_text("disconnect_device_button", hwid=_shorten_hwid_for_display(hwid), index=index)
        hwid_token = _hwid_callback_token(hwid)

        devices_kb.append([InlineKeyboardButton(text=device_button_text, callback_data=f"disconnect_device:{hwid_token}")])
    kb = devices_kb + kb
    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except Exception:
            await event.message.answer(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("disconnect_device:"))
async def disconnect_device_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    subscription_service: SubscriptionService,
    panel_service: PanelApiService,
    bot: Bot,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not settings.MY_DEVICES_SECTION_ENABLED:
        try:
            await callback.answer(get_text("my_devices_feature_disabled"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, hwid_token = callback.data.split(":", 1)
    except Exception:
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    active = await subscription_service.get_active_subscription_details(session, callback.from_user.id)
    if not active or not active.get("user_id"):
        await callback.answer(get_text("subscription_not_active"), show_alert=True)
        return

    devices = await panel_service.get_user_devices(active.get("user_id"))
    if not devices:
        await callback.answer(get_text("no_devices_found"), show_alert=True)
        return

    devices_list_raw = []
    if isinstance(devices, dict):
        devices_list_raw = devices.get("devices") or []
    elif isinstance(devices, list):
        devices_list_raw = devices

    hwid = None
    for device in devices_list_raw:
        hwid_candidate = device.get("hwid")
        if hwid_candidate and _hwid_callback_token(hwid_candidate) == hwid_token:
            hwid = hwid_candidate
            break

    if not hwid:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return

    success = await panel_service.disconnect_device(active.get("user_id"), hwid)
    if not success:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return
    await session.commit()
    try:
        await callback.answer(get_text("device_disconnected"))
    except Exception:
        pass
    await my_devices_command_handler(callback, i18n_data, settings, panel_service, subscription_service, session, bot)


@router.callback_query(F.data.startswith("toggle_autorenew:"))
async def toggle_autorenew_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    subscription_service: SubscriptionService,
    panel_service: PanelApiService,
    bot: Bot,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    try:
        _, payload = callback.data.split(":", 1)
        sub_id_str, enable_str = payload.split(":")
        sub_id = int(sub_id_str)
        enable = bool(int(enable_str))
    except Exception:
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    sub = await session.get(Subscription, sub_id)
    if not sub or sub.user_id != callback.from_user.id:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return
    if sub.provider != "yookassa":
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return
    if enable:
        has_saved_card = await user_billing_dal.user_has_saved_payment_method(session, callback.from_user.id)
        if not has_saved_card:
            try:
                await callback.answer(get_text("autorenew_enable_requires_card"), show_alert=True)
            except Exception:
                pass
            return

    confirm_text = get_text("autorenew_confirm_enable") if enable else get_text("autorenew_confirm_disable")
    kb = get_autorenew_confirm_keyboard(enable, sub.subscription_id, current_lang, i18n)
    try:
        await callback.message.edit_text(confirm_text, reply_markup=kb)
    except Exception:
        try:
            await callback.message.answer(confirm_text, reply_markup=kb)
        except Exception:
            pass
    try:
        await callback.answer()
    except Exception:
        pass
    return


@router.callback_query(F.data.startswith("autorenew:confirm:"))
async def confirm_autorenew_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    subscription_service: SubscriptionService,
    panel_service: PanelApiService,
    bot: Bot,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    try:
        _, _, sub_id_str, enable_str = callback.data.split(":", 3)
        sub_id = int(sub_id_str)
        enable = bool(int(enable_str))
    except Exception:
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    sub = await session.get(Subscription, sub_id)
    if not sub or sub.user_id != callback.from_user.id:
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return
    if sub.provider != "yookassa":
        await callback.answer(get_text("error_try_again"), show_alert=True)
        return
    if enable:
        has_saved_card = await user_billing_dal.user_has_saved_payment_method(session, callback.from_user.id)
        if not has_saved_card:
            try:
                await callback.answer(get_text("autorenew_enable_requires_card"), show_alert=True)
            except Exception:
                pass
            try:
                await my_subscription_command_handler(callback, i18n_data, settings, panel_service, subscription_service, session, bot)
            except Exception:
                pass
            return

    await subscription_dal.update_subscription(session, sub.subscription_id, {"auto_renew_enabled": enable})
    await session.commit()
    try:
        await callback.answer(get_text("subscription_autorenew_updated"))
    except Exception:
        pass
    await my_subscription_command_handler(callback, i18n_data, settings, panel_service, subscription_service, session, bot)


@router.callback_query(F.data == "autorenew:cancel")
async def autorenew_cancel_from_webhook_button(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    subscription_service: SubscriptionService,
    panel_service: PanelApiService,
    bot: Bot,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    sub = await subscription_dal.get_active_subscription_by_user_id(session, callback.from_user.id)
    if not sub:
        try:
            await callback.answer(get_text("subscription_not_active"), show_alert=True)
        except Exception:
            pass
        return
    if sub.provider != "yookassa":
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return
    await subscription_dal.update_subscription(session, sub.subscription_id, {"auto_renew_enabled": False})
    await session.commit()
    try:
        await callback.answer(get_text("subscription_autorenew_updated"))
    except Exception:
        pass
    await my_subscription_command_handler(callback, i18n_data, settings, panel_service, subscription_service, session, bot)


@router.message(Command("connect"))
async def connect_command_handler(
    message: types.Message,
    i18n_data: dict,
    settings: Settings,
    panel_service: PanelApiService,
    subscription_service: SubscriptionService,
    session: AsyncSession,
    bot: Bot,
):
    logging.info(f"User {message.from_user.id} used /connect command.")
    await my_subscription_command_handler(message, i18n_data, settings, panel_service, subscription_service, session, bot)
