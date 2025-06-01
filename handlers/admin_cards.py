"""
Admin handlers for managing payment cards
"""
import logging
import re
from typing import Optional, List, Dict, Any, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Try to import enhanced logging if available
try:
    from debug_logger import log_function_call
    ENHANCED_LOGGING = True
except ImportError:
    # Define dummy decorator
    def log_function_call(func):
        return func
    ENHANCED_LOGGING = False

# States
WAITING_FOR_CARD_INFO = 100
WAITING_FOR_CARD_EDIT = 101

@log_function_call
async def show_cards_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    """Show the list of payment cards with pagination"""
    query = update.callback_query
    if query:
        await query.answer()
    
    # Page size
    page_size = 10
    offset = page * page_size
    
    # Get cards from database
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # Get total count for pagination
            cur.execute("SELECT COUNT(*) FROM cards WHERE active = TRUE")
            total_count = cur.fetchone()[0]
            
            # Get cards for current page
            cur.execute(
                """SELECT id, title, card_number FROM cards 
                   WHERE active = TRUE 
                   ORDER BY id DESC 
                   LIMIT %s OFFSET %s""", 
                (page_size, offset)
            )
            cards = cur.fetchall()
    
    # Calculate pagination info
    total_pages = (total_count + page_size - 1) // page_size
    has_prev = page > 0
    has_next = page < total_pages - 1
    
    # Format message
    message_text = "💳 *مدیریت کارت‌های بانکی*\n\n"
    
    if not cards:
        message_text += "هیچ کارتی ثبت نشده است."
    else:
        for card_id, title, number in cards:
            # Mask card number
            masked_number = number[:6] + "****" + number[-4:] if len(number) >= 10 else number
            message_text += f"`{card_id}. {title}`  `{masked_number}`\n"
    
    # Create keyboard
    keyboard = []
    
    # Add button
    keyboard.append([
        InlineKeyboardButton("➕ افزودن کارت", callback_data="card:add")
    ])
    
    # Action buttons for each card
    for card_id, title, number in cards:
        keyboard.append([
            InlineKeyboardButton(f"🔻 حذف {card_id}", callback_data=f"card:del:{card_id}"),
            InlineKeyboardButton(f"✏️ ویرایش {card_id}", callback_data=f"card:edit:{card_id}")
        ])
    
    # Navigation buttons
    nav_buttons = []
    if has_prev:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"admin:cards|{page-1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(" ", callback_data="noop"))
        
    nav_buttons.append(InlineKeyboardButton("⏹️", callback_data="admin:back"))
    
    if has_next:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"admin:cards|{page+1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(" ", callback_data="noop"))
    
    keyboard.append(nav_buttons)
    
    # Send or edit message
    if query:
        await query.edit_message_text(
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

@log_function_call
async def add_card_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to add a new card"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ *افزودن کارت جدید*\n\n"
        "عنوان کارت و شماره کارت را با فاصله بفرست\n"
        "مثال: `کارت سامان 6219861234567890`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
        ]])
    )
    
    # Set state to wait for card info
    context.user_data['awaiting_card_info'] = True

