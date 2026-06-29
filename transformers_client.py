# transformers_client.py
import requests
import logging
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("dayan.llm")


class TransformersClient:
    def __init__(self):
        self.lm_studio_base = os.getenv("LM_STUDIO_URL", "http://localhost:1234")
        self.lm_studio_url = f"{self.lm_studio_base}/v1/chat/completions"
        self.temperature = float(os.getenv("TEMPERATURE", "0.1"))
        self.system_prompt = self._load_prompt_from_json()
        self.model_name = os.getenv("LM_MODEL_NAME", "qwen3:8b")

    def _load_prompt_from_json(self) -> str:
        """بارگذاری system prompt از prompt.json"""
        prompt_path = Path(__file__).parent / "prompt.json"
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                prompt = data.get("system_prompt", "")
                logger.info(f"✅ پرامپت از {prompt_path.name} بارگذاری شد (نسخه: {data.get('version', 'نامشخص')})")
                return prompt
        except FileNotFoundError:
            logger.error(f"❌ فایل {prompt_path} پیدا نشد")
            return ""
        except json.JSONDecodeError:
            logger.error(f"❌ فایل {prompt_path} فرمت JSON معتبر ندارد")
            return ""

    def update_settings(self, new_model_name: str, new_temperature: float):
        self.model_name = new_model_name
        self.temperature = new_temperature
        logger.info(f"⚙️ تنظیمات: مدل={self.model_name} | دما={self.temperature}")

    def is_available(self) -> bool:
        """بررسی دسترسی به Ollama"""
        try:
            response = requests.get(f"{self.lm_studio_base}/v1/models", timeout=5)
            if response.status_code == 200:
                logger.info(f"✅ Ollama در دسترس است: {self.lm_studio_base}")
                return True
            logger.warning(f"⚠️ Ollama پاسخ داد اما با کد: {response.status_code}")
            return False
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ Ollama در {self.lm_studio_base} در دسترس نیست - سرور خاموش است")
            return False
        except requests.exceptions.Timeout:
            logger.error(f"❌ زمان درخواست به Ollama تمام شد")
            return False
        except Exception as e:
            logger.error(f"❌ خطا در بررسی Ollama: {e}")
            return False

    def generate(self, query: str, context: str) -> str:
        """تولید پاسخ بر اساس متن مرجع"""

        user_message = f"""متن مرجع:
{context}

سوال کاربر:
{query}

پاسخ بده (بر اساس متن مرجع، مختصر و مفید):"""

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": self.temperature,
            "max_tokens": 512,
            "stream": False
        }

        logger.debug(f"📤 سوال: {query[:80]}")
        logger.debug(f"📄 متن مرجع: {context[:150]}...")

        try:
            response = requests.post(self.lm_studio_url, json=payload, timeout=60)
            response.raise_for_status()

            result = response.json()
            output = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            if not output:
                logger.warning("⚠️ مدل پاسخ خالی برگرداند")
                return "متاسفم، پاسخی دریافت نشد."

            logger.debug(f"📥 پاسخ: {output[:100]}")
            return output

        except requests.exceptions.ConnectionError:
            msg = "خطا: سرور مدل زبان در دسترس نیست. لطفاً از وضعیت سرویس Ollama مطمئن شوید."
            logger.error(msg)
            return msg
        except requests.exceptions.Timeout:
            msg = "خطا: زمان پاسخگویی مدل به پایان رسید."
            logger.error(msg)
            return msg
        except requests.exceptions.HTTPError as e:
            msg = f"خطای HTTP از مدل: {e.response.status_code}"
            logger.error(msg)
            return "متاسفم، خطایی در پردازش رخ داد. دوباره تلاش کنید."
        except Exception as e:
            msg = f"خطای غیرمنتظره: {str(e)}"
            logger.error(msg)
            return "متاسفم، خطایی رخ داد."

    def get_welcome_message(self) -> str:
        """پیام خوشامدگویی"""
        return "سلام. من دایان هستم، دستیار دانشگاه. چه سوالی دارید؟"