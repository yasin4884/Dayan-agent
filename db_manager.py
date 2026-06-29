# db_manager.py
import os
import json
import asyncpg
import hashlib
import logging
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any, Tuple

from config import FORMS_JSON_PATH, FAQ_JSON_PATH

load_dotenv()
logger = logging.getLogger("dayan.db")


class DatabaseManager:
    def __init__(self, database_url: str = None):
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL not found")
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.database_url, min_size=2, max_size=10, command_timeout=60)
        await self._init_tables()
        logger.info("✅ PostgreSQL متصل شد")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _init_tables(self):
        async with self.pool.acquire() as conn:
            # 1. users - جدول کاربران (برای احراز هویت)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    national_code VARCHAR(10) NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NOT NULL,
                    phone VARCHAR(11),
                    city VARCHAR(50) NOT NULL,
                    university_name VARCHAR(200) DEFAULT '',
                    degree VARCHAR(50),
                    field_of_study VARCHAR(200),
                    entry_year INTEGER,
                    role VARCHAR(20) NOT NULL DEFAULT 'student' CHECK(role IN ('admin', 'professor', 'student')),
                    is_active BOOLEAN DEFAULT TRUE,
                    is_approved BOOLEAN DEFAULT TRUE,
                    last_login TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_national ON users(national_code)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

            # 2. refresh_tokens
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT NOT NULL UNIQUE,
                    expires_at TIMESTAMP NOT NULL,
                    revoked BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 3. password_resets
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT NOT NULL UNIQUE,
                    phone VARCHAR(11),
                    expires_at TIMESTAMP NOT NULL,
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 4. uploaded_files
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(500) NOT NULL,
                    original_name VARCHAR(500),
                    file_data BYTEA NOT NULL,
                    file_size BIGINT,
                    file_type VARCHAR(20),
                    content_type VARCHAR(100),
                    category VARCHAR(100),
                    uploaded_by VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ufilename ON uploaded_files(filename)")

            # 5. text_documents
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS text_documents (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    content TEXT NOT NULL,
                    content_hash VARCHAR(64) UNIQUE,
                    category VARCHAR(100),
                    tags TEXT[],
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 6. professors
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS professors (
                    id SERIAL PRIMARY KEY,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NOT NULL,
                    national_code VARCHAR(10) UNIQUE,
                    employee_code VARCHAR(20) UNIQUE,
                    degree VARCHAR(50),
                    field_of_study VARCHAR(200),
                    email VARCHAR(200),
                    phone VARCHAR(20),
                    room_number VARCHAR(50),
                    office_hours TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 7. physical_forms
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS physical_forms (
                    id SERIAL PRIMARY KEY,
                    form_key VARCHAR(100) UNIQUE NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    file_id INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,
                    file_name VARCHAR(255) NOT NULL DEFAULT '',
                    file_type VARCHAR(20),
                    file_size BIGINT,
                    keywords TEXT[],
                    description TEXT,
                    category VARCHAR(100) DEFAULT 'عمومی',
                    download_count INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    uploaded_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 8. embeddings_store
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings_store (
                    id SERIAL PRIMARY KEY,
                    embeddings_data BYTEA NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    source_info JSONB DEFAULT '{}',
                    version INTEGER DEFAULT 1,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 9. faqs
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS faqs (
                    id SERIAL PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    keywords TEXT[] DEFAULT '{}',
                    category VARCHAR(100) DEFAULT 'عمومی',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 10. chat_history
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(100) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id, created_at)")

            # 11. query_cache
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    id SERIAL PRIMARY KEY,
                    cache_key VARCHAR(64) UNIQUE NOT NULL,
                    query TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON query_cache(cache_key)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON query_cache(expires_at)")

            # 12. session_memory
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS session_memory (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(100) NOT NULL,
                    key VARCHAR(100) NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(session_id, key)
                )
            """)

            # ایجاد ادمین پیش‌فرض اگر وجود نداشت
            await self._ensure_admin(conn)
            
            await self._sync_forms_from_json(conn)
            await self._sync_faqs_from_json(conn)
            logger.info("✅ جداول آماده شدند")

    async def _ensure_admin(self, conn):
        """ایجاد ادمین پیش‌فرض"""
        import secrets
        admin_exists = await conn.fetchval("SELECT id FROM users WHERE role = 'admin'")
        if not admin_exists:
            salt = secrets.token_hex(16)
            password_hash = f"{salt}${hashlib.sha256(f'{salt}admin123'.encode()).hexdigest()}"
            await conn.execute(
                """INSERT INTO users (national_code, password_hash, first_name, last_name, city, role)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                "admin", password_hash, "مدیر", "سیستم", "tehran", "admin"
            )
            logger.info("✅ ادمین پیش‌فرض ایجاد شد: admin / admin123")

    async def _sync_forms_from_json(self, conn):
        json_path = FORMS_JSON_PATH
        if not json_path.exists():
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                forms_data = json.load(f)
            if not forms_data:
                return
            synced = 0
            for form_key, form_info in forms_data.items():
                exists = await conn.fetchval("SELECT id FROM physical_forms WHERE form_key = $1", form_key)
                if exists:
                    continue
                await conn.execute(
                    """INSERT INTO physical_forms (form_key, name, file_name, keywords, description, category)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    form_key,
                    form_info.get("name", form_key),
                    form_info.get("filename", ""),
                    form_info.get("keywords", []),
                    form_info.get("description", ""),
                    form_info.get("category", "عمومی"),
                )
                synced += 1
            if synced > 0:
                logger.info(f"📋 {synced} فرم از JSON منتقل شد")
        except Exception as e:
            logger.error(f"❌ همگام‌سازی فرم‌ها: {e}")

    async def _sync_faqs_from_json(self, conn):
        json_path = FAQ_JSON_PATH
        if not json_path.exists():
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            faqs_data = data.get("faqs", [])
            if not faqs_data:
                return
            synced = 0
            for faq in faqs_data:
                exists = await conn.fetchval(
                    "SELECT id FROM faqs WHERE question = $1 AND answer = $2",
                    faq.get("question", ""), faq.get("answer", "")
                )
                if exists:
                    continue
                await conn.execute(
                    """INSERT INTO faqs (question, answer, keywords, category)
                       VALUES ($1, $2, $3, $4)""",
                    faq.get("question", ""),
                    faq.get("answer", ""),
                    faq.get("keywords", []),
                    faq.get("category", "عمومی"),
                )
                synced += 1
            if synced > 0:
                logger.info(f"📋 {synced} FAQ از JSON منتقل شد")
        except Exception as e:
            logger.error(f"❌ همگام‌سازی FAQ: {e}")

    # ========== users ==========
    async def get_user_by_national_code(self, national_code: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE national_code = $1 AND is_active = TRUE", national_code)
            return dict(row) if row else None

    async def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1 AND is_active = TRUE", user_id)
            return dict(row) if row else None

    async def create_user(self, national_code: str, password_hash: str, first_name: str, last_name: str,
                          city: str, phone: str = None, degree: str = None, field_of_study: str = None,
                          entry_year: int = None, role: str = "student") -> int:
        async with self.pool.acquire() as conn:
            user_id = await conn.fetchval(
                """INSERT INTO users (national_code, password_hash, first_name, last_name, city, phone,
                   degree, field_of_study, entry_year, role)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id""",
                national_code, password_hash, first_name, last_name, city, phone,
                degree, field_of_study, entry_year, role
            )
            return user_id

    async def update_user_last_login(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET last_login = NOW() WHERE id = $1", user_id)

    async def update_user_password(self, user_id: int, new_password_hash: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2", new_password_hash, user_id)

    # ========== refresh_tokens ==========
    async def save_refresh_token(self, user_id: int, token: str, expires_at):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = $1", user_id)
            await conn.execute(
                "INSERT INTO refresh_tokens (user_id, token, expires_at) VALUES ($1, $2, $3)",
                user_id, token, expires_at
            )

    async def get_refresh_token(self, token: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM refresh_tokens WHERE token = $1 AND revoked = FALSE AND expires_at > NOW()",
                token
            )
            return dict(row) if row else None

    async def revoke_refresh_tokens(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = $1", user_id)

    # ========== uploaded_files ==========
    async def upload_file(self, filename: str, file_data: bytes, original_name: str = None,
                          file_type: str = None, content_type: str = None,
                          category: str = None, uploaded_by: str = None) -> int:
        async with self.pool.acquire() as conn:
            fid = await conn.fetchval(
                """INSERT INTO uploaded_files (filename, original_name, file_data, file_size, file_type, content_type, category, uploaded_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
                filename, original_name or filename, file_data, len(file_data),
                file_type, content_type, category, uploaded_by)
            return fid

    async def get_file_data(self, file_id: int) -> Optional[Tuple[bytes, str, str]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT file_data, filename, content_type FROM uploaded_files WHERE id=$1 AND is_active=TRUE", file_id)
            if row:
                return row["file_data"], row["filename"], row["content_type"]
            return None

    async def get_file_data_by_filename(self, filename: str) -> Optional[Tuple[bytes, str, str, int]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, file_data, filename, content_type FROM uploaded_files WHERE filename=$1 AND is_active=TRUE ORDER BY created_at DESC LIMIT 1",
                filename)
            if row:
                return row["file_data"], row["filename"], row["content_type"], row["id"]
            return None

    async def delete_file(self, file_id: int) -> bool:
        async with self.pool.acquire() as conn:
            r = await conn.execute("UPDATE uploaded_files SET is_active=FALSE WHERE id=$1", file_id)
            return r == "UPDATE 1"

    async def list_files(self, category: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
        async with self.pool.acquire() as conn:
            if category:
                rows = await conn.fetch(
                    "SELECT id, filename, original_name, file_size, file_type, category, uploaded_by, created_at FROM uploaded_files WHERE is_active=TRUE AND category=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                    category, limit, offset)
            else:
                rows = await conn.fetch(
                    "SELECT id, filename, original_name, file_size, file_type, category, uploaded_by, created_at FROM uploaded_files WHERE is_active=TRUE ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    limit, offset)
            return [dict(r) for r in rows]

    # ========== embeddings_store ==========
    async def save_embeddings(self, embeddings_data: bytes, chunk_count: int, source_info: Dict = None) -> int:
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE embeddings_store SET is_active=FALSE")
            eid = await conn.fetchval(
                "INSERT INTO embeddings_store (embeddings_data, chunk_count, source_info) VALUES ($1,$2,$3) RETURNING id",
                embeddings_data, chunk_count, json.dumps(source_info or {}))
            logger.info(f"💾 Embeddings ذخیره شد: {chunk_count} chunk")
            return eid

    async def load_embeddings(self) -> Optional[bytes]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT embeddings_data, chunk_count FROM embeddings_store WHERE is_active=TRUE ORDER BY created_at DESC LIMIT 1")
            if row:
                logger.info(f"📂 Embeddings بارگذاری شد: {row['chunk_count']} chunk")
                return row["embeddings_data"]
            return None

    async def get_embeddings_info(self) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, chunk_count, source_info, version, created_at FROM embeddings_store WHERE is_active=TRUE ORDER BY created_at DESC LIMIT 1")
            return dict(row) if row else None

    # ========== text_documents ==========
    async def add_text_document(self, title: str, content: str, category: str = None, tags: List[str] = None) -> Optional[int]:
        h = hashlib.md5(content.encode()).hexdigest()
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO text_documents (title, content, content_hash, category, tags) VALUES ($1,$2,$3,$4,$5) ON CONFLICT(content_hash) DO NOTHING RETURNING id",
                title, content, h, category, tags or [])

    async def get_text_document(self, doc_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM text_documents WHERE id=$1", doc_id)
            return dict(row) if row else None

    async def update_text_document(self, doc_id: int, title: str = None, content: str = None,
                                    category: str = None, tags: List[str] = None) -> bool:
        sets, params = [], []
        if title:
            sets.append(f"title=${len(params)+1}"); params.append(title)
        if content:
            h = hashlib.md5(content.encode()).hexdigest()
            sets.append(f"content=${len(params)+1}"); params.append(content)
            sets.append(f"content_hash=${len(params)+1}"); params.append(h)
        if category:
            sets.append(f"category=${len(params)+1}"); params.append(category)
        if tags is not None:
            sets.append(f"tags=${len(params)+1}"); params.append(tags)
        if not sets:
            return False
        sets.append("updated_at=NOW()"); params.append(doc_id)
        async with self.pool.acquire() as conn:
            r = await conn.execute(f"UPDATE text_documents SET {', '.join(sets)} WHERE id=${len(params)}", *params)
            return r == "UPDATE 1"

    async def delete_text_document(self, doc_id: int) -> bool:
        async with self.pool.acquire() as conn:
            r = await conn.execute("DELETE FROM text_documents WHERE id=$1", doc_id)
            return r == "DELETE 1"

    async def list_text_documents(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, title, category, tags, created_at FROM text_documents ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset)
            return [dict(r) for r in rows]

    async def get_all_text_contents(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, title, content FROM text_documents")
            return [{"id": r["id"], "title": r["title"], "text": r["content"]} for r in rows]

    # ========== professors ==========
    async def add_professor(self, first_name: str, last_name: str, national_code: str = None,
                            employee_code: str = None, degree: str = None, field_of_study: str = None,
                            email: str = None, phone: str = None, room_number: str = None,
                            office_hours: str = None) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO professors (first_name, last_name, national_code, employee_code, degree, field_of_study, email, phone, room_number, office_hours)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT(national_code) DO UPDATE SET first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name, updated_at=NOW()
                   RETURNING id""",
                first_name, last_name, national_code, employee_code, degree, field_of_study, email, phone, room_number, office_hours)

    async def get_all_professors_text(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, first_name, last_name, degree, field_of_study, email, phone, room_number, office_hours FROM professors WHERE is_active=TRUE")
            return [{"id": r["id"], "title": f"استاد {r['first_name']} {r['last_name']}",
                     "text": f"{r['first_name']} {r['last_name']} - {r['degree'] or ''} - {r['field_of_study'] or ''} - تلفن: {r['phone'] or ''} - اتاق: {r['room_number'] or ''} - ساعات: {r['office_hours'] or ''}"}
                    for r in rows]

    async def list_professors(self, limit: int = 100, offset: int = 0, only_active: bool = True) -> List[Dict]:
        async with self.pool.acquire() as conn:
            q = "SELECT id, first_name, last_name, national_code, degree, email, phone FROM professors"
            q += " WHERE is_active=TRUE" if only_active else ""
            q += " ORDER BY last_name LIMIT $1 OFFSET $2"
            rows = await conn.fetch(q, limit, offset)
            return [dict(r) for r in rows]

    async def update_professor(self, prof_id: int, **kwargs) -> bool:
        allowed = {'first_name', 'last_name', 'national_code', 'employee_code', 'degree',
                   'field_of_study', 'email', 'phone', 'room_number', 'office_hours', 'is_active'}
        sets, params = [], []
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=${len(params)+1}"); params.append(v)
        if not sets:
            return False
        sets.append("updated_at=NOW()"); params.append(prof_id)
        async with self.pool.acquire() as conn:
            r = await conn.execute(f"UPDATE professors SET {', '.join(sets)} WHERE id=${len(params)}", *params)
            return r == "UPDATE 1"

    async def delete_professor(self, prof_id: int) -> bool:
        async with self.pool.acquire() as conn:
            r = await conn.execute("DELETE FROM professors WHERE id=$1", prof_id)
            return r == "DELETE 1"

    # ========== physical_forms ==========
    async def add_physical_form(self, form_key: str, name: str, file_name: str,
                                file_id: int = None, file_type: str = None, file_size: int = None,
                                keywords: List[str] = None, description: str = None,
                                category: str = "عمومی", uploaded_by: str = "admin") -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO physical_forms (form_key, name, file_id, file_name, file_type, file_size, keywords, description, category, uploaded_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT(form_key) DO UPDATE SET name=EXCLUDED.name, file_id=EXCLUDED.file_id,
                   file_name=EXCLUDED.file_name, file_type=EXCLUDED.file_type, file_size=EXCLUDED.file_size,
                   keywords=EXCLUDED.keywords, description=EXCLUDED.description, category=EXCLUDED.category, updated_at=NOW()
                   RETURNING id""",
                form_key, name, file_id, file_name, file_type, file_size, keywords or [], description, category, uploaded_by)

    async def get_physical_form(self, form_key: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM physical_forms WHERE form_key=$1 AND is_active=TRUE", form_key)
            return dict(row) if row else None

    async def delete_physical_form(self, form_key: str) -> bool:
        async with self.pool.acquire() as conn:
            r = await conn.execute("DELETE FROM physical_forms WHERE form_key=$1", form_key)
            return r == "DELETE 1"

    async def get_all_physical_forms_for_intent(self) -> Dict[str, Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT form_key, name, keywords, category, description, file_name, file_id FROM physical_forms WHERE is_active=TRUE")
            if rows:
                return {r["form_key"]: {"name": r["name"], "keywords": r["keywords"] or [],
                        "category": r["category"], "description": r["description"] or "",
                        "filename": r["file_name"], "file_id": r["file_id"]} for r in rows}
        return {}

    async def increment_download_count(self, form_key: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE physical_forms SET download_count=download_count+1 WHERE form_key=$1", form_key)

    async def sync_forms_from_json(self) -> int:
        async with self.pool.acquire() as conn:
            if not FORMS_JSON_PATH.exists():
                return 0
            with open(FORMS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            synced = 0
            for k, v in data.items():
                if not await conn.fetchval("SELECT id FROM physical_forms WHERE form_key=$1", k):
                    await conn.execute(
                        "INSERT INTO physical_forms (form_key, name, file_name, keywords, description, category) VALUES ($1,$2,$3,$4,$5,$6)",
                        k, v.get("name", k), v.get("filename", ""), v.get("keywords", []),
                        v.get("description", ""), v.get("category", "عمومی"))
                    synced += 1
            return synced

    # ========== FAQ ==========
    async def get_all_faqs(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, question, answer, keywords, category, created_at FROM faqs WHERE is_active=TRUE ORDER BY id"
            )
            return [dict(r) for r in rows]

    async def get_faq_by_id(self, faq_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, question, answer, keywords, category FROM faqs WHERE id=$1 AND is_active=TRUE",
                faq_id
            )
            return dict(row) if row else None

    async def add_faq(self, question: str, answer: str, keywords: List[str] = None, category: str = "عمومی") -> int:
        async with self.pool.acquire() as conn:
            faq_id = await conn.fetchval(
                """INSERT INTO faqs (question, answer, keywords, category)
                   VALUES ($1, $2, $3, $4)
                   RETURNING id""",
                question, answer, keywords or [], category
            )
            logger.info(f"➕ FAQ اضافه شد: {question[:50]}... (id={faq_id})")
            return faq_id

    async def update_faq(self, faq_id: int, question: str = None, answer: str = None,
                         keywords: List[str] = None, category: str = None) -> bool:
        sets, params = [], []
        if question is not None:
            sets.append(f"question=${len(params)+1}"); params.append(question)
        if answer is not None:
            sets.append(f"answer=${len(params)+1}"); params.append(answer)
        if keywords is not None:
            sets.append(f"keywords=${len(params)+1}"); params.append(keywords)
        if category is not None:
            sets.append(f"category=${len(params)+1}"); params.append(category)
        if not sets:
            return False
        sets.append("updated_at=NOW()")
        params.append(faq_id)
        async with self.pool.acquire() as conn:
            r = await conn.execute(f"UPDATE faqs SET {', '.join(sets)} WHERE id=${len(params)}", *params)
            return r == "UPDATE 1"

    async def delete_faq(self, faq_id: int) -> bool:
        async with self.pool.acquire() as conn:
            r = await conn.execute("UPDATE faqs SET is_active=FALSE WHERE id=$1", faq_id)
            return r == "UPDATE 1"

    # ========== کش و تاریخچه ==========
    async def get_cached_answer(self, cache_key: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT answer FROM query_cache WHERE cache_key=$1 AND expires_at > NOW()",
                cache_key
            )
            return row["answer"] if row else None

    async def save_cached_answer(self, cache_key: str, query: str, answer: str, ttl_seconds: int = 86400) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO query_cache (cache_key, query, answer, expires_at)
                   VALUES ($1, $2, $3, NOW() + ($4::text || ' seconds')::INTERVAL)
                   ON CONFLICT (cache_key) DO UPDATE SET answer = $3, expires_at = NOW() + ($4::text || ' seconds')::INTERVAL""",
                cache_key, query, answer, str(ttl_seconds)
            )
            return True

    async def add_chat_message(self, session_id: str, role: str, content: str) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_history (session_id, role, content) VALUES ($1, $2, $3)",
                session_id, role, content
            )
            return True

    async def get_chat_history(self, session_id: str, limit: int = 20) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, content, created_at FROM chat_history WHERE session_id=$1 ORDER BY created_at DESC LIMIT $2",
                session_id, limit
            )
            return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in reversed(rows)]

    async def get_session_data(self, session_id: str, key: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM session_memory WHERE session_id=$1 AND key=$2",
                session_id, key
            )
            return row["value"] if row else None

    async def set_session_data(self, session_id: str, key: str, value: str) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO session_memory (session_id, key, value)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (session_id, key) DO UPDATE SET value = $3, updated_at = NOW()""",
                session_id, key, value
            )
            return True

    async def delete_session_data(self, session_id: str, key: str) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM session_memory WHERE session_id=$1 AND key=$2",
                session_id, key
            )
            return True

    async def cleanup_expired_cache(self) -> int:
        async with self.pool.acquire() as conn:
            r = await conn.execute("DELETE FROM query_cache WHERE expires_at <= NOW()")
            return int(r.split()[-1]) if "DELETE" in r else 0