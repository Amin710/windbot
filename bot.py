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
import subprocess
import tempfile
import uuid
import telegram
import time
import base64
import pyotp
import random
import asyncio
import traceback
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Union, Tuple, List, Any

# Import handlers modules with error handling
try:
    from handlers import referral
    print("Successfully imported referral handler")
except ImportError as e:
    print(f"Could not import referral handler: {e}")
    referral = None

try:
    from handlers import admin_cards
    print("Successfully imported admin_cards handler")
except ImportError as e:
    print(f"Could not import admin_cards handler: {e}")
    admin_cards = None

try:
    from handlers import card_manager
    print("Successfully imported card_manager handler")
except ImportError as e:
    print(f"Could not import card_manager handler: {e}")
    card_manager = None

try:
    from tabulate import tabulate
    print("Successfully imported tabulate")
except ImportError as e:
    print(f"Could not import tabulate: {e}")
    # Define a dummy tabulate function
    def tabulate(data, headers=None, tablefmt="simple"):
        return str(data)

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

# Import database module with error handling
try:
    import db
    print("Successfully imported db module")
except ImportError as e:
    print(f"Could not import db module: {e}")
    raise SystemExit("Database module is required for the bot to function")
except Exception as e:
    print(f"Error initializing db module: {e}")
    raise SystemExit(f"Database initialization failed: {e}")

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
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URI = os.getenv("DB_URI")
FERNET_KEY = os.getenv("FERNET_KEY")
RECEIPT_CHANNEL_ID = os.getenv("RECEIPT_CHANNEL_ID")
LOG_SELL_CHID = os.getenv("LOG_SELL_CHID")
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


# Force Join Settings - Global variables
FORCE_JOIN_ENABLED = False
REQUIRED_CHANNELS = []

async def load_force_join_settings():
    """Load force join settings from database."""
    global FORCE_JOIN_ENABLED, REQUIRED_CHANNELS
    
    # Set defaults first
    FORCE_JOIN_ENABLED = True  # Enable by default
    REQUIRED_CHANNELS = ["@AccYarVPN"]  # Default channel - AccYarVPN channel
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Force update the database to use the correct channel
                cur.execute("""
                    INSERT INTO settings (key, val) VALUES ('force_join_enabled', 'true')
                    ON CONFLICT (key) DO UPDATE SET val = 'true'
                """)
                
                cur.execute("""
                    INSERT INTO settings (key, val) VALUES ('required_channels', '@AccYarVPN')
                    ON CONFLICT (key) DO UPDATE SET val = '@AccYarVPN'
                """)
                
                # Now load the settings
                try:
                    cur.execute("SELECT val FROM settings WHERE key = 'force_join_enabled'")
                    result = cur.fetchone()
                    if result:
                        FORCE_JOIN_ENABLED = result[0].lower() == 'true'
                except Exception as e:
                    logger.warning(f"force_join_enabled setting not found, using default: {e}")
                
                try:
                    cur.execute("SELECT val FROM settings WHERE key = 'required_channels'")
                    result = cur.fetchone()
                    if result and result[0]:
                        # Parse comma-separated channel IDs/usernames
                        REQUIRED_CHANNELS = [ch.strip() for ch in result[0].split(',') if ch.strip()]
                except Exception as e:
                    logger.warning(f"required_channels setting not found, using default: {e}")
                
                # Commit changes
                conn.commit()
                    
        logger.info(f"Force join settings loaded: enabled={FORCE_JOIN_ENABLED}, channels={REQUIRED_CHANNELS}")
                    
    except Exception as e:
        logger.error(f"Error loading force join settings: {e}")
        # Keep defaults
        FORCE_JOIN_ENABLED = True
        REQUIRED_CHANNELS = ["@AccYarVPN"]  # AccYarVPN channel username

async def check_channel_membership(user_id: int, bot) -> tuple[bool, list]:
    """
    Check if user is member of all required channels.
    Returns (is_member_of_all, list_of_missing_channels)
    """
    if not FORCE_JOIN_ENABLED or not REQUIRED_CHANNELS:
        return True, []
    
    missing_channels = []
    
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked']:
                missing_channels.append(channel)
        except Exception as e:
            logger.error(f"Error checking membership for channel {channel}: {e}")
            # If we can't check, assume user is not member
            missing_channels.append(channel)
    
    return len(missing_channels) == 0, missing_channels

async def get_channel_join_keyboard(missing_channels: list):
    """Create inline keyboard with channel join buttons."""
    keyboard = []
    
    # Add AccYarVPN channel button
    keyboard.append([InlineKeyboardButton("🛍 @AccYarVPN", url="https://t.me/AccYarVPN")])
    
    # Add check membership button
    keyboard.append([InlineKeyboardButton("✅ تأیید عضویت", callback_data="check_membership")])
    
    return InlineKeyboardMarkup(keyboard)

async def send_join_channels_message(update: Update, context: ContextTypes.DEFAULT_TYPE, missing_channels: list):
    """Send message asking user to join required channels."""
    # Check if this is from a failed membership check
    is_recheck = update.callback_query and update.callback_query.data == "check_membership"
    
    if is_recheck:
        message_text = (
            "❌ *عضویت شما تأیید نشد!*\n\n"
            "🔸 کاربر محترم، شما هنوز در کانال مورد نظر عضو نشده‌اید.\n\n"
            "لطفا ابتدا روی لینک زیر کلیک کرده و عضو کانال شوید:\n\n"
            "🛍 @AccYarVPN\n"
            "🛍 @AccYarVPN\n\n"
            "⚠️ توجه: بعد از عضویت، حتماً روی دکمه «تأیید عضویت» بزنید تا ربات براتون فعال بشه"
        )
    else:
        message_text = (
            "🔸 کاربر محترم اکانت یار ، جهت استفاده بهتر از ربات و اطلاع از آخرین اخبار و اطلاعیه ها لطفا ابتدا عضو کانال زیر بشید 👇\n\n"
            "🛍 @AccYarVPN\n"
            "🛍 @AccYarVPN\n\n"
            "پس از عضویت روی دکمه «تأیید عضویت» بزنید تا ربات براتون فعال بشه"
        )
    
    keyboard = await get_channel_join_keyboard(missing_channels)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    elif update.message:
        await update.message.reply_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )


