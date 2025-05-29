#!/usr/bin/env python
"""
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
"""

import csv
import io
import json
import logging
import os
import re
import uuid
import telegram
import time
import base64
import pyotp
import random
import asyncio
import traceback
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Union, Tuple, List, Any

# Import handlers modules
from handlers import referral
from handlers import admin_cards
from handlers import card_manager
from tabulate import tabulate

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

import db

# Import enhanced debug logger
try:
    from debug_logger import log_exception, log_function_call, log_telegram_update, logger
    ENHANCED_LOGGING = True
    print("Enhanced logging enabled")
except ImportError:
    # Configure basic logging if debug_logger is not available
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
        level=logging.INFO
    )
    logger = logging.getLogger(__name__)
    ENHANCED_LOGGING = False
    print("Using basic logging - debug_logger module not found")
    
    # Define dummy decorators
    def log_function_call(func):
        return func
        
    def log_exception(e, context=None):
        logger.error(f"Exception: {e}\nContext: {context}")
        return str(e)
        
    def log_telegram_update(update):
        if update.message:
            logger.info(f"Message from {update.effective_user.id}: {update.message.text if update.message.text else '[Media]'}")
        elif update.callback_query:
            logger.info(f"Callback from {update.effective_user.id}: {update.callback_query.data}")

