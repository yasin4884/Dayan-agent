import requests
from bs4 import BeautifulSoup
import logging
import re
import urllib.parse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebScraper:
    def __init__(self):
        # دامنه‌ی سایت دانشگاه شما
        self.target_domain = "dfm.tvu.ac.ir"
        
    def preprocess_text(self, text: str) -> str:
        """پاکسازی متن از فاصله‌ها و خطوط خالی اضافی"""
        if not text:
            return ""
        # جایگزینی فاصله‌های متوالی با یک فاصله
        text = re.sub(r'[ \t]+', ' ', text)
        # جایگزینی اینترهای متوالی با نهایتا یک اینتر
        text = re.sub(r'\n{2,}', '\n', text)
        return text.strip()

    def search(self, query: str) -> str:
        try:
            # ساخت کوئری مخصوص موتور جستجو (محدود کردن سرچ به سایت دانشگاه)
            search_query = f"site:{self.target_domain} {query}"
            
            # انکد کردن کوئری برای قرارگیری در URL (تبدیل فاصله‌ها و حروف فارسی به فرمت استاندارد لینک)
            encoded_query = urllib.parse.quote(search_query)
            
            # آدرس جستجوی ذره‌بین
            url = f"https://zarebin.ir/search?q={encoded_query}"
            
            # لاگ زدن کلمه سرچ شده و آدرس
            logger.info(f"🔍 در حال جستجو کوئری: '{search_query}'")
            logger.info(f"🌐 لینک موتور جستجو: {url}")
            
            # هدر مرورگر برای جلوگیری از بلاک شدن توسط فایروال ذره‌بین
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            # ارسال درخواست
            response = requests.get(url, headers=headers, timeout=10)
            logger.info(f"📡 وضعیت پاسخ سرور ذره‌بین: {response.status_code}")
            
            if response.status_code != 200:
                logger.warning("⚠️ دریافت پاسخ ناموفق از موتور جستجو")
                return ""

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # استخراج تگ‌های متنی (معمولاً نتایج جستجو در این تگ‌ها قرار دارند)
            text_elements = soup.find_all(['p', 'span', 'h3', 'div'])
            logger.info(f"📄 تعداد المان‌های بررسی شده: {len(text_elements)}")
            
            # چسباندن تمام متن‌ها به هم
            raw_text = ' '.join([el.get_text().strip() for el in text_elements if el.get_text().strip()])
            
            # اعمال تابع پیش‌پردازش برای تمیز کردن متن (حذف فاصله‌های اضافی)
            clean_text = self.preprocess_text(raw_text)
            
            if clean_text:
                logger.info(f"✅ نتیجه خام استخراج شد: {len(clean_text)} کاراکتر")
                # برگرداندن 1500 کاراکتر اول برای جلوگیری از سرریز شدن حافظه مدل
                return clean_text[:1500]
            
            logger.warning("⚠️ جستجو انجام شد اما متنی در نتایج یافت نشد.")
            return ""
            
        except requests.exceptions.Timeout:
            logger.error("❌ خطا: زمان درخواست به ذره‌بین تمام شد (Timeout)")
            return ""
        except Exception as e:
            logger.error(f"❌ خطا در جستجوی وب: {str(e)}")
            return ""
