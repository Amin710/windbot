#!/usr/bin/env python3
"""
Setup script for configuring force join settings in the Windbot
"""

import db
from dotenv import load_dotenv

def setup_force_join():
    """Setup force join functionality"""
    print("🔧 نصب و راه‌اندازی قفل کانال")
    print("-" * 40)
    
    # Initialize database
    db.init_db()
    
    # Get user input for enabling force join
    enable = input("آیا می‌خواهید قفل کانال را فعال کنید؟ (y/n): ").lower().strip()
    if enable in ['y', 'yes', 'بله', 'آره']:
        enable_force_join = 'true'
        
        print("\n📝 وارد کردن کانال‌های اجباری:")
        print("می‌توانید چندین کانال وارد کنید. برای هر کانال یکی از فرمت‌های زیر را استفاده کنید:")
        print("- @channel_username")
        print("- -1001234567890 (برای کانال‌های خصوصی)")
        print("- channel_username (بدون @)")
        print("\nبرای پایان دادن به ورودی، خط خالی وارد کنید:")
        
        channels = []
        while True:
            channel = input(f"کانال #{len(channels) + 1}: ").strip()
            if not channel:
                break
            channels.append(channel)
        
        channels_str = ','.join(channels)
        
    else:
        enable_force_join = 'false'
        channels_str = ''
    
    try:
        # Update database settings
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Update or insert force_join_enabled
                cur.execute("""
                    INSERT INTO settings (key, val) VALUES ('force_join_enabled', %s)
                    ON CONFLICT (key) DO UPDATE SET val = EXCLUDED.val
                """, (enable_force_join,))
                
                # Update or insert required_channels
                cur.execute("""
                    INSERT INTO settings (key, val) VALUES ('required_channels', %s)
                    ON CONFLICT (key) DO UPDATE SET val = EXCLUDED.val
                """, (channels_str,))
                
                conn.commit()
        
        print("\n✅ تنظیمات با موفقیت ذخیره شد!")
        if enable_force_join == 'true':
            print(f"🔒 قفل کانال فعال شد برای {len(channels)} کانال:")
            for i, channel in enumerate(channels, 1):
                print(f"   {i}. {channel}")
        else:
            print("🔓 قفل کانال غیرفعال است")
            
        print("\n⚠️  نکته مهم:")
        print("- مطمئن شوید که ربات در تمام کانال‌ها ادمین است")
        print("- ربات باید دسترسی 'مشاهده اعضا' داشته باشد")
        print("- پس از تغییر تنظیمات، ربات را restart کنید")
        
    except Exception as e:
        print(f"\n❌ خطا در ذخیره تنظیمات: {e}")
        return False
    
    return True

def show_current_settings():
    """Show current force join settings"""
    print("📋 تنظیمات فعلی قفل کانال")
    print("-" * 30)
    
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                # Get force join enabled status
                cur.execute("SELECT val FROM settings WHERE key = 'force_join_enabled'")
                result = cur.fetchone()
                enabled = result[0] if result else 'false'
                
                # Get required channels
                cur.execute("SELECT val FROM settings WHERE key = 'required_channels'")
                result = cur.fetchone()
                channels_str = result[0] if result and result[0] else ''
                
                print(f"وضعیت: {'✅ فعال' if enabled == 'true' else '❌ غیرفعال'}")
                
                if channels_str:
                    channels = [ch.strip() for ch in channels_str.split(',') if ch.strip()]
                    print(f"تعداد کانال‌ها: {len(channels)}")
                    for i, channel in enumerate(channels, 1):
                        print(f"  {i}. {channel}")
                else:
                    print("کانال‌ها: هیچ‌کدام")
                    
    except Exception as e:
        print(f"❌ خطا در خواندن تنظیمات: {e}")

def main():
    load_dotenv()
    
    print("🤖 تنظیمات قفل کانال ربات ویندسکرایب")
    print("=" * 50)
    
    while True:
        print("\nانتخاب کنید:")
        print("1. مشاهده تنظیمات فعلی")
        print("2. تنظیم قفل کانال")
        print("3. خروج")
        
        choice = input("\nانتخاب شما: ").strip()
        
        if choice == '1':
            show_current_settings()
        elif choice == '2':
            setup_force_join()
        elif choice == '3':
            print("خروج...")
            break
        else:
            print("❌ انتخاب نامعتبر!")

if __name__ == "__main__":
    main() 