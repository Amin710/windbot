-- Wind Reseller Database Schema

-- Users table for storing Telegram users
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT UNIQUE NOT NULL,
    first_name TEXT,
    username TEXT,
    is_admin BOOLEAN DEFAULT FALSE,
    joined_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seats table for storing encrypted credentials
CREATE TABLE seats (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    pass_enc BYTEA NOT NULL,
    secret_enc BYTEA NOT NULL,
    max_slots INTEGER DEFAULT 15,
    sold INTEGER DEFAULT 0,
    status VARCHAR(10) DEFAULT 'active'
);

-- Order status enum
CREATE TYPE order_status AS ENUM ('pending', 'receipt', 'approved', 'rejected');

-- Orders table
CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    amount NUMERIC(10,2) NOT NULL,
    currency VARCHAR(4) DEFAULT 'IRR',
    status order_status DEFAULT 'pending',
    seat_id INTEGER REFERENCES seats(id),
    utm_keyword TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ
);

-- Receipts table with 1:1 relationship to orders
CREATE TABLE receipts (
    order_id BIGINT PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    tg_file_id TEXT NOT NULL,
    orig_chat_id BIGINT NOT NULL,
    channel_msg_id BIGINT
);

-- Wallets table with 1:1 relationship to users
CREATE TABLE wallets (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    balance NUMERIC(12,2) DEFAULT 0,
    free_credit NUMERIC(12,2) DEFAULT 0
);

-- Settings key-value table
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    val TEXT
);

-- Order log for audit trail
CREATE TABLE order_log (
    id SERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    ts TIMESTAMPTZ DEFAULT NOW()
);

-- UTM tracking statistics
CREATE TABLE utm_stats (
    keyword TEXT PRIMARY KEY,
    starts INTEGER DEFAULT 0,
    buys INTEGER DEFAULT 0,
    amount NUMERIC(12,2) DEFAULT 0
);

-- Initial settings
INSERT INTO settings (key, val) VALUES 
    ('min_order_amount', '100000'),
    ('support_username', 'support'),
    ('version', '1.0.0');

-- Create indexes for better performance
CREATE INDEX idx_users_tg_id ON users(tg_id);
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);
CREATE INDEX idx_order_log_order_id ON order_log(order_id);
