-- Add cards table for managing multiple payment cards
CREATE TABLE IF NOT EXISTS cards (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,          -- e.g. «کارت سامان»
  number TEXT NOT NULL,         -- 16-digit
  active BOOLEAN DEFAULT TRUE
);

-- Import existing card from settings (if any)
INSERT INTO cards (title, number)
SELECT 'کارت بانکی', value FROM settings WHERE key = 'card_number'
ON CONFLICT DO NOTHING;
