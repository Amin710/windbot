#!/usr/bin/env python3
"""
Wind Reseller Bot - Web Admin Panel Runner
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_environment():
    """Check if all required environment variables are set"""
    required_vars = [
        'DB_URI',
        'FERNET_KEY'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print("❌ خطا: متغیرهای محیطی زیر تنظیم نشده‌اند:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nلطفاً فایل .env را بررسی کنید.")
        return False
    
    return True

def main():
    """Main function to run the web admin panel"""
    
    print("🚀 راه‌اندازی پنل وب مدیریت ویندسکرایب...")
    
    # Check environment variables
    if not check_environment():
        sys.exit(1)
    
    try:
        # Import and run the Flask app
        from app import app
        
        # Get configuration from environment
        debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
        host = os.getenv('FLASK_HOST', '0.0.0.0')
        port = int(os.getenv('FLASK_PORT', '5000'))
        
        print(f"🌐 پنل در حال اجرا روی: http://{host}:{port}")
        print("📝 اطلاعات ورود پیش‌فرض:")
        print("   نام کاربری: admin")
        print("   رمز عبور: admin123")
        print("\n⚠️  توجه: حتماً رمز عبور پیش‌فرض را تغییر دهید!")
        print("━" * 50)
        
        # Run the Flask app
        app.run(
            debug=debug,
            host=host,
            port=port,
            threaded=True
        )
        
    except ImportError as e:
        print(f"❌ خطا در وارد کردن ماژول‌ها: {e}")
        print("لطفاً مطمئن شوید که تمام وابستگی‌ها نصب شده‌اند:")
        print("pip install -r requirements.txt")
        sys.exit(1)
        
    except Exception as e:
        print(f"❌ خطا در راه‌اندازی پنل: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main() 