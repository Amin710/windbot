#!/usr/bin/env python3
"""
Configuration file for Wind Reseller Bot Web Admin Panel
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration class"""
    
    # Flask Configuration
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-change-in-production')
    DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    # Database Configuration
    DB_URI = os.getenv('DB_URI')
    
    # Security Configuration
    FERNET_KEY = os.getenv('FERNET_KEY')
    
    # Admin Panel Configuration
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
    
    # Bot Configuration (for integration)
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    RECEIPT_CHANNEL_ID = os.getenv('RECEIPT_CHANNEL_ID')
    LOG_SELL_CHID = os.getenv('LOG_SELL_CHID')
    
    # Application Configuration
    APP_NAME = 'Wind Reseller Bot Admin Panel'
    APP_VERSION = '1.0.0'
    
    # Pagination
    ITEMS_PER_PAGE = 20
    
    # Upload Configuration
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    @staticmethod
    def validate_config():
        """Validate essential configuration"""
        required_vars = ['DB_URI', 'FERNET_KEY']
        missing_vars = []
        
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return True

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    
    # Additional security settings for production
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True
    
    # Use in-memory database for testing
    WTF_CSRF_ENABLED = False

# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
} 