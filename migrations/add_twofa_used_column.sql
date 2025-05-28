-- Add twofa_used column to orders table
ALTER TABLE orders ADD COLUMN IF NOT EXISTS twofa_used BOOLEAN DEFAULT FALSE;
