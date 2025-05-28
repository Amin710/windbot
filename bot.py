#!/usr/bin/env python
"""
test
Wind Reseller Telegram Bot

A Telegram bot for reselling Wind VPN service accounts.
This bot allows users to:
- Purchase service access via bank card payments
- Manage their purchased services
- Access their VPN credentials
- Generate 2FA/TOTP codes for authentication
- Track referrals and marketing campaigns via UTM parameters

Features:
- Secure credential storage with Fernet encryption
- PostgreSQL database backend for user and order management
- Admin panel for order approval/rejection
- Multi-language support (Persian/English)
- Receipt verification workflow
- UTM parameter tracking for marketing campaigns

Usage:
    python bot.py

Requires:
    - PostgreSQL database
    - Telegram Bot API token
    - Fernet encryption key
    - Receipt channel for admin approval
"""
import asyncio
import base64
import datetime
import logging
import os
import re
import csv
import pytz
import pyotp
import psycopg2
import tempfile
import subprocess
from tabulate import tabulate
from pathlib import Path
from typing import Dict, Optional, Union, Tuple, List, Any

from telegram.error import TelegramError, Forbidden, BadRequest, RetryAfter

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Import our database module
import db

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URI = os.getenv("DB_URI")
FERNET_KEY = os.getenv("FERNET_KEY")
RECEIPT_CHANNEL_ID = os.getenv("RECEIPT_CHANNEL_ID")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")

# Initialize Fernet for encryption/decryption
if not FERNET_KEY:
    logger.error("FERNET_KEY environment variable not set")
    raise ValueError("FERNET_KEY environment variable not set")

try:
    # Ensure the key is properly padded for base64
    key = FERNET_KEY
    if len(key) % 4 != 0:
        key += '=' * (4 - len(key) % 4)
    FERNET = Fernet(key.encode() if isinstance(key, str) else key)
    logger.info("Fernet encryption initialized")
except Exception as e:
    logger.error(f"Error initializing Fernet encryption: {e}")
    raise


def encrypt(text: Union[str, bytes]) -> bytes:
    """
    Encrypt text using Fernet symmetric encryption.
    
    Args:
        text: String or bytes to encrypt
        
    Returns:
        Encrypted bytes
    """
    if isinstance(text, str):
        text = text.encode()
    return FERNET.encrypt(text)


def decrypt_secret(token) -> str:
    """Decrypt Fernet token to plain string, accepting bytes, memoryview or str."""
    if isinstance(token, memoryview):
        token = token.tobytes()
    elif isinstance(token, str):
        token = token.encode()
    try:
        return FERNET.decrypt(token).decode()
    except InvalidToken as e:
        logger.error(f"Failed to decrypt: {e}")
        raise ValueError("Failed to decrypt data") from e


# Keep the old function for backwards compatibility
def decrypt(token: bytes) -> str:
    """Decrypt bytes using Fernet symmetric encryption (legacy version)."""
    return decrypt_secret(token)


def get_main_menu_keyboard():
    """Create the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("⭐️ خرید سرویس", callback_data="buy_service"),
            InlineKeyboardButton("🔐 مدیریت سرویس", callback_data="manage_service")
        ],
        [
            InlineKeyboardButton("💰 کیف پول", callback_data="wallet"),
            InlineKeyboardButton("🆓 اعتبار رایگان", callback_data="free_credit")
        ],
        [
            InlineKeyboardButton("💬 پشتیبانی", callback_data="support")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_approval_keyboard(order_id):
    """Create admin approval keyboard for receipts."""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve:{order_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject:{order_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_2fa_button(seat_id):
    """Create 2FA button for approved orders."""
    keyboard = [
        [
            InlineKeyboardButton("📲 دریافت کد 2FA", callback_data=f"2fa:{seat_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def create_or_get_user(user):
    """Create a user record if it doesn't exist, or return existing user."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Check if user exists
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                result = cur.fetchone()
                
                if result:
                    # User exists, return user_id
                    return result[0]
                else:
                    # Create new user
                    cur.execute(
                        "INSERT INTO users (tg_id, first_name, username) VALUES (%s, %s, %s) RETURNING id",
                        (user.id, user.first_name, user.username)
                    )
                    user_id = cur.fetchone()[0]
                    
                    # Create wallet for the new user
                    cur.execute(
                        "INSERT INTO wallets (user_id) VALUES (%s)",
                        (user_id,)
                    )
                    conn.commit()
                    logger.info(f"Created new user: {user.first_name} (ID: {user_id})")
                    return user_id
    except Exception as e:
        logger.error(f"Error creating/getting user: {e}")
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command, create user, process UTM, and show main menu."""
    if not update.message:
        return
        
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Create or get user
    try:
        user_id = await create_or_get_user(user)
    except Exception as e:
        logger.error(f"Failed to create/get user: {e}")
        await update.message.reply_text(
            "متأسفانه در حال حاضر با مشکلی مواجه شدیم. لطفا بعدا دوباره تلاش کنید."
        )
        return
    
    # Check for deep link parameter (UTM)
    if context.args and len(context.args) > 0:
        utm_keyword = context.args[0]
        # Store UTM in user_data for later use
        context.user_data['utm'] = utm_keyword
        # Increment UTM stats
        db.inc_utm(utm_keyword, 'starts')
        logger.info(f"User {user_id} started bot with UTM: {utm_keyword}")
    
    # Send welcome message with main menu
    support_username = db.get_setting('support_username', 'support')
    await update.message.reply_text(
        f"🌬 *به بات فروش سرویس ویند خوش آمدید*\n\n"
        f"از منوی زیر، گزینه مورد نظر خود را انتخاب کنید.\n\n"
        f"برای راهنمایی بیشتر با پشتیبانی @{support_username} در ارتباط باشید.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for the start command to show the main menu."""
    await start(update, context)


async def check_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT is_admin FROM users WHERE tg_id = %s", (user_id,))
                result = cur.fetchone()
                return result is not None and result[0]
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False


