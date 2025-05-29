-- Migration: Add 2FA retry limits to orders table
-- Date: 2024-12-29
-- Description: Add columns to track 2FA usage count and last usage time

ALTER TABLE orders
  ADD COLUMN twofa_count SMALLINT DEFAULT 0,
  ADD COLUMN twofa_last  TIMESTAMPTZ;

-- Create index for better performance on twofa queries
CREATE INDEX IF NOT EXISTS idx_orders_twofa_last ON orders(twofa_last);

-- Update existing orders to have default values
UPDATE orders SET twofa_count = 0 WHERE twofa_count IS NULL; 