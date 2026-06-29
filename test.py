# test_db.py
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    DATABASE_URL = os.getenv("DATABASE_URL")
    print(f"📌 DATABASE_URL: {DATABASE_URL}")
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ اتصال به PostgreSQL موفق")
        
        # بررسی جدول users
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        print("📋 جدول‌های موجود:")
        for t in tables:
            print(f"   - {t['table_name']}")
        
        # اگر جدول users وجود نداره، بساز
        if 'users' not in [t['table_name'] for t in tables]:
            print("⚠️ جدول users وجود ندارد - در حال ساخت...")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    national_code VARCHAR(10) NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NOT NULL,
                    city VARCHAR(50) NOT NULL,
                    role VARCHAR(20) DEFAULT 'student'
                )
            """)
            print("✅ جدول users ساخته شد")
        
        # درج یک کاربر تست
        import hashlib
        import secrets
        salt = secrets.token_hex(16)
        password_hash = f"{salt}${hashlib.sha256(f'{salt}1234'.encode()).hexdigest()}"
        
        try:
            await conn.execute("""
                INSERT INTO users (national_code, password_hash, first_name, last_name, city, role)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (national_code) DO NOTHING
            """, "1234567890", password_hash, "کاربر", "تست", "tehran", "student")
            print("✅ کاربر تست اضافه شد (کد ملی: 1234567890 / رمز: 1234)")
        except Exception as e:
            print(f"⚠️ خطا در اضافه کردن کاربر: {e}")
        
        # خواندن کاربران
        users = await conn.fetch("SELECT id, national_code, first_name, last_name, role FROM users")
        print("\n👥 لیست کاربران:")
        for u in users:
            print(f"   ID: {u['id']} | کد ملی: {u['national_code']} | نام: {u['first_name']} {u['last_name']} | نقش: {u['role']}")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ خطا: {e}")

if __name__ == "__main__":
    asyncio.run(test())