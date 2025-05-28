-- Migration: Create cards table
-- Date: 2025-05-28
-- Description: Add cards table for card management system

CREATE TABLE IF NOT EXISTS cards (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    card_number TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_cards_active ON cards(active);

-- Insert a default card if none exists
INSERT INTO cards (title, card_number, active) 
SELECT 'کارت پیش‌فرض', '1234-5678-9012-3456', TRUE
WHERE NOT EXISTS (SELECT 1 FROM cards LIMIT 1); 