# main.py
import re
import json
import os
import time
import secrets
import asyncio
import logging
import traceback
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
from pydantic import BaseModel, ConfigDict

from config import *
from pdf_loader import PDFProcessor
from embedder import HybridRAG
from intent_detector import IntentDetector
from transformers_client import TransformersClient
from web_scraper import WebScraper
from db_manager import DatabaseManager
from auth_manager import AuthManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("dayan")

# ═══════════════════════════════════════════════════════════════
# امنیت
# ═══════════════════════════════════════════════════════════════
security = HTTPBearer(auto_error=False)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
RATE_LIMIT_ASK = int(os.getenv("RATE_LIMIT_ASK", "30"))
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

MAGIC_BYTES = {
    b"%PDF": ".pdf",
    b"PK\x03\x04": ".docx/.xlsx",
    b"\xd0\xcf\x11\xe0": ".doc/.xls",
}


class RateLimiter:
    def __init__(self):
        self.requests = {}
        self.lock = threading.Lock()

    def check(self, ip: str, limit: int, window: int = 60) -> bool:
        now = time.time()
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            self.requests[ip] = [t for t in self.requests[ip] if now - t < window]
            if len(self.requests[ip]) >= limit:
                return False
            self.requests[ip].append(now)
            return True


rate_limiter = RateLimiter()


async def check_rate_limit_ask(request: Request):
    if not rate_limiter.check(request.client.host, RATE_LIMIT_ASK):
        raise HTTPException(429, "تعداد درخواست‌ها بیش از حد مجاز (۳۰ در دقیقه)")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[Dict]:
    if not credentials:
        return None
    is_valid, payload, _ = auth_manager.verify(credentials.credentials)
    if not is_valid:
        return None
    return payload


async def require_admin(user: Dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="لطفاً وارد شوید")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز - فقط ادمین")
    return user


async def require_auth(user: Dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="لطفاً وارد شوید")
    return user


# ═══════════════════════════════════════════════════════════════
# اعتبارسنجی فایل
# ═══════════════════════════════════════════════════════════════
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls'}
MEDIA_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel',
}


async def validate_upload(file: UploadFile) -> bytes:
    if not file.filename:
        raise HTTPException(400, "نام فایل الزامی است")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"فرمت {ext} مجاز نیست")
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(400, f"حجم فایل بیشتر از {MAX_UPLOAD_SIZE // (1024*1024)}MB است")
    if len(content) < 4:
        raise HTTPException(400, "فایل خالی یا خراب است")
    valid_magic = any(content[:len(magic)] == magic for magic in MAGIC_BYTES)
    if not valid_magic:
        raise HTTPException(400, "محتوای فایل با فرمت آن مطابقت ندارد")
    return content


def safe_filename(original: str) -> str:
    name = Path(original).stem
    ext = Path(original).suffix.lower()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name).strip()
    safe = re.sub(r'\s+', '_', safe)
    if not safe:
        safe = f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return safe + ext


def get_media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return MEDIA_TYPES.get(ext, 'application/octet-stream')


def preprocess_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ═══════════════════════════════════════════════════════════════
# سرویس‌های اصلی
# ═══════════════════════════════════════════════════════════════
rag = HybridRAG()
pdf_processor = PDFProcessor()
intent_detector = IntentDetector()
client = TransformersClient()
web_scraper = WebScraper()
executor = ThreadPoolExecutor(max_workers=4)

model_settings = {
    "model_name": LOCAL_MODEL_PATH,
    "temperature": MODEL_TEMPERATURE
}

# ═══════════════════════════════════════════════════════════════
# Managers
# ═══════════════════════════════════════════════════════════════
db: Optional[DatabaseManager] = None
auth_manager: Optional[AuthManager] = None

_build_lock: Optional[asyncio.Lock] = None