# Create logs directory if it doesn't exist
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

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
            InlineKeyboardButton("📣 کسب اعتبار با دعوت دوستان", callback_data="menu:ref")
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
    """Handle the /start command, create user, process UTM, and handle referrals."""
    user = update.effective_user
    
    # Check for UTM parameters or referrals in the start command
    message_text = update.message.text if update.message else ""
    match = re.search(r"\/start\s+(\w+)", message_text)
    
    if match:
        param = match.group(1)
        
        # Handle referral link
        if param.startswith('ref'):
            try:
                ref_id = int(param[3:])  # Extract referrer id from 'ref12345'
                
                # Check if this is a valid user id and not self-referral
                if ref_id != user.id:
                    with db.get_conn() as conn:
                        with conn.cursor() as cur:
                            # Check if referrer exists
                            cur.execute("SELECT id FROM users WHERE tg_id = %s", (ref_id,))
                            referrer_result = cur.fetchone()
                            
                            if referrer_result:
                                referrer_id = referrer_result[0]
                                
                                # Check if user already has a referrer
                                cur.execute("SELECT referrer FROM users WHERE tg_id = %s", (user.id,))
                                user_result = cur.fetchone()
                                
                                if user_result and user_result[0] is None:
                                    # Set referrer if user doesn't have one
                                    cur.execute(
                                        "UPDATE users SET referrer = %s WHERE tg_id = %s", 
                                        (referrer_id, user.id)
                                    )
                                    conn.commit()
                                    logger.info(f"User {user.id} set referrer to {ref_id}")
            except Exception as e:
                logger.error(f"Error processing referral: {e}")
        else:
            # Treat as UTM parameter
            utm = param
            context.user_data['utm'] = utm
            logger.info(f"User {user.id} started with UTM: {utm}")
            
            # Record UTM start in stats
            try:
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO utm_stats (keyword, starts) VALUES (%s, 1) "
                            "ON CONFLICT (keyword) DO UPDATE SET starts = utm_stats.starts + 1",
                            (utm,)
                        )
                        conn.commit()
            except Exception as e:
                logger.error(f"Error recording UTM start: {e}")
    
    # Create user record if it doesn't exist
    await create_or_get_user(user)
    
    # Send welcome message with main menu
    await update.message.reply_text(
        f"🌬 *به بات فروش سرویس ویند خوش آمدید*\n\n"
        f"از منوی زیر، گزینه مورد نظر خود را انتخاب کنید.",
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
            InlineKeyboardButton("📊 آمار", callback_data="admin:stats"),
            InlineKeyboardButton("📢 ارسال گروهی", callback_data="admin:broadcast")
        ],
        [
            InlineKeyboardButton("➕ افزودن صندلی", callback_data="admin:addseat"),
            InlineKeyboardButton("📑 لیست اکانت‌ها", callback_data="admin:listcsv")
        ],
        [
            InlineKeyboardButton("🗂️ مدیریت اکانت‌ها", callback_data="admin:list"),
            InlineKeyboardButton("💵 تغییر قیمت", callback_data="admin:price")
        ],
        [
            InlineKeyboardButton("🔧 تغییر قیمت یک‌ماهه", callback_data="admin:price1")
        ],
        [
            InlineKeyboardButton("💳 مدیریت کارت‌ها", callback_data="admin:cards"),
            InlineKeyboardButton("📅 CSV گروهی", callback_data="admin:bulkcsv")
        ],
        [
            InlineKeyboardButton("📉 آمار UTM", callback_data="admin:utm"),
            InlineKeyboardButton("📃 بکاپ دیتابیس", callback_data="admin:backup")
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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


async def show_subscription_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available subscription options."""
    # Get the one-month price from settings
    one_month_price = int(db.get_setting('one_month_price', '70000'))
    
    # Create formatted price display
    one_month_price_display = f"{one_month_price:,} تومان"
    
    # Create message with subscription features
    message = (
        f"🥇 *ویژگی‌های اکانت ویندسکرایب یک‌ماهه (تک‌کاربره):*\n\n"
        f"• اتصال سریع و پایدار\n"
        f"• بدون محدودیت حجم مصرفی\n"
        f"• قابل استفاده روی *یک دستگاه*\n"
        f"• مدت زمان: *۱ ماه*\n"
        f"• قیمت: *{one_month_price_display}*\n\n"
    )
    
    # Create keyboard with only one-month subscription option
    keyboard = [
        [
            InlineKeyboardButton(f"💳 خرید ویندسکرایب یک‌ماهه", callback_data="buy:1mo")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")
        ]
    ]
    
    # Send message with keyboard
    if isinstance(update, Update) and update.callback_query:
        await update.callback_query.edit_message_text(
            message, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.effective_message.reply_text(
            message, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def show_purchase_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show purchase information and payment details."""
    # Get a random active card using the new card management system
    card_title, card_number = card_manager.get_random_payment_card()
    
    if not card_number:
        card_title = "کارت بانکی"
        card_number = "شماره کارت در سیستم ثبت نشده است"
        logger.error("No active cards found in database and no fallback card configured")
    
    # Get one-month price from settings
    amount = int(db.get_setting('one_month_price', '70000'))
    plan_description = "اشتراک یک‌ماهه ویندسکرایب"
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
                    (order_id, "Order created for one-month plan")
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
    payment_message = card_manager.format_payment_message(card_title, card_number, amount)
    
    message = (
        f"💳 *اطلاعات پرداخت*\n\n"
        f"🕊 نوع پلن: *{plan_description}*\n"
        f"💰 مبلغ: *{amount_display}*\n\n"
        f"{payment_message}\n\n"
        f"📧 شناسه سفارش: `#{order_id}`\n\n"
        f"❌ *لطفا شناسه سفارش را در توضیحات واریز ذکر کنید*\n\n"
        f"📷 پس از پرداخت، لطفا عکس رسید پرداخت خود را ارسال کنید."
    )
    
    # Add back button
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به انتخاب پلن", callback_data="buy_service")]
    ]
    
    # Send message
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            message, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif isinstance(update, Update) and update.callback_query:
        await update.callback_query.edit_message_text(
            message, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


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


async def process_seat_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process seat edit input from a message."""
    message = update.message
    text = message.text.strip()
    user = update.effective_user
    
    # Get the seat_id from context
    seat_id = context.user_data.get('edit_seat_id')
    return_page = context.user_data.get('edit_return_page', 1)
    
    try:
        # Parse the input - split into maximum 4 parts
        parts = text.split(maxsplit=3)
        
        # Pad with '-' if there are fewer than 4 parts
        while len(parts) < 4:
            parts.append('-')
        
        # Extract the parts
        email, password, secret, slots = parts
        
        # Fetch current row data
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get current seat data
                cur.execute(
                    "SELECT email, pass_enc, secret_enc, max_slots FROM seats WHERE id = %s",
                    (seat_id,)
                )
                result = cur.fetchone()
                
                if not result:
                    await message.reply_text(
                        f"❌ *خطا: صندلی شماره {seat_id} یافت نشد*",
                        parse_mode="Markdown"
                    )
                    context.user_data.pop('edit_seat_id', None)
                    context.user_data.pop('edit_return_page', None)
                    return
                
                current_email, current_pass_enc, current_secret_enc, current_max_slots = result
                
                # Prepare new values
                new_email = email if email != '-' else current_email
                new_pass_enc = encrypt(password) if password != '-' else current_pass_enc
                new_secret_enc = encrypt(secret) if secret != '-' else current_secret_enc
                
                # Handle slots conversion
                try:
                    new_slots = int(slots) if slots != '-' else current_max_slots
                except ValueError:
                    await message.reply_text(
                        "❌ *خطا: تعداد صندلی باید یک عدد باشد*",
                        parse_mode="Markdown"
                    )
                    return
                
                # Validate email if it's changing
                if email != '-' and '@' not in new_email:
                    await message.reply_text(
                        "❌ *خطا: فرمت ایمیل نامعتبر است*",
                        parse_mode="Markdown"
                    )
                    return
                
                # Check if any changes were made
                if (new_email == current_email and 
                    new_pass_enc == current_pass_enc and 
                    new_secret_enc == current_secret_enc and 
                    new_slots == current_max_slots):
                    await message.reply_text(
                        "ℹ️ *هیچ تغییری اعمال نشد*",
                        parse_mode="Markdown"
                    )
                    context.user_data.pop('edit_seat_id', None)
                    context.user_data.pop('edit_return_page', None)
                    return
                
                # Update the seat
                cur.execute(
                    "UPDATE seats SET email=%s, pass_enc=%s, secret_enc=%s, max_slots=%s WHERE id=%s",
                    (new_email, new_pass_enc, new_secret_enc, new_slots, seat_id)
                )
                conn.commit()
                
                # Confirm success
                await message.reply_text(
                    f"✅ *ویرایش شد*\n\n"
                    f"💬 ایمیل: `{new_email}`\n"
                    f"💺 صندلی‌ها: {new_slots}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"admin:list|{return_page}")]
                    ])
                )
                
                # Clear edit mode
                context.user_data.pop('edit_seat_id', None)
                context.user_data.pop('edit_return_page', None)
                
    except Exception as e:
        logger.error(f"Error editing seat: {e}")
        await message.reply_text(
            f"❌ *خطا در ویرایش صندلی*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown"
        )
        context.user_data.pop('edit_seat_id', None)
        context.user_data.pop('edit_return_page', None)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Universal message handler for all message types."""
    user_id = update.effective_user.id
    
    # Handle document uploads (CSV files)
    if update.message.document and context.user_data.get('awaiting_csv', False):
        # Clear the flag immediately
        context.user_data.pop('awaiting_csv', None)
        # Process CSV upload directly
        await process_csv_upload_direct(update, context)
        return
    
    # Handle text messages
    if update.message.text:
        text = update.message.text
        
        # Log the message
        logger.info(f"Received message from {user_id}: {text}")
        
        # Check if we're in seat edit mode
        if 'edit_seat_id' in context.user_data:
            await process_seat_edit(update, context)
            return
            
        # Check if we're expecting card info
        if context.user_data.get('awaiting_card_info', False):
            from handlers import admin_cards
            await admin_cards.process_add_card(update, context)
            return
            
        # Check if we're expecting card edit info
        if 'edit_card_id' in context.user_data:
            from handlers import admin_cards
            await admin_cards.process_edit_card(update, context)
            return
            
        # Check if we're expecting a seat input
        if context.user_data.get('awaiting_single_seat', False):
            # Clear the flag immediately
            context.user_data.pop('awaiting_single_seat', None)
            # Process seat input directly
            await process_add_seat_direct(update, context)
            return


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message - default fallback."""
    # Forward to the message handler
    await message_handler(update, context)


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
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # First check if order exists at all
                cur.execute(
                    "SELECT status FROM orders WHERE id = %s",
                    (order_id,)
                )
                order_check = cur.fetchone()
                
                if not order_check:
                    logger.error(f"Order {order_id} not found in database")
                    return False, "خطا: سفارش یافت نشد"
                
                # If order exists but is not in pending or receipt status, give specific error
                if order_check[0] not in ('pending', 'receipt'):
                    logger.error(f"Order {order_id} exists but status is '{order_check[0]}', not 'pending' or 'receipt'")
                    return False, f"خطا: سفارش در وضعیت '{order_check[0]}' است، نه قابل تایید"
                
                # Get order details
                cur.execute(
                    "SELECT o.user_id, o.amount, o.utm_keyword, u.tg_id, u.referrer FROM orders o "
                    "JOIN users u ON o.user_id = u.id "
                    "WHERE o.id = %s AND o.status IN ('pending', 'receipt')",
                    (order_id,)
                )
                order = cur.fetchone()
                
                if not order:
                    logger.error(f"Order {order_id} not found or not in pending/receipt status")
                    return False, "خطا: سفارش یافت نشد یا در وضعیت قابل تایید نیست"
                    
                user_id, amount, utm_keyword, tg_id, referrer_id = order
                
                # Get an available seat
                seat = await get_available_seat()
                if not seat:
                    logger.error(f"No available seats for order {order_id}")
                    return False, "خطا: هیچ صندلی خالی برای تخصیص وجود ندارد"
                
                # Update order status and assign seat
                cur.execute(
                    "UPDATE orders SET status = 'approved', seat_id = %s, approved_at = %s "
                    "WHERE id = %s",
                    (seat["id"], datetime.now(), order_id)
                )
                
                # Log the approval
                cur.execute(
                    "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                    (order_id, "Order approved")
                )
                
                # Process referral commission if user has a referrer
                if referrer_id is not None:
                    # Calculate 10% commission
                    commission = float(amount) * 0.10
                    
                    # Credit the referrer's wallet
                    cur.execute(
                        "UPDATE wallets SET balance = balance + %s, "
                        "referral_earned = referral_earned + %s "
                        "WHERE user_id = %s",
                        (commission, commission, referrer_id)
                    )
                    
                    # Log the referral commission
                    logger.info(f"Credited referrer {referrer_id} with {commission} for order {order_id}")
                    
                    # Add a log entry for the referral commission
                    cur.execute(
                        "INSERT INTO order_log (order_id, event) VALUES (%s, %s)",
                        (order_id, f"Referral commission of {commission} credited to user {referrer_id}")
                    )
                
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
                # First check if order exists at all
                cur.execute(
                    "SELECT status FROM orders WHERE id = %s",
                    (order_id,)
                )
                order_check = cur.fetchone()
                
                if not order_check:
                    logger.error(f"Order {order_id} not found in database")
                    return False, "خطا: سفارش یافت نشد"
                
                # If order exists but is not in pending or receipt status, give specific error
                if order_check[0] not in ('pending', 'receipt'):
                    logger.error(f"Order {order_id} exists but status is '{order_check[0]}', not 'pending' or 'receipt'")
                    return False, f"خطا: سفارش در وضعیت '{order_check[0]}' است، نه قابل رد"
                
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
ADMIN_WAITING_EDIT_SEAT = 6


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


async def handle_list_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send a CSV file with active seat information."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        # Update status message
        status_msg = await query.edit_message_text(
            "⏳ *در حال تهیه لیست اکانت‌ها...*",
            parse_mode="Markdown"
        )
        
        # Get all active seats with available slots
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, pass_enc, secret_enc, max_slots-sold AS free_slots "
                    "FROM seats WHERE status='active'"
                )
                seats = cur.fetchall()
                
                # Calculate total free slots
                total_free_slots = sum(seat[3] for seat in seats)
        
        # Generate CSV file
        import io
        import csv
        from datetime import datetime
        
        # Create CSV in memory
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        
        # Write header
        csv_writer.writerow(['email', 'password', 'secret', 'free_slots'])
        
        # Write data rows
        for seat in seats:
            email = seat[0]
            password = decrypt_secret(seat[1])  # Decrypt password
            secret = decrypt_secret(seat[2])    # Decrypt secret
            free_slots = seat[3]
            
            csv_writer.writerow([email, password, secret, free_slots])
        
        # Get the CSV content
        csv_content = csv_buffer.getvalue()
        csv_buffer.close()
        
        # Create a bytes buffer from the CSV content
        bytes_buffer = io.BytesIO(csv_content.encode('utf-8'))
        
        # Generate filename with current date
        current_date = datetime.now().strftime("%Y%m%d")
        filename = f"seats_{current_date}.csv"
        
        # Send the CSV file
        await context.bot.send_document(
            chat_id=user.id,
            document=bytes_buffer,
            filename=filename,
            caption=f"صندلی خالی: {total_free_slots}"
        )
        
        # Update status message
        await status_msg.edit_text(
            f"✅ *لیست اکانت‌ها با موفقیت ارسال شد*\n\n"
            f"🗂️ تعداد کل اکانت‌ها: {len(seats)}\n"
            f"💺 صندلی‌های خالی: {total_free_slots}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Error generating CSV list: {e}")
        await query.edit_message_text(
            "❌ *خطا در تهیه لیست اکانت‌ها*\n\n"
            f"`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )

async def handle_utm_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show UTM tracking statistics by sending a .txt file."""
    query = update.callback_query
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        # Update status message
        status_msg = await query.edit_message_text(
            "⏳ *در حال تهیه آمار UTM...*",
            parse_mode="Markdown"
        )
        
        # Fetch UTM stats from database
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT keyword, starts, buys, amount FROM utm_stats ORDER BY starts DESC"
                )
                utm_stats = cur.fetchall()
        
        if not utm_stats:
            # No stats available
            await status_msg.edit_text(
                "📈 *UTM Stats*\n\n"
                "No statistics recorded yet.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Generate text file content
        import io
        from datetime import datetime
        
        # Create content in English
        content = f"UTM Tracking Statistics Report\n"
        content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += f"="*50 + "\n\n"
        
        # Add header
        content += f"{'Keyword':<20} {'Starts':<8} {'Buys':<8} {'Amount (Toman)':<15}\n"
        content += f"{'-'*20} {'-'*8} {'-'*8} {'-'*15}\n"
        
        # Add data rows
        total_starts = 0
        total_buys = 0
        total_amount = 0
        
        for keyword, starts, buys, amount in utm_stats:
            content += f"{keyword:<20} {starts:<8} {buys:<8} {amount:<15,}\n"
            total_starts += starts
            total_buys += buys
            total_amount += amount
        
        # Add totals
        content += f"{'-'*20} {'-'*8} {'-'*8} {'-'*15}\n"
        content += f"{'TOTAL':<20} {total_starts:<8} {total_buys:<8} {total_amount:<15,}\n\n"
        
        # Add summary statistics
        content += f"Summary:\n"
        content += f"- Total Campaigns: {len(utm_stats)}\n"
        content += f"- Total Starts: {total_starts:,}\n"
        content += f"- Total Purchases: {total_buys:,}\n"
        content += f"- Total Revenue: {total_amount:,} Toman\n"
        if total_starts > 0:
            conversion_rate = (total_buys / total_starts) * 100
            content += f"- Overall Conversion Rate: {conversion_rate:.2f}%\n"
            avg_revenue_per_start = total_amount / total_starts
            content += f"- Average Revenue per Start: {avg_revenue_per_start:,.0f} Toman\n"
        if total_buys > 0:
            avg_order_value = total_amount / total_buys
            content += f"- Average Order Value: {avg_order_value:,.0f} Toman\n"
        
        # Create file buffer
        file_buffer = io.BytesIO(content.encode('utf-8'))
        
        # Generate filename with current date
        current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"utm_stats_{current_date}.txt"
        
        # Send the file
        await context.bot.send_document(
            chat_id=user.id,
            document=file_buffer,
            filename=filename,
            caption=f"📊 UTM Statistics Report\n{len(utm_stats)} campaigns, {total_buys:,} purchases"
        )
        