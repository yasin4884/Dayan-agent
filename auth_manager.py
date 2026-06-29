# auth_manager.py
import re
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
import jwt
import asyncpg
from config import *

logger = logging.getLogger("dayan.auth")


class AuthManager:
    def __init__(self, db_pool=None):
        self.pool = db_pool
        logger.info("✅ AuthManager آماده شد")

    @staticmethod
    def _hash(password: str) -> str:
        salt = secrets.token_hex(16)
        return f"{salt}${hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"

    @staticmethod
    def _check(password: str, hashed: str) -> bool:
        try:
            salt, h = hashed.split("$", 1)
            return secrets.compare_digest(h, hashlib.sha256(f"{salt}{password}".encode()).hexdigest())
        except:
            return False

    async def register(self, national_code: str, password: str, first_name: str, last_name: str,
                       city: str, role: str = "student") -> Tuple[bool, str, Optional[Dict]]:
        """ثبت‌نام کاربر جدید"""
        if not re.match(r'^\d{10}$', national_code):
            return False, "کد ملی باید ۱۰ رقم باشد", None
        if len(password) < 4:
            return False, "رمز حداقل ۴ کاراکتر", None

        try:
            async with self.pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT id FROM users WHERE national_code = $1", national_code
                )
                if exists:
                    return False, "این کد ملی قبلاً ثبت شده است", None

                user_id = await conn.fetchval(
                    """INSERT INTO users (national_code, password_hash, first_name, last_name, city, role)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       RETURNING id""",
                    national_code, self._hash(password), first_name, last_name, city, role
                )

                return True, "ثبت‌نام با موفقیت انجام شد", {
                    "id": user_id,
                    "national_code": national_code,
                    "first_name": first_name,
                    "last_name": last_name,
                    "city": city,
                    "role": role
                }
        except Exception as e:
            logger.error(f"register error: {e}")
            return False, f"خطا: {str(e)}", None

    async def login(self, national_code: str, password: str) -> Tuple[bool, str, Optional[Dict]]:
        """ورود کاربر"""
        print(f"🔐 تلاش برای ورود: {national_code}")

        if not self.pool:
            print("❌ self.pool وجود ندارد!")
            return False, "خطای داخلی سرور - دیتابیس متصل نیست", None

        try:
            async with self.pool.acquire() as conn:
                print("✅ اتصال به دیتابیس برقرار شد")

                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE national_code = $1",
                    national_code
                )

                if not user:
                    print(f"❌ کاربر با کد ملی {national_code} یافت نشد")
                    return False, "کد ملی یا رمز اشتباه است", None

                print(f"✅ کاربر یافت شد: {user['first_name']} {user['last_name']}")

                if not self._check(password, user["password_hash"]):
                    print("❌ رمز اشتباه است")
                    return False, "کد ملی یا رمز اشتباه است", None

                print("✅ رمز درست است")

                access_token = jwt.encode({
                    "sub": str(user["id"]),
                    "national_code": user["national_code"],
                    "first_name": user["first_name"],
                    "last_name": user["last_name"],
                    "city": user["city"],
                    "role": user["role"],
                    "type": "access",
                    "exp": datetime.utcnow() + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
                }, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

                await conn.execute("UPDATE users SET last_login = NOW() WHERE id = $1", user["id"])

                return True, "", {
                    "access_token": access_token,
                    "token_type": "bearer",
                    "user": {
                        "id": user["id"],
                        "national_code": user["national_code"],
                        "first_name": user["first_name"],
                        "last_name": user["last_name"],
                        "city": user["city"],
                        "role": user["role"]
                    }
                }
        except Exception as e:
            print(f"❌ خطا در login: {e}")
            logger.error(f"login error: {e}")
            return False, f"خطا: {str(e)}", None

    def verify(self, token: str) -> Tuple[bool, Optional[Dict], str]:
        """بررسی توکن"""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            if payload.get("type") != "access":
                return False, None, "توکن نامعتبر"
            return True, payload, ""
        except jwt.ExpiredSignatureError:
            return False, None, "توکن منقضی شده"
        except Exception as e:
            return False, None, str(e)

    async def refresh(self, refresh_token: str) -> Tuple[bool, str, Optional[str]]:
        return False, "این ویژگی فعلاً غیرفعال است", None

    async def logout(self, user_id: int) -> bool:
        return True

    async def change_password(self, user_id: int, old_password: str, new_password: str) -> Tuple[bool, str]:
        return False, "تغییر رمز فعلاً غیرفعال است"

    async def request_password_reset(self, national_code: str, city: str) -> Tuple[bool, str]:
        return False, "بازیابی رمز فعلاً غیرفعال است"

    async def reset_password(self, token: str, new_password: str) -> Tuple[bool, str]:
        return False, "بازیابی رمز فعلاً غیرفعال است"

    async def get_users(self, admin_id: int, city: str = None, role: str = None,
                        approved_only: bool = False, limit: int = 100, offset: int = 0) -> Tuple[bool, List[Dict], str]:
        """دریافت لیست کاربران از دیتابیس"""
        try:
            async with self.pool.acquire() as conn:
                conditions = []
                params = []
                i = 1

                if city:
                    conditions.append(f"city = ${i}")
                    params.append(city)
                    i += 1

                if role:
                    conditions.append(f"role = ${i}")
                    params.append(role)
                    i += 1

                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

                query = f"""
                    SELECT id, national_code, first_name, last_name, city, role, last_login
                    FROM users
                    {where}
                    ORDER BY id DESC
                    LIMIT ${i} OFFSET ${i + 1}
                """
                params.extend([limit, offset])

                rows = await conn.fetch(query, *params)
                users = []
                for r in rows:
                    users.append({
                        "id": r["id"],
                        "national_code": r["national_code"],
                        "first_name": r["first_name"],
                        "last_name": r["last_name"],
                        "city": r["city"],
                        "role": r["role"],
                        "last_login": r["last_login"].isoformat() if r["last_login"] else None
                    })

                logger.info(f"✅ get_users: {len(users)} کاربر دریافت شد")
                return True, users, ""

        except Exception as e:
            logger.error(f"get_users error: {e}")
            return False, [], f"خطا: {str(e)}"

    async def approve_user(self, admin_id: int, user_id: int) -> Tuple[bool, str]:
        """تغییر نقش کاربر به student (تأیید)"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE users SET role = 'student' WHERE id = $1", user_id
                )
                if result == "UPDATE 0":
                    return False, "کاربر یافت نشد"
                logger.info(f"✅ کاربر {user_id} تأیید شد")
                return True, "کاربر تأیید شد"
        except Exception as e:
            logger.error(f"approve_user error: {e}")
            return False, f"خطا: {str(e)}"

    async def delete_user(self, admin_id: int, user_id: int) -> Tuple[bool, str]:
        """حذف کاربر از دیتابیس"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM users WHERE id = $1", user_id
                )
                if result == "DELETE 0":
                    return False, "کاربر یافت نشد"
                logger.info(f"✅ کاربر {user_id} حذف شد")
                return True, "کاربر حذف شد"
        except Exception as e:
            logger.error(f"delete_user error: {e}")
            return False, f"خطا: {str(e)}"