# ═══════════════════════════════════════════════════════════════
# ساخت embeddings
# ═══════════════════════════════════════════════════════════════
async def build_embeddings_from_db(force: bool = False):
    async with _build_lock:
        logger.info("🔄 ساخت embeddings از دیتابیس...")
        all_chunks = []

        try:
            text_docs = await db.get_all_text_contents()
            for doc in text_docs:
                chunks = pdf_processor.chunk_text(doc["text"])
                for c in chunks:
                    c["source"] = f"قانون: {doc['title']}"
                    c["doc_id"] = doc["id"]
                    c["doc_type"] = "text_document"
                all_chunks.extend(chunks)
            logger.info(f"  ✅ text_documents: {len(text_docs)} سند")
        except Exception as e:
            logger.error(f"  ❌ text_documents: {e}")

        try:
            prof_texts = await db.get_all_professors_text()
            for prof in prof_texts:
                chunks = pdf_processor.chunk_text(prof["text"])
                for c in chunks:
                    c["source"] = f"استاد: {prof['title']}"
                    c["doc_id"] = prof["id"]
                    c["doc_type"] = "professor"
                all_chunks.extend(chunks)
            logger.info(f"  ✅ professors: {len(prof_texts)} استاد")
        except Exception as e:
            logger.error(f"  ❌ professors: {e}")

        if all_chunks:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, rag.fit, all_chunks)
            embeddings_bytes = await loop.run_in_executor(executor, rag.save_to_bytes)
            await db.save_embeddings(
                embeddings_data=embeddings_bytes,
                chunk_count=len(all_chunks),
                source_info={"built_at": datetime.now().isoformat()}
            )
            logger.info(f"✅ Embeddings ذخیره شد: {len(all_chunks)} chunk")
        else:
            logger.warning("⚠️ هیچ داده‌ای یافت نشد")


def build_context(results: list, max_chars: int = 3000) -> str:
    parts = []
    total = 0
    for chunk, score in results:
        text = chunk['text']
        source = chunk.get('source', 'نامشخص')
        part = f"【منبع: {source}】\n{text}"
        if total + len(part) > max_chars:
            break
        parts.append(part)
        total += len(part)
    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global db, auth_manager, _build_lock

    _build_lock = asyncio.Lock()
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    db = DatabaseManager(DATABASE_URL)
    await db.connect()
    
    # مهم: auth_manager رو با پول دیتابیس بساز
    auth_manager = AuthManager(db.pool)
    logger.info("✅ AuthManager با دیتابیس PostgreSQL متصل شد")

    # همگام‌سازی اولیه
    async with db.pool.acquire() as _conn:
        await db._sync_faqs_from_json(_conn)

    forms = await db.get_all_physical_forms_for_intent()
    intent_detector.update_forms(forms)
    logger.info(f"✅ intent_detector: {len(forms)} فرم")

    # بارگذاری embeddings
    embeddings_loaded = False
    try:
        emb_data = await db.load_embeddings()
        if emb_data:
            loop = asyncio.get_running_loop()
            embeddings_loaded = await loop.run_in_executor(executor, rag.load_from_bytes, emb_data)
    except Exception as e:
        logger.warning(f"⚠️ خطا در بارگذاری embeddings: {e}")

    if not embeddings_loaded and EMBEDDINGS_PATH.exists():
        if rag.load(str(EMBEDDINGS_PATH.resolve())):
            embeddings_loaded = True
            logger.info("✅ Embeddings از فایل fallback بارگذاری شد")

    if not embeddings_loaded:
        logger.info("🔄 ساخت embeddings از صفر...")
        await build_embeddings_from_db(force=True)

    if client.is_available():
        logger.info(f"✅  آماده | دستیار: {ASSISTANT_NAME}")
    else:
        logger.warning("⚠️  در دسترس نیست")

    logger.info(f"🚀 {ASSISTANT_NAME} آماده | v5.0 | دیتابیس: PostgreSQL")
    yield

    executor.shutdown(wait=True)
    await db.close()
    logger.info(f"🛑 {ASSISTANT_NAME} خاموش شد")


