#!/usr/bin/env python3
"""
Manual script to create the cards table
Run this script if the migration doesn't run automatically
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    import db
    
    def create_cards_table():
        """Create the cards table manually."""
        print("Creating cards table...")
        
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    # Create cards table
                    cur.execute("""
                    CREATE TABLE IF NOT EXISTS cards (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        card_number TEXT NOT NULL,
                        active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    """)
                    
                    # Create index
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
                    print("✅ Cards table created successfully!")
                    
                    # Check if cards exist
                    cur.execute("SELECT COUNT(*) FROM cards")
                    count = cur.fetchone()[0]
                    print(f"📊 Cards in database: {count}")
                    
        except Exception as e:
            print(f"❌ Error creating cards table: {e}")
            return False
            
        return True
    
    if __name__ == "__main__":
        success = create_cards_table()
        sys.exit(0 if success else 1)
        
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure this script is run in the correct environment with all dependencies installed.")
    sys.exit(1) 