# faq_manager.py
import os
import json
import logging
from typing import Optional, List, Dict, Tuple
from rapidfuzz import fuzz
from dotenv import load_dotenv

from config import FAQ_JSON_PATH

load_dotenv()
logger = logging.getLogger("dayan.faq")


class FAQManager:
    """
    مدیریت سوالات متداول (FAQ)
    - بارگذاری از فایل JSON
    - تشخیص سوال کاربر (فازی + کلمات کلیدی)
    - پاسخ مستقیم (بدون مدل)
    - تشخیص درخواست «توضیح بیشتر» با cache_manager
    - مدیریت کامل CRUD
    """

    def __init__(self, cache_manager, json_path: Optional[str] = None):
        """
        cache_manager: نمونه از CacheManager برای حافظه نشست
        json_path: مسیر فایل JSON
        """
        self.cache = cache_manager
        if json_path is None:
            json_path = str(FAQ_JSON_PATH.resolve())
        self.json_path = json_path
        self.faq_items: List[Dict] = []
        self._load_faqs()

    # ============================================================
    # بارگذاری و ذخیره
    # ============================================================

    def _load_faqs(self):
        """بارگذاری سوالات از فایل JSON"""
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.faq_items = data.get("faqs", [])
                logger.info(f"✅ {len(self.faq_items)} سوال متداول از {os.path.basename(self.json_path)} بارگذاری شد")
                return
            except Exception as e:
                logger.error(f"خطا در خواندن {self.json_path}: {e}")

        # ایجاد فایل با سوالات پیش‌فرض
        self._create_default_faqs()
        self._save_faqs()

    def _create_default_faqs(self):
        """ایجاد لیست پیش‌فرض"""
        self.faq_items = [
            {
                "id": 1,
                "question": "فرآیند ثبت نام چیست؟",
                "answer": "مراحل ثبت نام: ۱- تکمیل فرم پیش‌ثبت‌نام در پورتال ۲- ارائه مدارک به امور آموزشی ۳- پرداخت شهریه ۴- دریافت کارت دانشجویی.",
                "keywords": ["ثبت نام", "نام نویسی", "فرآیند ثبت نام"],
                "category": "آموزشی"
            },
            {
                "id": 2,
                "question": "مدارک لازم برای ثبت نام چیست؟",
                "answer": "مدارک: اصل و کپی شناسنامه، کارت ملی، عکس ۳×۴، مدرک تحصیلی قبلی، رسید پرداخت شهریه.",
                "keywords": ["مدارک", "اسناد", "مدارک مورد نیاز"],
                "category": "آموزشی"
            }
        ]

    def _save_faqs(self):
        """ذخیره لیست در فایل JSON"""
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump({"faqs": self.faq_items}, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 {len(self.faq_items)} سوال در {os.path.basename(self.json_path)} ذخیره شد")
        except Exception as e:
            logger.error(f"خطا در ذخیره {self.json_path}: {e}")

    def _get_next_id(self) -> int:
        if not self.faq_items:
            return 1
        return max(item["id"] for item in self.faq_items) + 1

    # ============================================================
    # CRUD
    # ============================================================

    def add_faq(self, question: str, answer: str, keywords: List[str], category: str = "عمومی") -> Dict:
        new_id = self._get_next_id()
        new_item = {
            "id": new_id,
            "question": question.strip(),
            "answer": answer.strip(),
            "keywords": [kw.strip() for kw in keywords if kw.strip()],
            "category": category.strip() or "عمومی"
        }
        self.faq_items.append(new_item)
        self._save_faqs()
        logger.info(f"➕ سوال جدید: {question[:50]}... (id={new_id})")
        return new_item

    def update_faq(self, faq_id: int, question: Optional[str] = None,
                   answer: Optional[str] = None,
                   keywords: Optional[List[str]] = None,
                   category: Optional[str] = None) -> bool:
        for item in self.faq_items:
            if item["id"] == faq_id:
                if question is not None:
                    item["question"] = question.strip()
                if answer is not None:
                    item["answer"] = answer.strip()
                if keywords is not None:
                    item["keywords"] = [kw.strip() for kw in keywords if kw.strip()]
                if category is not None:
                    item["category"] = category.strip()
                self._save_faqs()
                logger.info(f"✏️ سوال {faq_id} به‌روز شد")
                return True
        return False

    def delete_faq(self, faq_id: int) -> bool:
        for i, item in enumerate(self.faq_items):
            if item["id"] == faq_id:
                self.faq_items.pop(i)
                self._save_faqs()
                logger.info(f"🗑️ سوال {faq_id} حذف شد")
                return True
        return False

    def get_all_faqs(self) -> List[Dict]:
        return self.faq_items.copy()

    def get_faq_by_id(self, faq_id: int) -> Optional[Dict]:
        for item in self.faq_items:
            if item["id"] == faq_id:
                return item.copy()
        return None

    def get_public_faqs(self) -> List[Dict]:
        """دریافت لیست سوالات برای نمایش در فرانت (فقط سوال، بدون پاسخ کامل)"""
        return [
            {"id": item["id"], "question": item["question"], "category": item.get("category", "عمومی")}
            for item in self.faq_items
        ]

    # ============================================================
    # تطابق سوال
    # ============================================================

    def match_faq(self, query: str, threshold: int = 80) -> Optional[Dict]:
        """
        تطابق سوال کاربر با FAQ
        ترکیب: فازی مستقیم (60٪) + کلمات کلیدی (40٪)
        """
        query_lower = query.strip().lower()
        best_match = None
        best_score = 0

        for item in self.faq_items:
            # امتیاز تطابق مستقیم با سوال
            score = fuzz.ratio(query_lower, item["question"].lower())

            # امتیاز کلمات کلیدی
            keyword_score = 0
            for kw in item.get("keywords", []):
                if kw.lower() in query_lower:
                    keyword_score = max(keyword_score, 90)
                else:
                    kw_score = fuzz.partial_ratio(kw.lower(), query_lower)
                    keyword_score = max(keyword_score, kw_score)

            # امتیاز تطابق بخشی
            partial = fuzz.partial_ratio(query_lower, item["question"].lower())

            # ترکیب
            combined = (score * 0.3) + (partial * 0.3) + (keyword_score * 0.4)

            if combined > best_score:
                best_score = combined
                best_match = item

        if best_match and best_score >= threshold:
            logger.info(f"🎯 FAQ matched: '{query[:50]}' -> '{best_match['question'][:50]}' (score={best_score:.1f})")
            return best_match

        return None

    # ============================================================
    # مدیریت FAQ با cache
    # ============================================================

    async def handle_faq_request(self, session_id: str, user_query: str,
                                  rag_search_func, llm_client) -> Tuple[bool, str]:
        """
        ورودی:
            session_id: شناسه نشست
            user_query: سوال کاربر
            rag_search_func: تابع جستجوی RAG (async)
            llm_client: کلاینت مدل زبانی
        خروجی:
            (handled, response)
            handled: True اگر FAQ مدیریتش کرد
        """
        # ۱. تشخیص درخواست توضیح بیشتر
        if self.cache.is_followup_request(user_query):
            last_faq_id = self.cache.get_last_faq_id(session_id)
            last_topic = self.cache.get_last_topic(session_id)

            if last_faq_id:
                faq_item = self.get_faq_by_id(last_faq_id)
                if faq_item:
                    # جستجوی RAG برای اطلاعات بیشتر درباره همون موضوع
                    search_query = last_topic or faq_item["question"]
                    rag_results = rag_search_func(search_query)

                    if rag_results:
                        # ساخت context از نتایج RAG
                        context_parts = [f"پاسخ قبلی ما: {faq_item['answer']}"]
                        for chunk, score in rag_results[:5]:
                            context_parts.append(f"اطلاعات تکمیلی از اسناد (منبع: {chunk.get('source', 'نامشخص')}):\n{chunk['text'][:600]}")

                        context = "\n\n---\n\n".join(context_parts)
                        extended = llm_client.generate(
                            f"لطفاً توضیح کامل‌تری درباره «{search_query}» ارائه بده.",
                            context
                        )
                        self.cache.set_last_topic(session_id, search_query)
                        return True, extended
                    else:
                        # فقط از خود FAQ بسط بده
                        extended = llm_client.generate(
                            f"لطفاً توضیح کامل‌تری درباره «{faq_item['question']}» ارائه بده.",
                            f"پاسخ قبلی: {faq_item['answer']}"
                        )
                        return True, extended

            if last_topic:
                # موضوع قبلی هست ولی FAQ نبوده - RAG دوباره
                rag_results = rag_search_func(last_topic)
                if rag_results:
                    context = "\n\n".join([c['text'][:600] for c, s in rag_results[:5]])
                    extended = llm_client.generate(
                        f"لطفاً توضیح کامل‌تری درباره «{last_topic}» ارائه بده.",
                        context
                    )
                    return True, extended

        # ۲. تطابق با سوالات متداول
        matched = self.match_faq(user_query)
        if matched:
            self.cache.set_last_faq_id(session_id, matched["id"])
            self.cache.set_last_topic(session_id, matched["question"])
            answer = matched["answer"]
            full_answer = f"{answer}\n\n💡 اگر نیاز به توضیح بیشتر دارید، بپرسید «بیشتر توضیح بده»."
            return True, full_answer

        # ۳. ذخیره موضوع بحث حتی اگر FAQ نبود
        self.cache.set_last_topic(session_id, user_query)
        return False, ""