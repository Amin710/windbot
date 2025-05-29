"""
Admin account management handlers.
Implements list view with pagination and CRUD operations for seat accounts.
"""
import re
import logging
from typing import Optional, Tuple, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db
from bot import encrypt, decrypt, check_admin

# Configure logging
logger = logging.getLogger(__name__)

# Constants
PAGE_SIZE = 10

async def handle_accounts_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    """
    Handle the account list view with pagination.
    
    Args:
        update: The update object
        context: The context object
        page: The page number (starting from 1)
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    # Store the current page in user_data for reference when returning from other operations
    context.user_data['last_list_page'] = f"admin:list|{page}"
    
    # Calculate offset
    offset = (page - 1) * PAGE_SIZE
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get total count for pagination
                cur.execute("SELECT COUNT(*) FROM seats WHERE status = 'active'")
                total_count = cur.fetchone()[0]
                
                # Calculate total pages
                total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
                
                # Get seats for current page
                cur.execute(
                    "SELECT id, email, max_slots, sold FROM seats "
                    "WHERE status = 'active' "
                    "ORDER BY id "
                    "LIMIT %s OFFSET %s",
                    (PAGE_SIZE, offset)
                )
                seats = cur.fetchall()
                
                # Create keyboard with seat items and actions
                keyboard = []
                
                # Add seat items with actions
                for seat_id, email, max_slots, sold in seats:
                    free_slots = max_slots - sold
                    seat_text = f"{email} | {free_slots}/{max_slots}"
                    
                    keyboard.append([
                        InlineKeyboardButton(seat_text, callback_data=f"seat:info:{seat_id}")
                    ])
                    
                    keyboard.append([
                        InlineKeyboardButton("🔻 حذف", callback_data=f"seat:del:{seat_id}"),
                        InlineKeyboardButton("✏️ ویرایش", callback_data=f"seat:edit:{seat_id}")
                    ])
                
                # Add pagination controls
                pagination = []
                
                if page > 1:
                    pagination.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:list|{page-1}"))
                
                pagination.append(InlineKeyboardButton(f"⏹️ صفحه {page}/{total_pages}", callback_data="noop"))
                
                if page < total_pages:
                    pagination.append(InlineKeyboardButton("▶️ بعدی", callback_data=f"admin:list|{page+1}"))
                
                keyboard.append(pagination)
                
                # Add delete all button above back button
                keyboard.append([
                    InlineKeyboardButton("🗑️ حذف همه اکانت‌ها", callback_data="admin:deleteall")
                ])
                
                # Add back button
                keyboard.append([
                    InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back")
                ])
                
                # Create message text
                message = f"🗂️ *مدیریت اکانت‌ها* (صفحه {page}/{total_pages})\n\n"
                
                if not seats:
                    message += "هیچ اکانتی یافت نشد."
                else:
                    message += "لیست اکانت‌های فعال:\n"
                    message += "ایمیل | صندلی‌های خالی/کل"
                
                # Send or edit message
                await query.edit_message_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        await query.edit_message_text(
            f"❌ *خطا در نمایش لیست اکانت‌ها*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown"
        )

async def handle_seat_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, seat_id: int) -> None:
    """
    Handle seat deletion (soft delete).
    
    Args:
        update: The update object
        context: The context object
        seat_id: The seat ID to delete
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Check if the seat has any active orders
                cur.execute(
                    "SELECT COUNT(*) FROM orders WHERE seat_id = %s AND status = 'approved'",
                    (seat_id,)
                )
                active_orders = cur.fetchone()[0]
                
                if active_orders > 0:
                    await query.answer(
                        f"این اکانت دارای {active_orders} سفارش فعال است و نمی‌تواند حذف شود.",
                        show_alert=True
                    )
                    return
                
                # Get the current page to return to it after deletion
                match = re.search(r"admin:list\|(\d+)", context.user_data.get('last_list_page', 'admin:list|1'))
                current_page = int(match.group(1)) if match else 1
                
                # Soft delete the seat by setting status to 'disabled'
                cur.execute(
                    "UPDATE seats SET status = 'disabled' WHERE id = %s",
                    (seat_id,)
                )
                conn.commit()
                
                # Show confirmation and return to the list
                await query.answer("اکانت با موفقیت غیرفعال شد.")
                
                # Return to the same page of the account list
                await handle_accounts_list(update, context, current_page)
                
    except Exception as e:
        logger.error(f"Error deleting seat: {e}")
        await query.answer(f"خطا در حذف اکانت: {str(e)[:200]}", show_alert=True)