# ═══════════════════════════════════════════════════════════════
# FastAPI App
# ═══════════════════════════════════════════════════════════════
app = FastAPI(lifespan=lifespan, title="Dayan Chatbot API", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════
# مدل‌های Pydantic
# ═══════════════════════════════════════════════════════════════
class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: Optional[str] = None
    message: Optional[str] =None
    text: Optional[str] = None
    messages: Optional[list] = None
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None  # اضافه کن

    def get_user_text(self) -> str:
        if self.query: return self.query
        if self.message: return self.message
        if self.text: return self.text
        if self.messages and len(self.messages) > 0:
            last = self.messages[-1]
            if isinstance(last, dict) and "content" in last:
                return last["content"]
        return ""

    def get_session_id(self) -> str:
        return self.session_id or "default"


class SettingsRequest(BaseModel):
    model_name: str
    temperature: float


class FormRegisterRequest(BaseModel):
    key: str
    name: str
    filename: str
    keywords: list = []
    description: Optional[str] = None
    category: Optional[str] = None


class TextDocumentRequest(BaseModel):
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[List[str]] = []


class FAQRequest(BaseModel):
    question: str
    answer: str
    keywords: List[str] = []
    category: str = "عمومی"


class LoginRequest(BaseModel):
    national_code: str
    password: str


class RegisterRequest(BaseModel):
    national_code: str
    password: str
    first_name: str
    last_name: str
    city: str
    role: str = "professor"


class ForgotPasswordRequest(BaseModel):
    national_code: str
    city: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ═══════════════════════════════════════════════════════════════
# مسیرهای عمومی
# ═══════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """صفحه اصلی چت - عمومی (بدون لاگین)"""
    frontend = Path(__file__).parent / "frontend" / "chat" / "index.html"
    if frontend.exists():
        return FileResponse(frontend)
    return {"status": "ok", "assistant": ASSISTANT_NAME}

@app.get("/admin")
async def admin_page():
    """صفحه پنل ادمین - بدون احراز هویت (خود admin.html لاگین داره)"""
    admin_html = Path(__file__).parent / "frontend" / "admin.html"
    if admin_html.exists():
        return FileResponse(admin_html)
    raise HTTPException(404, "صفحه مدیریت یافت نشد")

@app.get("/admin/panel")
async def admin_panel_page():
    admin_html = Path(__file__).parent / "frontend" / "admin.html"
    if admin_html.exists():
        return FileResponse(admin_html)
    raise HTTPException(404, "صفحه مدیریت یافت نشد")


@app.get("/welcome")
async def welcome():
    return {"type": "welcome", "message": "سلام. من دایان هستم، دستیار دانشگاه. چه سوالی دارید؟"}


@app.get("/health")
async def health():
    all_forms = await db.get_all_physical_forms_for_intent()
    emb_info = await db.get_embeddings_info()
    all_faqs = await db.get_all_faqs()
    return {
        "status": "healthy",
        "total_chunks": len(rag.chunks),
        "total_forms": len(all_forms),
        "total_faqs": len(all_faqs),
        "model_available": client.is_available(),
        "assistant_name": ASSISTANT_NAME,
    }


@app.get("/faqs/public")
async def public_faqs():
    faqs = await db.get_all_faqs()
    return {"faqs": [{"id": f["id"], "question": f["question"], "category": f.get("category", "عمومی")} for f in faqs]}


# ═══════════════════════════════════════════════════════════════
# دانلود فایل
# ═══════════════════════════════════════════════════════════════

@app.get("/download/{form_key}")
async def download_form(form_key: str):
    form = await db.get_physical_form(form_key)
    if not form:
        raise HTTPException(404, "فرم یافت نشد")

    file_id = form.get('file_id')
    file_name = form.get('file_name', f'{form_key}.pdf')

    if file_id:
        file_info = await db.get_file_data(file_id)
        if file_info:
            file_data, db_filename, content_type = file_info
            await db.increment_download_count(form_key)
            return Response(
                content=file_data,
                media_type=content_type or 'application/octet-stream',
                headers={"Content-Disposition": f'attachment; filename="{db_filename or file_name}"'}
            )

    raise HTTPException(404, "فایل یافت نشد")


@app.get("/download-file/{file_id}")
async def download_file_by_id(file_id: int):
    file_info = await db.get_file_data(file_id)
    if file_info:
        file_data, filename, content_type = file_info
        return Response(
            content=file_data,
            media_type=content_type or 'application/octet-stream',
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    raise HTTPException(404, "فایل یافت نشد")


@app.get("/forms")
async def list_forms():
    all_forms = await db.get_all_physical_forms_for_intent()
    forms_list = []
    for k, v in all_forms.items():
        forms_list.append({
            "key": k,
            "name": v.get('name', k),
            "filename": v.get('filename', ''),
            "file_id": v.get('file_id'),
            "category": v.get('category', 'عمومی'),
            "available": v.get('file_id') is not None
        })
    return {"forms": forms_list}

# ═══════════════════════════════════════════════════════════════
# چت
# ═══════════════════════════════════════════════════════════════

async def check_faq_in_db(user_query: str) -> Optional[Dict]:
    """بررسی سوال در دیتابیس FAQ"""
    faqs = await db.get_all_faqs()
    for faq in faqs:
        if faq["question"] in user_query or any(kw in user_query for kw in faq.get("keywords", [])):
            return faq
    return None


async def get_followup_context(session_id: str, user_query: str) -> Optional[str]:
    """دریافت متن مرجع برای توضیح بیشتر"""
    last_topic = await db.get_session_data(session_id, "last_topic")
    if not last_topic:
        return None
    
    results = await asyncio.get_running_loop().run_in_executor(executor, rag.search, last_topic, TOP_K_RESULTS)
    valid_results = [r for r in results if r[1] >= MIN_SIMILARITY_SCORE]
    
    if valid_results:
        return build_context(valid_results[:RERANK_TOP_K], max_chars=3500)
    return None


@app.post("/chat", dependencies=[Depends(check_rate_limit_ask)])
async def chat(req: QueryRequest):
    try:
        raw_query = req.get_user_text()
        if not raw_query or len(raw_query.strip()) < 2:
            raise HTTPException(400, "متن خالی یا خیلی کوتاه است")

        user_query = preprocess_text(raw_query)
        session_id = req.get_session_id()
        
        # بررسی احوالپرسی
        greeting_phrases = ["سلام", "درود", "صبح بخیر", "شب بخیر", "عصر بخیر", "خوبی", "چطوری", "چه خبر", "سلام خوبی"]
        if any(phrase in user_query for phrase in greeting_phrases) and len(user_query) < 20:
            return {"type": "answer", "answer": "سلام. من دایان هستم، دستیار دانشگاه. چه سوالی دارید؟", "source": "greeting"}
        
        logger.info(f"❓ [{session_id[:8]}]: {user_query[:80]}")        

        

        # ۱. بررسی درخواست "توضیح بیشتر"
        followup_phrases = ["بیشتر توضیح بده", "توضیح بیشتر", "بیشتر بگو", "جزئیات بیشتر", "کامل‌تر توضیح بده"]
        is_followup = any(phrase in user_query for phrase in followup_phrases)
        
        if is_followup:
            last_answer = await db.get_session_data(session_id, "last_answer")
            last_topic = await db.get_session_data(session_id, "last_topic")
            
            if last_topic:
                context = await get_followup_context(session_id, user_query)
                if context:
                    ai_answer = await asyncio.get_running_loop().run_in_executor(
                        executor, client.generate, 
                        f"لطفاً درباره «{last_topic}» بیشتر و کامل‌تر توضیح بده.", 
                        context
                    )
                    await db.add_chat_message(session_id, "user", user_query)
                    await db.add_chat_message(session_id, "assistant", ai_answer)
                    await db.set_session_data(session_id, "last_answer", ai_answer)
                    return {"type": "answer", "answer": ai_answer, "source": "followup"}
                elif last_answer:
                    return {"type": "answer", "answer": last_answer + "\n\n💡 اطلاعات بیشتری در اسناد موجود نیست.", "source": "followup"}
        
        # ۲. بررسی کش در PostgreSQL
        cache_key = hashlib.md5(user_query.encode()).hexdigest()
        cached = await db.get_cached_answer(cache_key)
        if cached:
            await db.add_chat_message(session_id, "user", user_query)
            await db.add_chat_message(session_id, "assistant", cached)
            await db.set_session_data(session_id, "last_topic", user_query)
            await db.set_session_data(session_id, "last_answer", cached)
            return {"type": "answer", "answer": cached, "source": "cache"}

        # ۳. بررسی FAQ در دیتابیس
        faq_match = await check_faq_in_db(user_query)
        if faq_match:
            answer = faq_match["answer"]
            await db.save_cached_answer(cache_key, user_query, answer)
            await db.add_chat_message(session_id, "user", user_query)
            await db.add_chat_message(session_id, "assistant", answer)
            await db.set_session_data(session_id, "last_topic", user_query)
            await db.set_session_data(session_id, "last_answer", answer)
            return {"type": "answer", "answer": answer + "\n\n💡 اگر نیاز به توضیح بیشتر دارید، بپرسید «بیشتر توضیح بده».", "source": "faq"}

        # ۴. تشخیص فرم
        intent = intent_detector.detect(user_query)
        form_data = None
        if intent['type'] == 'download_form':
            form_key = intent['form_key']
            form = await db.get_physical_form(form_key)
            if form and form.get('file_id'):
                form_data = {
                    "type": "form_download",
                    "form_key": form_key,
                    "form_name": form['name'],
                    "downloadLink": f"/download/{form_key}"
                }

        # ۵. جستجوی RAG
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(executor, rag.search, user_query, TOP_K_RESULTS)
        valid_results = [r for r in results if r[1] >= MIN_SIMILARITY_SCORE]

        ai_answer = ""
        source = "none"

        if valid_results:
            best_score = valid_results[0][1]
            logger.info(f"📚 RAG: {len(valid_results)} chunk | best={best_score:.3f}")

            context = build_context(valid_results[:RERANK_TOP_K])
            ai_answer = await loop.run_in_executor(executor, client.generate, user_query, context)

            if ("موجود نیست" in ai_answer or "یافت نشد" in ai_answer) and best_score >= 0.4:
                logger.warning("⚠️ مدل گفت موجود نیست - تلاش مجدد")
                retry_context = build_context(valid_results[:RERANK_TOP_K], max_chars=4000)
                ai_answer = await loop.run_in_executor(executor, client.generate, user_query, retry_context)
            
            source = "pdf"
        else:
            web_results = await loop.run_in_executor(executor, web_scraper.search, user_query)
            if web_results:
                ai_answer = await loop.run_in_executor(executor, client.generate, user_query, web_results[:2000])
                source = "web"
            else:
                ai_answer = f"اطلاعاتی درباره «{user_query}» در اسناد موجود نیست."

        # ۶. ذخیره در دیتابیس
        await db.save_cached_answer(cache_key, user_query, ai_answer)
        await db.add_chat_message(session_id, "user", user_query)
        await db.add_chat_message(session_id, "assistant", ai_answer)
        await db.set_session_data(session_id, "last_topic", user_query)
        await db.set_session_data(session_id, "last_answer", ai_answer)
        
        # ۷. پاسخ
        if form_data:
            final = f"📄 **[دانلود فرم {form_data['form_name']}]({form_data['downloadLink']})**\n\n---\n\n{ai_answer.strip()}"
            form_data.update({"message": final, "answer": final, "source": source})
            return form_data
        
        return {"type": "answer", "answer": ai_answer.strip(), "source": source}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ چت: {e}\n{traceback.format_exc()}")
        return {"type": "error", "answer": "خطا در پردازش. دوباره تلاش کنید.", "source": "error"}


# ═══════════════════════════════════════════════════════════════
# احراز هویت
# ═══════════════════════════════════════════════════════════════

@app.post("/auth/register")
async def register(req: RegisterRequest):
    success, message, user_info = await auth_manager.register(  # await اضافه کن
        req.national_code, req.password,
        req.first_name, req.last_name,
        req.city, req.role
    )
    if not success:
        raise HTTPException(400, message)
    return {"message": message, "user": user_info}

@app.post("/auth/login")
async def login(req: LoginRequest):
    success, message, data = await auth_manager.login(req.national_code, req.password)
    if not success:
        raise HTTPException(401, message)
    user_role = data["user"]["role"]
    if user_role == "admin":
        data["redirect_url"] = "/admin/panel"
    else:
        data["redirect_url"] = "/"
    return data


@app.post("/auth/refresh")
async def refresh_token(refresh_token: str = Header(..., alias="X-Refresh-Token")):
    success, message, new_access = await auth_manager.refresh(refresh_token)  # await اضافه کن
    if not success:
        raise HTTPException(401, message)
    return {"access_token": new_access, "token_type": "bearer"}


@app.post("/auth/logout")
async def logout(user: Dict = Depends(require_auth)):
    await auth_manager.logout(int(user["sub"]))  # await اضافه کن
    return {"message": "با موفقیت خارج شدید"}


@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: Dict = Depends(require_auth)):
    success, message = await auth_manager.change_password(int(user["sub"]), req.old_password, req.new_password)  # await اضافه کن
    if not success:
        raise HTTPException(400, message)
    return {"message": message}


@app.post("/auth/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    success, message = await auth_manager.request_password_reset(req.national_code, req.city)  # await اضافه کن
    if not success:
        raise HTTPException(400, message)
    return {"message": "لینک بازیابی صادر شد", "reset_token": message}


@app.post("/auth/reset-password")
async def reset_password(req: ResetPasswordRequest):
    success, message = await auth_manager.reset_password(req.token, req.new_password)  # await اضافه کن
    if not success:
        raise HTTPException(400, message)
    return {"message": message}


# ═══════════════════════════════════════════════════════════════
# پنل ادمین (بدون احراز هویت)
# ═══════════════════════════════════════════════════════════════

@app.get("/admin/users", dependencies=[Depends(require_admin)])
async def admin_list_users(city: str = None, role: str = None, limit: int = 100, offset: int = 0):
    success, users, msg = await auth_manager.get_users(None, city, role, False, limit, offset)
    if not success:
        raise HTTPException(403, msg)
    return {"users": users, "total": len(users)}


@app.post("/admin/upload", dependencies=[Depends(require_admin)])
async def admin_upload_file(file: UploadFile = File(...), category: str = None):
    content = await validate_upload(file)
    safe_name = safe_filename(file.filename)
    ext = Path(file.filename).suffix.lower()
    content_type = MEDIA_TYPES.get(ext, 'application/octet-stream')

    file_id = await db.upload_file(
        filename=safe_name,
        file_data=content,
        original_name=file.filename,
        file_type=ext.lstrip('.'),
        content_type=content_type,
        category=category,
        uploaded_by="admin"
    )
    return {"message": f"فایل آپلود شد", "file_id": file_id, "filename": safe_name}


@app.post("/admin/upload-doc", dependencies=[Depends(require_admin)])
async def admin_upload_doc(file: UploadFile = File(...)):
    return await admin_upload_file(file, category="document")


@app.post("/admin/upload-form", dependencies=[Depends(require_admin)])
async def admin_upload_form(file: UploadFile = File(...)):
    return await admin_upload_file(file, category="form")


@app.get("/admin/files", dependencies=[Depends(require_admin)])
async def admin_list_files(category: str = None, limit: int = 100, offset: int = 0):
    files = await db.list_files(category, limit, offset)
    return {"files": files, "total": len(files)}


@app.delete("/admin/files/{file_id}", dependencies=[Depends(require_admin)])
async def admin_delete_file(file_id: int):
    success = await db.delete_file(file_id)
    if not success:
        raise HTTPException(404, "فایل یافت نشد")
    return {"message": "فایل حذف شد"}


@app.post("/admin/register-form", dependencies=[Depends(require_admin)])
async def admin_register_form(form_data: FormRegisterRequest):
    file_info = await db.get_file_data_by_filename(form_data.filename)
    file_id = file_info[3] if file_info else None

    await db.add_physical_form(
        form_key=form_data.key,
        name=form_data.name,
        file_name=form_data.filename,
        file_id=file_id,
        keywords=form_data.keywords,
        description=form_data.description,
        category=form_data.category or "عمومی",
        uploaded_by="admin",
    )
    all_forms = await db.get_all_physical_forms_for_intent()
    intent_detector.update_forms(all_forms)
    return {"message": f"فرم «{form_data.name}» ثبت شد"}


@app.get("/admin/forms", dependencies=[Depends(require_admin)])
async def admin_list_forms():
    all_forms = await db.get_all_physical_forms_for_intent()
    forms_list = []
    for k, v in all_forms.items():
        forms_list.append({
            "key": k,
            "name": v.get('name'),
            "filename": v.get('filename'),
            "file_id": v.get('file_id'),
            "available": v.get('file_id') is not None
        })
    return {"forms": forms_list, "total": len(forms_list)}


@app.delete("/admin/forms/{form_key}", dependencies=[Depends(require_admin)])
async def admin_delete_form(form_key: str):
    deleted = await db.delete_physical_form(form_key)
    if not deleted:
        raise HTTPException(404, "فرم یافت نشد")
    all_forms = await db.get_all_physical_forms_for_intent()
    intent_detector.update_forms(all_forms)
    return {"message": f"فرم {form_key} حذف شد"}


@app.post("/admin/text-documents", dependencies=[Depends(require_admin)])
async def add_text_document(doc: TextDocumentRequest):
    await db.add_text_document(doc.title, doc.content, doc.category, doc.tags)
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "سند اضافه شد"}


@app.get("/admin/text-documents", dependencies=[Depends(require_admin)])
async def list_text_documents(limit: int = 100, offset: int = 0):
    docs = await db.list_text_documents(limit, offset)
    return {"documents": docs, "total": len(docs)}


@app.get("/admin/text-documents/{doc_id}", dependencies=[Depends(require_admin)])
async def get_text_document(doc_id: int):
    doc = await db.get_text_document(doc_id)
    if not doc:
        raise HTTPException(404, "سند یافت نشد")
    return doc


@app.put("/admin/text-documents/{doc_id}", dependencies=[Depends(require_admin)])
async def update_text_document(doc_id: int, doc: TextDocumentRequest):
    success = await db.update_text_document(doc_id, doc.title, doc.content, doc.category, doc.tags)
    if not success:
        raise HTTPException(404, "سند یافت نشد")
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "سند به‌روزرسانی شد"}


