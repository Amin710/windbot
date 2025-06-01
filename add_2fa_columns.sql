-- Add 2FA retry limit columns to orders table
ALTER TABLE orders 
ADD COLUMN IF NOT EXISTS twofa_count SMALLINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS twofa_last TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS twofa_disabled BOOLEAN DEFAULT FALSE;

-- Create index for better performance
CREATE INDEX IF NOT EXISTS idx_orders_twofa_last ON orders(twofa_last);

-- Update existing orders to have default values
UPDATE orders SET twofa_count = 0 WHERE twofa_count IS NULL;
UPDATE orders SET twofa_disabled = FALSE WHERE twofa_disabled IS NULL; 