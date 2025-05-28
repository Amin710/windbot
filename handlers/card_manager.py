"""
Card Manager for Wind Reseller Bot
This module handles the card selection for payment process
"""
import logging
import random
from typing import Tuple, Optional

import db
from debug_logger import log_function_call

# Setup logging
logger = logging.getLogger(__name__)


@log_function_call
def get_random_payment_card() -> Tuple[Optional[str], Optional[str]]:
    """
    Get a random active card from database for payment.
    
    Returns:
        Tuple[str, str]: Title and number of the card, or (None, None) if no cards
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Try to get active cards from the cards table
                cur.execute("SELECT title, card_number FROM cards WHERE active = TRUE")
                cards = cur.fetchall()
                
                if not cards:
                    # Fallback: Get card from settings
                    cur.execute("SELECT value FROM settings WHERE key = 'card_number'")
                    result = cur.fetchone()
                    if result:
                        return "کارت بانکی", result[0]
                    else:
                        return None, None
                
                # Choose a random card
                card = random.choice(cards)
                return card[0], card[1]
                
    except Exception as e:
        logger.error(f"Error getting random card: {e}")
        
        # Try fallback if database query fails
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM settings WHERE key = 'card_number'")
                    result = cur.fetchone()
                    if result:
                        return "کارت بانکی", result[0]
        except Exception as fallback_error:
            logger.error(f"Error getting fallback card: {fallback_error}")
            
        return None, None


@log_function_call
def format_payment_message(title: str, number: str, amount: int) -> str:
    """
    Format a payment message with card details.
    
    Args:
        title: Card title (e.g. 'کارت سامان')
        number: Card number
        amount: Payment amount in tomans
        
    Returns:
        str: Formatted payment message
    """
    # Format amount with thousand separators
    formatted_amount = "{:,}".format(amount)
    
    return (
        f"💳 *{title}*\n"
        f"`{number}`\n\n"
        f"💰 مبلغ: *{formatted_amount} تومان*\n\n"
        f"⚠️ لطفا پس از واریز، رسید پرداخت را ارسال کنید."
    )