def get_admin_keyboard():
    """Create admin panel keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("➕ افزودن اکانت", callback_data="admin:addseat"),
            InlineKeyboardButton("📂 افزودن گروهی (CSV)", callback_data="admin:bulkcsv"),
            InlineKeyboardButton("💲 تغییر قیمت سرویس", callback_data="admin:price")
        ],
        [
            InlineKeyboardButton("تغییر کارت", callback_data="admin:card"),
            InlineKeyboardButton("تغییر قیمت دلار", callback_data="admin:usd")
        ],
        [
            InlineKeyboardButton("آمار", callback_data="admin:stats"),
            InlineKeyboardButton("بکاپ دیتابیس", callback_data="admin:backup")
        ],
        [
            InlineKeyboardButton("لینک‌های UTM", callback_data="admin:utm"),
            InlineKeyboardButton("بردکست", callback_data="admin:broadcast")
        ],
        [
            InlineKeyboardButton("یوزرها", callback_data="admin:users"),
            InlineKeyboardButton("حذف سرویس", callback_data="admin:delete_service")
        ],
        [
            InlineKeyboardButton("غیرفعال کارت", callback_data="admin:disable_card"),
            InlineKeyboardButton("غیرفعال کریپتو", callback_data="admin:disable_crypto")
        ],
        [
            InlineKeyboardButton("فعال‌سازی درگاه", callback_data="admin:enable_gateway")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /admin command to show admin panel."""
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    
    if not is_admin:
        await update.message.reply_text("شما دسترسی ادمین ندارید.")
        return
    
    # Show admin panel
    await update.message.reply_text(
        f"💻 *پنل مدیریت*\n\n"
        f"به پنل مدیریت بات خوش آمدید.\n"
        f"لطفا گزینه مورد نظر خود را انتخاب کنید:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /broadcast command to send a message to all users."""
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    
    if not is_admin:
        await update.message.reply_text("شما دسترسی ادمین ندارید.")
        return
    
    # Check if message is provided
    if not context.args or len(''.join(context.args).strip()) == 0:
        await update.message.reply_text(
            "لطفا متن پیام را وارد کنید:\n"
            "/broadcast <متن پیام>"
        )
        return
    
    # Get broadcast message
    broadcast_text = " ".join(context.args)
    
    # Get all users from database
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tg_id FROM users")
                users = [row[0] for row in cur.fetchall()]
                
                # Log broadcast event in order_log
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (NULL, %s) RETURNING id",
                    (f"Broadcast: {broadcast_text[:50]}{'...' if len(broadcast_text) > 50 else ''}",)
                )
                log_id = cur.fetchone()[0]
                conn.commit()
                
    except Exception as e:
        logger.error(f"Error getting users for broadcast: {e}")
        await update.message.reply_text("خطا در دریافت لیست کاربران.")
        return
    
    # Confirm broadcast
    await update.message.reply_text(
        f"📣 *در حال ارسال پیام به {len(users)} کاربر*\n\n"
        f"پیام شما:\n"
        f"`{broadcast_text}`\n\n"
        f"لطفا منتظر بمانید. این فرایند ممکن است چند دقیقه طول بکشد.",
        parse_mode="Markdown"
    )
    
    # Start broadcast in background
    asyncio.create_task(send_broadcast_messages(context.bot, broadcast_text, users, update.effective_chat.id))


