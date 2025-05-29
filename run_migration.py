#!/usr/bin/env python3
"""
Standalone migration script to add 2FA retry limit columns
Run this on the server with: python run_migration.py
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def main():
    """Run the 2FA migration."""
    db_uri = os.getenv('DB_URI')
    if not db_uri:
        print("❌ DB_URI environment variable not found")
        return False
    
    try:
        # Connect to database
        conn = psycopg2.connect(db_uri)
        cur = conn.cursor()
        
        print("🔄 Adding 2FA retry limit columns...")
        
        # Add twofa_count and twofa_last columns
        cur.execute("""
        ALTER TABLE orders 
        ADD COLUMN IF NOT EXISTS twofa_count SMALLINT DEFAULT 0,
        ADD COLUMN IF NOT EXISTS twofa_last TIMESTAMPTZ;
        """)
        
        # Create index for better performance
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_orders_twofa_last ON orders(twofa_last);
        """)
        
        # Update existing orders to have default values
        cur.execute("""
        UPDATE orders SET twofa_count = 0 WHERE twofa_count IS NULL;
        """)
        
        # Commit changes
        conn.commit()
        print("✅ Migration completed successfully!")
        
        # Verify columns exist
        cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'orders' AND column_name IN ('twofa_count', 'twofa_last');
        """)
        columns = cur.fetchall()
        print(f"📊 Verified columns: {[col[0] for col in columns]}")
        
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    main() 