@log_function_call
async def process_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process the card info received from user and add it to database"""
    message = update.message
    text = message.text.strip()
    
    # Check if we're expecting card info
    if not context.user_data.get('awaiting_card_info', False):
        return
    
    # Clear the flag
    context.user_data.pop('awaiting_card_info', None)
    
    # Try to extract card number from the end of the text
    # Look for a sequence of digits (possibly with spaces/dashes) at the end
    import re
    
    # Match card number pattern at the end: digits, spaces, dashes (minimum 13 digits for a valid card)
    card_pattern = r'[\d\s\-]{13,}$'
    match = re.search(card_pattern, text)
    
    if not match:
        await message.reply_text(
            "❌ *خطا در فرمت*\n\n"
            "لطفا عنوان و شماره کارت را وارد کنید.\n"
            "مثال: `کارت سامان 6219861234567890`\n\n"
            "شماره کارت باید حداقل ۱۳ رقم باشد.",
            parse_mode="Markdown"
        )
        return
    
    # Extract card number and clean it
    card_number_raw = match.group().strip()
    number = card_number_raw.replace(' ', '').replace('-', '')
    
    # Validate card number (should be all digits and reasonable length)
    if not number.isdigit() or len(number) < 13 or len(number) > 19:
        await message.reply_text(
            "❌ *خطا در شماره کارت*\n\n"
            "شماره کارت باید فقط شامل ارقام باشد و بین ۱۳ تا ۱۹ رقم داشته باشد.",
            parse_mode="Markdown"
        )
        return
    
    # Extract title (everything before the card number)
    title = text[:match.start()].strip()
    
    if not title:
        await message.reply_text(
            "❌ *خطا در عنوان*\n\n"
            "لطفا عنوان کارت را وارد کنید.\n"
            "مثال: `کارت سامان 6219861234567890`",
            parse_mode="Markdown"
        )
        return
    
    # Save to database
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO cards (title, card_number) VALUES (%s, %s) RETURNING id",
                    (title, number)
                )
                card_id = cur.fetchone()[0]
                conn.commit()
        
        # Success message
        await message.reply_text(
            f"✅ *کارت جدید با موفقیت اضافه شد*\n\n"
            f"شناسه: `{card_id}`\n"
            f"عنوان: `{title}`\n"
            f"شماره: `{number}`",
            parse_mode="Markdown"
        )
        
        # Show cards list
        await show_cards_list(update, context)
        
    except Exception as e:
        logger.error(f"Error adding card: {e}")
        await message.reply_text(
            "❌ *خطا در ثبت کارت*\n\n"
            f"خطای سیستمی: {str(e)}",
            parse_mode="Markdown"
        )

@log_function_call
async def delete_card(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    """Delete a card (set active=FALSE)"""
    query = update.callback_query
    await query.answer()
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cards SET active = FALSE WHERE id = %s RETURNING title, card_number",
                    (card_id,)
                )
                result = cur.fetchone()
                conn.commit()
                
                if result:
                    title, number = result
                    
                    # Show temporary success message
                    await query.edit_message_text(
                        f"✅ *کارت با موفقیت حذف شد*\n\n"
                        f"عنوان: `{title}`\n"
                        f"شماره: `{number}`",
                        parse_mode="Markdown"
                    )
                    
                    # After a short pause, refresh the list
                    import asyncio
                    await asyncio.sleep(1.5)
                    
                else:
                    await query.edit_message_text(
                        "❌ *کارت یافت نشد*\n\n"
                        "کارت مورد نظر در سیستم وجود ندارد یا قبلاً حذف شده است.",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
                        ]])
                    )
                    return
                
    except Exception as e:
        logger.error(f"Error deleting card {card_id}: {e}")
        await query.edit_message_text(
            "❌ *خطا در حذف کارت*\n\n"
            f"خطای سیستمی: {str(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
            ]])
        )
        return
    
    # Show the updated list
    await show_cards_list(update, context)

@log_function_call
async def edit_card_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for editing a card"""
    query = update.callback_query
    await query.answer()
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                card_id = int(query.data.split(':')[2])
                cur.execute(
                    "SELECT title, card_number FROM cards WHERE id = %s AND active = TRUE",
                    (card_id,)
                )
                result = cur.fetchone()
                
                if not result:
                    await query.edit_message_text(
                        "❌ *کارت یافت نشد*\n\n"
                        "کارت مورد نظر در سیستم وجود ندارد یا قبلاً حذف شده است.",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
                        ]])
                    )
                    return
                
                title, number = result
                
                await query.edit_message_text(
                    f"✏️ *ویرایش کارت #{card_id}*\n\n"
                    f"اطلاعات فعلی:\n"
                    f"عنوان: `{title}`\n"
                    f"شماره: `{number}`\n\n"
                    f"عنوان و شماره جدید را با یک فاصله وارد کنید\n"
                    f"(یا برای هر کدام که قصد تغییر ندارید، `-` وارد کنید)\n\n"
                    f"مثال 1: `کارت ملی 6037991234567890`\n"
                    f"مثال 2: `- 6037991234567890`\n"
                    f"مثال 3: `کارت ملی -`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
                    ]])
                )
                
                # Store card info in context
                context.user_data['edit_card_id'] = card_id
                context.user_data['edit_card_title'] = title 
                context.user_data['edit_card_number'] = number
                
    except Exception as e:
        logger.error(f"Error fetching card for edit: {e}")
        await query.edit_message_text(
            "❌ *خطا در دریافت اطلاعات کارت*\n\n"
            f"خطای سیستمی: {str(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت", callback_data="admin:cards")
            ]])
        )