async def backup_db(bot, status_message):
    """Create a database backup using pg_dump and send it to the admin channel."""
    if not RECEIPT_CHANNEL_ID:
        await status_message.edit_text(
            "❌ *خطا: RECEIPT_CHANNEL_ID تنظیم نشده است*",
            parse_mode="Markdown"
        )
        return
        
    try:
        # Parse database connection string
        db_uri = DB_URI
        if not db_uri:
            await status_message.edit_text(
                "❌ *خطا: DB_URI تنظیم نشده است*",
                parse_mode="Markdown"
            )
            return
            
        # Extract database connection details
        # Expected format: postgresql://username:password@host:port/dbname
        db_parts = db_uri.replace('postgresql://', '').split('@')
        if len(db_parts) != 2:
            await status_message.edit_text(
                "❌ *خطا: فرمت DB_URI نامعتبر است*",
                parse_mode="Markdown"
            )
            return
            
        user_pass = db_parts[0].split(':')
        host_port_db = db_parts[1].split('/')
        
        if len(user_pass) != 2 or len(host_port_db) < 2:
            await status_message.edit_text(
                "❌ *خطا: فرمت DB_URI نامعتبر است*",
                parse_mode="Markdown"
            )
            return
            
        username = user_pass[0]
        password = user_pass[1]
        
        host_port = host_port_db[0].split(':')
        host = host_port[0]
        port = host_port[1] if len(host_port) > 1 else '5432'
        
        dbname = host_port_db[1]
        
        # Create a temporary file for the backup
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"wind_reseller_backup_{timestamp}.sql"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / backup_filename
            
            # Set up environment with password
            env = os.environ.copy()
            env["PGPASSWORD"] = password
            
            # Create backup using pg_dump
            await status_message.edit_text(
                "💾 *در حال اجرای pg_dump...*",
                parse_mode="Markdown"
            )
            
            # Build pg_dump command
            cmd = [
                "pg_dump",
                "-h", host,
                "-p", port,
                "-U", username,
                "-F", "p",  # Plain text format
                "-f", str(backup_path),
                dbname
            ]
            
            # Run pg_dump
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"pg_dump failed: {error_msg}")
                await status_message.edit_text(
                    f"❌ *خطا در تهیه بکاپ*\n\n`{error_msg[:500]}`",
                    parse_mode="Markdown"
                )
                return
            
            # Check if backup file exists and has content
            if not backup_path.exists() or backup_path.stat().st_size == 0:
                await status_message.edit_text(
                    "❌ *خطا: فایل بکاپ ایجاد نشد*",
                    parse_mode="Markdown"
                )
                return
            
            # Send the backup file to the receipt channel
            await status_message.edit_text(
                "📤 *در حال ارسال فایل بکاپ...*",
                parse_mode="Markdown"
            )
            
            file_size = backup_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            try:
                with open(backup_path, "rb") as backup_file:
                    await bot.send_document(
                        chat_id=RECEIPT_CHANNEL_ID,
                        document=backup_file,
                        filename=backup_filename,
                        caption=f"Database Backup - {timestamp}\nSize: {file_size_mb:.2f} MB"
                    )
                
                # Update status message
                await status_message.edit_text(
                    f"✅ *بکاپ با موفقیت ایجاد و ارسال شد*\n\n"
                    f"📁 نام فایل: `{backup_filename}`\n"
                    f"💾 حجم: {file_size_mb:.2f} MB",
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard()
                )
            except Exception as e:
                logger.error(f"Error sending backup file: {e}")
                await status_message.edit_text(
                    f"⚠️ *بکاپ ایجاد شد اما در ارسال آن خطا رخ داد*\n\n"
                    f"خطا: `{str(e)[:200]}`",
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard()
                )
    except Exception as e:
        logger.error(f"Error in backup_db: {e}")
        await status_message.edit_text(
            f"❌ *خطا در تهیه بکاپ*\n\n`{str(e)[:500]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )


async def send_broadcast_messages(bot, message, user_ids, admin_chat_id):
    """Send broadcast messages to all users with rate limiting and error handling."""
    # Constants for broadcasting - updated per requirements
    CHUNK_SIZE = 20  # Number of users to process in each chunk (changed from 30 to 20)
    SLEEP_BETWEEN_MESSAGES = 0.1  # Seconds to sleep between individual messages
    SLEEP_BETWEEN_CHUNKS = 3  # Seconds to sleep between chunks
    
    success_count = 0
    error_count = 0
    blocked_count = 0
    retry_count = 0
    
    # Process users in chunks to avoid hitting rate limits
    for i in range(0, len(user_ids), CHUNK_SIZE):
        chunk = user_ids[i:i+CHUNK_SIZE]
        logger.info(f"Processing broadcast chunk {i//CHUNK_SIZE + 1}/{(len(user_ids) + CHUNK_SIZE - 1) // CHUNK_SIZE}")
        
        # Process each user in the chunk
        for user_id in chunk:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="Markdown"
                )
                success_count += 1
                
                # Small delay between messages to avoid flood limits
                await asyncio.sleep(SLEEP_BETWEEN_MESSAGES)
                
            except RetryAfter as e:
                # Handle Telegram rate limiting
                retry_seconds = e.retry_after
                logger.warning(f"Rate limit hit, sleeping for {retry_seconds} seconds")
                await asyncio.sleep(retry_seconds)
                retry_count += 1
                
                # Retry this user
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode="Markdown"
                    )
                    success_count += 1
                except Exception as retry_e:
                    logger.error(f"Failed to send message on retry: {retry_e}")
                    error_count += 1
                    
            except Forbidden:
                # User has blocked the bot
                logger.info(f"User {user_id} has blocked the bot")
                blocked_count += 1
                
            except Exception as e:
                # Other errors
                logger.error(f"Error sending broadcast message to {user_id}: {e}")
                error_count += 1
        
        # Sleep between chunks to avoid hitting rate limits
        if i + CHUNK_SIZE < len(user_ids):  # If not the last chunk
            await asyncio.sleep(SLEEP_BETWEEN_CHUNKS)
    
    # Send summary to admin
    summary = (
        f"📣 *نتیجه ارسال پیام گروهی*\n\n"
        f"✅ ارسال موفق: *{success_count}*\n"
        f"❌ خطا در ارسال: *{error_count}*\n"
        f"🚫 بلاک شده: *{blocked_count}*\n"
        f"🔄 تلاش مجدد: *{retry_count}*\n\n"
        f"💬 متن پیام:\n`{message[:100]}{'...' if len(message) > 100 else ''}`"
    )
    
    try:
        # Log to database that broadcast completed
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (NULL, %s)",
                    (f"Broadcast completed: {success_count} sent, {error_count} errors, {blocked_count} blocked",)
                )
                conn.commit()
        
        # Send summary to admin
        await bot.send_message(
            chat_id=admin_chat_id,
            text=summary,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send broadcast summary to admin: {e}")


async def manage_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show and manage user's approved services."""
    user = update.effective_user
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user ID
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                result = cur.fetchone()
                if not result:
                    # User not found in database
                    user_id = await create_or_get_user(user)
                else:
                    user_id = result[0]
                
                # Get user's approved orders with seat information
                cur.execute(
                    """SELECT o.id, s.email, s.id as seat_id 
                       FROM orders o 
                       JOIN seats s ON o.seat_id = s.id 
                       WHERE o.user_id = %s AND o.status = 'approved' 
                       ORDER BY o.approved_at DESC""",
                    (user_id,)
                )
                orders = cur.fetchall()
                
                # Create message and keyboard
                if not orders:
                    message = (
                        f"🔐 *مدیریت سرویس*\n\n"
                        f"❌ شما هیچ سرویس فعالی ندارید.\n\n"
                        f"👉 برای خرید سرویس از منوی اصلی گزینه 'خرید سرویس' را انتخاب کنید."
                    )
                    keyboard = [
                        [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")]
                    ]
                else:
                    message = f"🔐 *مدیریت سرویس*\n\nسرویس‌های فعال شما:\n"
                    
                    # Create buttons for each service
                    keyboard = []
                    for order_id, email, seat_id in orders:
                        message += f"\n✅ سرویس #{order_id}: `{email}`"
                        # Add 2FA code button for this service
                        keyboard.append([
                            InlineKeyboardButton(
                                f"📲 کد 2FA برای {email}", 
                                callback_data=f"code:{seat_id}"
                            )
                        ])
                    
                    # Add back button
                    message += "\n\nبرای دریافت کد 2FA روی دکمه مربوطه کلیک کنید."
                    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Send message
                if update.callback_query:
                    await update.callback_query.edit_message_text(
                        message,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                else:
                    await update.message.reply_text(
                        message,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
    
    except Exception as e:
        logger.error(f"Error managing services: {e}")
        error_message = "متأسفانه در نمایش سرویس‌ها خطایی رخ داد. لطفا بعدا تلاش کنید."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message)
        else:
            await update.message.reply_text(error_message)


async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's wallet balance and free credit."""
    user = update.effective_user
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user ID
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                result = cur.fetchone()
                if not result:
                    # User not found in database
                    user_id = await create_or_get_user(user)
                else:
                    user_id = result[0]
                    
                # Get wallet information
                cur.execute(
                    "SELECT balance, free_credit FROM wallets WHERE user_id = %s",
                    (user_id,)
                )
                wallet = cur.fetchone()
                
                if not wallet:
                    # Create wallet if it doesn't exist
                    cur.execute(
                        "INSERT INTO wallets (user_id) VALUES (%s) RETURNING balance, free_credit",
                        (user_id,)
                    )
                    wallet = cur.fetchone()
                    conn.commit()
                
                balance, free_credit = wallet
                
                # Format numbers with Persian style
                def format_currency(amount):
                    # Format with thousand separators
                    formatted = f"{int(amount):,}"
                    # Replace numbers with Persian digits if needed
                    return formatted + " تومان"
                
                # Create wallet message
                message = (
                    f"💰 *کیف پول شما*\n\n"
                    f"💵 موجودی: *{format_currency(balance)}*\n"
                    f"🎁 اعتبار رایگان: *{format_currency(free_credit)}*\n\n"
                    f"💫 موجودی کل: *{format_currency(balance + free_credit)}*\n\n"
                    f"📝 از منوی اصلی می‌توانید سرویس خریداری کنید."
                )
                
                # Create back button
                keyboard = [
                    [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Send wallet information
                if update.callback_query:
                    await update.callback_query.edit_message_text(
                        message,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                else:
                    await update.message.reply_text(
                        message,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                
    except Exception as e:
        logger.error(f"Error showing wallet: {e}")
        error_message = "متأسفانه در نمایش اطلاعات کیف پول خطایی رخ داد. لطفا بعدا تلاش کنید."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message)
        else:
            await update.message.reply_text(error_message)


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /buy command to purchase service."""
    await show_purchase_info(update, context)


async def show_purchase_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show purchase information and payment details."""
    # Get card number from settings or environment variable
    card_number = db.get_setting('card_number', CARD_NUMBER)
    if not card_number:
        card_number = "شماره کارت در تنظیمات سیستم ثبت نشده است"
        logger.error("Card number not configured in settings or environment variables")
    
    # Get service price from settings or use default value
    amount = int(db.get_setting('service_price', '70000'))
    amount_display = f"{amount:,} تومان"
    
    # Get user ID
    user = update.effective_user
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user_id from database
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                result = cur.fetchone()
                if not result:
                    # User not found, create user
                    user_id = await create_or_get_user(user)
                else:
                    user_id = result[0]
                
                # Create new pending order
                utm_keyword = context.user_data.get('utm', None)
                cur.execute(
                    "INSERT INTO orders (user_id, amount, utm_keyword) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, amount, utm_keyword)
                )
                order_id = cur.fetchone()[0]
                
                # Log order creation
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                    (order_id, "Order created")
                )
                conn.commit()
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "خطا در ثبت سفارش. لطفا بعدا تلاش کنید."
            )
        return
    
    # Store order_id in user_data for handling receipt
    context.user_data['pending_order_id'] = order_id
    
    # Send payment instructions
    message = (
        f"💳 *اطلاعات پرداخت*\n\n"
        f"💰 مبلغ: *{amount_display}*\n\n"
        f"💳 شماره کارت:\n`{card_number}`\n\n"
        f"✏️ به نام: *محمد محمدی*\n\n"
        f"📧 شناسه سفارش: `#{order_id}`\n\n"
        f"❌ *لطفا شناسه سفارش را در توضیحات واریز ذکر کنید*\n\n"
        f"📷 پس از پرداخت، لطفا عکس رسید پرداخت خود را ارسال کنید."
    )
    
    # Send message
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(message, parse_mode="Markdown")
    elif isinstance(update, Update) and update.callback_query:
        await update.callback_query.edit_message_text(message, parse_mode="Markdown")


async def handle_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle receipt photos sent by the user."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Check if user has a pending order
    pending_order_id = context.user_data.get('pending_order_id', None)
    
    if not pending_order_id:
        # Check if user has any pending orders in database
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    # Get user_id
                    cur.execute("SELECT id FROM users WHERE tg_id = %s", (user.id,))
                    result = cur.fetchone()
                    if not result:
                        await update.message.reply_text(
                            "شما سفارش فعالی ندارید. ابتدا از طریق /buy سفارش جدیدی ثبت کنید."
                        )
                        return
                        
                    user_id = result[0]
                    
                    # Check for pending orders
                    cur.execute(
                        "SELECT id FROM orders WHERE user_id = %s AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                        (user_id,)
                    )
                    result = cur.fetchone()
                    if result:
                        pending_order_id = result[0]
                        # Store in user_data for future use
                        context.user_data['pending_order_id'] = pending_order_id
                    else:
                        await update.message.reply_text(
                            "شما سفارش فعالی ندارید. ابتدا از طریق /buy سفارش جدیدی ثبت کنید."
                        )
                        return
        except Exception as e:
            logger.error(f"Error checking for pending orders: {e}")
            await update.message.reply_text(
                "خطا در بررسی سفارشات. لطفا بعدا تلاش کنید."
            )
            return
    
    # We have a pending order, process the receipt
    photo = update.message.photo[-1]  # Get the largest size of the photo
    file_id = photo.file_id
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Update order status to 'receipt'
                cur.execute(
                    "UPDATE orders SET status = 'receipt' WHERE id = %s", 
                    (pending_order_id,)
                )
                
                # Store receipt information
                cur.execute(
                    "INSERT INTO receipts (order_id, tg_file_id, orig_chat_id) VALUES (%s, %s, %s)",
                    (pending_order_id, file_id, chat_id)
                )
                
                # Log event
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                    (pending_order_id, "Receipt submitted")
                )
                conn.commit()
    except Exception as e:
        logger.error(f"Error processing receipt: {e}")
        await update.message.reply_text(
            "خطا در ذخیره رسید. لطفا بعدا تلاش کنید."
        )
        return
    
    # Send confirmation to user
    await update.message.reply_text(
        f"✅ رسید پرداخت شما برای سفارش #{pending_order_id} دریافت شد.\n\n"
        f"✏️ سفارش شما در حال بررسی است و به زودی نتیجه آن اعلام خواهد شد."
    )
    
    # Forward receipt to admin channel
    if RECEIPT_CHANNEL_ID:
        try:
            # Forward with order info in caption
            forwarded_msg = await context.bot.send_photo(
                chat_id=RECEIPT_CHANNEL_ID,
                photo=file_id,
                caption=f"Order #{pending_order_id}\nUser: {user.first_name} (@{user.username or 'N/A'})\nTG ID: {user.id}",
                reply_markup=get_admin_approval_keyboard(pending_order_id)
            )
            
            # Save forwarded message ID
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE receipts SET channel_msg_id = %s WHERE order_id = %s",
                        (forwarded_msg.message_id, pending_order_id)
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"Error forwarding receipt to admin channel: {e}")
    else:
        logger.error("RECEIPT_CHANNEL_ID not set, could not forward receipt")
    
    # Clear pending order from user_data
    context.user_data.pop('pending_order_id', None)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Help message")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    await update.message.reply_text(update.message.text)


async def get_available_seat():
    """Find an available seat where sold < max_slots."""
    try:
        with db.get_conn() as conn:
            # Start a transaction with row locking
            conn.autocommit = False
            with conn.cursor() as cur:
                # Find a seat with available slots
                cur.execute(
                    "SELECT id, email, pass_enc, secret_enc, max_slots, sold FROM seats "
                    "WHERE status = 'active' AND sold < max_slots "
                    "ORDER BY sold DESC LIMIT 1 FOR UPDATE"
                )
                result = cur.fetchone()
                
                if not result:
                    conn.rollback()
                    return None
                    
                seat_id, email, pass_enc, secret_enc, max_slots, sold = result
                
                # Increment sold count
                cur.execute(
                    "UPDATE seats SET sold = sold + 1 WHERE id = %s",
                    (seat_id,)
                )
                
                # Commit the transaction
                conn.commit()
                
                return {
                    "id": seat_id,
                    "email": email,
                    "pass_enc": pass_enc,
                    "secret_enc": secret_enc,
                    "max_slots": max_slots,
                    "sold": sold + 1  # Include the increment
                }
    except Exception as e:
        logger.error(f"Error getting available seat: {e}")
        try:
            conn.rollback()
        except:
            pass
        return None


async def approve_order(order_id):
    """Approve an order and assign a seat."""
    try:
        # Get an available seat
        seat = await get_available_seat()
        if not seat:
            return False, "خطا: هیچ صندلی خالی برای تخصیص وجود ندارد"
        
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get order details for UTM tracking
                cur.execute(
                    "SELECT user_id, amount, utm_keyword FROM orders WHERE id = %s",
                    (order_id,)
                )
                result = cur.fetchone()
                if not result:
                    return False, "خطا: سفارش یافت نشد"
                    
                user_id, amount, utm_keyword = result
                
                # Update order
                cur.execute(
                    "UPDATE orders SET status = 'approved', seat_id = %s, approved_at = %s "
                    "WHERE id = %s",
                    (seat["id"], datetime.datetime.now(), order_id)
                )
                
                # Log the approval
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                    (order_id, "Order approved")
                )
                
                # Get user's Telegram ID for notification
                cur.execute("SELECT tg_id FROM users WHERE id = %s", (user_id,))
                tg_id_result = cur.fetchone()
                if not tg_id_result:
                    return False, "خطا: کاربر یافت نشد"
                    
                tg_id = tg_id_result[0]
                
                # Update UTM stats if keyword exists
                if utm_keyword:
                    # Increment buys count
                    db.inc_utm(utm_keyword, 'buys')
                    # Increment amount
                    db.inc_utm(utm_keyword, 'amount', amount)
                
                conn.commit()
                
                return True, {
                    "tg_id": tg_id,
                    "order_id": order_id,
                    "seat": seat
                }
    except Exception as e:
        logger.error(f"Error approving order: {e}")
        return False, str(e)


async def reject_order(order_id):
    """Reject an order."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user's Telegram ID for notification
                cur.execute(
                    "SELECT u.tg_id FROM users u JOIN orders o ON u.id = o.user_id "
                    "WHERE o.id = %s",
                    (order_id,)
                )
                result = cur.fetchone()
                if not result:
                    return False, "خطا: کاربر یافت نشد"
                    
                tg_id = result[0]
                
                # Update order status
                cur.execute(
                    "UPDATE orders SET status = 'rejected' WHERE id = %s",
                    (order_id,)
                )
                
                # Log the rejection
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                    (order_id, "Order rejected")
                )
                
                conn.commit()
                
                return True, tg_id
    except Exception as e:
        logger.error(f"Error rejecting order: {e}")
        return False, str(e)


# Admin state constants for conversation handlers
ADMIN_WAITING_CARD = 1
ADMIN_WAITING_USD_RATE = 2
ADMIN_WAITING_SEAT_INFO = 3
ADMIN_WAITING_CSV = 4
ADMIN_WAITING_PRICE = 5


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show admin statistics."""
    query = update.callback_query
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get user count
                cur.execute("SELECT COUNT(*) FROM users")
                user_count = cur.fetchone()[0]
                
                # Get approved sales count
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'approved'")
                approved_sales = cur.fetchone()[0]
                
                # Get total amount
                cur.execute("SELECT SUM(amount) FROM orders WHERE status = 'approved'")
                total_amount = cur.fetchone()[0] or 0
                
                # Get seats sold
                cur.execute("SELECT SUM(sold) FROM seats")
                seats_sold = cur.fetchone()[0] or 0
                
                # Get available seats
                cur.execute("SELECT SUM(max_slots - sold) FROM seats WHERE status = 'active'")
                available_slots = cur.fetchone()[0] or 0
                
                # Format statistics message
                stats_message = (
                    f"📊 *آمار سیستم*\n\n"
                    f"👤 تعداد کاربران: *{user_count:,}*\n"
                    f"💳 تعداد فروش: *{approved_sales:,}*\n"
                    f"💰 مبلغ کل فروش: *{total_amount:,} تومان*\n\n"
                    f"💺 صندلی‌های فروخته شده: *{int(seats_sold):,}*\n"
                    f"💿 ظرفیت باقیمانده: *{int(available_slots):,}*"
                )
                
                # Send statistics
                await query.edit_message_text(
                    stats_message,
                    reply_markup=get_admin_keyboard(),
                    parse_mode="Markdown"
                )
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        await query.edit_message_text(
            "خطا در دریافت آمار. لطفا بعدا تلاش کنید.",
            reply_markup=get_admin_keyboard()
        )


async def handle_admin_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin card number change request."""
    query = update.callback_query
    
    # Get current card number
    current_card = db.get_setting('card_number', CARD_NUMBER)
    
    await query.edit_message_text(
        f"💳 *تغییر شماره کارت*\n\n"
        f"شماره کارت فعلی: `{current_card or 'تنظیم نشده'}`\n\n"
        f"لطفا شماره کارت جدید را وارد کنید:",
        parse_mode="Markdown"
    )
    
    # Set next state
    context.user_data['admin_action'] = 'set_card'
    return ADMIN_WAITING_CARD


async def handle_admin_usd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin USD rate change request."""
    query = update.callback_query
    
    # Get current USD rate
    current_rate = db.get_setting('usd_rate', '0')
    
    await query.edit_message_text(
        f"💲 *تغییر نرخ دلار*\n\n"
        f"نرخ فعلی دلار: `{current_rate} تومان`\n\n"
        f"لطفا نرخ جدید دلار را به تومان وارد کنید:",
        parse_mode="Markdown"
    )
    
    # Set next state
    context.user_data['admin_action'] = 'set_usd_rate'
    return ADMIN_WAITING_USD_RATE


async def handle_add_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the add seat callback."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return -1
    
    # Set the awaiting flag and send instructions
    context.user_data['awaiting_single_seat'] = True
    
    await query.edit_message_text(
        f"➕ *افزودن اکانت جدید*\n\n"
        f"لطفاً اطلاعات اکانت را در یک خط به‌صورت:\n"
        f"`email password secret [slots]` ارسال کنید\n\n"
        f"slots اختیاری است و پیش‌فرض 15 می‌باشد.",
        parse_mode="Markdown"
    )
    
    return ADMIN_WAITING_SEAT_INFO


async def handle_bulk_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the bulk CSV upload callback."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return -1
    
    # Set the awaiting flag and send instructions
    context.user_data['awaiting_csv'] = True
    
    await query.edit_message_text(
        f"📂 *افزودن گروهی اکانت از CSV*\n\n"
        f"فایل CSV با ستون‌های email,password,secret,slots را ارسال کنید\n\n"
        f"*توجه:* ستون slots اختیاری است و پیش‌فرض 15 می‌باشد.",
        parse_mode="Markdown"
    )
    
    return ADMIN_WAITING_CSV


async def handle_utm_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show UTM tracking statistics."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        # Fetch UTM stats from database
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT keyword, starts, buys, amount FROM utm_stats ORDER BY starts DESC"
                )
                utm_stats = cur.fetchall()
        
        if not utm_stats:
            # No stats available
            await query.edit_message_text(
                "📈 *آمار UTM*\n\n"
                "آماری ثبت نشده است.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Format stats using tabulate
        headers = ["keyword", "شروع", "خرید", "مبلغ"]
        table = tabulate(utm_stats, headers=headers, tablefmt="github")
        
        # Build the message
        message = f"📈 *آمار لینک‌های UTM*\n\n"
        message += f"```\n{table}\n```"
        
        # Send the formatted table
        await query.edit_message_text(
            message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Error displaying UTM stats: {e}")
        await query.edit_message_text(
            "❌ *خطا در نمایش آمار UTM*\n\n"
            f"`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )


async def handle_change_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the change price callback."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return -1
    
    # Get current price
    current_price = db.get_setting('service_price', '70000')
    
    # Set the awaiting flag and send instructions
    context.user_data['awaiting_price'] = True
    
    await query.edit_message_text(
        f"💸 *تغییر قیمت سرویس*\n\n"
        f"قیمت فعلی: {current_price:,} تومان\n\n"
        f"قیمت جدید سرویس (تومان) را وارد کنید:",
        parse_mode="Markdown"
    )
    
    return ADMIN_WAITING_PRICE


async def process_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the price input message."""
    message_text = update.message.text.strip()
    
    # Check if we're expecting a price input
    if not context.user_data.get('awaiting_price', False):
        return -1
    
    # Clear the flag
    context.user_data.pop('awaiting_price', None)
    
    try:
        # Parse and validate the price
        price = int(message_text)
        if price <= 0:
            await update.message.reply_text(
                "❌ *خطا: قیمت باید عدد مثبت باشد*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return -1
        
        # Store the price in settings
        db.set_setting('service_price', str(price))
        
        # Format the price with Persian style
        formatted_price = f"{price:,}"
        
        # Confirm the change
        await update.message.reply_text(
            f"✅ *قیمت سرویس به {formatted_price} تومان تغییر کرد*",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        logger.info(f"Admin {update.effective_user.id} changed service price to {price} tomans")
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
            f"❌ *خطا در تغییر قیمت سرویس*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1


async def process_csv_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the uploaded CSV file for bulk seat import."""
    # Check if we're expecting a CSV file
    if not context.user_data.get('awaiting_csv', False):
        return -1
    
    # Clear the flag
    context.user_data.pop('awaiting_csv', None)
    
    # Get the document
    message = update.message
    document = message.document
    
    # Check if it's a CSV file
    if not document.file_name.lower().endswith('.csv'):
        await message.reply_text(
            "❌ *خطا: فایل باید CSV باشد*",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1
    
    # Status message
    status_msg = await message.reply_text(
        "⏳ *در حال دانلود و پردازش فایل CSV...*",
        parse_mode="Markdown"
    )
    
    try:
        # Download the file
        file = await context.bot.get_file(document.file_id)
        csv_file_path = f"temp_{message.message_id}.csv"
        await file.download_to_drive(csv_file_path)
        
        # Process CSV
        success_count = 0
        duplicate_count = 0
        error_count = 0
        errors = []
        
        try:
            # Update status
            await status_msg.edit_text(
                "⏳ *در حال پردازش ردیف‌های CSV...*",
                parse_mode="Markdown"
            )
            
            # Open and process CSV
            with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                # Verify required columns
                required_fields = ['email', 'password', 'secret']
                for field in required_fields:
                    if field not in reader.fieldnames:
                        await status_msg.edit_text(
                            f"❌ *خطا: ستون {field} در فایل CSV یافت نشد*\n\n"
                            f"ستون‌های مورد نیاز: email, password, secret, slots (اختیاری)",
                            parse_mode="Markdown",
                            reply_markup=get_admin_keyboard()
                        )
                        os.remove(csv_file_path)  # Clean up
                        return -1
                
                # Process each row
                for i, row in enumerate(reader, 1):
                    try:
                        # Extract data
                        email = row['email'].strip()
                        password = row['password'].strip()
                        secret = row['secret'].strip()
                        
                        # Get slots (optional)
                        max_slots = 15  # Default
                        if 'slots' in row and row['slots'].strip():
                            try:
                                max_slots = int(row['slots'].strip())
                                if max_slots <= 0:
                                    max_slots = 15
                            except ValueError:
                                pass  # Use default if conversion fails
                        
                        # Validate email
                        if '@' not in email:
                            error_count += 1
                            errors.append(f"Row {i}: Invalid email format")
                            continue
                        
                        # Encrypt credentials
                        pass_enc = encrypt(password)
                        secret_enc = encrypt(secret)
                        
                        # Insert into database
                        with db.get_conn() as conn:
                            with conn.cursor() as cur:
                                try:
                                    cur.execute(
                                        "INSERT INTO seats (email, pass_enc, secret_enc, max_slots) "
                                        "VALUES (%s, %s, %s, %s) RETURNING id",
                                        (email, pass_enc, secret_enc, max_slots)
                                    )
                                    seat_id = cur.fetchone()[0]
                                    conn.commit()
                                    success_count += 1
                                except psycopg2.errors.UniqueViolation:
                                    # Email already exists
                                    conn.rollback()
                                    duplicate_count += 1
                    except Exception as row_error:
                        error_count += 1
                        errors.append(f"Row {i}: {str(row_error)[:50]}")
                        
                    # Update status every 10 rows
                    if i % 10 == 0:
                        await status_msg.edit_text(
                            f"⏳ *پردازش ردیف‌های CSV...*\n\n"
                            f"تعداد ردیف‌های پردازش شده: {i}\n"
                            f"موفق: {success_count} | تکراری: {duplicate_count} | خطا: {error_count}",
                            parse_mode="Markdown"
                        )
        finally:
            # Clean up the temp file
            if os.path.exists(csv_file_path):
                os.remove(csv_file_path)
        
        # Final summary
        status_text = f"✅ *پردازش CSV تکمیل شد*\n\n"
        status_text += f"📊 *نتایج:*\n"
        status_text += f"- صندلی‌های اضافه شده: {success_count}\n"
        status_text += f"- ایمیل‌های تکراری: {duplicate_count}\n"
        
        if error_count > 0:
            status_text += f"- خطاها: {error_count}\n"
            # Show first few errors
            if errors:
                status_text += "\n*چند خطای اول:*\n"
                for e in errors[:3]:  # Show first 3 errors
                    status_text += f"- {e}\n"
                if len(errors) > 3:
                    status_text += f"- ... ({len(errors) - 3} خطای دیگر)\n"
        
        await status_msg.edit_text(
            status_text,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        logger.info(f"Admin {update.effective_user.id} bulk-imported {success_count} seats, {duplicate_count} duplicates, {error_count} errors")
        return -1
        
    except Exception as e:
        logger.error(f"Error processing CSV file: {e}")
        await status_msg.edit_text(
            f"❌ *خطا در پردازش فایل CSV*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1


async def process_add_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the add seat input message."""
    message_text = update.message.text.strip()
    
    # Check if we're expecting a seat input
    if not context.user_data.get('awaiting_single_seat', False):
        return -1
    
    # Clear the flag
    context.user_data.pop('awaiting_single_seat', None)
    
    # Parse the input
    parts = message_text.split()
    if len(parts) < 3:
        await update.message.reply_text(
            "❌ *خطا: فرمت نامعتبر*\n\n"
            "لطفاً اطلاعات را به صورت `email password secret [slots]` وارد کنید.",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1
    
    try:
        # Extract the parts
        email = parts[0]
        password = parts[1]
        secret = parts[2]
        max_slots = int(parts[3]) if len(parts) > 3 else 15
        
        # Validate email
        if '@' not in email:
            await update.message.reply_text(
                "❌ *خطا: ایمیل نامعتبر*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return -1
        
        # Validate slots
        if max_slots <= 0:
            await update.message.reply_text(
                "❌ *خطا: تعداد صندلی‌ها باید بزرگتر از صفر باشد*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return -1
        
        # Encrypt credentials
        pass_enc = encrypt(password)
        secret_enc = encrypt(secret)
        
        # Insert into database
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO seats (email, pass_enc, secret_enc, max_slots) VALUES (%s, %s, %s, %s) RETURNING id",
                    (email, pass_enc, secret_enc, max_slots)
                )
                seat_id = cur.fetchone()[0]
                conn.commit()
        
        # Confirm success
        await update.message.reply_text(
            f"✅ *صندلی ذخیره شد*\n\n"
            f"💬 ایمیل: `{email}`\n"
            f"💺 صندلی‌ها: {max_slots}\n"
            f"🆔 شناسه: #{seat_id}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        logger.info(f"Admin {update.effective_user.id} added new seat: {email} (ID: {seat_id})")
        return -1
        
    except Exception as e:
        logger.error(f"Error adding seat: {e}")
        await update.message.reply_text(
            f"❌ *خطا در افزودن صندلی*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return -1


async def admin_process_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process admin input for settings."""
    user = update.effective_user
    message_text = update.message.text.strip()
    action = context.user_data.get('admin_action')
    
    if not action:
        await update.message.reply_text("خطای سیستمی. لطفا مجددا /admin را اجرا کنید.")
        return -1  # End conversation
    
    if action == 'set_card':
        # Validate and set card number
        if not message_text or not message_text.replace('-', '').replace(' ', '').isdigit():
            await update.message.reply_text("شماره کارت نامعتبر است. لطفا دوباره تلاش کنید.")
            return ADMIN_WAITING_CARD
        
        # Set card number
        db.set_setting('card_number', message_text)
        await update.message.reply_text(
            f"✅ شماره کارت به `{message_text}` تغییر یافت.",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
    
    elif action == 'set_usd_rate':
        # Validate and set USD rate
        try:
            rate = int(message_text.replace(',', ''))
            if rate <= 0:
                raise ValueError("Rate must be positive")
                
            # Set USD rate
            db.set_setting('usd_rate', str(rate))
            await update.message.reply_text(
                f"✅ نرخ دلار به `{rate:,} تومان` تغییر یافت.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text("نرخ دلار نامعتبر است. لطفا یک عدد مثبت وارد کنید.")
            return ADMIN_WAITING_USD_RATE
    
    # Clear admin action
    context.user_data.pop('admin_action', None)
    return -1  # End conversation


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
    """Handle callback queries from inline keyboards."""
    query = update.callback_query
    await query.answer()  # Answer the callback query to stop loading indicator
    
    # Extract callback data
    data = query.data
    user = update.effective_user
    
    if data == "buy_service":
        # Handle buy service button
        await show_purchase_info(update, context)
        
    elif data == "wallet":
        # Handle wallet button
        await show_wallet(update, context)
        
    elif data == "manage_service":
        # Handle manage service button
        await manage_services(update, context)
        
    elif data == "back_to_menu":
        # Return to main menu
        await query.edit_message_text(
            f"🌬 *به بات فروش سرویس ویند خوش آمدید*\n\n"
            f"از منوی زیر، گزینه مورد نظر خود را انتخاب کنید.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
        
    # Admin panel callbacks
    elif data.startswith("admin:"):
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
            return
            
        admin_action = data.split(":")[1]
        
        if admin_action == "card":
            # Change card number
            return await handle_admin_card(update, context)
            
        elif admin_action == "usd":
            # Change USD rate
            return await handle_admin_usd_rate(update, context)
            
        elif admin_action == "stats":
            # Show statistics
            await admin_stats(update, context)
            
        elif admin_action == "broadcast":
            # Show broadcast prompt
            await query.edit_message_text(
                f"📣 *ارسال پیام گروهی*\n\n"
                f"برای ارسال پیام گروهی از دستور /broadcast استفاده کنید:\n\n"
                f"`/broadcast متن پیام شما`\n\n"
                f"این پیام به تمام کاربران بات ارسال خواهد شد.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            
        elif admin_action == "backup":
            # Create database backup
            status_msg = await query.edit_message_text(
                "📂 *در حال تهیه بکاپ از دیتابیس...*",
                parse_mode="Markdown"
            )
            await backup_db(context.bot, status_msg)
            
        elif admin_action == "addseat":
            # Add a new seat (account)
            await handle_add_seat(update, context)
            
        elif admin_action == "bulkcsv":
            # Bulk add seats from CSV
            await handle_bulk_csv(update, context)
            
        elif admin_action == "price":
            # Change service price
            await handle_change_price(update, context)
            
        elif admin_action == "utm":
            # Show UTM statistics
            await handle_utm_stats(update, context)
            
        # Other admin actions would be handled here
    
    # Handle order approval
    elif data.startswith("approve:"):
        # Check if user is admin
        is_admin = False
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT is_admin FROM users WHERE tg_id = %s", (user.id,))
                    result = cur.fetchone()
                    is_admin = result and result[0]
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
        
        if not is_admin:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
            return
        
        # Extract order ID
        order_id = int(data.split(":")[1])
        
        # Process approval
        success, result = await approve_order(order_id)
        
        if success:
            # Send credentials to user
            order_data = result
            seat = order_data["seat"]
            tg_id = order_data["tg_id"]
            order_id = order_data["order_id"]
            
            # Decrypt credentials
            email = seat["email"]
            password = decrypt(seat["pass_enc"])
            
            # Send message to user
            user_message = (
                f"🎉 *سفارش شماره #{order_id} تایید شد*\n\n"
                f"📧 ایمیل: `{email}`\n"
                f"🔑 رمز عبور: `{password}`\n\n"
                f"✅ از دکمه زیر برای دریافت کد 2FA استفاده کنید.\n\n"
                f"❌ لطفا اطلاعات حساب خود را با احتیاط نگهداری کنید."
            )
            
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=user_message,
                    parse_mode="Markdown",
                    reply_markup=get_2fa_button(seat["id"])
                )
            except Exception as e:
                logger.error(f"Error sending credentials to user: {e}")
            
            # Update receipt message caption
            try:
                # Get receipt channel message ID
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT channel_msg_id FROM receipts WHERE order_id = %s",
                            (order_id,)
                        )
                        result = cur.fetchone()
                        if result and result[0]:
                            channel_msg_id = result[0]
                            
                            # Update caption
                            await context.bot.edit_message_caption(
                                chat_id=RECEIPT_CHANNEL_ID,
                                message_id=channel_msg_id,
                                caption=f"Order #{order_id}\n\n✅ *تایید شده*\nصندلی: {seat['id']} ({seat['sold']}/{seat['max_slots']})",
                                parse_mode="Markdown"
                            )
            except Exception as e:
                logger.error(f"Error updating receipt caption: {e}")
            
            # Update admin message
            await query.edit_message_text(f"✅ سفارش #{order_id} تایید شد.")
        else:
            # Show error
            await query.edit_message_text(
                f"❌ خطا در تایید سفارش: {result}"
            )
    
    # Handle order rejection
    elif data.startswith("reject:"):
        # Check if user is admin
        is_admin = False
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT is_admin FROM users WHERE tg_id = %s", (user.id,))
                    result = cur.fetchone()
                    is_admin = result and result[0]
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
        
        if not is_admin:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
            return
        
        # Extract order ID
        order_id = int(data.split(":")[1])
        
        # Process rejection
        success, result = await reject_order(order_id)
        
        if success:
            tg_id = result
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=f"❌ *سفارش شماره #{order_id} رد شد*\n\n"
                         f"✏️ لطفا با پشتیبانی تماس بگیرید یا مجددا تلاش کنید.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error notifying user about rejection: {e}")
            
            # Update receipt message caption
            try:
                # Get receipt channel message ID
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT channel_msg_id FROM receipts WHERE order_id = %s",
                            (order_id,)
                        )
                        result = cur.fetchone()
                        if result and result[0]:
                            channel_msg_id = result[0]
                            
                            # Update caption
                            await context.bot.edit_message_caption(
                                chat_id=RECEIPT_CHANNEL_ID,
                                message_id=channel_msg_id,
                                caption=f"Order #{order_id}\n\n❌ *رد شده*",
                                parse_mode="Markdown"
                            )
            except Exception as e:
                logger.error(f"Error updating receipt caption: {e}")
            
            # Update admin message
            await query.edit_message_text(f"❌ سفارش #{order_id} رد شد.")
        else:
            # Show error
            await query.edit_message_text(
                f"❌ خطا در رد سفارش: {result}"
            )
    
    # Handle 2FA code request
    elif data.startswith("2fa:"):
        # Extract seat ID
        seat_id = int(data.split(":")[1])
        
        # Get the secret for the seat
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT secret_enc FROM seats WHERE id = %s", (seat_id,))
                    result = cur.fetchone()
                    if not result:
                        await query.edit_message_text("خطا: اطلاعات صندلی یافت نشد.")
                        return
                    
                    secret_enc = result[0]
                    
                    # Decrypt the secret
                    secret = decrypt(secret_enc)
                    
                    # Generate 2FA code using TOTP
                    import pyotp
                    totp = pyotp.TOTP(secret)
                    code = totp.now()
                    
                    # Reply with the code
                    await query.edit_message_text(
                        f"📲 *کد 2FA شما*: `{code}`\n\n"
                        f"⏰ این کد به مدت 30 ثانیه معتبر است.\n"
                        f"✅ برای دریافت کد جدید، مجددا روی دکمه کلیک کنید.",
                        reply_markup=get_2fa_button(seat_id),
                        parse_mode="Markdown"
                    )
        except Exception as e:
            logger.error(f"Error generating 2FA code: {e}")
            await query.edit_message_text(
                f"❌ خطا در تولید کد 2FA: {str(e)}"
            )
            
    # Handle quick TOTP code generation (alert style)
    elif data.startswith("code:"):
        # Extract secret ID from callback data
        secret_id = data.split(":")[1]
        
        try:
            # Get and decrypt secret
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT secret_enc FROM seats WHERE id = %s", (secret_id,))
                    result = cur.fetchone()
                    if not result:
                        await query.answer("خطا: اطلاعات یافت نشد", show_alert=True)
                        return
                    
                    secret_enc = result[0]
            
            # Decrypt secret
            secret = decrypt(secret_enc)
            
            # Generate TOTP code
            import pyotp
            import time
            
            totp = pyotp.TOTP(secret)
            code = totp.now()
            
            # Calculate remaining seconds until code expires
            remaining_seconds = 30 - (int(time.time()) % 30)
            
            # Show alert with code and TTL
            await query.answer(
                f"{code} \u2014 اعتبار {remaining_seconds} ثانیه",
                show_alert=True
            )
            
        except Exception as e:
            logger.error(f"Error generating TOTP code: {e}")
            await query.answer("خطا در تولید کد", show_alert=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by Updates."""
    logger.error(f"Exception while handling an update: {context.error}")


def main() -> None:
    """Start the bot."""
    # Create the Application
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in environment variables")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # Initialize database
    db.init_db()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))  # Alias for start
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Admin conversation handlers
    from telegram.ext import ConversationHandler
    admin_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_handler, pattern=r'^admin:')],
        states={
            ADMIN_WAITING_CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_input)],
            ADMIN_WAITING_USD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_input)],
            ADMIN_WAITING_SEAT_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_seat)],
            ADMIN_WAITING_CSV: [MessageHandler(filters.Document.ALL, process_csv_upload)],
            ADMIN_WAITING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_price_input)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: -1)],
        name="admin_conversation"
    )
    application.add_handler(admin_conv_handler)
    
    # Photo handler for receipts
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
    
    # Callback query handler for inline keyboards
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Echo handler (lowest priority)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    # Register error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
