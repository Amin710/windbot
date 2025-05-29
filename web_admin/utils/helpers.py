#!/usr/bin/env python3
"""
Utility functions for Web Admin Panel
"""

import re
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# Add parent directory to path to import bot modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from db import get_conn

def format_currency(amount: int) -> str:
    """Format amount as Persian currency"""
    return f"{amount:,} تومان"

def format_datetime(dt: datetime, include_time: bool = True) -> str:
    """Format datetime in Persian format"""
    if not dt:
        return "نامشخص"
    
    if include_time:
        return dt.strftime('%Y/%m/%d %H:%M')
    else:
        return dt.strftime('%Y/%m/%d')

def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_card_number(card_number: str) -> bool:
    """Validate Iranian bank card number (16 digits)"""
    # Remove non-digit characters
    clean_number = re.sub(r'\D', '', card_number)
    
    # Check if it's exactly 16 digits
    if len(clean_number) != 16:
        return False
    
    # Basic validation for Iranian bank cards
    # Most Iranian cards start with specific prefixes
    valid_prefixes = ['627760', '627412', '622106', '627648', '627593', '627381', '627053']
    
    for prefix in valid_prefixes:
        if clean_number.startswith(prefix):
            return True
    
    # If no specific prefix matches, still accept if it's 16 digits
    return True

def format_card_number(card_number: str) -> str:
    """Format card number with dashes"""
    clean_number = re.sub(r'\D', '', card_number)
    return f"{clean_number[:4]}-{clean_number[4:8]}-{clean_number[8:12]}-{clean_number[12:16]}"

def get_status_badge_class(status: str) -> str:
    """Get Bootstrap badge class for status"""
    status_classes = {
        'approved': 'bg-success',
        'pending': 'bg-warning',
        'receipt': 'bg-info',
        'rejected': 'bg-danger',
        'active': 'bg-success',
        'inactive': 'bg-danger',
        'blocked': 'bg-secondary'
    }
    return status_classes.get(status, 'bg-secondary')

def get_status_text(status: str) -> str:
    """Get Persian text for status"""
    status_texts = {
        'approved': 'تایید شده',
        'pending': 'در انتظار',
        'receipt': 'فیش ارسال شده',
        'rejected': 'رد شده',
        'active': 'فعال',
        'inactive': 'غیرفعال',
        'blocked': 'مسدود شده'
    }
    return status_texts.get(status, status)

def get_dashboard_stats() -> Dict[str, Any]:
    """Get comprehensive dashboard statistics"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                stats = {}
                
                # Users statistics
                cur.execute("SELECT COUNT(*) FROM users")
                stats['total_users'] = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM users WHERE joined_at >= CURRENT_DATE - INTERVAL '30 days'")
                stats['new_users_month'] = cur.fetchone()[0]
                
                # Orders statistics
                cur.execute("SELECT COUNT(*) FROM orders")
                stats['total_orders'] = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'approved'")
                stats['approved_orders'] = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
                stats['pending_orders'] = cur.fetchone()[0]
                
                cur.execute("SELECT SUM(amount) FROM orders WHERE status = 'approved'")
                result = cur.fetchone()[0]
                stats['total_revenue'] = result or 0
                
                # Seats statistics
                cur.execute("SELECT COUNT(*) FROM seats WHERE status = 'active'")
                stats['active_seats'] = cur.fetchone()[0]
                
                cur.execute("SELECT SUM(max_slots), SUM(sold) FROM seats WHERE status = 'active'")
                result = cur.fetchone()
                stats['total_slots'] = result[0] or 0
                stats['sold_slots'] = result[1] or 0
                stats['available_slots'] = stats['total_slots'] - stats['sold_slots']
                
                # Calculate success rate
                if stats['total_orders'] > 0:
                    stats['success_rate'] = round((stats['approved_orders'] / stats['total_orders']) * 100, 1)
                else:
                    stats['success_rate'] = 0
                
                return stats
                
    except Exception as e:
        print(f"Error getting dashboard stats: {e}")
        return {}

def get_recent_activity(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent system activity"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Get recent orders
                cur.execute("""
                    SELECT 'order' as type, o.id, o.amount, o.status, o.created_at, 
                           u.first_name, u.username
                    FROM orders o 
                    JOIN users u ON o.user_id = u.id 
                    ORDER BY o.created_at DESC 
                    LIMIT %s
                """, (limit,))
                
                activities = []
                for row in cur.fetchall():
                    activities.append({
                        'type': row[0],
                        'id': row[1],
                        'amount': row[2],
                        'status': row[3],
                        'created_at': row[4],
                        'user_name': row[5],
                        'username': row[6]
                    })
                
                return activities
                
    except Exception as e:
        print(f"Error getting recent activity: {e}")
        return []

def check_seat_availability(required_slots: int = 1) -> Optional[Dict[str, Any]]:
    """Check if there are available seats for new users"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, email, (max_slots - sold) as available
                    FROM seats 
                    WHERE status = 'active' AND (max_slots - sold) >= %s
                    ORDER BY available DESC
                    LIMIT 1
                """, (required_slots,))
                
                result = cur.fetchone()
                if result:
                    return {
                        'seat_id': result[0],
                        'email': result[1],
                        'available_slots': result[2]
                    }
                
                return None
                
    except Exception as e:
        print(f"Error checking seat availability: {e}")
        return None

def sanitize_input(text: str) -> str:
    """Sanitize user input to prevent XSS"""
    if not text:
        return ""
    
    # Basic HTML escaping
    text = str(text)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&#x27;')
    
    return text.strip()

def generate_report_data(start_date: datetime, end_date: datetime) -> Dict[str, Any]:
    """Generate report data for given date range"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                report = {
                    'period': {
                        'start': start_date,
                        'end': end_date
                    }
                }
                
                # Orders in period
                cur.execute("""
                    SELECT COUNT(*), SUM(amount), status
                    FROM orders 
                    WHERE created_at BETWEEN %s AND %s
                    GROUP BY status
                """, (start_date, end_date))
                
                orders_by_status = {}
                total_orders = 0
                total_amount = 0
                
                for row in cur.fetchall():
                    count, amount, status = row
                    orders_by_status[status] = {
                        'count': count,
                        'amount': amount or 0
                    }
                    total_orders += count
                    if amount:
                        total_amount += amount
                
                report['orders'] = {
                    'total': total_orders,
                    'total_amount': total_amount,
                    'by_status': orders_by_status
                }
                
                # New users in period
                cur.execute("""
                    SELECT COUNT(*) 
                    FROM users 
                    WHERE joined_at BETWEEN %s AND %s
                """, (start_date, end_date))
                
                report['new_users'] = cur.fetchone()[0]
                
                return report
                
    except Exception as e:
        print(f"Error generating report: {e}")
        return {} 