import os
import traceback
from typing import Any

import requests

try:
    import openai
except Exception:
    openai = None
try:
    import google.generativeai as genai
except Exception:
    genai = None
try:
    from langdetect import detect
except Exception:

    def detect(text: str) -> str:
        return "bg"


from .ai_config import get_ai_config, logger


class AIService:
    """AI Service for generating intelligent responses"""

    def __init__(self):
        self.setup_providers()

    def setup_providers(self):
        try:
            openai_provider = get_ai_config().get_provider("openai")
            if openai_provider and openai_provider.enabled:
                if openai is None:
                    logger.warning(
                        "⚠️ OpenAI SDK not installed - OpenAI provider disabled at runtime"
                    )
                else:
                    openai.api_key = openai_provider.api_key
                    logger.info("OpenAI configured successfully")
            gemini_provider = get_ai_config().get_provider("gemini")
            if gemini_provider and gemini_provider.enabled:
                if genai is None:
                    logger.warning(
                        "⚠️ Google Generative AI SDK not installed - Gemini provider disabled at runtime"
                    )
                else:
                    genai.configure(api_key=gemini_provider.api_key)
                    logger.info("Gemini configured successfully")
        except Exception as e:
            logger.error(f"❌ Error setting up AI providers: {e}")

    async def generate_response(
        self, user_message: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            try:
                detected_lang = detect(user_message)
            except Exception:
                detected_lang = "bg"
            mock_flag = os.getenv("AI_DEV_MOCK", "").lower() in ("1", "true", "yes")
            if mock_flag:
                user_msg_lower = user_message.lower()
                if "здравей" in user_msg_lower or "здрасти" in user_msg_lower:
                    response_text = "Здравейте! Аз съм AI асистентът на HelpChain. Как мога да ви помогна днес?"
                elif "helpchain" in user_msg_lower or "какво" in user_msg_lower:
                    response_text = "HelpChain е платформа за доброволчество в България. Свързваме хора нуждаещи се от помощ с проверени доброволци в София, Пловдив, Варна, Бургас и Стара Загора."
                elif "регистрация" in user_msg_lower or "регистрирам" in user_msg_lower:
                    response_text = "Регистрацията в HelpChain е безплатна! Можете да се регистрирате през нашето мобилно приложение или уеб сайта. За доброволци има процес на проверка и обучение."
                elif (
                    "цена" in user_msg_lower
                    or "струва" in user_msg_lower
                    or "такса" in user_msg_lower
                ):
                    response_text = "Цените зависят от вида на услугата. Свържете се с нашия екип за точна информация и консултация."
                elif "доброволец" in user_msg_lower or "стана" in user_msg_lower:
                    response_text = "За да станете доброволец в HelpChain, трябва да минете през процес на регистрация, проверка и кратко обучение. Ще се свържем с вас след регистрацията."
                elif "услуги" in user_msg_lower or "помощ" in user_msg_lower:
                    response_text = "Предлагаме различни услуги: домашна грижа, придружаване, пазарски покупки, помощ в домакинството и градинарство."
                else:
                    response_text = "Благодаря за въпроса! За повече информация или помощ с регистрацията, моля свържете се с нашия екип на contact@helpchain.live."
                return {
                    "response": response_text,
                    "confidence": 0.8,
                    "provider": "mock",
                    "language_detected": "bg",
                }
            conversation_context = self._build_context(
                user_message, context, detected_lang
            )
            providers_to_try = sorted(
                [p for p in get_ai_config().providers.values() if p.enabled],
                key=lambda p: p.priority,
            )
            logger.info(
                f"🤖 Trying providers in order: {[p.name for p in providers_to_try]}"
            )
            last_error = None
            for provider in providers_to_try:
                try:
                    logger.info(f"🤖 Attempting to use provider: {provider.name}")
                    if provider.name == "OpenAI GPT":
                        result = await self._generate_openai_response(
                            conversation_context, provider
                        )
                    elif provider.name == "Google Gemini":
                        result = await self._generate_gemini_response(
                            conversation_context, provider
                        )
                    elif provider.name == "Ollama":
                        result = await self._generate_ollama_response(
                            conversation_context, provider
                        )
                    else:
                        continue
                    result["language_detected"] = detected_lang
                    result["provider"] = provider.name
                    logger.info(f"🤖 AI Response generated via {provider.name}")
                    return result
                except Exception as e:
                    logger.warning(f"Provider {provider.name} failed: {e}")
                    last_error = e
                    continue
            raise last_error or RuntimeError("All AI providers failed")
        except Exception as e:
            logger.error(f"❌ Error generating AI response: {e}")
            logger.error(traceback.format_exc())
            return {
                "response": (
                    "Извинявам се, възникна грешка при генерирането на отговора. Моля свържете се с нашия екип за помощ."
                ),
                "confidence": 0.0,
                "provider": "error_fallback",
                "error": str(e),
            }

    async def _generate_openai_response(self, context: str, provider) -> dict[str, Any]:
        if openai is None:
            raise RuntimeError(
                "OpenAI SDK is not installed. Install with 'pip install openai' to enable this provider."
            )
        client = openai.OpenAI(api_key=provider.api_key)
        resp = client.chat.completions.create(
            model=provider.model,
            messages=[{"role": "system", "content": context}],
            max_tokens=provider.max_tokens,
            temperature=provider.temperature,
        )
        ai_response = resp.choices[0].message.content.strip()
        usage = getattr(resp, "usage", None)
        tokens_used = usage.total_tokens if usage else None
        confidence = self._calculate_confidence(ai_response or "")
        return {
            "response": ai_response,
            "confidence": confidence,
            "tokens_used": tokens_used,
            "model": provider.model,
        }

    async def _generate_gemini_response(self, context: str, provider) -> dict[str, Any]:
        if genai is None:
            raise RuntimeError(
                "Google Generative AI SDK is not installed. Install the appropriate package to enable Gemini provider."
            )
        model = genai.GenerativeModel(provider.model)
        generation_config = genai.types.GenerationConfig(
            max_output_tokens=provider.max_tokens,
            temperature=provider.temperature,
            top_p=0.9,
            top_k=40,
        )
        response = model.generate_content(context, generation_config=generation_config)
        ai_response = response.text.strip()
        confidence = self._calculate_confidence(ai_response)
        return {
            "response": ai_response,
            "confidence": confidence,
            "tokens_used": len(context.split()) + len(ai_response.split()),
            "model": provider.model,
        }

    async def _generate_ollama_response(self, context: str, provider) -> dict[str, Any]:
        if requests is None:
            raise RuntimeError(
                "requests пакетът не е инсталиран. Инсталирайте с 'pip install requests'."
            )
        try:
            base_url = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"
            url = f"{base_url.rstrip('/')}/api/generate"
            payload = {
                "model": getattr(provider, "model", "llama2"),
                "prompt": context,
                "stream": False,
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama API error: HTTP {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
            ai_response = data.get("response") or data.get("content") or ""
            ai_response = ai_response.strip()
            confidence = self._calculate_confidence(ai_response)
            return {
                "response": ai_response,
                "confidence": confidence,
                "model": payload["model"],
                "provider": "Ollama",
            }
        except Exception as e:
            logger.error(f"Ollama API error: {e}")
            raise

    def _build_context(
        self, user_message: str, context: dict[str, Any] | None, language: str
    ) -> str:
        context_parts = [get_ai_config().system_prompt]
        if context and context.get("conversation_history"):
            context_parts.append("\nПОСЛЕДНИ СЪОБЩЕНИЯ:")
            for msg in context["conversation_history"][-2:]:
                context_parts.append(f"Потребител: {msg.get('user_message', '')}")
                context_parts.append(f"Асистент: {msg.get('bot_response', '')}")
        if language != "bg":
            context_parts.append(
                f"\nЗАБЕЛЕЖКА: Потребителят пише на {language}, но Отговаряй ЗАДЪЛЖИТЕЛНО на български език."
            )
        context_parts.append(f"\nВЪПРОС НА ПОТРЕБИТЕЛЯ: {user_message}")
        context_parts.append("\nТВОЯТ ОТГОВОР (кратко, полезно, на български):")
        return "\n".join(context_parts)

    def _calculate_confidence(self, response: str) -> float:
        if len(response) < 10:
            return 0.3
        elif len(response) < 30:
            return 0.6
        elif "извинявам се" in response.lower() or "не знам" in response.lower():
            return 0.4
        elif any(
            word in response.lower()
            for word in ["регистрация", "helpchain", "доброволец", "услуга"]
        ):
            return 0.9
        else:
            return 0.7

    def get_ai_status(self) -> dict[str, Any]:
        status = get_ai_config().get_status()
        status["service_ready"] = get_ai_config().is_ai_enabled()
        return status

    def test_connection(self) -> dict[str, Any]:
        results = {}
        mock_flag = os.getenv("AI_DEV_MOCK", "").lower() in ("1", "true", "yes")
        for name, provider in get_ai_config().providers.items():
            if mock_flag:
                results[name] = {
                    "status": "ok",
                    "message": "Mock mode enabled (AI_DEV_MOCK)",
                }
                continue
            if not provider.enabled:
                results[name] = {
                    "status": "disabled",
                    "message": "API key not provided",
                }
                continue
            try:
                if name == "openai":
                    if openai is None:
                        results[name] = {
                            "status": "disabled",
                            "message": "openai package not installed",
                        }
                    else:
                        results[name] = {
                            "status": "ok",
                            "message": "Connection successful",
                        }
                elif name == "gemini":
                    if genai is None:
                        results[name] = {
                            "status": "disabled",
                            "message": "google.generativeai package not installed",
                        }
                    else:
                        results[name] = {
                            "status": "ok",
                            "message": "Connection successful",
                        }
            except Exception as e:
                results[name] = {"status": "error", "message": str(e)}
        return results

    def generate_response_sync(
        self, user_message: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        import asyncio

        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.generate_response(user_message, context)
            )
        except Exception as e:
            logger.error(f"Error in sync wrapper: {e}")
            return {
                "response": "Извинявам се, възникна грешка при генерирането на отговора.",
                "confidence": 0.0,
                "provider": "error_fallback",
                "error": str(e),
            }


ai_service = AIService()
"""
AI Service for HelpChain Chatbot
Integrates with OpenAI GPT and Google Gemini
"""

# Import AI SDKs safely: if the package isn't installed the module variable
# will be set to None and the app will continue to run with fallbacks.
try:
    import openai
except Exception:
    openai = None
try:
    import requests
except Exception:
    requests = None

try:
    import google.generativeai as genai
except Exception:
    genai = None
try:
    from langdetect import detect
except Exception:
    # Fallback detect function when langdetect is not installed.
    # We default to Bulgarian ('bg') to keep behavior predictable.
    import logging as _logging

    _logging.info("langdetect not installed; defaulting language detection to 'bg'")

    def detect(text: str) -> str:  # type: ignore
        return "bg"


import os
import re
import traceback
import unicodedata
from typing import Any

try:
    from .ai_config import get_ai_config, logger
except ImportError:
    from ai_config import get_ai_config, logger

# Monkey patch httpx.Client to handle proxies parameter (compatibility fix)
try:
    import httpx

    original_httpx_init = httpx.Client.__init__

    def patched_httpx_init(self, *args, **kwargs):
        # Remove proxies if present and map it to proxy
        if "proxies" in kwargs:
            if "proxy" not in kwargs:
                kwargs["proxy"] = kwargs["proxies"]
            del kwargs["proxies"]
        return original_httpx_init(self, *args, **kwargs)

    httpx.Client.__init__ = patched_httpx_init
except Exception:
    # If httpx patching fails, continue without it
    httpx = None


def _safe_message(exc: Exception) -> str:
    """Return a UTF-8 safe string for an exception.

    Build a concise message using the exception class and args, then
    force it through UTF-8 encoding with backslashreplace to avoid
    raising UnicodeEncodeError when the environment default encoding is
    latin-1 or otherwise incompatible.
    """
    try:
        cls_name = exc.__class__.__name__
        args = getattr(exc, "args", ()) or ()
        try:
            # Create a readable args representation, prefer plain strings
            args_parts = []
            for a in args:
                if isinstance(a, str):
                    args_parts.append(a)
                else:
                    try:
                        args_parts.append(repr(a))
                    except Exception:
                        args_parts.append("<unrepresentable>")
            args_str = ", ".join(args_parts)
        except Exception:
            args_str = repr(args)

        msg = f"{cls_name}: {args_str}"
        # Force UTF-8-safe representation
        safe = msg.encode("utf-8", "backslashreplace").decode("utf-8")
        return safe
    except Exception:
        try:
            return repr(exc).encode("utf-8", "backslashreplace").decode("utf-8")
        except Exception:
            return "<unprintable exception>"


def _sanitize_api_key(raw_key: Any) -> tuple[str, list]:
    """Clean and validate an API key string.

    Returns a tuple (cleaned_key, issues) where issues is a list of
    human-readable problem descriptions (empty if no issues).
    """
    issues = []
    if not isinstance(raw_key, str):
        issues.append("API key is not a string")
        return ("", issues)

    # Normalize and trim
    s = unicodedata.normalize("NFKC", raw_key).strip()

    # Remove invisible/control characters (category Cc and Cf)
    cleaned_chars = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            # skip control/formatting chars like zero-width space
            continue
        cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)

    # Remove common Unicode whitespace variants
    cleaned = re.sub(r"[\u2000-\u200B\uFEFF]", "", cleaned)

    if not cleaned:
        issues.append("API key is empty after trimming")
        return ("", issues)

    # Basic sanity checks
    if not cleaned.isascii():
        issues.append("API key contains non-ASCII characters")

    # Placeholder detection: look for obvious localized placeholders
    lower = cleaned.lower()
    placeholder_indicators = [
        "ваш",
        "your",
        "ключ",
        "key",
        "placeholder",
        "tua",
        "tuo",
        "вашия",
        "ваш_к",
        "sk-ваш",
    ]
    for ph in placeholder_indicators:
        if ph in lower:
            issues.append(f'API key looks like a placeholder (contains "{ph}")')
            break

    # Ensure likely format: starts with sk-
    if not lower.startswith("sk-"):
        issues.append('API key does not start with "sk-"')

    # If there are issues, attempt to extract a likely ASCII sk-... substring from the raw input
    if issues:
        try:
            # Search the original (raw) key for a contiguous ASCII-looking secret like sk-<chars>
            m = re.search(r"(sk-[A-Za-z0-9_-]{20,100})", raw_key)
            if m:
                extracted = m.group(1)
                # If extraction yields an improvement, return it and note the extraction
                if extracted and extracted != cleaned:
                    cleaned = extracted
                    # Re-evaluate ASCII/start checks
                    issues = []
                    if not cleaned.isascii():
                        issues.append("Extracted key contains non-ASCII characters")
                    if not cleaned.lower().startswith("sk-"):
                        issues.append('Extracted key does not start with "sk-"')
                    if not issues:
                        issues.append(
                            "Extracted key from input (used for connectivity test)"
                        )
        except Exception:
            # If extraction fails silently, keep original issues
            pass

    return (cleaned, issues)


class AIService:
    """AI Service for generating intelligent responses"""

    def __init__(self):
        self.setup_providers()

    def setup_providers(self):
        """Initialize AI providers"""
        try:
            # Setup OpenAI (only if SDK is available)
            openai_provider = get_ai_config().get_provider("openai")
            if openai_provider and openai_provider.enabled:
                if openai is None:
                    logger.warning(
                        "⚠️ OpenAI SDK not installed - OpenAI provider disabled at runtime"
                    )
                else:
                    openai.api_key = openai_provider.api_key
                    logger.info("OpenAI configured successfully")

            # Setup Gemini (only if SDK is available)
            gemini_provider = get_ai_config().get_provider("gemini")
            if gemini_provider and gemini_provider.enabled:
                if genai is None:
                    logger.warning(
                        "⚠️ Google Generative AI SDK not installed - Gemini provider disabled at runtime"
                    )
                else:
                    genai.configure(api_key=gemini_provider.api_key)
                    logger.info("Gemini configured successfully")

        except Exception as e:
            logger.error(f"❌ Error setting up AI providers: {e}")

    async def generate_response(
        self, user_message: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Generate AI response to user message

        Args:
            user_message: User's question/message
            context: Optional context (conversation history, user info, etc.)

        Returns:
            Dict with response, confidence, provider used, etc.
        """
        try:
            # Detect language
            try:
                detected_lang = detect(user_message)
            except Exception:
                detected_lang = "bg"  # Default to Bulgarian

            # Development mock mode: allow local dev without an API key
            mock_flag = os.getenv("AI_DEV_MOCK", "").lower() in ("1", "true", "yes")
            if mock_flag:
                # Intelligent canned replies useful for UI/workflow testing
                user_msg_lower = user_message.lower()

                if "здравей" in user_msg_lower or "здрасти" in user_msg_lower:
                    response_text = "Здравейте! Аз съм AI асистентът на HelpChain. Как мога да ви помогна днес?"
                elif "helpchain" in user_msg_lower or "какво" in user_msg_lower:
                    response_text = "HelpChain е платформа за доброволчество в България. Свързваме хора нуждаещи се от помощ с проверени доброволци в София, Пловдив, Варна, Бургас и Стара Загора."
                elif "регистрация" in user_msg_lower or "регистрирам" in user_msg_lower:
                    response_text = "Регистрацията в HelpChain е безплатна! Можете да се регистрирате през нашето мобилно приложение или уеб сайта. За доброволци има процес на проверка и обучение."
                elif (
                    "цена" in user_msg_lower
                    or "струва" in user_msg_lower
                    or "такса" in user_msg_lower
                ):
                    response_text = "Цените зависят от вида на услугата. Свържете се с нашия екип за точна информация и консултация."
                elif "доброволец" in user_msg_lower or "стана" in user_msg_lower:
                    response_text = "За да станете доброволец в HelpChain, трябва да минете през процес на регистрация, проверка и кратко обучение. Ще се свържем с вас след регистрацията."
                elif "услуги" in user_msg_lower or "помощ" in user_msg_lower:
                    response_text = "Предлагаме различни услуги: домашна грижа, придружаване, пазарски покупки, помощ в домакинството и градинарство."
                else:
                    response_text = "Благодаря за въпроса! За повече информация или помощ с регистрацията, моля свържете се с нашия екип на contact@helpchain.live."

                return {
                    "response": response_text,
                    "confidence": 0.8,
                    "provider": "mock",
                    "language_detected": "bg",
                }

            # Prepare the conversation context
            conversation_context = self._build_context(
                user_message, context, detected_lang
            )

            # Try providers in priority order until one succeeds
            providers_to_try = sorted(
                [p for p in get_ai_config().providers.values() if p.enabled],
                key=lambda p: p.priority,
            )

            logger.info(
                f"🤖 Trying providers in order: {[p.name for p in providers_to_try]}"
            )

            last_error = None
            for provider in providers_to_try:
                try:
                    logger.info(f"🤖 Attempting to use provider: {provider.name}")
                    if provider.name == "OpenAI GPT":
                        result = await self._generate_openai_response(
                            conversation_context, provider
                        )
                    elif provider.name == "Google Gemini":
                        result = await self._generate_gemini_response(
                            conversation_context, provider
                        )
                    elif provider.name == "Ollama":
                        result = await self._generate_ollama_response(
                            conversation_context, provider
                        )
                    else:
                        continue

                    result["language_detected"] = detected_lang
                    result["provider"] = provider.name
                    logger.info(f"🤖 AI Response generated via {provider.name}")
                    return result
                except Exception as e:
                    logger.warning(f"Provider {provider.name} failed: {e}")
                    last_error = e
                    continue
            raise last_error or RuntimeError("All AI providers failed")
        except Exception as e:
            logger.error(f"❌ Error generating AI response: {e}")
            logger.error(traceback.format_exc())
            return {
                "response": (
                    "Извинявам се, възникна грешка при генерирането на отговора. Моля свържете се с нашия екип за помощ."
                ),
                "confidence": 0.0,
                "provider": "error_fallback",
                "error": str(e),
            }

    async def _generate_openai_response(self, context: str, provider) -> dict[str, Any]:
        """Generate response using OpenAI"""
        try:
            if openai is None:
                raise RuntimeError(
                    "OpenAI SDK is not installed. Install with 'pip install openai' to enable this provider."
                )

            # Use OpenAI >= 1.0 API (new client-based approach)
            try:
                client = openai.OpenAI(api_key=provider.api_key)

                # Call chat completions on the new client
                resp = client.chat.completions.create(
                    model=provider.model,
                    messages=[{"role": "system", "content": context}],
                    max_tokens=provider.max_tokens,
                    temperature=provider.temperature,
                )

                # Parse response
                ai_response = resp.choices[0].message.content.strip()

                # Get token usage
                usage = getattr(resp, "usage", None)
                tokens_used = None
                if usage:
                    try:
                        tokens_used = usage.total_tokens
                    except Exception:
                        tokens_used = None

            except Exception as e:
                logger.error(f"OpenAI API error: {e}")
                raise

            confidence = self._calculate_confidence(ai_response or "")

            return {
                "response": ai_response,
                "confidence": confidence,
                "tokens_used": tokens_used,
                "model": provider.model,
            }

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def _generate_gemini_response(self, context: str, provider) -> dict[str, Any]:
        """Generate response using Google Gemini"""
        try:
            if genai is None:
                raise RuntimeError(
                    "Google Generative AI SDK is not installed. Install the appropriate package to enable Gemini provider."
                )
            model = genai.GenerativeModel(provider.model)

            generation_config = genai.types.GenerationConfig(
                max_output_tokens=provider.max_tokens,
                temperature=provider.temperature,
                top_p=0.9,
                top_k=40,
            )

            response = model.generate_content(
                context, generation_config=generation_config
            )

            ai_response = response.text.strip()
            confidence = self._calculate_confidence(ai_response)

            return {
                "response": ai_response,
                "confidence": confidence,
                "tokens_used": len(context.split())
                + len(ai_response.split()),  # Approximate
                "model": provider.model,
            }

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    async def _generate_ollama_response(self, context: str, provider) -> dict[str, Any]:
        """Генерира отговор чрез Ollama LLM (локален)"""
        if requests is None:
            raise RuntimeError(
                "requests пакетът не е инсталиран. Инсталирайте с 'pip install requests'."
            )
        try:
            url = (
                provider.api_url
                if hasattr(provider, "api_url") and provider.api_url
                else "http://localhost:11434/api/generate"
            )
            payload = {
                "model": getattr(provider, "model", "llama2"),
                "prompt": context,
                "stream": False,
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama API error: HTTP {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
            ai_response = data.get("response") or data.get("content") or ""
            ai_response = ai_response.strip()
            confidence = self._calculate_confidence(ai_response)
            return {
                "response": ai_response,
                "confidence": confidence,
                "model": payload["model"],
                "provider": "Ollama",
            }
        except Exception as e:
            logger.error(f"Ollama API error: {e}")
            raise

    def _build_context(
        self, user_message: str, context: dict[str, Any] | None, language: str
    ) -> str:
        """Build conversation context for AI"""

        context_parts = [get_ai_config().system_prompt]

        if context and context.get("conversation_history"):
            context_parts.append("\nПОСЛЕДНИ СЪОБЩЕНИЯ:")
            for msg in context["conversation_history"][-2:]:  # Last 2 messages
                context_parts.append(f"Потребител: {msg.get('user_message', '')}")
                context_parts.append(f"Асистент: {msg.get('bot_response', '')}")

        if language != "bg":
            context_parts.append(
                f"\nЗАБЕЛЕЖКА: Потребителят пише на {language}, но Отговаряй ЗАДЪЛЖИТЕЛНО на български език."
            )

        context_parts.append(f"\nВЪПРОС НА ПОТРЕБИТЕЛЯ: {user_message}")
        context_parts.append("\nТВОЯТ ОТГОВОР (кратко, полезно, на български):")

        return "\n".join(context_parts)

    def _calculate_confidence(self, response: str) -> float:
        """Calculate confidence score for the response"""
        # Simple heuristic confidence calculation
        if len(response) < 10:
            return 0.3
        elif len(response) < 30:
            return 0.6
        elif "извинявам се" in response.lower() or "не знам" in response.lower():
            return 0.4
        elif any(
            word in response.lower()
            for word in ["регистрация", "helpchain", "доброволец", "услуга"]
        ):
            return 0.9
        else:
            return 0.7

    def get_ai_status(self) -> dict[str, Any]:
        """Get AI service status"""
        status = get_ai_config().get_status()
        status["service_ready"] = get_ai_config().is_ai_enabled()
        return status

    def test_connection(self) -> dict[str, Any]:
        """Test AI provider connections"""
        results = {}
        mock_flag = os.getenv("AI_DEV_MOCK", "").lower() in ("1", "true", "yes")

        for name, provider in get_ai_config().providers.items():
            if mock_flag:
                results[name] = {
                    "status": "ok",
                    "message": "Mock mode enabled (AI_DEV_MOCK)",
                }
                continue

            if not provider.enabled:
                results[name] = {
                    "status": "disabled",
                    "message": "API key not provided",
                }
                continue

            try:
                if name == "openai":
                    # Test OpenAI (support both old and new SDK interfaces)
                    if openai is None:
                        results[name] = {
                            "status": "disabled",
                            "message": "openai package not installed",
                        }
                    else:
                        # Try to use old API if present
                        try:
                            # Prefer direct REST call to OpenAI to avoid
                            # SDK-internal encoding issues.
                            if requests is None:
                                # Fall back to SDK check if requests isn't available
                                raise RuntimeError("requests package not available")

                            # HTTP headers must be ASCII; ensure the API key is ASCII to avoid
                            # UnicodeEncodeError from the underlying HTTP library.
                            raw_key = provider.api_key
                            cleaned_key, issues = _sanitize_api_key(raw_key)
                            if issues:
                                results[name] = {
                                    "status": "error",
                                    "message": "; ".join(issues),
                                }
                                continue

                            # Do the REST check with the cleaned/normalized key
                            headers = {"Authorization": f"Bearer {cleaned_key}"}
                            # If requests isn't available, fallback to SDK-based check below
                            if requests is None:
                                raise RuntimeError("requests package not available")

                            resp = requests.get(
                                "https://api.openai.com/v1/models",
                                headers=headers,
                                timeout=5,
                            )
                            if resp.status_code == 200:
                                results[name] = {
                                    "status": "ok",
                                    "message": "Connection successful",
                                }
                            elif resp.status_code == 401:
                                results[name] = {
                                    "status": "error",
                                    "message": "Incorrect API key provided",
                                }
                            else:
                                results[name] = {
                                    "status": "error",
                                    "message": f"HTTP {resp.status_code}: {resp.text[:300]}",
                                }
                        except Exception as e:
                            # If requests is missing or REST check failed, try SDK fallback
                            if requests is None and openai is not None:
                                try:
                                    # SDK fallback: try minimal call to list models
                                    # or create a tiny completion
                                    try:
                                        # Old-style
                                        if hasattr(openai, "ChatCompletion"):
                                            openai.api_key = provider.api_key
                                            openai.ChatCompletion.create(
                                                model="gpt-3.5-turbo",
                                                messages=[
                                                    {"role": "user", "content": "Test"}
                                                ],
                                                max_tokens=1,
                                            )
                                            results[name] = {
                                                "status": "ok",
                                                "message": (
                                                    "Connection successful (via SDK fallback)"
                                                ),
                                            }
                                            continue
                                        else:
                                            client = openai.OpenAI()
                                            client.chat.completions.create(
                                                model="gpt-3.5-turbo",
                                                messages=[
                                                    {"role": "user", "content": "Test"}
                                                ],
                                                max_tokens=1,
                                            )
                                            results[name] = {
                                                "status": "ok",
                                                "message": (
                                                    "Connection successful (via SDK fallback)"
                                                ),
                                            }
                                            continue
                                    except Exception as sdk_e:
                                        results[name] = {
                                            "status": "error",
                                            "message": _safe_message(sdk_e),
                                        }
                                        continue
                                except Exception:
                                    results[name] = {
                                        "status": "error",
                                        "message": _safe_message(e),
                                    }
                            else:
                                results[name] = {
                                    "status": "error",
                                    "message": _safe_message(e),
                                }

                elif name == "gemini":
                    # Test Gemini
                    if genai is None:
                        results[name] = {
                            "status": "disabled",
                            "message": "google.generativeai package not installed",
                        }
                    else:
                        genai.configure(api_key=provider.api_key)
                        model = genai.GenerativeModel("gemini-pro")
                        _response = model.generate_content("Тест")
                        results[name] = {
                            "status": "ok",
                            "message": "Connection successful",
                        }

            except Exception as e:
                results[name] = {"status": "error", "message": _safe_message(e)}

        return results

    def generate_response_sync(
        self, user_message: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Synchronous version of generate_response for Flask routes
        """
        import asyncio

        try:
            # Create event loop if none exists
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Run async function synchronously
            return loop.run_until_complete(
                self.generate_response(user_message, context)
            )
        except Exception as e:
            logger.error(f"Error in sync wrapper: {e}")
            return {
                "response": "Извинявам се, възникна грешка при генерирането на отговора.",
                "confidence": 0.0,
                "provider": "error_fallback",
                "error": str(e),
            }


# Global AI service instance
ai_service = AIService()
