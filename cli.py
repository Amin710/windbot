#!/usr/bin/env python
"""
Wind Reseller CLI Admin Tool

A command-line tool for administrative tasks on the Wind Reseller system.
Provides commands for:
- Adding seats (service accounts) with encrypted credentials
- Promoting users to admin status

Usage:
    python cli.py add_seat <email> <password> <secret> [--slots=15]
    python cli.py make_admin <telegram_id>
"""
import argparse
import logging
import os
import sys
from typing import Optional

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Import our modules
import db
from bot import encrypt

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cli.log"),
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


def setup_argparse():
    """Set up argument parser for CLI commands."""
    parser = argparse.ArgumentParser(description="Wind Reseller Admin CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Add seat command
    add_seat_parser = subparsers.add_parser("add_seat", help="Add a new service account seat")
    add_seat_parser.add_argument("email", help="Email address for the seat")
    add_seat_parser.add_argument("password", help="Password for the seat")
    add_seat_parser.add_argument("secret", help="TOTP secret for the seat")
    add_seat_parser.add_argument("--slots", type=int, default=15, help="Maximum number of slots (default: 15)")
    
    # Make admin command
    make_admin_parser = subparsers.add_parser("make_admin", help="Make a user an admin")
    make_admin_parser.add_argument("tg_id", type=int, help="Telegram user ID to promote to admin")
    
    return parser


def add_seat(email: str, password: str, secret: str, slots: int = 15) -> bool:
    """
    Add a new seat with encrypted credentials.
    
    Args:
        email: Email address for the account
        password: Password for the account
        secret: TOTP secret for 2FA
        slots: Maximum number of slots this seat can have
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Initialize database if needed
        db.init_db()
        
        # Encrypt credentials
        pass_enc = encrypt(password)
        secret_enc = encrypt(secret)
        
        # Insert seat into database
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO seats (email, pass_enc, secret_enc, max_slots) VALUES (%s, %s, %s, %s) RETURNING id",
                    (email, pass_enc, secret_enc, slots)
                )
                seat_id = cur.fetchone()[0]
                conn.commit()
        
        logger.info(f"Added new seat ID {seat_id} with email {email} and {slots} slots")
        return True
    except Exception as e:
        logger.error(f"Error adding seat: {e}")
        return False


def make_admin(tg_id: int) -> bool:
    """
    Make a user an admin by their Telegram ID.
    
    Args:
        tg_id: Telegram user ID
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Initialize database if needed
        db.init_db()
        
        # Check if user exists
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE tg_id = %s", (tg_id,))
                result = cur.fetchone()
                
                if not result:
                    logger.error(f"User with Telegram ID {tg_id} not found")
                    return False
                
                # Update user to admin
                cur.execute(
                    "UPDATE users SET is_admin = TRUE WHERE tg_id = %s",
                    (tg_id,)
                )
                conn.commit()
        
        logger.info(f"User with Telegram ID {tg_id} promoted to admin")
        return True
    except Exception as e:
        logger.error(f"Error making user admin: {e}")
        return False


def main():
    """Main entry point for the CLI."""
    parser = setup_argparse()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize environment
    FERNET_KEY = os.getenv("FERNET_KEY")
    if not FERNET_KEY:
        logger.error("FERNET_KEY environment variable not set")
        sys.exit(1)
    
    # Execute command
    if args.command == "add_seat":
        success = add_seat(args.email, args.password, args.secret, args.slots)
        if success:
            print(f"✅ Successfully added seat with email {args.email}")
        else:
            print("❌ Failed to add seat")
            sys.exit(1)
    
    elif args.command == "make_admin":
        success = make_admin(args.tg_id)
        if success:
            print(f"✅ Successfully promoted user with Telegram ID {args.tg_id} to admin")
        else:
            print("❌ Failed to make user admin")
            sys.exit(1)


if __name__ == "__main__":
    main()