async def handle_seat_edit_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, seat_id: int) -> None:
    """
    Show edit prompt for a seat.
    
    Args:
        update: The update object
        context: The context object
        seat_id: The seat ID to edit
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get seat info
                cur.execute(
                    "SELECT id, email, pass_enc, secret_enc, max_slots, sold FROM seats WHERE id = %s",
                    (seat_id,)
                )
                result = cur.fetchone()
                
                if not result:
                    await query.answer("اکانت مورد نظر یافت نشد.", show_alert=True)
                    return
                
                seat_id, email, pass_enc, secret_enc, max_slots, sold = result
                
                # Set editing mode in user_data
                context.user_data['editing_seat'] = seat_id
                
                # Import the state constant
                from bot import ADMIN_WAITING_EDIT_SEAT
                # Return the conversation state
                return ADMIN_WAITING_EDIT_SEAT
                
                # Get the current page to return to after editing
                match = re.search(r"admin:list\|(\d+)", context.user_data.get('last_list_page', 'admin:list|1'))
                current_page = int(match.group(1)) if match else 1
                context.user_data['edit_return_page'] = current_page
                
                # Create keyboard
                keyboard = [
                    [
                        InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"admin:list|{current_page}")
                    ]
                ]
                
                # Send edit instructions
                await query.edit_message_text(
                    f"✏️ *ویرایش اکانت #{seat_id}*\n\n"
                    f"اطلاعات فعلی:\n"
                    f"📧 ایمیل: `{email}`\n"
                    f"🔢 صندلی‌ها: {sold}/{max_slots}\n\n"
                    f"برای ویرایش، اطلاعات جدید را به صورت زیر وارد کنید:\n"
                    f"`email password secret slots`\n\n"
                    f"اگر نمی‌خواهید فیلدی را تغییر دهید، به جای آن `-` وارد کنید.\n"
                    f"مثال: `new@email.com - newsecret -`",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                
    except Exception as e:
        logger.error(f"Error showing edit prompt: {e}")
        await query.answer(f"خطا در نمایش فرم ویرایش: {str(e)[:200]}", show_alert=True)

async def process_seat_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Process the edit input for a seat.
    
    Args:
        update: The update object
        context: The context object
    """
    message = update.message
    user = update.effective_user
    
    # Check if we're expecting an edit input
    seat_id = context.user_data.get('editing_seat')
    if not seat_id:
        return
    
    # Clear the editing flag
    context.user_data.pop('editing_seat', None)
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return
    
    # Get the page to return to
    return_page = context.user_data.get('edit_return_page', 1)
    context.user_data.pop('edit_return_page', None)
    
    # Parse the input (email, password, secret, slots)
    parts = message.text.strip().split(maxsplit=3)
    
    # Make sure we have at least one part
    if not parts:
        await message.reply_text("ورودی نامعتبر. لطفا دوباره تلاش کنید.")
        return
    
    # Fill in missing parts with '-' (no change)
    while len(parts) < 4:
        parts.append('-')
    
    email, password, secret, slots = parts
    
    # Process the edit
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get current seat data
                cur.execute(
                    "SELECT email, pass_enc, secret_enc, max_slots FROM seats WHERE id = %s",
                    (seat_id,)
                )
                result = cur.fetchone()
                
                if not result:
                    await message.reply_text("اکانت مورد نظر یافت نشد.")
                    return
                
                current_email, current_pass_enc, current_secret_enc, current_max_slots = result
                
                # Prepare update values
                update_values = []
                update_fields = []
                
                # Check if email should be updated
                if email != '-':
                    update_fields.append("email = %s")
                    update_values.append(email)
                
                # Check if password should be updated
                if password != '-':
                    update_fields.append("pass_enc = %s")
                    update_values.append(encrypt(password))
                
                # Check if secret should be updated
                if secret != '-':
                    update_fields.append("secret_enc = %s")
                    update_values.append(encrypt(secret))
                
                # Check if slots should be updated
                if slots != '-':
                    try:
                        new_slots = int(slots)
                        # Make sure new slots is not less than used slots
                        cur.execute("SELECT sold FROM seats WHERE id = %s", (seat_id,))
                        sold = cur.fetchone()[0]
                        
                        if new_slots < sold:
                            await message.reply_text(
                                f"خطا: تعداد صندلی‌های جدید ({new_slots}) کمتر از تعداد استفاده شده ({sold}) است."
                            )
                            return
                        
                        update_fields.append("max_slots = %s")
                        update_values.append(new_slots)
                    except ValueError:
                        await message.reply_text("خطا: تعداد صندلی‌ها باید یک عدد صحیح باشد.")
                        return
                
                # If nothing to update, return
                if not update_fields:
                    await message.reply_text("هیچ تغییری اعمال نشد.")
                    
                    # Show admin panel
                    admin_keyboard = [
                        [
                            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"admin:list|{return_page}")
                        ]
                    ]
                    await message.reply_text(
                        "لطفا از دکمه زیر برای بازگشت به لیست استفاده کنید:",
                        reply_markup=InlineKeyboardMarkup(admin_keyboard)
                    )
                    return
                
                # Update seat
                update_values.append(seat_id)  # Add seat_id for WHERE clause
                
                query = f"UPDATE seats SET {', '.join(update_fields)} WHERE id = %s"
                cur.execute(query, update_values)
                conn.commit()
                
                # Send confirmation
                await message.reply_text(
                    f"✅ *اکانت با موفقیت به‌روزرسانی شد*\n\n"
                    f"شناسه: #{seat_id}",
                    parse_mode="Markdown"
                )
                
                # Show admin panel
                admin_keyboard = [
                    [
                        InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"admin:list|{return_page}")
                    ]
                ]
                await message.reply_text(
                    "لطفا از دکمه زیر برای بازگشت به لیست استفاده کنید:",
                    reply_markup=InlineKeyboardMarkup(admin_keyboard)
                )
                
    except Exception as e:
        logger.error(f"Error editing seat: {e}")
        await message.reply_text(
            f"❌ *خطا در ویرایش اکانت*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown"
        )

