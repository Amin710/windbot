-- Add referral system support
ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer BIGINT NULL REFERENCES users(id);
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS referral_earned NUMERIC(12,2) DEFAULT 0;
