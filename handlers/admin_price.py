"""
Admin pricing handlers.
Handles setting and updating prices for different subscription periods.
"""
import logging
from typing import Optional, Union

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db
from bot import check_admin, ADMIN_WAITING_PRICE, get_admin_keyboard

# Configure logging
logger = logging.getLogger(__name__)

async def handle_change_price(update: Update, context: ContextTypes.DEFAULT_TYPE, price_type: str = "service_price") -> int:
    """
    Handle the change price callback.
    
    Args:
        update: The update object
        context: The context object
        price_type: The type of price to change (service_price or one_month_price)
    
    Returns:
        The conversation state
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return -1
    
    # Get current price
    default_value = '70000'
    price_label = "سرویس" if price_type == "service_price" else "یک‌ماهه"
    current_price = db.get_setting(price_type, default_value)
    
    # Set the awaiting flag and send instructions
    context.user_data['awaiting_price'] = True
    context.user_data['price_type'] = price_type
    
    await query.edit_message_text(
        f"💸 *تغییر قیمت {price_label}*\n\n"
        f"قیمت فعلی: {int(current_price):,} تومان\n\n"
        f"قیمت جدید {price_label} (تومان) را وارد کنید:",
        parse_mode="Markdown"
    )
    
    return ADMIN_WAITING_PRICE

async def process_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the price input message."""
    message_text = update.message.text.strip()
    
    # Check if we're expecting a price input
    if not context.user_data.get('awaiting_price', False):
        return -1
    
    # Get the price type (regular or one-month)
    price_type = context.user_data.get('price_type', 'service_price')
    price_label = "سرویس" if price_type == "service_price" else "یک‌ماهه"
    
    # Clear the flags immediately to ensure they're cleared even if errors occur
    context.user_data.pop('awaiting_price', None)
    context.user_data.pop('price_type', None)
    
    try:
        # Parse and validate the price
        price = int(message_text)
        if price <= 0:
            await update.message.reply_text(
                "❌ *خطا: قیمت باید بزرگتر از صفر باشد*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return -1
        
        # Update price in database
        if db.set_setting(price_type, str(price)):
            await update.message.reply_text(
                f"✅ *قیمت {price_label} با موفقیت به {price:,} تومان تغییر یافت*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
        else:
            await update.message.reply_text(
                f"❌ *خطا در تغییر قیمت {price_label}*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
        
        return -1
        
    except ValueError:
        await update.message.reply_text(
            "❌ *خطا: لطفاً یک عدد صحیح وارد کنید*",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1
    except Exception as e:
        logger.error(f"Error changing service price: {e}")
        await update.message.reply_text(
            f"❌ *خطا در تغییر قیمت {price_label}*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1
