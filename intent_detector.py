# intent_detector.py
from rapidfuzz import fuzz
import logging

logger = logging.getLogger("dayan.intent")


class IntentDetector:
    def __init__(self):
        # فرم‌های ثابت پیش‌فرض (اگه هیچی نبود از اینا استفاده میشه)
        self.static_keywords = {
            "internship": ["کارآموزی", "کار آموزی", "فرم کارآموزی", "internship"],
            "entkhab": ["انتخاب واحد", "فرم انتخاب واحد", "تکدرس"],
            "kolterm": ["برنامه ترم", "برنامه کل", "فرم برنامه ترم"],
        }
        # فرم‌های داینامیک — از main.py آپدیت میشه
        self.forms = {}

    def update_forms(self, forms_dict: dict):
        """آپدیت فرم‌ها از دیتابیس یا JSON"""
        self.forms = forms_dict
        logger.info(f"🔄 IntentDetector آپدیت شد: {len(forms_dict)} فرم")
        for key, info in forms_dict.items():
            kws = info.get("keywords", [])
            logger.debug(f"   📋 {key}: name={info.get('name')} keywords={kws}")

    def detect(self, query: str) -> dict:
        """تشخیص قصد کاربر - دانلود فرم یا سوال عمومی"""
        query_lower = query.strip().lower()

        # کلمات کلیدی که مستقیم درخواست فرم هستن
        form_request_keywords = [
            "فرم", "دانلود فرم", "فرم رو میخوام", "فرمش", "فرم دانلود",
            "لینک فرم", "فایل فرم", "دانلود کنم", "از کجا دانلود",
        ]
        is_form_request = any(kw in query_lower for kw in form_request_keywords)

        best_match = None
        best_score = 0

        # ۱. فرم‌های داینامیک (از PostgreSQL یا JSON)
        for form_key, form_info in self.forms.items():
            name = form_info.get("name", "")
            keywords = form_info.get("keywords", [])

            # چک نام فرم
            if name:
                score = fuzz.partial_ratio(name.lower(), query_lower)
                if score > best_score:
                    best_score = score
                    best_match = form_key

            # چک خود form_key
            score = fuzz.partial_ratio(form_key.lower(), query_lower)
            if score > best_score:
                best_score = score
                best_match = form_key

            # چک همه keyword ها
            for kw in keywords:
                if not kw or not kw.strip():
                    continue
                # split با کاما فارسی و انگلیسی
                sub_keywords = [k.strip() for k in kw.replace("،", ",").split(",") if k.strip()]
                for sub_kw in sub_keywords:
                    if len(sub_kw) < 2:
                        continue
                    score = fuzz.partial_ratio(sub_kw.lower(), query_lower)
                    if score > best_score:
                        best_score = score
                        best_match = form_key

        # ۲. فرم‌های ثابت (fallback اگه تو forms نیستن)
        for form_key, keywords in self.static_keywords.items():
            if form_key in self.forms:
                continue  # قبلاً چک شده
            for kw in keywords:
                score = fuzz.partial_ratio(kw.lower(), query_lower)
                if score > best_score:
                    best_score = score
                    best_match = form_key

        # ۳. تصمیم‌گیری نهایی
        # آستانه تشخیص:
        # - اگه کاربر صریحاً درخواست فرم داره → آستانه پایین‌تر (60)
        # - در غیر این صورت → آستانه بالاتر (75)
        threshold = 60 if is_form_request else 75

        if best_match and best_score >= threshold:
            logger.info(f"🎯 Intent: download_form | key={best_match} | score={best_score}")
            return {"type": "download_form", "form_key": best_match, "score": best_score}

        logger.debug(f"🔍 Intent: general_query | best_match={best_match} | best_score={best_score}")
        return {"type": "general_query", "best_match": best_match, "best_score": best_score}