@log_function_call
async def process_edit_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process edited card information"""
    message = update.message
    text = message.text.strip()
    
    # Check if we have card to edit
    card_id = context.user_data.get('edit_card_id')
    if not card_id:
        return
    
    # Get stored card info
    old_title = context.user_data.get('edit_card_title', '')
    old_number = context.user_data.get('edit_card_number', '')
    
    # Clear edit data from context
    context.user_data.pop('edit_card_id', None)
    context.user_data.pop('edit_card_title', None)
    context.user_data.pop('edit_card_number', None)
    
    # Parse input
    parts = text.split(maxsplit=1)
    
    if len(parts) < 2:
        # If only one part, assume it's just title change
        new_title = parts[0] if parts[0] != '-' else old_title
        new_number = old_number
    else:
        new_title = parts[0] if parts[0] != '-' else old_title
        new_number = parts[1].replace(' ', '').replace('-', '') if parts[1] != '-' else old_number
    
    # Validate card number if changed
    if new_number != old_number and not new_number.isdigit():
        await message.reply_text(
            "❌ *خطا در شماره کارت*\n\n"
            "شماره کارت باید فقط شامل ارقام باشد.",
            parse_mode="Markdown"
        )
        return
    
    # Update in database
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cards SET title = %s, card_number = %s WHERE id = %s AND active = TRUE",
                    (new_title, new_number, card_id)
                )
                conn.commit()
                
                if cur.rowcount == 0:
                    await message.reply_text(
                        "❌ *کارت یافت نشد*\n\n"
                        "کارت مورد نظر در سیستم وجود ندارد یا قبلاً حذف شده است.",
                        parse_mode="Markdown"
                    )
                    return
        
        # Success message
        await message.reply_text(
            f"✅ *کارت با موفقیت به‌روز شد*\n\n"
            f"شناسه: `{card_id}`\n"
            f"عنوان: `{new_title}`\n"
            f"شماره: `{new_number}`",
            parse_mode="Markdown"
        )
        
        # Show updated card list
        await show_cards_list(update, context)
        
    except Exception as e:
        logger.error(f"Error updating card {card_id}: {e}")
        await message.reply_text(
            "❌ *خطا در به‌روزرسانی کارت*\n\n"
            f"خطای سیستمی: {str(e)}",
            parse_mode="Markdown"
        )

@log_function_call
def get_random_card() -> Tuple[Optional[str], Optional[str]]:
    """Get a random active card from database"""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title, card_number FROM cards WHERE active = TRUE")
                cards = cur.fetchall()
                
                if not cards:
                    # Get fallback card from settings
                    cur.execute("SELECT value FROM settings WHERE key = 'card_number'")
                    result = cur.fetchone()
                    if result:
                        return "کارت بانکی", result[0]
                    else:
                        return None, None
                
                import random
                card = random.choice(cards)
                return card[0], card[1]
                
    except Exception as e:
        logger.error(f"Error getting random card: {e}")
        return None, None
