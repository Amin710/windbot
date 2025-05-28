"""
Database module for Wind Reseller
- Connection pool management
- Database initialization
- Helper functions for common operations
"""
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logger setup
logger = logging.getLogger(__name__)

# Get database URI from environment variables
DB_URI = os.getenv("DB_URI")
if not DB_URI:
    logger.error("DB_URI environment variable not set")
    raise ValueError("DB_URI environment variable not set")

# Create a global connection pool (min: 1, max: 10 connections)
try:
    connection_pool = pool.SimpleConnectionPool(1, 10, DB_URI)
    logger.info("Database connection pool initialized")
except psycopg2.Error as e:
    logger.error(f"Error initializing database connection pool: {e}")
    raise


@contextmanager
def get_conn():
    """
    Context manager for getting a connection from the pool.
    Automatically returns the connection to the pool when done.
    
    Usage:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users")
                rows = cur.fetchall()
    """
    conn = None
    try:
        conn = connection_pool.getconn()
        yield conn
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            connection_pool.putconn(conn)


def get_setting(key, default=None):
    """
    Get a setting value from the settings table.
    
    Args:
        key: The setting key
        default: Default value to return if setting not found
        
    Returns:
        The setting value or default if not found
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT val FROM settings WHERE key = %s", (key,))
                result = cur.fetchone()
                return result[0] if result else default
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
        return default


def set_setting(key, val):
    """
    Set a setting value in the settings table.
    Updates if key exists, inserts if it doesn't.
    
    Args:
        key: The setting key
        val: The setting value
        
    Returns:
        True if successful, False otherwise
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO settings (key, val) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET val = %s",
                    (key, val, val)
                )
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"Error setting {key}={val}: {e}")
        return False


def inc_utm(keyword, field, inc_amount=1):
    """
    Increment a field in the utm_stats table for a specific keyword.
    Creates the row if it doesn't exist.
    
    Args:
        keyword: The UTM keyword
        field: The field to increment ('starts', 'buys', 'amount')
        inc_amount: Amount to increment by (default: 1)
        
    Returns:
        True if successful, False otherwise
    """
    if not keyword:
        return True
        
    valid_fields = {'starts', 'buys', 'amount'}
    if field not in valid_fields:
        logger.error(f"Invalid UTM field: {field}")
        return False
    
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Insert row if it doesn't exist
                cur.execute(
                    "INSERT INTO utm_stats (keyword) VALUES (%s) ON CONFLICT (keyword) DO NOTHING",
                    (keyword,)
                )
                
                # Update the field
                cur.execute(
                    f"UPDATE utm_stats SET {field} = {field} + %s WHERE keyword = %s",
                    (inc_amount, keyword)
                )
                conn.commit()
                return True
    except Exception as e:
        logger.error(f"Error incrementing UTM stat {keyword}.{field}: {e}")
        return False


def table_exists(table_name):
    """Check if a table exists in the database."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = %s)",
                    (table_name,)
                )
                return cur.fetchone()[0]
    except Exception as e:
        logger.error(f"Error checking if table {table_name} exists: {e}")
        return False


def apply_migrations():
    """
    Apply any pending database migrations.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Add twofa_used column to orders table if it doesn't exist
                cur.execute("""
                ALTER TABLE orders ADD COLUMN IF NOT EXISTS twofa_used BOOLEAN DEFAULT FALSE;
                """)
                
                # Add referral system columns
                cur.execute("""
                -- Add referral system support
                ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer BIGINT NULL REFERENCES users(id);
                ALTER TABLE wallets ADD COLUMN IF NOT EXISTS referral_earned NUMERIC(12,2) DEFAULT 0;
                """)
                
                # Create cards table for card management system
                cur.execute("""
                CREATE TABLE IF NOT EXISTS cards (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    card_number TEXT NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                """)
                
                # Create index for cards table
                cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cards_active ON cards(active);
                """)
                
                # Insert default card if table is empty
                cur.execute("""
                INSERT INTO cards (title, card_number, active) 
                SELECT 'کارت پیش‌فرض', '1234-5678-9012-3456', TRUE
                WHERE NOT EXISTS (SELECT 1 FROM cards LIMIT 1);
                """)
                
                conn.commit()
        logger.info("Database migrations applied successfully")
        return True
    except Exception as e:
        logger.error(f"Error applying migrations: {e}")
        return False


def init_db():
    """
    Initialize the database by applying schema.sql if tables don't exist.
    """
    # Check if at least one of our tables exists
    if table_exists('users'):
        logger.info("Database already initialized")
        # Apply any pending migrations
        apply_migrations()
        return True
    
    try:
        # Read schema.sql file
        schema_path = Path(__file__).parent / 'schema.sql'
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        # Execute schema.sql
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
                conn.commit()
        
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        return False


# Additional helper functions can be added here as needed
