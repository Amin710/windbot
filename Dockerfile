FROM python:3.12-slim

WORKDIR /app

# Install PostgreSQL client for backup functionality
RUN apt-get update && apt-get install -y postgresql-client && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Set the entrypoint to run the bot
CMD ["python", "bot.py"]
