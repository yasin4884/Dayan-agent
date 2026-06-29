# config.py

import os
from pathlib import Path

# مسیر پایه پروژه (برای ذخیره داده و ...)
BASE_DIR = Path(__file__).parent


ADMIN_USERS = {
    "1234567890": "123"  # یه کد ملی ۱۰ رقمی معتبر
}
# مسیر ذخیره فایل embeddings (پیکلی)
EMBEDDINGS_PATH = BASE_DIR / "data" / "embeddings.pkl"

# اسم دستیار هوشمند
ASSISTANT_NAME = "دایان"

# کتابخانه PDFهای اصلی دانشجویی/دانشگاهی (نام و فایل)
KNOWLEDGE_PDFS = {
    "university_rules": {
        "filename": "قوانین.pdf",
        "name": "قوانین و مقررات دانشگاه"
    }
}

# فرم‌های قابل دانلود در پنل
DOWNLOADABLE_FORMS = {
    "internship": {
        "filename": "internship_form.pdf",
        "name": "فرم کارآموزی"
    }
}

# مسیر مدل محلی Ollama
LOCAL_MODEL_PATH = "qwen3:8b"
HOST = "http://localhost:11434"

# لینک وب‌سایت دانشگاه (برای اسکریپت و جستجو)
UNIVERSITY_URL = "https://znu.ac.ir"

# تنظیمات جستجو
TOP_K_RESULTS = 5
MIN_SIMILARITY_SCORE = 0.3
RERANK_TOP_K = 3
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# تنظیمات مدل زبان (LLM)
MODEL_MAX_INPUT = 1024
MODEL_MAX_OUTPUT = 300
MODEL_TEMPERATURE = 0.1

# --- تنظیمات JWT و احراز هویت ---
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "DAYAN_SECRET_KEY_CHANGE_ME")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # ۱ روز
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 30  # ۳۰ روز

# --- دیتابیس‌ها ---

# اتصال پایگاه داده اصلی (PostgreSQL)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:123@localhost:5432/dayan"
)

# مسیر دیتابیس SQLite برای کاربران و احراز هویت
AUTH_DB_PATH = BASE_DIR / "data" / "auth.db"

# مسیر دیتابیس SQLite برای کش و تاریخچه مکالمه (جایگزین Redis)
CACHE_DB_PATH = BASE_DIR / "data" / "cache.db"

# --- تنظیمات کش ---
CACHE_TTL_SECONDS = 30 * 24 * 3600  # ۳۰ روز
CACHE_CLEANUP_INTERVAL_HOURS = 24   # پاک‌سازی هر ۲۴ ساعت
CHAT_HISTORY_MAX_MESSAGES = 100     # حداکثر تعداد پیام در تاریخچه هر کاربر

# --- تنظیمات آپلود فایل ---
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB

# پسوندهای معتبر برای آپلود فایل
ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".docx", ".doc"}

# پوشه ذخیره فایل‌های آپلود شده
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))

# --- ادمین پیش‌فرض (برای اولین راه‌اندازی) ---
ADMIN_USERS = {
    "modir": "123"  # دقت: پسوردها در این مرحله به صورت متن است
}

# --- تنظیمات Redis (اختیاری - می‌تونه خاموش باشه) ---
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- فرم‌های JSON ---
FORMS_JSON_PATH = BASE_DIR / "forms_db.json"
FAQ_JSON_PATH = BASE_DIR / "faqs.json"

# --- تنظیمات شهرها و دانشگاه‌ها (برای ثبت‌نام) ---
AVAILABLE_CITIES = {
    "zanjan": "دانشگاه زنجان",
    "tehran": "دانشگاه تهران",
    "tabriz": "دانشگاه تبریز",
    "mashhad": "دانشگاه مشهد",
    "shiraz": "دانشگاه شیراز",
    "isfahan": "دانشگاه اصفهان",
}