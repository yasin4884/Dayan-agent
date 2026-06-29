# embedder.py
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rapidfuzz import fuzz
import numpy as np
import pickle
import logging
import re
from typing import Optional, List, Tuple
from config import CHUNK_SIZE, CHUNK_OVERLAP, TOP_K_RESULTS, MIN_SIMILARITY_SCORE

logger = logging.getLogger("dayan.embedder")

# ✅ stopwords فارسی
PERSIAN_STOPWORDS = {
    "و", "در", "به", "از", "که", "این", "را", "با", "است",
    "برای", "آن", "یک", "خود", "تا", "کرد", "شد", "هم",
    "اما", "یا", "اگر", "همه", "بود", "شود", "دارد", "هر",
    "ما", "او", "من", "تو", "ها", "های", "می", "نیست",
    "باید", "نه", "بر", "هستند", "هست", "داشت", "کند"
}


def normalize_persian(text: str) -> str:
    """نرمال‌سازی متن فارسی"""
    if not text:
        return ""

    # یکسان‌سازی کاراکترهای عربی/فارسی
    text = text.replace("ك", "ک").replace("ي", "ی")
    text = text.replace("ة", "ه").replace("ؤ", "و")
    text = text.replace("أ", "ا").replace("إ", "ا")

    # حذف اعراب
    text = re.sub(r'[\u064B-\u065F]', '', text)

    # فاصله‌گذاری درست
    text = re.sub(r'\s+', ' ', text).strip()

    # حذف کاراکترهای غیرضروری
    text = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', text)

    return text


def tokenize_persian(text: str) -> list:
    """توکن‌سازی و حذف stopwords فارسی"""
    text = normalize_persian(text)
    tokens = text.split()
    return [t for t in tokens if t not in PERSIAN_STOPWORDS and len(t) > 1]


class HybridRAG:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            analyzer='char_wb',
            ngram_range=(2, 4),
            max_features=5000,
            sublinear_tf=True,
            min_df=1,
            strip_accents=None
        )
        self.tfidf_matrix = None
        self.chunks = []
        self.normalized_chunks = []

    def fit(self, chunks: list):
        """ایندکس‌گذاری chunks"""
        self.chunks = chunks

        # نرمال‌سازی همه متون
        self.normalized_chunks = [
            normalize_persian(c['text']) for c in chunks
        ]

        # fit روی متون نرمال‌شده
        self.tfidf_matrix = self.vectorizer.fit_transform(self.normalized_chunks)

        logger.info(f"✅ ایندکس شد: {len(chunks)} chunk")

    def search(self, query: str, top_k: int = TOP_K_RESULTS) -> list:
        """جستجوی هیبرید: TF-IDF + Fuzzy + کلمه کلیدی"""
        if not self.chunks:
            return []

        # نرمال‌سازی query
        normalized_query = normalize_persian(query)
        query_tokens = tokenize_persian(query)

        # --- ۱. امتیاز TF-IDF ---
        query_vec = self.vectorizer.transform([normalized_query])
        tfidf_scores = cosine_similarity(query_vec, self.tfidf_matrix)[0]

        # --- ۲. امتیاز Fuzzy ---
        fuzzy_scores = np.array([
            fuzz.partial_ratio(normalized_query, nc) / 100.0
            for nc in self.normalized_chunks
        ])

        # --- ۳. امتیاز کلمه کلیدی (token overlap) ---
        keyword_scores = np.zeros(len(self.chunks))
        if query_tokens:
            for i, chunk_text in enumerate(self.normalized_chunks):
                chunk_tokens = set(tokenize_persian(chunk_text))
                query_token_set = set(query_tokens)
                if chunk_tokens:
                    overlap = len(chunk_tokens & query_token_set)
                    keyword_scores[i] = overlap / len(query_token_set)

        # --- ۴. نرمال‌سازی هر امتیاز به [0,1] ---
        def normalize_scores(arr):
            mn, mx = arr.min(), arr.max()
            if mx - mn < 1e-9:
                return np.zeros_like(arr)
            return (arr - mn) / (mx - mn)

        tfidf_norm = normalize_scores(tfidf_scores)
        fuzzy_norm = normalize_scores(fuzzy_scores)
        keyword_norm = normalize_scores(keyword_scores)

        # --- ۵. ترکیب وزن‌دار ---
        combined = (
            tfidf_norm * 0.4 +
            fuzzy_norm * 0.3 +
            keyword_norm * 0.3
        )

        # --- ۶. فیلتر بر اساس MIN_SIMILARITY_SCORE ---
        top_indices = np.argsort(combined)[::-1][:top_k]

        results = []
        for i in top_indices:
            score = float(combined[i])
            if score >= MIN_SIMILARITY_SCORE:
                results.append((self.chunks[i], score))

        return results

    def save_to_bytes(self) -> bytes:
        """سریالایز کردن به bytes برای ذخیره در دیتابیس"""
        data = {
            'vectorizer': self.vectorizer,
            'tfidf_matrix': self.tfidf_matrix,
            'chunks': self.chunks,
            'normalized_chunks': self.normalized_chunks
        }
        return pickle.dumps(data)

    def load_from_bytes(self, data: bytes) -> bool:
        """بارگذاری از bytes (از دیتابیس)"""
        try:
            loaded = pickle.loads(data)
            self.vectorizer = loaded['vectorizer']
            self.tfidf_matrix = loaded['tfidf_matrix']
            self.chunks = loaded['chunks']
            self.normalized_chunks = loaded.get('normalized_chunks',
                                                 [normalize_persian(c['text']) for c in self.chunks])
            logger.info(f"✅ Embeddings از دیتابیس بارگذاری شد: {len(self.chunks)} chunk")
            return True
        except Exception as e:
            logger.error(f"❌ خطا در بارگذاری embeddings از دیتابیس: {e}")
            return False

    def save(self, path: str) -> bool:
        """ذخیره به فایل (fallback)"""
        try:
            data = self.save_to_bytes()
            with open(path, 'wb') as f:
                f.write(data)
            logger.info(f"✅ ذخیره شد در فایل: {path}")
            return True
        except Exception as e:
            logger.error(f"❌ خطا در ذخیره فایل: {e}")
            return False

    def load(self, path: str) -> bool:
        """بارگذاری از فایل (fallback)"""
        try:
            with open(path, 'rb') as f:
                data = f.read()
            return self.load_from_bytes(data)
        except FileNotFoundError:
            logger.warning(f"⚠️ فایل {path} یافت نشد")
            return False
        except Exception as e:
            logger.error(f"❌ خطا در بارگذاری فایل: {e}")
            return False


Embedder = HybridRAG