def get_main_menu_keyboard():
    """Create the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("⭐️ خرید سرویس", callback_data="buy_service"),
            InlineKeyboardButton("🔐 مدیریت سرویس", callback_data="manage_service")
        ],
        [
            InlineKeyboardButton("💰 کیف پول", callback_data="wallet"),
            InlineKeyboardButton("📣 کسب اعتبار  ", callback_data="menu:ref")
        ],
        [
            InlineKeyboardButton("💬 پشتیبانی", url="https://t.me/AccountYarSup")
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
            InlineKeyboardButton("📲 دریافت کد دومرحله ای", callback_data=f"2fa:{seat_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_setup_2fa_button(order_id):
    """Create setup 2FA button for approved orders."""
    keyboard = [
        [
            InlineKeyboardButton("📱 آموزش ورود و دریافت کد دومرحله ای", callback_data=f"setup2fa:{order_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_code_2fa_button(order_id):
    """Create code 2FA button for generating TOTP codes."""
    keyboard = [
        [
            InlineKeyboardButton("📲 دریافت کد دومرحله ای", callback_data=f"code:{order_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_code_2fa_retry_button(order_id):
    """Create retry 2FA button for second code generation."""
    keyboard = [
        [
            InlineKeyboardButton("📲 دریافت مجدد کد", callback_data=f"code:{order_id}")
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
    
    # Check channel membership
    is_member, missing_channels = await check_channel_membership(user.id, context.bot)
    if not is_member:
        await send_join_channels_message(update, context, missing_channels)
        return
    
    # Send welcome message with main menu
    await update.message.reply_text(
        f"👤  به ربات \"اکانت یار\" : فروش اکانت قانونی فیلترشکن خوش آمدید 👋\n\n"
        f"✅ سرویس های فعال درحال حاضر:\n"
        f"- اکانت قانونی فیلترشکن پرسرعت ویندسکرایب 🔐\n\n"
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
            InlineKeyboardButton("💱 تغییر نرخ دلار", callback_data="admin:usd")
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
    """Create a database backup using Python and send it to the sales log channel."""
    if not LOG_SELL_CHID:
        await status_message.edit_text(
            "❌ *خطا: LOG_SELL_CHID تنظیم نشده است*",
            parse_mode="Markdown"
        )
        return
        
    try:
        # Create a timestamp for the backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"wind_reseller_backup_{timestamp}.sql"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / backup_filename
            
            await status_message.edit_text(
                "💾 *در حال ایجاد بکاپ...*",
                parse_mode="Markdown"
            )
            
            # Create backup using Python
            with open(backup_path, "w", encoding="utf-8") as backup_file:
                # Write backup header
                backup_file.write(f"-- Wind Reseller Database Backup\n")
                backup_file.write(f"-- Generated on: {timestamp}\n")
                backup_file.write(f"-- PostgreSQL Database Backup\n\n")
                backup_file.write("BEGIN;\n\n")
                
                # Get database connection
                with db.get_conn() as conn:
                    with conn.cursor() as cur:
                        # Get all table names
                        cur.execute("""
                            SELECT tablename FROM pg_tables 
                            WHERE schemaname = 'public' 
                            ORDER BY tablename
                        """)
                        tables = [row[0] for row in cur.fetchall()]
                        
                        await status_message.edit_text(
                            f"💾 *در حال بکاپ {len(tables)} جدول...*",
                            parse_mode="Markdown"
                        )
                        
                        for table in tables:
                            # Get table structure
                            cur.execute(f"""
                                SELECT column_name, data_type, is_nullable, column_default
                                FROM information_schema.columns 
                                WHERE table_name = %s AND table_schema = 'public'
                                ORDER BY ordinal_position
                            """, (table,))
                            columns = cur.fetchall()
                            
                            # Write table structure comment
                            backup_file.write(f"-- Table: {table}\n")
                            
                            # Get table data
                            cur.execute(f"SELECT * FROM {table}")
                            rows = cur.fetchall()
                            
                            if rows:
                                # Get column names
                                column_names = [desc[0] for desc in cur.description]
                                
                                # Write INSERT statements
                                for row in rows:
                                    values = []
                                    for value in row:
                                        if value is None:
                                            values.append("NULL")
                                        elif isinstance(value, str):
                                            # Escape single quotes
                                            escaped_value = value.replace("'", "''")
                                            values.append(f"'{escaped_value}'")
                                        elif isinstance(value, (int, float)):
                                            values.append(str(value))
                                        elif isinstance(value, bool):
                                            values.append("TRUE" if value else "FALSE")
                                        else:
                                            # For other types, convert to string and escape
                                            escaped_value = str(value).replace("'", "''")
                                            values.append(f"'{escaped_value}'")
                                    
                                    backup_file.write(
                                        f"INSERT INTO {table} ({', '.join(column_names)}) "
                                        f"VALUES ({', '.join(values)});\n"
                                    )
                                
                                backup_file.write(f"\n")
                        
                        # Write backup footer
                        backup_file.write("COMMIT;\n")
                        backup_file.write(f"\n-- Backup completed at {timestamp}\n")
            
            # Check if backup file exists and has content
            if not backup_path.exists() or backup_path.stat().st_size == 0:
                await status_message.edit_text(
                    "❌ *خطا: فایل بکاپ ایجاد نشد*",
                    parse_mode="Markdown"
                )
                return
            
            # Send the backup file to the sales log channel
            await status_message.edit_text(
                "📤 *در حال ارسال فایل بکاپ...*",
                parse_mode="Markdown"
            )
            
            file_size = backup_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            try:
                with open(backup_path, "rb") as backup_file:
                    await bot.send_document(
                        chat_id=LOG_SELL_CHID,
                        document=backup_file,
                        filename=backup_filename,
                        caption=f"📂 *بکاپ دیتابیس* - {timestamp}\n💾 حجم: {file_size_mb:.2f} MB"
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
                    
                    # Add back button
                    message += "\n\n📧 اطلاعات حساب شما در بالا نمایش داده شده است."
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
    amount_display = f"{amount:,}"
    
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
    
    # Create new payment message format
    message = (
        f"💳 اطلاعات پرداخت:\n\n"
        f"🕊 نوع پلن: {plan_description}\n\n"
        f"مبلغ {amount_display} تومان به کارت زیر واریز کرده و اسکرین شات واریز رو همین‌جا در ربات ارسال کنید\n"
        f"🔻🔻\n"
        f"`{card_number}`\n"
        f"{card_title}\n\n"
        f"تایید تراکنش شما به نوبت در سریع‌ترین زمان ممکن انجام خواهد شد🙏\n\n"
        f"❔در صورت مشکل در پرداخت، از همراه بانک، تاپ، ۷۸۰، بله یا خودپرداز ATM استفاده کنید"
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
        f"با تشکر، سفارش شما ثبت شد و در انتظار تایید می‌باشد ✅\n\n"
        f"فرایند تایید ممکنه تا چند ساعت زمان ببره لطفا از پیام مکرر به پشتیبانی خودداری کنید\n\n"
        f"💬 پشتیبانی: @AccountYarSupport"
    )
    
    # Forward receipt to admin channel
    if RECEIPT_CHANNEL_ID:
        try:
            # Get order details and card info
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    # Get order amount
                    cur.execute(
                        "SELECT amount FROM orders WHERE id = %s",
                        (pending_order_id,)
                    )
                    order_result = cur.fetchone()
                    amount = order_result[0] if order_result else 0
                    
                    # Get card info (first active card if no specific one is set)
                    cur.execute(
                        "SELECT card_number, title FROM cards WHERE active = true LIMIT 1"
                    )
                    card_result = cur.fetchone()
                    if card_result:
                        card_number = card_result[0]
                        card_holder_name = card_result[1]  # title field used as holder name
                    else:
                        # Fallback to environment variable
                        card_number = CARD_NUMBER if CARD_NUMBER else "نامشخص"
                        card_holder_name = "نامشخص"
            
            # Format user display
            user_display = f"@{user.username}" if user.username else f"کاربر #{user.id}"
            
            # Create detailed caption
            caption = (
                f"🧾 رسید جدید پرداخت کارت به کارت:\n\n"
                f"👤 کاربر: {user_display}\n"
                f"🔢 شماره تراکنش: #{pending_order_id}\n"
                f"💰 مبلغ: {amount:,} تومان\n\n"
                f"💳 کارت مقصد:\n"
                f"🔢 {card_number}\n"
                f"👤 {card_holder_name}"
            )
            
            # Forward with order info in caption
            forwarded_msg = await context.bot.send_photo(
                chat_id=RECEIPT_CHANNEL_ID,
                photo=file_id,
                caption=caption,
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
        username, password, secret, slots = parts
        
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
                
                current_username, current_pass_enc, current_secret_enc, current_max_slots = result
                
                # Prepare new values
                new_username = username if username != '-' else current_username
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
                
                # Validate username if it's changing
                if username != '-' and len(new_username.strip()) < 3:
                    await message.reply_text(
                        "❌ *خطا: نام کاربری باید حداقل ۳ کاراکتر باشد*",
                        parse_mode="Markdown"
                    )
                    return
                
                # Check if any changes were made
                if (new_username == current_username and 
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
                    (new_username, new_pass_enc, new_secret_enc, new_slots, seat_id)
                )
                conn.commit()
                
                # Confirm success
                await message.reply_text(
                    f"✅ *ویرایش شد*\n\n"
                    f"👤 نام کاربری: `{new_username}`\n"
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
                
                # Send sell report to LOG_SELL_CHID
                if LOG_SELL_CHID:
                    try:
                        # Get seat details
                        cur.execute(
                            "SELECT email, pass_enc, secret_enc, max_slots, sold FROM seats WHERE id = %s",
                            (seat["id"],)
                        )
                        seat_details = cur.fetchone()
                        if seat_details:
                            email, pass_enc, secret_enc, max_slots, sold = seat_details
                            remaining_slots = max_slots - sold
                            
                            # Decrypt password and secret
                            password = decrypt(pass_enc)
                            secret = decrypt(secret_enc)
                            
                            # Get username if available
                            cur.execute(
                                "SELECT username FROM users WHERE id = %s",
                                (user_id,)
                            )
                            username_result = cur.fetchone()
                            username = username_result[0] if username_result else "نامشخص"
                            
                            # Create sell report message
                            sell_report = (
                                f"✅ گزارش فروش\n\n"
                                f"اکانت ویندسکرایب یک ماهه برای کاربر @{username} ارسال شد\n\n"
                                f"👤 نام کاربری: {email}\n"
                                f"🔑 رمز عبور: {password}\n"
                                f"🔐 کد 2FA اکانت: {secret}\n\n"
                                f"💺 ظرفیت کل صندلی های باقی مانده: {remaining_slots}"
                            )
                            
                            # Send sell report to LOG_SELL_CHID
                            await context.bot.send_message(
                                chat_id=LOG_SELL_CHID,
                                text=sell_report
                            )
                    except Exception as e:
                        logger.error(f"Error sending sell report to LOG_SELL_CHID: {e}")
                
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


# Admin state constants for conversation handlers (moved here to prevent circular imports)
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
                # Get USD rate with error handling
                try:
                    usd_rate = int(db.get_setting('usd_rate', '70000'))  # Default 70,000 Toman per USD
                except:
                    usd_rate = 70000
                
                # User registration statistics with error handling
                users_today = 0
                users_this_month = 0
                total_users = 0
                
                try:
                    # Check if joined_at column exists in users table
                    cur.execute("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'users' AND column_name = 'joined_at'
                    """)
                    has_joined_at = cur.fetchone() is not None
                    
                    if has_joined_at:
                        # Users registered today
                        cur.execute("""
                            SELECT COUNT(*) 
                            FROM users 
                            WHERE DATE(joined_at) = CURRENT_DATE
                        """)
                        users_today = cur.fetchone()[0]
                        
                        # Users registered this month
                        cur.execute("""
                            SELECT COUNT(*) 
                            FROM users 
                            WHERE DATE(joined_at) >= DATE_TRUNC('month', CURRENT_DATE)
                        """)
                        users_this_month = cur.fetchone()[0]
                    
                    # Total users
                    cur.execute("SELECT COUNT(*) FROM users")
                    total_users = cur.fetchone()[0]
                    
                except Exception as user_error:
                    logger.error(f"Error getting user stats: {user_error}")
                    # Fallback to just total users
                    cur.execute("SELECT COUNT(*) FROM users")
                    total_users = cur.fetchone()[0]
                    users_today = 0
                    users_this_month = 0
                
                # Get approved sales count
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'approved'")
                approved_sales = cur.fetchone()[0]
                
                # Get seats sold
                cur.execute("SELECT SUM(sold) FROM seats")
                seats_sold = cur.fetchone()[0] or 0
                
                # Get available seats
                cur.execute("SELECT SUM(max_slots - sold) FROM seats WHERE status = 'active'")
                available_slots = cur.fetchone()[0] or 0
                
                # Sales statistics with error handling
                today_count, today_amount = 0, 0
                week_count, week_amount = 0, 0
                month_count, month_amount = 0, 0
                total_amount = 0
                
                try:
                    # Check if created_at column exists in orders table
                    cur.execute("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'orders' AND column_name = 'created_at'
                    """)
                    has_orders_created_at = cur.fetchone() is not None
                    
                    if has_orders_created_at:
                        # Today's sales
                        cur.execute("""
                            SELECT COUNT(*), COALESCE(SUM(amount), 0) 
                            FROM orders 
                            WHERE status = 'approved' 
                            AND DATE(created_at) = CURRENT_DATE
                        """)
                        today_count, today_amount = cur.fetchone()
                        today_amount = today_amount or 0
                        
                        # This week's sales (current week)
                        cur.execute("""
                            SELECT COUNT(*), COALESCE(SUM(amount), 0) 
                            FROM orders 
                            WHERE status = 'approved' 
                            AND DATE(created_at) >= DATE_TRUNC('week', CURRENT_DATE)
                        """)
                        week_count, week_amount = cur.fetchone()
                        week_amount = week_amount or 0
                        
                        # This month's sales
                        cur.execute("""
                            SELECT COUNT(*), COALESCE(SUM(amount), 0) 
                            FROM orders 
                            WHERE status = 'approved' 
                            AND DATE(created_at) >= DATE_TRUNC('month', CURRENT_DATE)
                        """)
                        month_count, month_amount = cur.fetchone()
                        month_amount = month_amount or 0
                    
                    # Total sales
                    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'approved'")
                    total_amount = cur.fetchone()[0] or 0
                    
                except Exception as sales_error:
                    logger.error(f"Error getting sales stats: {sales_error}")
                    # Fallback to just total amount
                    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'approved'")
                    total_amount = cur.fetchone()[0] or 0
                
                # Convert to USD
                today_usd = today_amount / usd_rate if usd_rate > 0 else 0
                week_usd = week_amount / usd_rate if usd_rate > 0 else 0
                month_usd = month_amount / usd_rate if usd_rate > 0 else 0
                total_usd = total_amount / usd_rate if usd_rate > 0 else 0
                
                # Format statistics message
                stats_message = (
                    f"📊 *آمار سیستم*\n\n"
                    f"👤 *کاربران:*\n"
                    f"├ امروز: {users_today:,}\n"
                    f"├ این ماه: {users_this_month:,}\n"
                    f"└ کل: {total_users:,}\n\n"
                    
                    f"💺 صندلی‌های فروخته: *{int(seats_sold):,}*\n"
                    f"💿 ظرفیت باقیمانده: *{int(available_slots):,}*\n\n"
                    
                    f"💰 *فروش امروز:*\n"
                    f"├ تعداد: {today_count:,}\n"
                    f"├ تومان: {today_amount:,}\n"
                    f"└ دلار: ${today_usd:.2f}\n\n"
                    
                    f"📅 *فروش هفته:*\n"
                    f"├ تعداد: {week_count:,}\n"
                    f"├ تومان: {week_amount:,}\n"
                    f"└ دلار: ${week_usd:.2f}\n\n"
                    
                    f"📆 *فروش این ماه:*\n"
                    f"├ تعداد: {month_count:,}\n"
                    f"├ تومان: {month_amount:,}\n"
                    f"└ دلار: ${month_usd:.2f}\n\n"
                    
                    f"🏆 *فروش کل:*\n"
                    f"├ تعداد: {approved_sales:,}\n"
                    f"├ تومان: {total_amount:,}\n"
                    f"└ دلار: ${total_usd:.2f}\n\n"
                    
                    f"💱 نرخ دلار: {usd_rate:,} تومان"
                )
                
                # Send statistics
                await query.edit_message_text(
                    stats_message,
                    reply_markup=get_admin_keyboard(),
                    parse_mode="Markdown"
                )
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        await query.edit_message_text(
            f"خطا در دریافت آمار: {str(e)[:100]}",
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
    await query.answer()  # Answer the callback query
    
    logger.info(f"handle_admin_usd_rate called for user {update.effective_user.id}")
    
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
    logger.info(f"Returning state ADMIN_WAITING_USD_RATE ({ADMIN_WAITING_USD_RATE}) from handle_admin_usd_rate")
    return ADMIN_WAITING_USD_RATE


async def handle_add_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the add seat callback."""
    query = update.callback_query
    await query.answer()  # Answer the callback query
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
        f"`username password secret [slots]` ارسال کنید\n\n"
        f"slots اختیاری است و پیش‌فرض 15 می‌باشد.",
        parse_mode="Markdown"
    )
    
    return ADMIN_WAITING_SEAT_INFO


