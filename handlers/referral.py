"""
Referral system handlers.
"""
import logging
from typing import Optional, Union

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db

# Configure logging
logger = logging.getLogger(__name__)

async def show_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the referral menu with the user's referral link and statistics.
    
    Args:
        update: The update object
        context: The context object
    """
    query = update.callback_query
    user = update.effective_user
    bot_username = context.bot.username
    
    # Create referral link
    ref_link = f"https://t.me/{bot_username}?start=ref{user.id}"
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user ID from the database
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                user_id_result = cur.fetchone()
                
                if not user_id_result:
                    await query.edit_message_text(
                        "خطا در دریافت اطلاعات کاربر. لطفا مجددا تلاش کنید."
                    )
                    return
                
                user_id = user_id_result[0]
                
                # Get count of referrals
                cur.execute("SELECT COUNT(*) FROM users WHERE referrer = %s", (user_id,))
                count_subs = cur.fetchone()[0]
                
                # Get total earned from referrals
                cur.execute("SELECT referral_earned FROM wallets WHERE user_id = %s", (user_id,))
                wallet_result = cur.fetchone()
                total_earned = wallet_result[0] if wallet_result and wallet_result[0] is not None else 0
                
                # Format the message
                message = (
                    f"🔗 *سیستم دعوت از دوستان*\n\n"
                    f"می‌توانید با معرفی ربات به دیگران اعتبار رایگان بگیرید👇\n"
                    f"`{ref_link}`\n\n"
                    f"✓ 10٪ مبلغ هر خرید زیرمجموعه‌ها به موجودی شما افزوده می‌شود\n"
                    f"• تعداد زیرمجموعه‌ها: *{count_subs}*\n"
                    f"• اعتبار کسب‌شده: *{total_earned:,.0f} تومان*\n\n"
                    f"*بنر پیشنهادی:*\n"
                    f"💎 اسمارت وی‌پی‌ان با سرعت بالا\n"
                    f"✅ بدون قطعی و محدودیت\n"
                    f"🔒 امن و مطمئن\n"
                    f"👨‍💻 پشتیبانی 24 ساعته\n"
                    f"💰 قیمت مناسب\n"
                    f"{ref_link}"
                )
                
                # Create back button
                keyboard = [[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")]]
                
                # Send the referral info
                await query.edit_message_text(
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
    except Exception as e:
        logger.error(f"Error showing referral menu: {e}")
        await query.edit_message_text(
            "خطا در دریافت اطلاعات. لطفا مجددا تلاش کنید.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")
            ]])
        )
