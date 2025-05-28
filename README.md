# Wind Reseller

A Telegram bot for wind reselling services.

## Setup

### Requirements
- Python 3.8+
- PostgreSQL database

### Installation

#### Using Poetry (recommended)
```bash
# Install poetry if you don't have it
pip install poetry

# Install dependencies
poetry install

# Activate the virtual environment
poetry shell
```

#### Using pip and venv
```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows
venv\Scripts\activate
# On Unix or MacOS
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration
1. Copy `.env.example` to `.env`
2. Fill in the required environment variables:
   - `BOT_TOKEN`: Your Telegram bot token from BotFather
   - `DB_URI`: PostgreSQL database connection string
   - `FERNET_KEY`: Encryption key for sensitive data
   - `RECEIPT_CHANNEL_ID`: Telegram channel ID for receipts

### Running the bot
```bash
python bot.py
```

## Features
- [Add your features here]

## License
[Your chosen license]