async def handle_bulk_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the bulk CSV upload callback."""
    query = update.callback_query
    await query.answer()  # Answer the callback query
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
        f"فایل CSV با ستون‌های username,password,secret,slots را ارسال کنید\n\n"
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
        csv_writer.writerow(['username', 'password', 'secret', 'free_slots'])
        
        # Write data rows
        for seat in seats:
            username = seat[0]  # Database still uses 'email' field, but content is username
            password = decrypt_secret(seat[1])  # Decrypt password
            secret = decrypt_secret(seat[2])    # Decrypt secret
            free_slots = seat[3]
            
            csv_writer.writerow([username, password, secret, free_slots])
        
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
                    "SELECT keyword, starts, buys, amount "
                    "FROM utm_stats ORDER BY starts DESC"
                )
                utm_stats = cur.fetchall()
        
        if not utm_stats:
            # No stats available
            await query.edit_message_text(
                "📈 *UTM Stats*\n\n"
                "No statistics recorded yet.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Format stats using tabulate in English
        headers = ["Keyword", "Starts", "Buys", "Amount (T)"]
        
        # Format the data for better readability
        formatted_data = []
        total_starts = 0
        total_buys = 0
        total_amount = 0
        
        for keyword, starts, buys, amount in utm_stats:
            formatted_data.append([
                keyword,
                f"{starts:,}",
                f"{buys:,}",
                f"{amount:,}"
            ])
            total_starts += starts
            total_buys += buys
            total_amount += amount
        
        # Add totals row
        formatted_data.append([
            "TOTAL",
            f"{total_starts:,}",
            f"{total_buys:,}",
            f"{total_amount:,}"
        ])
        
        table = tabulate(formatted_data, headers=headers, tablefmt="grid")
        
        # Calculate conversion rate
        conversion_rate = (total_buys / total_starts * 100) if total_starts > 0 else 0
        
        # Build the message in English
        message = f"📈 *UTM Campaign Statistics*\n\n"
        message += f"```\n{table}\n```\n\n"
        message += f"📊 *Summary:*\n"
        message += f"• Total Campaigns: {len(utm_stats)}\n"
        message += f"• Conversion Rate: {conversion_rate:.2f}%\n"
        message += f"• Avg Revenue/Start: {(total_amount/total_starts):,.0f}T\n" if total_starts > 0 else "• Avg Revenue/Start: 0T\n"
        message += f"• Avg Order Value: {(total_amount/total_buys):,.0f}T" if total_buys > 0 else "• Avg Order Value: 0T"
        
        # Send the formatted table
        await query.edit_message_text(
            message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Error displaying UTM stats: {e}")
        await query.edit_message_text(
            "❌ *Error displaying UTM stats*\n\n"
            f"`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )


async def handle_change_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the change price callback."""
    query = update.callback_query
    await query.answer()  # Answer the callback query
    user = update.effective_user
    
    logger.info(f"handle_change_price called for user {user.id}")
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await query.edit_message_text("شما دسترسی ادمین ندارید.")
        return -1
    
    # Get current price
    current_price = int(db.get_setting('one_month_price', '70000'))
    
    # Set the awaiting flag and price type
    context.user_data['awaiting_price'] = True
    context.user_data['price_type'] = 'one_month_price'  # Set the correct price type
    
    await query.edit_message_text(
        f"💸 *تغییر قیمت سرویس*\n\n"
        f"قیمت فعلی: {current_price:,} تومان\n\n"
        f"قیمت جدید سرویس (تومان) را وارد کنید:",
        parse_mode="Markdown"
    )
    
    logger.info(f"Returning state ADMIN_WAITING_PRICE ({ADMIN_WAITING_PRICE}) from handle_change_price")
    return ADMIN_WAITING_PRICE