async def handle_delete_all_accounts_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show confirmation prompt for deleting all accounts.
    
    Args:
        update: The update object
        context: The context object
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get count of active accounts
                cur.execute("SELECT COUNT(*) FROM seats WHERE status = 'active'")
                active_count = cur.fetchone()[0]
                
                # Get count of accounts with active orders
                cur.execute("""
                    SELECT COUNT(DISTINCT s.id) 
                    FROM seats s 
                    JOIN orders o ON s.id = o.seat_id 
                    WHERE s.status = 'active' AND o.status = 'approved'
                """)
                with_orders_count = cur.fetchone()[0]
                
                if active_count == 0:
                    await query.edit_message_text(
                        "ℹ️ *هیچ اکانت فعالی وجود ندارد*",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:list|1")]
                        ]),
                        parse_mode="Markdown"
                    )
                    return
                
                # Create confirmation keyboard
                keyboard = [
                    [
                        InlineKeyboardButton("✅ تایید حذف", callback_data="admin:deleteall:confirm"),
                        InlineKeyboardButton("❌ انصراف", callback_data="admin:list|1")
                    ]
                ]
                
                # Show warning message
                warning_message = (
                    f"⚠️ *هشدار: حذف همه اکانت‌ها*\n\n"
                    f"📊 تعداد اکانت‌های فعال: {active_count:,}\n"
                    f"⚠️ اکانت‌های دارای سفارش فعال: {with_orders_count:,}\n\n"
                    f"🚨 *توجه:* این عمل تمام اکانت‌های فعال را غیرفعال می‌کند.\n"
                    f"اکانت‌هایی که سفارش فعال دارند نیز غیرفعال خواهند شد.\n\n"
                    f"❓ آیا مطمئن هستید که می‌خواهید ادامه دهید؟"
                )
                
                await query.edit_message_text(
                    warning_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                
    except Exception as e:
        logger.error(f"Error showing delete all prompt: {e}")
        await query.edit_message_text(
            f"❌ *خطا در نمایش فرم تایید*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown"
        )

async def handle_delete_all_accounts_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Actually delete all accounts after confirmation.
    
    Args:
        update: The update object
        context: The context object
    """
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    # Show processing message
    await query.edit_message_text(
        "⏳ *در حال حذف همه اکانت‌ها...*\n\nلطفا منتظر بمانید...",
        parse_mode="Markdown"
    )
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get count before deletion
                cur.execute("SELECT COUNT(*) FROM seats WHERE status = 'active'")
                active_count = cur.fetchone()[0]
                
                # Disable all active accounts (soft delete)
                cur.execute("UPDATE seats SET status = 'disabled' WHERE status = 'active'")
                affected_rows = cur.rowcount
                conn.commit()
                
                # Show success message
                success_message = (
                    f"✅ *حذف همه اکانت‌ها با موفقیت انجام شد*\n\n"
                    f"📊 تعداد اکانت‌های حذف شده: {affected_rows:,}\n"
                    f"🔄 وضعیت: غیرفعال شده\n\n"
                    f"ℹ️ اکانت‌ها به صورت نرم‌افزاری حذف شده‌اند و در صورت نیاز قابل بازیابی هستند."
                )
                
                keyboard = [
                    [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin:list|1")]
                ]
                
                await query.edit_message_text(
                    success_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                
                # Log this action
                logger.info(f"Admin {user.id} deleted all accounts. Count: {affected_rows}")
                
    except Exception as e:
        logger.error(f"Error deleting all accounts: {e}")
        await query.edit_message_text(
            f"❌ *خطا در حذف اکانت‌ها*\n\n`{str(e)[:200]}`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:list|1")]
            ]),
            parse_mode="Markdown"
        )