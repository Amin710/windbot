# 🚀 راهنمای نصب سریع پنل وب مدیریت

## 📋 پیش‌نیازها

```bash
✅ Python 3.8 یا بالاتر
✅ PostgreSQL (همان دیتابیس ربات)
✅ Git
```

## ⚡ نصب سریع

### 1️⃣ کلون پروژه (اگر کرده‌اید، این مرحله را رد کنید)

```bash
git clone <repository-url>
cd windbot
```

### 2️⃣ نصب وابستگی‌های پنل وب

```bash
cd web_admin
pip install -r requirements.txt
```

### 3️⃣ تنظیم فایل محیطی

فایل `.env` در پوشه اصلی پروژه (windbot) باید شامل موارد زیر باشد:

```env
# Database (همان تنظیمات ربات)
DB_URI=postgresql://winduser:yourpassword@localhost:5432/wind_reseller
FERNET_KEY=your-fernet-key-from-bot

# Flask Configuration
FLASK_SECRET_KEY=your-very-secret-flask-key
FLASK_DEBUG=True
FLASK_HOST=0.0.0.0
FLASK_PORT=5000

# Admin Panel (اختیاری)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123

# Bot Token (برای ادغام آینده)
BOT_TOKEN=your-bot-token
```

### 4️⃣ اجرای پنل

```bash
# روش 1: استفاده از runner script
python run.py

# روش 2: اجرای مستقیم
python app.py
```

## 🌐 دسترسی به پنل

بعد از اجرا، پنل در آدرس زیر در دسترس است:

```
http://localhost:5000
```

**اطلاعات ورود پیش‌فرض:**
- نام کاربری: `admin`
- رمز عبور: `admin123`

## ⚠️ مهم

1. **حتماً رمز عبور پیش‌فرض را تغییر دهید!**
2. برای استفاده در production از HTTPS استفاده کنید
3. `FLASK_DEBUG=False` را در production تنظیم کنید

## 🔧 رفع مشکلات رایج

### مشکل اتصال به دیتابیس
```bash
# بررسی کنید که PostgreSQL در حال اجراست
sudo systemctl status postgresql

# بررسی کنید که دیتابیس و کاربر وجود دارد
psql -U winduser -d wind_reseller -h localhost
```

### مشکل وابستگی‌ها
```bash
# نصب مجدد وابستگی‌ها
pip install --upgrade -r requirements.txt

# در صورت مشکل با psycopg2
sudo apt-get install libpq-dev python3-dev
```

### مشکل دسترسی
```bash
# اطمینان از باز بودن پورت
sudo ufw allow 5000

# یا استفاده از پورت دیگر
FLASK_PORT=8080 python run.py
```

## 🎯 ویژگی‌های فعال

✅ **داشبورد:** آمار کلی سیستم  
✅ **مدیریت اکانت‌ها:** افزودن، مشاهده، ویرایش  
✅ **مدیریت سفارشات:** فیلتر، تایید، رد  
✅ **مدیریت کاربران:** لیست و اطلاعات  
✅ **مدیریت کارت‌ها:** افزودن، ویرایش، حذف  
✅ **امنیت:** احراز هویت و رمزگذاری  

## 🔄 ویژگی‌های در حال توسعه

🚧 ارسال پیام مستقیم به کاربران  
🚧 گزارش‌گیری پیشرفته  
🚧 سیستم نوتیفیکیشن  
🚧 تنظیمات ربات از پنل  

## 📞 پشتیبانی

در صورت بروز مشکل:

1. ابتدا لاگ‌های سیستم را بررسی کنید
2. مطمئن شوید تمام متغیرهای محیطی تنظیم شده‌اند
3. بررسی کنید که دیتابیس و ربات اصلی کار می‌کنند

---

**🎉 پنل آماده استفاده است! لذت ببرید!** 