async def process_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the price input message."""
    # Import the price input handler from the module
    from handlers.admin_price import process_price_input as handle_price_input
    return await handle_price_input(update, context)


async def process_csv_upload_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process the uploaded CSV file for bulk seat import directly."""
    message = update.message
    document = message.document
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return
    
    # Check if it's a CSV file
    if not document.file_name.lower().endswith('.csv'):
        await message.reply_text(
            "❌ *خطا: فایل باید CSV باشد*",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return
    
    # Status message
    status_msg = await message.reply_text(
        "⏳ *در حال دانلود و پردازش فایل CSV...*",
        parse_mode="Markdown"
    )
    
    csv_file_path = f"temp_{message.message_id}.csv"
    
    try:
        # Download the file
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(csv_file_path)
        
        await status_msg.edit_text(
            "✅ *فایل با موفقیت دانلود شد، در حال پردازش...*",
            parse_mode="Markdown"
        )
        
        # Process CSV
        success_count = 0
        duplicate_count = 0
        error_count = 0
        errors = []
        
        # Try opening with different encodings to find the correct one
        encodings = ['utf-8', 'latin-1', 'cp1256']
        working_encoding = None
        header_fields = None
        
        # First find the correct encoding and read headers
        for encoding in encodings:
            try:
                with open(csv_file_path, 'r', newline='', encoding=encoding) as csvfile:
                    test_reader = csv.DictReader(csvfile)
                    # Test reading headers to verify encoding
                    header_fields = test_reader.fieldnames
                    if header_fields:  # Successfully parsed headers
                        working_encoding = encoding
                        break
            except Exception as enc_error:
                logger.error(f"Error with encoding {encoding}: {enc_error}")
                continue
        
        if not working_encoding or not header_fields:
            await status_msg.edit_text(
                "❌ *خطا در خواندن فایل CSV: فرمت فایل نامعتبر است*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            if os.path.exists(csv_file_path):
                os.remove(csv_file_path)
            return
        
        # Log the fieldnames for debugging
        logger.info(f"CSV fieldnames: {header_fields}")
        
        # Verify required columns
        required_fields = ['username', 'password', 'secret']
        missing_fields = [field for field in required_fields if field not in header_fields]
        
        if missing_fields:
            await status_msg.edit_text(
                f"❌ *خطا: ستون‌های {', '.join(missing_fields)} در فایل CSV یافت نشد*\n\n"
                f"ستون‌های مورد نیاز: username, password, secret, slots (اختیاری)",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            if os.path.exists(csv_file_path):
                os.remove(csv_file_path)
            return
        
        # Now process rows with the correct encoding
        total_rows = 0
        
        # Read and process the file with the correct encoding
        with open(csv_file_path, 'r', newline='', encoding=working_encoding) as csvfile:
            reader = csv.DictReader(csvfile)
            
            for i, row in enumerate(reader, 1):
                total_rows = i
                try:
                    # Extract data with detailed validation
                    if 'username' not in row or not row['username'].strip():
                        error_count += 1
                        errors.append(f"Row {i}: Missing username")
                        continue
                        
                    if 'password' not in row or not row['password'].strip():
                        error_count += 1
                        errors.append(f"Row {i}: Missing password")
                        continue
                        
                    if 'secret' not in row or not row['secret'].strip():
                        error_count += 1
                        errors.append(f"Row {i}: Missing secret")
                        continue
                    
                    username = row['username'].strip()
                    password = row['password'].strip()
                    secret = row['secret'].strip()
                    
                    # Validate username (should be at least 3 characters)
                    if len(username.strip()) < 3:
                        error_count += 1
                        errors.append(f"Row {i}: Username too short")
                        continue
                    
                    # Username validation passed - no email format required
                    
                    # Get slots (optional)
                    max_slots = 15  # Default value
                    if 'slots' in row and row['slots'] and row['slots'].strip():
                        try:
                            max_slots = int(row['slots'].strip())
                            if max_slots <= 0:
                                max_slots = 15
                        except ValueError:
                            # Use default if conversion fails
                            errors.append(f"Row {i}: Invalid slots value, using default")
                            max_slots = 15
                    
                    # Encrypt credentials
                    pass_enc = encrypt(password)
                    secret_enc = encrypt(secret)
                    
                    # Insert into database
                    with db.get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """INSERT INTO seats (email, pass_enc, secret_enc, max_slots)
                                   VALUES (%s, %s, %s, %s)
                                   ON CONFLICT (email) DO NOTHING
                                   RETURNING id""",
                                (username, pass_enc, secret_enc, max_slots)
                            )
                            result = cur.fetchone()
                            conn.commit()
                            
                            if result is None or cur.rowcount == 0:
                                # Username already exists
                                duplicate_count += 1
                            else:
                                success_count += 1
                                logger.info(f"Added seat: {username}")
                                
                except Exception as row_error:
                    error_count += 1
                    error_str = str(row_error)[:100]
                    errors.append(f"Row {i}: {error_str}")
                    logger.error(f"Error processing row {i}: {error_str}")
                
                # Update status every 5 rows
                if i % 5 == 0:
                    try:
                        await status_msg.edit_text(
                            f"⏳ *در حال پردازش ردیف‌های CSV...*\n\n"
                            f"پردازش شده: {i}\n"
                            f"موفق: {success_count}\n"
                            f"تکراری: {duplicate_count}\n"
                            f"خطا: {error_count}",
                            parse_mode="Markdown"
                        )
                    except Exception as status_error:
                        logger.error(f"Error updating status: {status_error}")
        
        # Show final results
        result_message = f"✅ *افزودن گروهی اکانت‌ها انجام شد*\n\n"
        result_message += f"🔢 کل ردیف‌ها: {total_rows}\n"
        result_message += f"✅ موفق: {success_count}\n"
        result_message += f"🔄 تکراری: {duplicate_count}\n"
        result_message += f"❌ خطا: {error_count}\n"
        
        if errors:
            result_message += "\n📋 *خطاها:*\n"
            # Show first 5 errors max
            for error in errors[:5]:
                result_message += f"- {error}\n"
            
            if len(errors) > 5:
                result_message += f"و {len(errors) - 5} خطای دیگر..."
        
        await status_msg.edit_text(
            result_message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Error in CSV processing: {e}")
        await status_msg.edit_text(
            f"❌ *خطای سیستمی در پردازش فایل*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
    
    finally:
        # Clean up temp file
        try:
            if os.path.exists(csv_file_path):
                os.remove(csv_file_path)
        except Exception as e:
            logger.error(f"Error cleaning up temp file: {e}")
    
    return -1  # End conversation


async def process_add_seat_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process the add seat input message directly."""
    message = update.message
    message_text = message.text.strip()
    user = update.effective_user
    
    # Check if user is admin
    is_admin = await check_admin(user.id)
    if not is_admin:
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return
    
    try:
        # Parse the input - split into maximum 4 parts (username, password, secret, slots)
        # This allows password and secret to contain spaces
        parts = message_text.split(maxsplit=3)
        if len(parts) < 3:
            await message.reply_text(
                "❌ *خطا: فرمت نامعتبر*\n\n"
                "لطفاً اطلاعات را به صورت `username password secret [slots]` وارد کنید.",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Extract the parts
        username = parts[0].strip()
        password = parts[1].strip()
        
        # For the last part, check if it contains both secret and slots
        if len(parts) == 4:
            secret = parts[2].strip()
            try:
                max_slots = int(parts[3].strip())
            except ValueError:
                # If the last part isn't a valid integer, treat it as part of the secret
                secret = parts[2] + ' ' + parts[3]
                max_slots = 15
        else:
            secret = parts[2].strip()
            max_slots = 15
        
        # Validate username (should be at least 3 characters)
        if len(username.strip()) < 3:
            await message.reply_text(
                "❌ *خطا: نام کاربری باید حداقل ۳ کاراکتر باشد*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Validate slots
        if max_slots <= 0:
            await message.reply_text(
                "❌ *خطا: تعداد صندلی‌ها باید بزرگتر از صفر باشد*",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return
        
        # Encrypt credentials
        pass_enc = encrypt(password)
        secret_enc = encrypt(secret)
        
        # Insert into database
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO seats (email, pass_enc, secret_enc, max_slots)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (email) DO NOTHING
                       RETURNING id""",
                    (username, pass_enc, secret_enc, max_slots)
                )
                
                result = cur.fetchone()
                conn.commit()
                
                # Check if the insert was successful
                if result is None or cur.rowcount == 0:
                    # Username already exists
                    await message.reply_text(
                        f"⚠️ *این نام کاربری قبلاً ثبت شده است*\n\n"
                        f"👤 نام کاربری: `{username}`",
                        parse_mode="Markdown",
                        reply_markup=get_admin_keyboard()
                    )
                    return
                
                seat_id = result[0]
        
        # Confirm success
        await message.reply_text(
            f"✅ *صندلی اضافه شد*\n\n"
            f"👤 نام کاربری: `{username}`\n"
            f"💺 صندلی‌ها: {max_slots}\n"
            f"🆔 شناسه: #{seat_id}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        logger.info(f"Admin {update.effective_user.id} added new seat: {username} (ID: {seat_id})")
        
    except Exception as e:
        logger.error(f"Error adding seat: {e}")
        await message.reply_text(
            f"❌ *خطا در افزودن صندلی*\n\n`{str(e)[:200]}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )


async def process_add_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the add seat input message via ConversationHandler."""
    # This function is no longer used directly, but remains for compatibility
    # Instead, process_add_seat_direct is called from echo handler
    return -1


async def admin_process_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process admin input for settings."""
    user = update.effective_user
    message_text = update.message.text.strip()
    action = context.user_data.get('admin_action')
    
    logger.info(f"admin_process_input called for user {user.id} with action '{action}' and text '{message_text}'")
    
    if not action:
        logger.error(f"No admin_action found in context.user_data for user {user.id}")
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
    
    # Log all callback queries for debugging
    logger.info(f"Callback handler processing: '{data}' from user {user.id}")
    
    # Skip membership check for admin callbacks and check_membership itself
    skip_membership_check = (
        data == "check_membership" or 
        data.startswith("admin:") or 
        data.startswith("approve:") or 
        data.startswith("reject:") or
        data.startswith("seat:") or
        await check_admin(user.id)
    )
    
    # Check channel membership for regular users (not admins)
    if not skip_membership_check:
        is_member, missing_channels = await check_channel_membership(user.id, context.bot)
        if not is_member:
            await send_join_channels_message(update, context, missing_channels)
            return
    
    if data == "buy_service":
        # Show subscription options
        await show_subscription_options(update, context)
        
    elif data == "buy:1mo":
        # Show purchase info for one-month plan
        await show_purchase_info(update, context)
        
    elif data == "wallet":
        # Handle wallet button
        await show_wallet(update, context)
        
    elif data == "menu:ref":
        # Handle referral menu
        from handlers.referral import show_referral_menu
        await show_referral_menu(update, context)
        
    elif data == "manage_service":
        # Handle manage service button
        await manage_services(update, context)
        
    elif data == "back_to_menu":
        # Return to main menu
        await query.edit_message_text(
            f"👤 * به ربات \"اکانت یار\" : فروش اکانت قانونی فیلترشکن خوش آمدید👋*\n\n"
            f"از منوی زیر، گزینه مورد نظر خود را انتخاب کنید.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
        
    elif data == "check_membership":
        # Check channel membership when user clicks the button
        is_member, missing_channels = await check_channel_membership(user.id, context.bot)
        if is_member:
            # User is now a member, show main menu
            await query.edit_message_text(
                f"✅ *عضویت شما تأیید شد!*\n\n"
                f"👤  به ربات \"اکانت یار\" : فروش اکانت قانونی فیلترشکن خوش آمدید 👋\n\n"
                f"✅ سرویس های فعال درحال حاضر:\n"
                f"- اکانت قانونی فیلترشکن پرسرعت ویندسکرایب 🔐\n\n"
                f"از منوی زیر، گزینه مورد نظر خود را انتخاب کنید.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )
        else:
            # User is still not a member, show join channels message again
            await send_join_channels_message(update, context, missing_channels)
    
    # Seat management callbacks
    elif data.startswith("seat:"):
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
            return
            
        # Extract seat action and ID
        match = re.match(r'^seat:(\w+):(\d+)$', data)
        if match:
            action, seat_id = match.groups()
            seat_id = int(seat_id)
            
            if action == "del":
                # Handle seat deletion
                try:
                    # Get the current page to return to it after deletion
                    page_match = re.search(r"admin:list\|(\d+)", context.user_data.get('last_list_page', 'admin:list|1'))
                    current_page = int(page_match.group(1)) if page_match else 1
                    
                    # Update seat status to disabled
                    with db.get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE seats SET status='disabled' WHERE id=%s", 
                                (seat_id,)
                            )
                            conn.commit()
                            
                    # Show confirmation
                    await query.answer("حذف شد", show_alert=True)
                    
                    # Refresh the current page
                    from handlers.admin_accounts import handle_accounts_list
                    await handle_accounts_list(update, context, current_page)
                    
                except Exception as e:
                    logger.error(f"Error deleting seat: {e}")
                    await query.answer(f"خطا در حذف صندلی: {str(e)[:200]}", show_alert=True)
            
            elif action == "edit":
                # Handle seat edit
                try:
                    # Save seat_id in context for the message handler
                    context.user_data['edit_seat_id'] = seat_id
                    
                    # Get the current page to return to after editing
                    page_match = re.search(r"admin:list\|(\d+)", context.user_data.get('last_list_page', 'admin:list|1'))
                    current_page = int(page_match.group(1)) if page_match else 1
                    context.user_data['edit_return_page'] = current_page
                    
                    # Create keyboard
                    keyboard = [
                        [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"admin:list|{current_page}")]
                    ]
                    
                    # Show edit prompt
                    await query.edit_message_text(
                        f"✏️ *ویرایش صندلی شماره #{seat_id}*\n\n"
                        f"نام کاربری پسورد سکرت اسلات (برای نگه‌داشتن مقدار فعلی از - استفاده کن)\n\n"
                        f"مثال:\n`newusername - newsecret 25`\n\n"
                        f"یا برای حفظ همه مقادیر:\n`- - - -`",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception as e:
                    logger.error(f"Error preparing seat edit: {e}")
                    await query.answer(f"خطا در آماده‌سازی ویرایش: {str(e)[:200]}", show_alert=True)
    
    # Admin panel callbacks
    elif data.startswith("admin:"):
        # Handle admin back button first
        if data == "admin:back":
            try:
                logger.info(f"admin:back callback received from user {user.id}")
                
                # Check if user is admin
                is_admin = await check_admin(user.id)
                logger.info(f"User {user.id} admin check result: {is_admin}")
                
                if not is_admin:
                    logger.warning(f"User {user.id} attempted admin:back but is not admin")
                    await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
                    return
                
                logger.info(f"Getting admin keyboard...")
                try:
                    admin_keyboard = get_admin_keyboard()
                    logger.info(f"Admin keyboard created successfully")
                except Exception as kb_error:
                    logger.error(f"Error creating admin keyboard: {kb_error}")
                    raise kb_error
                
                logger.info(f"Editing message for admin:back - user {user.id}")
                
                # Return to admin panel
                await query.edit_message_text(
                    f"💻 *پنل مدیریت*\n\n"
                    f"به پنل مدیریت بات خوش آمدید.\n"
                    f"لطفا گزینه مورد نظر خود را انتخاب کنید:",
                    reply_markup=admin_keyboard,
                    parse_mode="Markdown"
                )
                
                logger.info(f"Successfully returned to admin panel for user {user.id}")
                return
                
            except Exception as e:
                logger.error(f"Error in admin:back callback for user {user.id}: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                await query.answer(f"خطا: {str(e)[:100]}", show_alert=True)
                return
        
        # Check if user is admin for other admin actions
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.edit_message_text("شما دسترسی ادمین ندارید.")
            return
            
        admin_action = data.split(":")[1]
        
        if admin_action == "cards" or admin_action.startswith("cards|"):
            # Cards management
            # Check if pagination parameter is included
            page = 0
            if "|" in admin_action:
                try:
                    page = int(admin_action.split("|")[1])
                except (ValueError, IndexError):
                    page = 0
            
            # Show cards list
            await admin_cards.show_cards_list(update, context, page)
            return
            
        elif admin_action == "card":
            # Legacy card management - redirect to new system
            await admin_cards.show_cards_list(update, context)
            return
            
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
            try:
                from handlers.admin_price import handle_change_price
                await handle_change_price(update, context, "service_price")
            except ImportError:
                # Fallback to built-in handler
                await handle_change_price(update, context)
            
        elif admin_action == "usd":
            # Change USD rate
            await handle_admin_usd_rate(update, context)
            
        elif admin_action == "utm":
            # Show UTM statistics
            await handle_utm_stats(update, context)
            
        elif admin_action == "listcsv":
            # Generate and send CSV list of accounts
            await handle_list_csv(update, context)
            
        elif admin_action == "list" or admin_action.startswith("list|"):
            # Handle account management list with pagination
            from handlers.admin_accounts import handle_accounts_list
            
            # Check if page number is specified
            page = 1
            if "|" in admin_action:
                try:
                    page = int(admin_action.split("|")[1])
                except (ValueError, IndexError):
                    page = 1
            
            await handle_accounts_list(update, context, page)
            
        elif admin_action == "deleteall":
            # Show delete all accounts confirmation prompt
            from handlers.admin_accounts import handle_delete_all_accounts_prompt
            await handle_delete_all_accounts_prompt(update, context)
            
        elif admin_action.startswith("deleteall:"):
            # Handle delete all accounts confirmation
            if admin_action == "deleteall:confirm":
                from handlers.admin_accounts import handle_delete_all_accounts_confirm
                await handle_delete_all_accounts_confirm(update, context)
            
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
            username = seat["email"]  # Database still uses 'email' field, but content is username
            password = decrypt(seat["pass_enc"])
            
            # Send message to user
            user_message = (
                f"🎉 *سفارش شماره #{order_id} تایید شد*\n\n"
                f"👤 نام کاربری: `{username}`\n"
                f"🔑 رمز عبور: `{password}`\n\n"
                f"✅ برای آموزش ورود به اکانت و دریافت کد 2FA، روی دکمه زیر کلیک کنید.\n\n"
                f"❌ لطفا اطلاعات حساب خود را با احتیاط نگهداری کنید."
            )
            
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=user_message,
                    parse_mode="Markdown",
                    reply_markup=get_setup_2fa_button(order_id)
                )
            except Exception as e:
                logger.error(f"Error sending credentials to user: {e}")
            
            # Send sales report to LOG_SELL_CHID channel if configured
            if LOG_SELL_CHID:
                try:
                    # Get user details for the report
                    with db.get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT username, first_name FROM users WHERE tg_id = %s", (tg_id,))
                            user_details = cur.fetchone()
                            
                            # Get total remaining capacity across all seats
                            cur.execute("SELECT SUM(max_slots - sold) FROM seats WHERE status = 'active'")
                            remaining_capacity = cur.fetchone()[0] or 0
                            
                    username = user_details[0] if user_details and user_details[0] else user_details[1] if user_details else "کاربر"
                    user_mention = f"@{username}" if username and not username.startswith('کاربر') else username
                    
                    # Decrypt TOTP secret for the report
                    totp_secret = decrypt_secret(seat["secret_enc"])
                    
                    sales_report = (
                        f"✅ گزارش فروش\n\n"
                        f"اکانت ویندسکرایب یک ماهه برای کاربر {user_mention} ارسال شد\n\n"
                        f"👤 نام کاربری: {username}\n"
                        f"🔑 رمز عبور: {password}\n"
                        f"🔐 کد 2FA اکانت: {totp_secret}\n\n"
                        f"💺 ظرفیت کل صندلی های باقی مانده: {remaining_capacity}"
                    )
                    
                    await context.bot.send_message(
                        chat_id=LOG_SELL_CHID,
                        text=sales_report
                    )
                except Exception as e:
                    logger.error(f"Error sending sales report: {e}")
            
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
            
            # Update admin message - safely
            try:
                # First try to edit message text
                await query.edit_message_text(f"✅ سفارش #{order_id} تایید شد.")
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    # If message has no text (e.g. it's a photo), answer callback query instead
                    await query.answer(f"✅ سفارش #{order_id} تایید شد.", show_alert=True)
                    
                    # Try to edit caption if it's a media message
                    try:
                        await query.edit_message_caption(f"✅ سفارش #{order_id} تایید شد.")
                    except Exception:
                        # If we can't edit caption either, just log it
                        logger.info(f"Could not edit message or caption for order #{order_id} approval")
                else:
                    # For other BadRequest errors, just log and notify
                    logger.error(f"Error updating admin message on approval: {e}")
                    await query.answer("خطا در بروزرسانی پیام", show_alert=True)
        else:
            # Show error
            try:
                # First try to edit message text
                await query.edit_message_text(
                    f"❌ خطا در تایید سفارش: {result}"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    # If message has no text (e.g. it's a photo), answer callback query instead
                    await query.answer(f"❌ خطا در تایید سفارش: {result}", show_alert=True)
                    
                    # Try to edit caption if it's a media message
                    try:
                        await query.edit_message_caption(f"❌ خطا در تایید سفارش: {result}")
                    except Exception:
                        # If we can't edit caption either, just log it
                        logger.info(f"Could not edit message or caption for order error")
                else:
                    # For other BadRequest errors, just log and notify
                    logger.error(f"Error updating admin message on approval error: {e}")
                    await query.answer("خطا در بروزرسانی پیام", show_alert=True)
    
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
                         f"✏️ لطفا با پشتیبانی تماس بگیرید یا مجددا تلاش کنید.\n\n"
                         f"💬 پشتیبانی: @AccountYarSup",
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
            
            # Update admin message - safely
            try:
                # First try to edit message text
                await query.edit_message_text(f"❌ سفارش #{order_id} رد شد.")
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    # If message has no text (e.g. it's a photo), answer callback query instead
                    await query.answer(f"❌ سفارش #{order_id} رد شد.", show_alert=True)
                    
                    # Try to edit caption if it's a media message
                    try:
                        await query.edit_message_caption(f"❌ سفارش #{order_id} رد شد.")
                    except Exception:
                        # If we can't edit caption either, just log it
                        logger.info(f"Could not edit message or caption for order #{order_id} rejection")
                else:
                    # For other BadRequest errors, just log and notify
                    logger.error(f"Error updating admin message on rejection: {e}")
                    await query.answer("خطا در بروزرسانی پیام", show_alert=True)
        else:
            # Show error
            try:
                # First try to edit message text
                await query.edit_message_text(
                    f"❌ خطا در رد سفارش: {result}"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    # If message has no text (e.g. it's a photo), answer callback query instead
                    await query.answer(f"❌ خطا در رد سفارش: {result}", show_alert=True)
                    
                    # Try to edit caption if it's a media message
                    try:
                        await query.edit_message_caption(f"❌ خطا در رد سفارش: {result}")
                    except Exception:
                        # If we can't edit caption either, just log it
                        logger.info(f"Could not edit message or caption for order #{order_id} rejection")
                else:
                    # For other BadRequest errors, just log and notify
                    logger.error(f"Error updating admin message on rejection: {e}")
                    await query.answer("خطا در بروزرسانی پیام", show_alert=True)
    
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
                    
                    # Calculate remaining seconds until code expires (codes are valid for 30 seconds + 30 sec buffer)
                    remaining_seconds = (30 - (int(time.time()) % 30)) + 30
                    
                    # Create appropriate message based on attempt count
                    if new_count == 1:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه"
                        full_message = (
                            f"📲 *کد 2FA شما:*\n\n"
                            f"`{code}`\n\n"
                            f"⏰ این کد {remaining_seconds} ثانیه اعتبار دارد\n\n"
                            f"🔑 *دریافت کد مجدد:* (مهلت دریافت 2 دقیقه!)\n\n"
                            f"⚠️ *توجه:* این آخرین باری است که می‌توانید کد دریافت کنید"
                        )
                        keyboard = get_code_2fa_retry_button(order_id)
                    elif new_count == 2:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه (دفعهٔ دوم)"
                        full_message = f"📲 *کد 2FA شما:*\n\n`{code}`\n\n⏰ این کد {remaining_seconds} ثانیه اعتبار دارد (دفعهٔ دوم).\n\n⚠️ *توجه:* این آخرین باری است که می‌توانید کد دریافت کنید"
                        keyboard = None
                    else:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه"
                        full_message = f"📲 *کد 2FA شما:*\n\n`{code}`\n\n⏰ این کد {remaining_seconds} ثانیه اعتبار دارد"
                        keyboard = None
                    
                    # Show alert with code and TTL
                    await query.answer(alert_message, show_alert=True)
                    
                    # Also send the code as a separate message for easier copying
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=full_message,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
        except Exception as e:
            logger.error(f"Error generating TOTP code: {e}")
            # Log detailed error information using the enhanced logger
            if ENHANCED_LOGGING:
                log_exception(e, {"order_id": order_id, "callback_data": data})
            await query.answer("خطا در تولید کد", show_alert=True)
            # Also send as regular message
            await context.bot.send_message(
                chat_id=user.id,
                text="❌ *خطا در تولید کد 2FA*\n\n"
                     "متأسفانه در تولید کد خطایی رخ داده است. "
                     "لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید.\n\n"
                     "💬 پشتیبانی: @AccountYarSup",
                parse_mode="Markdown"
            )
    
    # Handle seat operations
    elif data.startswith("seat:"):
        # Handle seat operations (delete, edit, info)
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("درخواست نامعتبر", show_alert=True)
            return
            
        action = parts[1]
        seat_id = int(parts[2])
        
        # Import account management handlers
        from handlers.admin_accounts import handle_seat_delete, handle_seat_edit_prompt
        
        if action == "del":
            # Handle seat deletion
            await handle_seat_delete(update, context, seat_id)
        elif action == "edit":
            # Handle seat editing
            await handle_seat_edit_prompt(update, context, seat_id)
        elif action == "info":
            # Show seat info (currently redirects to edit for simplicity)
            await handle_seat_edit_prompt(update, context, seat_id)

    # Admin: Card management
    elif data == "admin:card" or data == "admin:cards":
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
            return

        # Redirect to new card management system
        await admin_cards.show_cards_list(update, context)
        
    # Card management callbacks
    elif data == "card:add":
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
            return
        
        await admin_cards.add_card_prompt(update, context)
        
    elif data.startswith("card:del:"):
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
            return
        
        try:
            card_id = int(data.split(":")[2])
            await admin_cards.delete_card(update, context, card_id)
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid card deletion ID format: {e}")
            await query.answer("خطا در حذف کارت", show_alert=True)
            
    elif data.startswith("card:edit:"):
        # Check if user is admin
        is_admin = await check_admin(user.id)
        if not is_admin:
            await query.answer("شما اجازه دسترسی به این بخش را ندارید.", show_alert=True)
            return
        
        try:
            card_id = int(data.split(":")[2])
            await admin_cards.edit_card_prompt(update, context)
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid card edit ID format: {e}")
            await query.answer("خطا در ویرایش کارت", show_alert=True)

    # Handle quick TOTP code generation with limited retries
    elif data.startswith("code:"):
        # Extract order ID from callback data
        order_id = int(data.split(":")[1])
        
        try:
            from datetime import datetime, timezone
            
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    # Get current 2FA usage info
                    cur.execute("SELECT twofa_count, twofa_last, twofa_disabled FROM orders WHERE id = %s", (order_id,))
                    result = cur.fetchone()
                    
                    if not result:
                        await query.answer("خطا: سفارش یافت نشد", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="❌ خطا: سفارش یافت نشد"
                        )
                        return
                    
                    twofa_count, twofa_last, twofa_disabled = result
                    now = datetime.now(timezone.utc)
                    
                    # Check if 2FA is permanently disabled
                    if twofa_disabled:
                        await query.answer("شما کد رو دریافت کردید و در صورت مشکل با پشتیبانی @AccountYarSup تماس بگیرید.", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="⏰ *مهلت استفاده از کد 2FA به پایان رسیده*\n\n"
                                 "شما قبلاً کد 2FA خود را دریافت کرده‌اید. اگر مشکلی دارید، "
                                 "لطفاً با پشتیبانی تماس بگیرید.\n\n"
                                 "💬 پشتیبانی: @AccountYarSup",
                            parse_mode="Markdown"
                        )
                        return
                    
                    # Check if we need to disable 2FA due to timeout
                    if twofa_count > 0 and twofa_last and (now - twofa_last).total_seconds() >= 120:
                        # 120 seconds passed since first attempt - disable permanently
                        cur.execute("UPDATE orders SET twofa_disabled = TRUE WHERE id = %s", (order_id,))
                        conn.commit()
                        await query.answer("مهلت دریافت کد به پایان رسیده است. در صورت مشکل با پشتیبانی تماس بگیرید.", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="⏰ *مهلت دریافت کد 2FA به پایان رسیده*\n\n"
                                 "بیش از 2 دقیقه از اولین درخواست شما گذشته است. "
                                 "اگر مشکلی دارید، لطفاً با پشتیبانی تماس بگیرید.",
                            parse_mode="Markdown"
                        )
                        return
                    
                    # Check retry limits
                    if twofa_count >= 2:
                        # Already used 2 times - disable permanently
                        cur.execute("UPDATE orders SET twofa_disabled = TRUE WHERE id = %s", (order_id,))
                        conn.commit()
                        await query.answer("شما کد رو دریافت کردید و در صورت مشکل با پشتیبانی تماس بگیرید.", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="⚡ *حداکثر تعداد درخواست کد 2FA*\n\n"
                                 "شما 2 بار کد 2FA دریافت کرده‌اید و دیگر امکان دریافت کد جدید وجود ندارد. "
                                 "اگر مشکلی دارید، لطفاً با پشتیبانی تماس بگیرید.",
                            parse_mode="Markdown"
                        )
                        return
                    
                    # Get seat ID and secret for this order
                    cur.execute("SELECT seat_id FROM orders WHERE id = %s", (order_id,))
                    result = cur.fetchone()
                    if not result or not result[0]:
                        await query.answer("خطا: اطلاعات صندلی یافت نشد", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="❌ خطا: اطلاعات صندلی یافت نشد"
                        )
                        return
                        
                    seat_id = result[0]
                    
                    # Get the secret for the seat
                    cur.execute("SELECT secret_enc FROM seats WHERE id = %s", (seat_id,))
                    result = cur.fetchone()
                    if not result:
                        await query.answer("خطا: اطلاعات رمز یافت نشد", show_alert=True)
                        # Also send as regular message
                        await context.bot.send_message(
                            chat_id=user.id,
                            text="❌ خطا: اطلاعات رمز یافت نشد"
                        )
                        return
                    
                    secret_enc = result[0]
                    
                    # Decrypt secret
                    secret = decrypt(secret_enc)
                    
                    # Generate TOTP code
                    import pyotp
                    import time
                    
                    totp = pyotp.TOTP(secret)
                    code = totp.now()
                    
                    # Calculate remaining seconds until code expires (codes are valid for 30 seconds + 30 sec buffer)
                    remaining_seconds = (30 - (int(time.time()) % 30)) + 30
                    
                    # Update usage count and timestamp
                    new_count = twofa_count + 1
                    
                    # If this is the second code, disable 2FA permanently
                    if new_count >= 2:
                        cur.execute(
                            "UPDATE orders SET twofa_count = %s, twofa_last = %s, twofa_disabled = TRUE WHERE id = %s",
                            (new_count, now, order_id)
                        )
                    else:
                        cur.execute(
                            "UPDATE orders SET twofa_count = %s, twofa_last = %s WHERE id = %s",
                            (new_count, now, order_id)
                        )
                    conn.commit()
                    
                    # Create appropriate message based on attempt count
                    if new_count == 1:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه"
                        full_message = f"📲 *کد 2FA شما:*\n\n`{code}`\n\n⏰ این کد {remaining_seconds} ثانیه اعتبار دارد"
                    elif new_count == 2:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه (دفعهٔ دوم)"
                        full_message = f"📲 *کد 2FA شما:*\n\n`{code}`\n\n⏰ این کد {remaining_seconds} ثانیه اعتبار دارد (دفعهٔ دوم)."
                    else:
                        alert_message = f"📲 کد 2FA شما: {code}\n\n⏰ اعتبار {remaining_seconds} ثانیه"
                        full_message = f"📲 *کد 2FA شما:*\n\n`{code}`\n\n⏰ این کد {remaining_seconds} ثانیه اعتبار دارد"
                    
                    # Show alert with code and TTL
                    await query.answer(alert_message, show_alert=True)
                    
                    # Also send the code as a separate message for easier copying
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=full_message,
                        parse_mode="Markdown"
                    )
                    
        except Exception as e:
            logger.error(f"Error generating TOTP code: {e}")
            # Log detailed error information using the enhanced logger
            if ENHANCED_LOGGING:
                log_exception(e, {"order_id": order_id, "callback_data": data})
            await query.answer("خطا در تولید کد", show_alert=True)
    
    # Handle no-operation callback (navigation spacer buttons)
    elif data == "noop":
        # Just answer the callback query to acknowledge it, no action needed
        await query.answer()
    
    # Handle 2FA setup request
    elif data.startswith("setup2fa:"):
        # Extract order ID
        order_id = int(data.split(":")[1])
        
        try:
            # Answer the callback query first
            await query.answer()
            
            # Send tutorial message as a separate message (not editing the original)
            tutorial_message = (
                f"📱 *آموزش ورود به اکانت در ویندسکرایب*\n\n"
                f"📥 *دانلود اپلیکیشن:*\n"
                f"• [لینک پلی استور](https://play.google.com/store/apps/details?id=com.windscribe.vpn)\n"
                f"• [دانلود لینک مستقیم](https://t.me/AccYarVPN/5)\n\n"
                f"🔧 *آموزش ورود:*\n"
                f"1️⃣ برنامه ویندسکرایب را نصب و باز کنید\n"
                f"2️⃣ روی دکمه *Login* کلیک کنید\n"
                f"3️⃣ نام کاربری و پسورد خریداری شده را وارد کنید\n"
                f"4️⃣ پیام *Two-Factor Authentication* ظاهر می‌شود\n"
                f"5️⃣ روی دکمه زیر برای دریافت کد 2FA بزنید\n"
                f"6️⃣ کد دریافتی را در برنامه وارد کنید\n\n"
                f"⚠️ *توجه:* هر کد 60 ثانیه اعتبار دارد و حداکثر 2 بار می‌توانید کد دریافت کنید."
            )
            
            await context.bot.send_message(
                chat_id=user.id,
                text=tutorial_message,
                parse_mode="Markdown",
                reply_markup=get_code_2fa_button(order_id)
            )
            
        except Exception as e:
            logger.error(f"Error sending 2FA tutorial: {e}")
            await query.answer("خطا در ارسال آموزش", show_alert=True)
    
    # Handle 2FA code request


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Get the exception
    error = context.error
    
    # Record full stack trace and context
    error_context = {}
    
    # Safely extract information from update
    try:
        if update:
            error_context["update_id"] = update.update_id if hasattr(update, "update_id") else None
            error_context["user_id"] = update.effective_user.id if hasattr(update, "effective_user") and update.effective_user else None
            error_context["chat_id"] = update.effective_chat.id if hasattr(update, "effective_chat") and update.effective_chat else None
            
            # Add callback data if present
            if hasattr(update, "callback_query") and update.callback_query:
                error_context["callback_data"] = update.callback_query.data
            
            # Add message text or document if present
            if hasattr(update, "message") and update.message:
                if update.message.text:
                    error_context["message_text"] = update.message.text
                elif update.message.document:
                    error_context["document_filename"] = update.message.document.file_name
    except Exception as context_error:
        error_context["context_extraction_error"] = str(context_error)
    
    # Try to safely get user data
    try:
        if hasattr(context, "user_data") and context.user_data:
            error_context["user_data"] = dict(context.user_data)
    except Exception as user_data_error:
        error_context["user_data_error"] = str(user_data_error)
    
    # Use enhanced logging if available
    if ENHANCED_LOGGING:
        error_details = log_exception(error, error_context)
    else:
        # Basic logging
        logger.error("Exception while handling an update:", exc_info=error)
        exc_type, exc_value, exc_traceback = sys.exc_info()
        stack_trace = traceback.format_exception(exc_type, exc_value, exc_traceback)
        error_details = f"Error: {str(error)}\nStack Trace: {''.join(stack_trace)}"
        
    # Format error message for admin notification
    error_message = f"⚠️ *خطای سیستمی*\n\n"
    
    # Add user information
    if hasattr(update, "effective_user") and update.effective_user:
        user = update.effective_user
        error_message += f"👤 کاربر: {user.full_name} (@{user.username})\n"
        error_message += f"🆔 آیدی: `{user.id}`\n\n"
    
    # Add error type and message
    error_message += f"❌ نوع خطا: `{error.__class__.__name__}`\n"
    error_message += f"📝 پیام خطا: `{str(error)[:100]}`\n\n"
    
    # Add context of where error occurred
    if hasattr(update, "callback_query") and update.callback_query:
        error_message += f"🔄 Callback: `{update.callback_query.data}`\n"
    elif hasattr(update, "message") and update.message:
        if update.message.text:
            error_message += f"💬 پیام: `{update.message.text[:50]}`\n"
        elif update.message.document:
            error_message += f"📎 فایل: `{update.message.document.file_name}`\n"
    
    # Add timestamp
    error_message += f"⏰ زمان: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    # Add log file location
    error_message += f"📂 فایل لاگ: `{os.path.join(LOG_DIR, 'error.log')}`"
    
    # Try to notify admin
    try:
        admin_id = os.environ.get("ADMIN_ID")
        if admin_id:
            await context.bot.send_message(
                chat_id=admin_id,
                text=error_message,
                parse_mode="Markdown"
            )
    except Exception as notify_error:
        logger.error(f"Failed to notify admin about error: {notify_error}")
        
    # If this was from a user, inform them about the error (only private chats)
    try:
        if (hasattr(update, "effective_chat") and update.effective_chat and 
            hasattr(update, "effective_user") and update.effective_user and
            update.effective_chat.type == "private"):
            await context.bot.send_message(
                chat_id=update.effective_user.id,  # Use user ID instead of chat ID
                text="❌ خطایی در سیستم رخ داده است. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید.\n\n💬 پشتیبانی: @AccountYarSup"
            )
    except Exception as inform_error:
        logger.error(f"Failed to inform user about error: {inform_error}")


async def async_main() -> None:
    """Async main function to properly handle event loop."""
    try:
        logger.info("Starting async_main...")
        
        # Create the Application
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN is not set in environment variables")
            return

        logger.info("BOT_TOKEN is set, creating application...")
        application = Application.builder().token(BOT_TOKEN).build()
        logger.info("Application created successfully")

        # Initialize database
        logger.info("Initializing database...")
        db.init_db()
        logger.info("Database initialized successfully")
        
        # Load force join settings
        logger.info("Loading force join settings...")
        await load_force_join_settings()
        logger.info("Force join settings loaded successfully")
        
        # Register handlers
        logger.info("Registering command handlers...")
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("menu", menu))  # Alias for start
        application.add_handler(CommandHandler("buy", buy_command))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # Photo handler for receipts
        application.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
        logger.info("Command handlers registered successfully")
        
        # Admin conversation handlers - MOVED BEFORE main CallbackQueryHandler
        logger.info("Setting up conversation handlers...")
        from telegram.ext import ConversationHandler
        
        # Try to import seat editing handler, fallback if not available
        try:
            from handlers.admin_accounts import process_seat_edit
            seat_edit_handler = process_seat_edit
            logger.info("Successfully imported process_seat_edit")
        except ImportError as e:
            logger.warning(f"Could not import process_seat_edit from handlers.admin_accounts: {e}")
            # Create a dummy handler that just returns ConversationHandler.END
            async def dummy_seat_edit(update, context):
                await update.message.reply_text("Seat editing not available")
                return ConversationHandler.END
            seat_edit_handler = dummy_seat_edit
        
        admin_conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(handle_admin_usd_rate, pattern=r'^admin:usd$'),
                CallbackQueryHandler(handle_change_price, pattern=r'^admin:price$'),
                CallbackQueryHandler(handle_add_seat, pattern=r'^admin:addseat$'),
                CallbackQueryHandler(handle_bulk_csv, pattern=r'^admin:bulkcsv$'),
            ],
            states={
                ADMIN_WAITING_CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_input)],
                ADMIN_WAITING_USD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_input)],
                ADMIN_WAITING_SEAT_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_seat)],
                ADMIN_WAITING_CSV: [MessageHandler(filters.Document.ALL, process_csv_upload_direct)],
                ADMIN_WAITING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_price_input)],
                # Add handler for seat editing
                ADMIN_WAITING_EDIT_SEAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, seat_edit_handler)],
            },
            fallbacks=[
                CommandHandler("cancel", lambda u, c: -1),
                CallbackQueryHandler(handle_admin_usd_rate, pattern=r'^admin:usd$'),
                CallbackQueryHandler(handle_change_price, pattern=r'^admin:price$'),
                CallbackQueryHandler(handle_add_seat, pattern=r'^admin:addseat$'),
                CallbackQueryHandler(handle_bulk_csv, pattern=r'^admin:bulkcsv$'),
            ],
            allow_reentry=True,
            name="admin_conversation"
        )
        application.add_handler(admin_conv_handler)
        logger.info("Conversation handlers set up successfully")
        
        # Callback query handler for inline keyboards - MOVED AFTER ConversationHandler
        logger.info("Adding callback query handler...")
        application.add_handler(CallbackQueryHandler(callback_handler))
        
        # Message handler for text messages (for card info and other text processing)
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        ))
        
        # Message handler for documents only (lowest priority)
        application.add_handler(MessageHandler(
            filters.Document.ALL & ~filters.COMMAND,
            message_handler
        ))
        
        # Register error handler
        application.add_error_handler(error_handler)
        logger.info("All handlers registered successfully")

        # Run the bot until the user presses Ctrl-C
        logger.info("Starting bot polling...")
        
        # Initialize the application
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logger.info("Bot is now running. Press Ctrl+C to stop.")
        
        # Keep the bot running until interrupted
        import signal
        import asyncio
        
        stop_event = asyncio.Event()
        
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal")
            stop_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            await stop_event.wait()
        finally:
            logger.info("Shutting down bot...")
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        
    except Exception as e:
        logger.error(f"Critical error in async_main: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise


def main() -> None:
    """Start the bot."""
    import asyncio
    asyncio.run(async_main())


if __name__ == "__main__":
    main()