@app.delete("/admin/text-documents/{doc_id}", dependencies=[Depends(require_admin)])
async def delete_text_document(doc_id: int):
    success = await db.delete_text_document(doc_id)
    if not success:
        raise HTTPException(404, "سند یافت نشد")
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "سند حذف شد"}


@app.post("/admin/professors", dependencies=[Depends(require_admin)])
async def add_professor(data: dict):
    await db.add_professor(
        data.get('first_name'), data.get('last_name'),
        data.get('national_code'), data.get('employee_code'),
        data.get('degree'), data.get('field_of_study'),
        data.get('email'), data.get('phone'),
        data.get('room_number'), data.get('office_hours')
    )
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "استاد اضافه شد"}


@app.get("/admin/professors", dependencies=[Depends(require_admin)])
async def list_professors(limit: int = 100, offset: int = 0):
    profs = await db.list_professors(limit, offset, only_active=False)
    return {"professors": profs}


@app.put("/admin/professors/{prof_id}", dependencies=[Depends(require_admin)])
async def update_professor(prof_id: int, data: dict):
    success = await db.update_professor(prof_id, **data)
    if not success:
        raise HTTPException(404, "استاد یافت نشد")
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "استاد به‌روزرسانی شد"}


@app.delete("/admin/professors/{prof_id}", dependencies=[Depends(require_admin)])
async def delete_professor(prof_id: int):
    success = await db.delete_professor(prof_id)
    if not success:
        raise HTTPException(404, "استاد یافت نشد")
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "استاد حذف شد"}


@app.get("/admin/faqs", dependencies=[Depends(require_admin)])
async def list_faqs():
    faqs = await db.get_all_faqs()
    return {"faqs": faqs}


@app.post("/admin/faqs", dependencies=[Depends(require_admin)])
async def add_faq(faq: FAQRequest):
    faq_id = await db.add_faq(faq.question, faq.answer, faq.keywords, faq.category)
    return {"message": "سوال متداول اضافه شد", "id": faq_id}


@app.put("/admin/faqs/{faq_id}", dependencies=[Depends(require_admin)])
async def update_faq(faq_id: int, faq: FAQRequest):
    success = await db.update_faq(faq_id, faq.question, faq.answer, faq.keywords, faq.category)
    if not success:
        raise HTTPException(404, "سوال متداول یافت نشد")
    return {"message": "سوال متداول به‌روزرسانی شد"}


@app.delete("/admin/faqs/{faq_id}", dependencies=[Depends(require_admin)])
async def delete_faq(faq_id: int):
    success = await db.delete_faq(faq_id)
    if not success:
        raise HTTPException(404, "سوال متداول یافت نشد")
    return {"message": "سوال متداول حذف شد"}


@app.post("/admin/rebuild", dependencies=[Depends(require_admin)])
async def admin_rebuild():
    asyncio.create_task(build_embeddings_from_db(True))
    return {"message": "بازسازی embeddings شروع شد"}


@app.get("/admin/settings", dependencies=[Depends(require_admin)])
async def admin_get_settings():
    return model_settings


@app.post("/admin/settings", dependencies=[Depends(require_admin)])
async def admin_update_settings(settings: SettingsRequest):
    model_settings["model_name"] = settings.model_name
    model_settings["temperature"] = settings.temperature
    client.model_name = settings.model_name
    client.temperature = settings.temperature
    return {"message": "تنظیمات ذخیره شد", **model_settings}


# ═══════════════════════════════════════════════════════════════
# استاتیک
# ═══════════════════════════════════════════════════════════════
chat_path = Path(__file__).parent / "frontend" / "chat"
if chat_path.exists():
    app.mount("/assets", StaticFiles(directory=str(chat_path / "assets")), name="assets")
    app.mount("/images", StaticFiles(directory=str(chat_path / "images")), name="images")

background_path = Path(__file__).parent / "frontend" / "background"
if background_path.exists():
    app.mount("/static/background", StaticFiles(directory=str(background_path)), name="bg")

logo_path = Path(__file__).parent / "frontend" / "chat" / "images"
if logo_path.exists():
    app.mount("/static/images", StaticFiles(directory=str(logo_path)), name="logo")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, workers=1, log_level="info")