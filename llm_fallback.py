#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM fallback для извлечения полей из претензий через Ollama.
Используется только когда правила не нашли данные.
"""

import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from validators import DataValidator

logger = logging.getLogger(__name__)


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def get_llm_config() -> Dict[str, Any]:
    """
    Загружает конфигурацию LLM из переменных окружения.
    """
    load_dotenv()
    base_url = (
        _get_env("OLLAMA_BASE_URL")
        or _get_env("OLLAMA_HOST")
        or ""
    ).strip()
    enabled_raw = os.getenv("LLM_ENABLED")
    if enabled_raw is None:
        enabled = bool(base_url)
    else:
        enabled = enabled_raw.lower() in ("1", "true", "yes", "on")
    model = _get_env("OLLAMA_MODEL", "qwen2.5:7b-instruct").strip()
    timeout = int(_get_env("OLLAMA_TIMEOUT", "60"))  # Увеличен для 14b модели
    max_chars = int(_get_env("OLLAMA_MAX_CHARS", "12000"))
    return {
        "enabled": enabled,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout": timeout,
        "max_chars": max_chars,
    }


def get_vision_config() -> Dict[str, Any]:
    """
    Загружает конфигурацию Vision LLM из переменных окружения.
    """
    load_dotenv()
    base_url = (
        _get_env("OLLAMA_BASE_URL")
        or _get_env("OLLAMA_HOST")
        or ""
    ).strip()
    enabled_raw = os.getenv("LLM_VISION_ENABLED")
    if enabled_raw is None:
        enabled_raw = os.getenv("LLM_ENABLED")
    if enabled_raw is None:
        enabled = bool(base_url)
    else:
        enabled = enabled_raw.lower() in ("1", "true", "yes", "on")
    model = _get_env("OLLAMA_VISION_MODEL", "").strip()
    timeout = int(_get_env("OLLAMA_VISION_TIMEOUT", "120"))
    max_pages = int(_get_env("OLLAMA_VISION_MAX_PAGES", "5"))
    return {
        "enabled": enabled and bool(model),
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout": timeout,
        "max_pages": max_pages,
    }


def check_ollama_health(config: Dict[str, Any]) -> bool:
    """
    Проверяет доступность Ollama API.
    """
    if not config["base_url"]:
        return False
    try:
        response = requests.get(
            f"{config['base_url']}/api/tags",
            timeout=5
        )
        return response.status_code == 200
    except Exception as exc:
        logger.warning(f"Ollama health check failed: {exc}")
        return False


def _build_prompt(text: str) -> str:
    schema = {
        "plaintiff_name": "string|null",
        "plaintiff_inn": "string|null",
        "plaintiff_kpp": "string|null",
        "plaintiff_ogrn": "string|null",
        "plaintiff_address": "string|null",
        "defendant_name": "string|null",
        "defendant_inn": "string|null",
        "defendant_kpp": "string|null",
        "defendant_ogrn": "string|null",
        "defendant_address": "string|null",
        "debt": "string|null",
        "payment_terms": "string|null",
        "payment_days": "string|null",
        "payment_due_date": "string|null",
        "postal_numbers": "array<string>",
        "postal_dates": "array<string>",
        "legal_fees": "string|null",
        "legal_contract_number": "string|null",
        "legal_contract_date": "string|null",
        "legal_payment_number": "string|null",
        "legal_payment_date": "string|null",
    }
    return (
        "Ты извлекаешь реквизиты из претензии.\n"
        "Верни ТОЛЬКО JSON без комментариев.\n"
        "Если значения нет, укажи null, для списков - пустой массив [].\n"
        "\n"
        "ВАЖНЫЕ ПРАВИЛА:\n"
        "- ИНН: 10 или 12 цифр (только цифры, без пробелов)\n"
        "- КПП: ровно 9 цифр\n"
        "- ОГРН: 13 цифр (ООО) или 15 цифр (ИП)\n"
        "- Трек-номера почты: 10-20 цифр\n"
        "- Даты: строго формат ДД.ММ.ГГГГ (например: 15.03.2024)\n"
        "- payment_days: количество дней (только цифры)\n"
        "- debt и legal_fees: суммы в рублях (цифры, можно с пробелами)\n"
        "\n"
        "Истец (plaintiff) - тот, кто направляет претензию.\n"
        "Ответчик (defendant) - тот, кому направлена претензия.\n"
        "\n"
        f"Схема: {json.dumps(schema, ensure_ascii=False)}\n"
        "\n"
        "Текст претензии:\n"
        f"{text}\n"
        "\n"
        "Верни JSON:\n"
    )


def _extract_json(payload: str) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    cleaned = payload.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
        cleaned = cleaned.rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def _protect_text_tokens(
    text: str,
    protected_values: Optional[List[str]] = None
) -> Tuple[str, Dict[str, str]]:
    if not text:
        return text, {}

    protected_values = [
        value for value in (protected_values or [])
        if value and value in text
    ]
    replacements: Dict[str, str] = {}
    counter = 1

    def stash(value: str) -> str:
        nonlocal counter
        key = f"@@PROTECT_{counter}@@"
        counter += 1
        replacements[key] = value
        return key

    safe_text = text
    for value in protected_values:
        if value in safe_text:
            safe_text = safe_text.replace(value, stash(value))

    patterns = [
        r'\b\d{1,2}\.\d{1,2}\.\d{4}\b',
        r'\b[А-ЯA-Z]{1,3}\d{6,}[A-ZА-Я]*\b',
        r'\b(?:ИНН|КПП|ОГРН|ОГРНИП|БИК|ОКПО|ОКАТО|ОКТМО|ОКВЭД)\s*\d+\b',
        r'№\s*[A-Za-zА-Яа-я0-9/\\-]+',
        r'\bст\.\s*\d+(?:\.\d+)?\b',
        r'\bп\.\s*\d+(?:\.\d+)?\b',
        r'\bч\.\s*\d+(?:\.\d+)?\b',
        r'\b\d[\d\s.,/-]{2,}\b',
    ]

    def protect_match(match: re.Match) -> str:
        value = match.group(0)
        return stash(value)

    for pattern in patterns:
        safe_text = re.sub(pattern, protect_match, safe_text)

    return safe_text, replacements


def _restore_text_tokens(text: str, replacements: Dict[str, str]) -> str:
    if not replacements:
        return text
    restored = text
    for key, value in replacements.items():
        restored = restored.replace(key, value)
    return restored


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in ("null", "none", "не указано"):
        return None
    return text


def _clean_digits(value: Any, allowed_lengths: Optional[List[int]] = None) -> Optional[str]:
    text = _clean_str(value)
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    if allowed_lengths and len(digits) not in allowed_lengths:
        return None
    return digits


def _strip_llm_answer_prefix(value: str) -> str:
    if not value:
        return value
    cleaned = value.strip()
    prefixes = (
        "ответ:",
        "ответ -",
        "ответ —",
        "исправленный текст:",
        "исправленный вариант:",
        "исправленный:",
        "корректировка:",
    )
    lowered = cleaned.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            lowered = cleaned.lower()
            break
    lines = cleaned.splitlines()
    if lines:
        first = lines[0].strip().lower().rstrip(":")
        if first == "ответ":
            cleaned = "\n".join(lines[1:]).strip()
    return cleaned


def _is_suspicious_proofread_output(cleaned: str, original: str) -> bool:
    if not cleaned:
        return True
    original_len = len(original or "")
    cleaned_len = len(cleaned)
    if original_len:
        if cleaned_len > original_len * 1.6 + 40:
            return True
        if cleaned_len < max(8, original_len * 0.5):
            return True
    markers = (
        "орфографических ошибок",
        "исправления внесены",
        "исправленный текст",
        "текст остается",
        "комментар",
        "предлагаю",
        "ответ:",
        "обратите внимание",
    )
    lower_cleaned = cleaned.lower()
    lower_original = (original or "").lower()
    for marker in markers:
        if marker in lower_cleaned and marker not in lower_original:
            return True
    return False


def proofread_text_with_llm(
    text: str,
    protected_values: Optional[List[str]] = None
) -> str:
    """
    Исправляет орфографию/склонения/регистр через LLM без изменения смысла.
    """
    if not text:
        return text

    enabled_raw = os.getenv("LLM_PROOFREAD_ENABLED")
    if enabled_raw is None or enabled_raw.lower() not in ("1", "true", "yes", "on"):
        return text

    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        return text

    if not check_ollama_health(config):
        logger.warning("Ollama not available for proofread")
        return text

    trimmed = text[: config["max_chars"]] if config["max_chars"] else text
    safe_text, replacements = _protect_text_tokens(trimmed, protected_values)

    prompt = (
        "Ты редактор русского юридического текста.\n"
        "Исправь орфографию, пунктуацию, регистр и склонения.\n"
        "Не меняй смысл, числа, даты, реквизиты, статьи закона.\n"
        "Маркеры вида @@PROTECT_1@@ не изменяй и не удаляй.\n"
        "Сохрани разбиение на строки.\n"
        "Верни только исправленный текст без комментариев.\n\n"
        f"Текст:\n{safe_text}\n\n"
        "Ответ:\n"
    )

    try:
        response = _call_ollama(prompt, config)
    except Exception as exc:
        logger.warning("LLM proofread failed: %s", exc)
        return text

    if not response:
        return text

    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()

    cleaned = _strip_llm_answer_prefix(cleaned)
    if replacements:
        missing = [token for token in replacements if token not in cleaned]
        if missing:
            return text

    if _is_suspicious_proofread_output(cleaned, text):
        return text

    cleaned = _restore_text_tokens(cleaned, replacements)
    return cleaned.strip() or text


def _clean_date(value: Any) -> Optional[str]:
    text = _clean_str(value)
    if not text:
        return None
    match = re.search(r"\d{2}\.\d{2}\.\d{4}", text)
    if not match:
        return None
    date_str = match.group(0)
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return None
    return date_str


def _clean_amount(value: Any) -> Optional[str]:
    text = _clean_str(value)
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None
    try:
        amount = float(cleaned.replace(",", "."))
    except ValueError:
        return None
    return f"{amount:,.0f}".replace(",", " ")


def _clean_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, list):
        items = values
    else:
        items = [values]
    cleaned = []
    for item in items:
        text = _clean_str(item)
        if text:
            cleaned.append(text)
    return cleaned


def _normalize_text(value: str) -> str:
    text = value or ""
    text = text.replace("\u00A0", " ")
    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[\"'`«»„“”]", "", text)
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _text_contains(value: str, text: str) -> bool:
    if not value or not text:
        return False
    return _normalize_text(value) in _normalize_text(text)


def _digits_in_text(value: str, text: str) -> bool:
    if not value or not text:
        return False
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return False
    pattern = r"(?<!\d)" + r"[\s\u00A0]*".join(list(digits)) + r"(?!\d)"
    return re.search(pattern, text) is not None


def _date_in_text(value: str, text: str) -> bool:
    if not value or not text:
        return False
    try:
        datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return False
    return re.search(
        rf"(?<!\d){re.escape(value)}(?!\d)", text
    ) is not None


def _amount_in_text(value: str, text: str) -> bool:
    if not value or not text:
        return False
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return False
    pattern = r"(?<!\d)" + r"[\s\u00A0]*".join(list(digits)) + r"(?!\d)"
    return re.search(pattern, text) is not None


def _payment_days_in_text(value: str, text: str) -> bool:
    if not value or not text:
        return False
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return False
    pattern = (
        rf"(?<!\d){digits}(?!\d)\s*"
        r"(?:раб|рабоч|б/д|дн|дней|дня|банков)"
    )
    return re.search(pattern, text, re.IGNORECASE) is not None


def _filter_llm_data_by_text(text: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not text:
        return {}

    filtered: Dict[str, Any] = {}

    digit_fields = {
        "plaintiff_inn",
        "plaintiff_kpp",
        "plaintiff_ogrn",
        "defendant_inn",
        "defendant_kpp",
        "defendant_ogrn",
    }
    amount_fields = {"debt", "legal_fees"}
    date_fields = {
        "payment_due_date",
        "legal_contract_date",
        "legal_payment_date",
    }

    for key, value in data.items():
        if value is None:
            continue
        if key in ("postal_numbers", "postal_dates"):
            values = value if isinstance(value, list) else [value]
            if key == "postal_numbers":
                items = [
                    item for item in values
                    if _digits_in_text(str(item), text)
                ]
            else:
                items = [
                    item for item in values
                    if _date_in_text(str(item), text)
                ]
            if items:
                filtered[key] = items
            continue

        if key == "payment_days":
            if _payment_days_in_text(str(value), text):
                filtered[key] = value
            continue

        if key in digit_fields:
            if _digits_in_text(str(value), text):
                filtered[key] = value
            continue

        if key in amount_fields:
            if _amount_in_text(str(value), text):
                filtered[key] = value
            continue

        if key in date_fields:
            if _date_in_text(str(value), text):
                filtered[key] = value
            continue

        if _text_contains(str(value), text):
            filtered[key] = value

    return filtered


def _sanitize_llm_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    validator = DataValidator()
    data: Dict[str, Any] = {}

    plaintiff_inn = _clean_digits(raw.get("plaintiff_inn"))
    if plaintiff_inn and validator.validate_inn(plaintiff_inn).is_valid():
        data["plaintiff_inn"] = plaintiff_inn

    defendant_inn = _clean_digits(raw.get("defendant_inn"))
    if defendant_inn and validator.validate_inn(defendant_inn).is_valid():
        data["defendant_inn"] = defendant_inn

    plaintiff_kpp = _clean_digits(raw.get("plaintiff_kpp"), [9])
    if plaintiff_kpp and validator.validate_kpp(plaintiff_kpp).is_valid():
        data["plaintiff_kpp"] = plaintiff_kpp

    defendant_kpp = _clean_digits(raw.get("defendant_kpp"), [9])
    if defendant_kpp and validator.validate_kpp(defendant_kpp).is_valid():
        data["defendant_kpp"] = defendant_kpp

    plaintiff_ogrn = _clean_digits(raw.get("plaintiff_ogrn"), [13, 15])
    if plaintiff_ogrn and validator.validate_ogrn(plaintiff_ogrn).is_valid():
        data["plaintiff_ogrn"] = plaintiff_ogrn

    defendant_ogrn = _clean_digits(raw.get("defendant_ogrn"), [13, 15])
    if defendant_ogrn and validator.validate_ogrn(defendant_ogrn).is_valid():
        data["defendant_ogrn"] = defendant_ogrn

    for key in [
        "plaintiff_name",
        "plaintiff_address",
        "defendant_name",
        "defendant_address",
        "payment_terms",
        "legal_contract_number",
        "legal_payment_number",
    ]:
        value = _clean_str(raw.get(key))
        if value:
            data[key] = value

    for date_key in ["payment_due_date", "legal_contract_date", "legal_payment_date"]:
        date_val = _clean_date(raw.get(date_key))
        if date_val:
            data[date_key] = date_val

    payment_days = _clean_digits(raw.get("payment_days"))
    if payment_days:
        data["payment_days"] = payment_days

    debt = _clean_amount(raw.get("debt"))
    if debt:
        data["debt"] = debt

    legal_fees = _clean_amount(raw.get("legal_fees"))
    if legal_fees:
        data["legal_fees"] = legal_fees

    postal_numbers = [
        num for num in _clean_list(raw.get("postal_numbers"))
        if _clean_digits(num, list(range(10, 21))) is not None
    ]
    postal_dates = [
        date for date in _clean_list(raw.get("postal_dates"))
        if _clean_date(date)
    ]
    if postal_numbers:
        data["postal_numbers"] = [
            _clean_digits(num) for num in postal_numbers if _clean_digits(num)
        ]
    if postal_dates:
        data["postal_dates"] = [
            _clean_date(date) for date in postal_dates if _clean_date(date)
        ]

    return data


def _call_ollama(
    prompt: str, config: Dict[str, Any], max_retries: int = 3
) -> Optional[str]:
    """
    Вызывает Ollama API с retry-логикой для устойчивости.
    """
    if not config["base_url"] or not config["model"]:
        return None

    url = f"{config['base_url']}/api/generate"
    payload = {
        "model": config["model"],
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url, json=payload, timeout=config["timeout"]
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response")
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"LLM request failed (attempt {attempt + 1}/{max_retries}), "
                    f"retrying in {wait_time}s: {exc}"
                )
                time.sleep(wait_time)
            else:
                logger.error(f"LLM request failed after {max_retries} attempts")
                raise last_error
        except requests.exceptions.RequestException as exc:
            # Don't retry on other errors (4xx, 5xx status codes)
            logger.error(f"LLM request error: {exc}")
            raise exc

    return None


def _call_ollama_vision(
    prompt: str,
    image_b64: str,
    config: Dict[str, Any],
    max_retries: int = 2
) -> Optional[str]:
    """
    Вызывает Ollama API для vision-модели.
    """
    if not config.get("base_url") or not config.get("model"):
        return None

    url = f"{config['base_url']}/api/generate"
    payload = {
        "model": config["model"],
        "prompt": prompt,
        "stream": False,
        "images": [image_b64],
        "options": {
            "temperature": 0,
        },
    }

    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url, json=payload, timeout=config["timeout"]
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response")
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(
                    f"Vision request failed (attempt {attempt + 1}/{max_retries}), "
                    f"retrying in {wait_time}s: {exc}"
                )
                time.sleep(wait_time)
            else:
                logger.error("Vision request failed after retries")
                raise last_error
        except requests.exceptions.RequestException as exc:
            logger.error(f"Vision request error: {exc}")
            raise exc

    return None


def apply_llm_fallback(text: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Применяет LLM fallback только для незаполненных полей.
    """
    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        logger.debug("LLM fallback disabled or no base_url configured")
        return data

    if not check_ollama_health(config):
        logger.warning(
            f"Ollama API not available at {config['base_url']}, "
            "skipping LLM fallback"
        )
        return data

    missing_fields = [
        "plaintiff_name", "plaintiff_inn", "plaintiff_ogrn",
        "plaintiff_address", "plaintiff_kpp",
        "defendant_name", "defendant_inn", "defendant_ogrn",
        "defendant_address", "defendant_kpp",
        "debt", "payment_terms", "payment_days", "payment_due_date",
        "postal_numbers", "postal_dates",
        "legal_fees", "legal_contract_number", "legal_contract_date",
        "legal_payment_number", "legal_payment_date",
    ]

    def is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, list):
            return len(value) == 0
        text = str(value).strip()
        return not text or text == "Не указано"

    if not any(is_missing(data.get(field)) for field in missing_fields):
        return data

    trimmed = text[: config["max_chars"]] if config["max_chars"] else text
    prompt = _build_prompt(trimmed)
    try:
        raw_response = _call_ollama(prompt, config)
        logger.info(f"LLM response received: {len(raw_response or '')} chars")
    except Exception as exc:
        logger.warning("LLM fallback failed: %s", exc)
        return data

    parsed = _extract_json(raw_response or "")
    if not parsed:
        logger.warning("LLM fallback returned no JSON")
        logger.debug(f"Raw response: {raw_response[:500] if raw_response else 'None'}")
        return data

    logger.info(f"LLM extracted fields: {list(parsed.keys())}")
    llm_data = _sanitize_llm_data(parsed)
    if not llm_data:
        logger.warning("LLM data failed validation, all fields rejected")
        return data
    logger.info(f"After validation: {list(llm_data.keys())}")

    llm_data = _filter_llm_data_by_text(text, llm_data)
    if not llm_data:
        logger.warning("LLM data not found in source text, all fields rejected")
        return data
    logger.info(f"After text verification: {list(llm_data.keys())}")

    merged = data.copy()
    for key, value in llm_data.items():
        if is_missing(merged.get(key)):
            merged[key] = value
    return merged


# =============================================================================
# LLM Fallback для транспортных данных (из накладных, заявок)
# =============================================================================

def _build_transport_prompt(text: str) -> str:
    """
    Строит промпт для извлечения транспортных данных из страницы документа.
    """
    schema = {
        "driver_name": "string|null - ФИО водителя",
        "vehicle_plate": "string|null - госномер транспортного средства/тягача",
        "trailer_plate": "string|null - госномер прицепа/полуприцепа",
        "load_date": "string|null - дата погрузки в формате ДД.ММ.ГГГГ",
        "unload_date": "string|null - дата разгрузки в формате ДД.ММ.ГГГГ",
        "load_address": "string|null - адрес/место погрузки",
        "unload_address": "string|null - адрес/место разгрузки",
        "sender_name": "string|null - наименование грузоотправителя",
        "receiver_name": "string|null - наименование грузополучателя",
    }
    return (
        "Извлеки транспортные данные из документа (заявка на перевозку, "
        "транспортная накладная, ТТН, УПД).\n"
        "Верни ТОЛЬКО JSON без комментариев.\n"
        "Если значения нет, укажи null.\n"
        "\n"
        "ВАЖНЫЕ ПРАВИЛА:\n"
        "- Госномера: русские буквы и цифры, например: А123БВ77, ЕТ9226 77\n"
        "- Даты: строго формат ДД.ММ.ГГГГ (например: 15.11.2025)\n"
        "- ФИО водителя: полностью Фамилия Имя Отчество\n"
        "- Адреса: полный адрес с городом\n"
        "- Грузоотправитель/получатель: название организации или ИП\n"
        "\n"
        "ВНИМАНИЕ: В таблицах данные могут быть в разных ячейках.\n"
        "Ищи:\n"
        "- Водитель/Ф.И.О. водителя - это driver_name\n"
        "- Гос.номер/ТС/Тягач/Автомобиль - это vehicle_plate\n"
        "- Прицеп/Полуприцеп - это trailer_plate\n"
        "- Погрузка/Время начала (первая дата) - это load_date\n"
        "- Разгрузка/Время окончания (вторая дата) - это unload_date\n"
        "- Грузоотправитель/Отправитель - это sender_name\n"
        "- Грузополучатель/Получатель - это receiver_name\n"
        "- Адрес погрузки/Место погрузки - это load_address\n"
        "- Адрес разгрузки/Место разгрузки - это unload_address\n"
        "\n"
        f"Схема: {json.dumps(schema, ensure_ascii=False)}\n"
        "\n"
        "Текст документа:\n"
        f"{text}\n"
        "\n"
        "Верни JSON:\n"
    )


def _validate_vehicle_plate(plate: str) -> bool:
    """
    Проверяет формат госномера РФ.
    Допустимые буквы: АВЕКМНОРСТУХ (русские, соответствуют латинским).
    """
    if not plate:
        return False
    # Убираем пробелы и приводим к верхнему регистру
    clean = plate.upper().replace(' ', '').replace('-', '')
    # Стандартный формат: А123БВ77 или А123БВ777
    pattern = r'^[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}$'
    if re.match(pattern, clean):
        return True
    # Формат прицепа: АБ1234 77 или АБ123477
    trailer_pattern = r'^[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{4}\d{2,3}$'
    if re.match(trailer_pattern, clean):
        return True
    # Более мягкий формат (буквы + цифры, минимум 6 символов)
    if len(clean) >= 6 and re.match(r'^[А-ЯA-Z\d]+$', clean):
        return True
    return False


def _sanitize_transport_data(data: Dict[str, Any], source_text: str) -> Dict[str, Any]:
    """
    Валидирует и очищает транспортные данные от LLM.
    """
    result = {}
    source_lower = source_text.lower()

    # Водитель
    driver = _clean_str(data.get("driver_name"))
    if driver:
        # Проверяем что похоже на ФИО (минимум 2 слова)
        words = driver.split()
        if len(words) >= 2:
            # Проверяем наличие в тексте (хотя бы фамилии)
            if words[0].lower() in source_lower:
                result["driver_name"] = driver

    # Госномер ТС
    vehicle = _clean_str(data.get("vehicle_plate"))
    if vehicle:
        # Нормализуем и проверяем
        vehicle_clean = vehicle.upper().replace(' ', '').replace('-', '')
        if _validate_vehicle_plate(vehicle_clean):
            result["vehicle_plate"] = vehicle
        elif len(vehicle_clean) >= 5:
            # Если не прошел строгую валидацию, но есть в тексте
            if vehicle_clean[:4].lower() in source_lower.replace(' ', ''):
                result["vehicle_plate"] = vehicle

    # Госномер прицепа
    trailer = _clean_str(data.get("trailer_plate"))
    if trailer:
        trailer_clean = trailer.upper().replace(' ', '').replace('-', '')
        if _validate_vehicle_plate(trailer_clean):
            result["trailer_plate"] = trailer
        elif len(trailer_clean) >= 5:
            if trailer_clean[:4].lower() in source_lower.replace(' ', ''):
                result["trailer_plate"] = trailer

    # Даты
    date_pattern = re.compile(r'\d{2}[./]\d{2}[./]\d{4}')
    normalized_source = source_text.replace('/', '.')

    load_date = _clean_str(data.get("load_date"))
    if load_date and date_pattern.match(load_date):
        normalized_load = load_date.replace('/', '.')
        if normalized_load in normalized_source:
            result["load_date"] = normalized_load

    unload_date = _clean_str(data.get("unload_date"))
    if unload_date and date_pattern.match(unload_date):
        normalized_unload = unload_date.replace('/', '.')
        if normalized_unload in normalized_source:
            result["unload_date"] = normalized_unload

    # Адреса (менее строгая проверка)
    load_addr = _clean_str(data.get("load_address"))
    if load_addr and len(load_addr) > 10:
        # Проверяем наличие ключевых слов адреса в тексте
        addr_words = [w for w in load_addr.lower().split() if len(w) > 3]
        matches = sum(1 for w in addr_words if w in source_lower)
        if matches >= 2:
            result["load_address"] = load_addr

    unload_addr = _clean_str(data.get("unload_address"))
    if unload_addr and len(unload_addr) > 10:
        addr_words = [w for w in unload_addr.lower().split() if len(w) > 3]
        matches = sum(1 for w in addr_words if w in source_lower)
        if matches >= 2:
            result["unload_address"] = unload_addr

    # Грузоотправитель/получатель
    sender = _clean_str(data.get("sender_name"))
    if sender and len(sender) > 3:
        # Проверяем наличие в тексте
        sender_key = sender.split()[0].lower() if sender.split() else ""
        if sender_key and sender_key in source_lower:
            result["sender_name"] = sender

    receiver = _clean_str(data.get("receiver_name"))
    if receiver and len(receiver) > 3:
        receiver_key = receiver.split()[0].lower() if receiver.split() else ""
        if receiver_key and receiver_key in source_lower:
            result["receiver_name"] = receiver

    return result


def extract_transport_details_llm(
    page_text: str,
    existing_details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Извлекает транспортные данные из страницы документа с помощью LLM.
    Используется как fallback когда regex не справился.

    Args:
        page_text: Текст страницы документа
        existing_details: Уже извлеченные данные (не будут перезаписаны)

    Returns:
        Словарь с транспортными данными
    """
    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        logger.debug("LLM transport fallback disabled")
        return existing_details or {}

    existing = existing_details or {}

    # Проверяем какие поля отсутствуют
    transport_fields = [
        "driver_name", "vehicle_plate", "trailer_plate",
        "load_date", "unload_date",
        "load_address", "unload_address",
        "sender_name", "receiver_name"
    ]

    def is_missing(key: str) -> bool:
        val = existing.get(key)
        return val is None or val == "" or val == "Не указано"

    missing = [f for f in transport_fields if is_missing(f)]
    if not missing:
        logger.debug("All transport fields already filled, skipping LLM")
        return existing

    if not check_ollama_health(config):
        logger.warning("Ollama not available for transport extraction")
        return existing

    # Ограничиваем текст
    max_chars = min(config.get("max_chars", 8000), 8000)
    trimmed = page_text[:max_chars] if len(page_text) > max_chars else page_text

    prompt = _build_transport_prompt(trimmed)
    try:
        raw_response = _call_ollama(prompt, config)
        logger.info(f"LLM transport response: {len(raw_response or '')} chars")
    except Exception as exc:
        logger.warning(f"LLM transport extraction failed: {exc}")
        return existing

    parsed = _extract_json(raw_response or "")
    if not parsed:
        logger.warning("LLM transport returned no JSON")
        return existing

    logger.info(f"LLM transport extracted: {list(parsed.keys())}")

    # Валидируем данные
    sanitized = _sanitize_transport_data(parsed, page_text)
    if not sanitized:
        logger.warning("All LLM transport data failed validation")
        return existing

    logger.info(f"LLM transport validated: {list(sanitized.keys())}")

    # Мержим только отсутствующие поля
    result = existing.copy()
    for key, value in sanitized.items():
        if is_missing(key):
            result[key] = value
            logger.debug(f"LLM filled transport field: {key}={value}")

    return result


def _build_payment_terms_prompt(text: str) -> str:
    schema = {
        "payment_terms": "string|null",
        "payment_days": "integer|null",
    }
    return (
        "Ты извлекаешь условия оплаты из текста заявки/документа.\n"
        "Верни ТОЛЬКО JSON без комментариев.\n"
        "Если условий оплаты нет, верни null.\n"
        "\n"
        "Правила:\n"
        "- payment_terms: точный фрагмент условий оплаты (можно несколько предложений).\n"
        "  Включай предоплату/аванс, сумму и условия остатка, если они есть.\n"
        "- payment_days: только если указан единый срок оплаты в днях.\n"
        "  Если сроков несколько или условия частичной оплаты — укажи null.\n"
        "\n"
        f"Схема: {json.dumps(schema, ensure_ascii=False)}\n"
        "\n"
        "Текст:\n"
        f"{text}\n"
        "\n"
        "Верни JSON:\n"
    )


def extract_payment_terms_llm(
    text: str
) -> Tuple[Optional[str], Optional[int]]:
    """
    Извлекает условия оплаты с помощью LLM, если regex не справился.
    """
    if not text:
        return None, None

    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        logger.debug("LLM payment terms disabled")
        return None, None

    if not check_ollama_health(config):
        logger.warning("Ollama not available for payment terms extraction")
        return None, None

    trimmed = text[: config["max_chars"]] if config["max_chars"] else text
    prompt = _build_payment_terms_prompt(trimmed)
    try:
        raw_response = _call_ollama(prompt, config)
        logger.info(
            "LLM payment terms response received: %s chars",
            len(raw_response or "")
        )
    except Exception as exc:
        logger.warning("LLM payment terms extraction failed: %s", exc)
        return None, None

    parsed = _extract_json(raw_response or "")
    if not parsed:
        logger.warning("LLM payment terms returned no JSON")
        return None, None

    terms = _clean_str(parsed.get("payment_terms"))
    days = None
    raw_days = parsed.get("payment_days")
    if raw_days is not None:
        digits = re.sub(r"[^\d]", "", str(raw_days))
        if digits:
            try:
                days = int(digits)
            except ValueError:
                days = None

    return terms, days


def match_cargo_to_application_llm(
    cargo: Dict[str, Any],
    applications: List[Dict[str, Any]]
) -> Tuple[str, float]:
    """
    Подбирает заявку к грузовому документу через LLM.
    Возвращает (label, confidence). Если нет уверенности - ("", 0.0).
    """
    if not cargo or not applications:
        return "", 0.0

    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        return "", 0.0

    if not check_ollama_health(config):
        logger.warning("Ollama not available for cargo matching")
        return "", 0.0

    def simplify_date(value: Any) -> str:
        if isinstance(value, datetime):
            return value.strftime("%d.%m.%Y")
        return str(value) if value else ""

    apps_payload = []
    for app in applications[:20]:
        apps_payload.append({
            "label": app.get("label"),
            "number": app.get("number"),
            "date": simplify_date(app.get("date")),
            "driver": app.get("driver_name"),
            "vehicle": app.get("vehicle_plate"),
            "trailer": app.get("trailer_plate"),
            "load_date": simplify_date(app.get("load_date")),
            "unload_date": simplify_date(app.get("unload_date")),
            "load_address": app.get("load_address"),
            "unload_address": app.get("unload_address"),
            "sender": app.get("sender_name"),
            "receiver": app.get("receiver_name"),
        })

    cargo_payload = {
        "label": cargo.get("label"),
        "doc_type": cargo.get("doc_type"),
        "number": cargo.get("number"),
        "date": simplify_date(cargo.get("date")),
        "driver": cargo.get("driver_name"),
        "vehicle": cargo.get("vehicle_plate"),
        "trailer": cargo.get("trailer_plate"),
        "load_date": simplify_date(cargo.get("load_date")),
        "unload_date": simplify_date(cargo.get("unload_date")),
        "load_address": cargo.get("load_address"),
        "unload_address": cargo.get("unload_address"),
        "sender": cargo.get("sender_name"),
        "receiver": cargo.get("receiver_name"),
    }

    schema = {
        "application": "string|null",
        "confidence": "number (0-1)",
        "reason": "string|null",
    }

    prompt = (
        "Ты сопоставляешь грузовой документ с заявкой на перевозку.\n"
        "Используй только данные о перевозке: водитель, ТС, прицеп, даты, адреса, отправитель, получатель.\n"
        "Если уверенности нет — верни null.\n"
        "Верни ТОЛЬКО JSON без комментариев.\n"
        f"Схема: {json.dumps(schema, ensure_ascii=False)}\n"
        f"Заявки: {json.dumps(apps_payload, ensure_ascii=False)}\n"
        f"Документ: {json.dumps(cargo_payload, ensure_ascii=False)}\n"
        "Верни JSON:\n"
    )

    try:
        raw_response = _call_ollama(prompt, config)
    except Exception as exc:
        logger.warning("LLM cargo matching failed: %s", exc)
        return "", 0.0

    parsed = _extract_json(raw_response or "")
    if not parsed:
        return "", 0.0

    label = _clean_str(parsed.get("application")) or ""
    confidence = parsed.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    valid_labels = {app.get("label") for app in apps_payload if app.get("label")}
    if label not in valid_labels:
        return "", 0.0

    return label, confidence_value


def extract_transport_details_vision(
    pdf_path: str,
    page_num: int,
    existing_details: Optional[Dict[str, Any]] = None,
    doc_type: str = "transport"
) -> Dict[str, Any]:
    """
    Извлекает транспортные данные из PDF страницы через Vision LLM.
    Использует pdf_extractor для конвертации и вызова Vision модели.

    Args:
        pdf_path: Путь к PDF файлу
        page_num: Номер страницы (0-indexed)
        existing_details: Уже извлеченные данные (не будут перезаписаны)
        doc_type: Тип документа ("transport" или "application")

    Returns:
        Словарь с транспортными данными
    """
    existing = existing_details or {}

    # Проверяем какие поля отсутствуют
    transport_fields = [
        "driver_name", "vehicle_plate", "trailer_plate",
        "load_date", "unload_date",
        "load_address", "unload_address",
        "sender_name", "receiver_name"
    ]

    def is_missing(key: str) -> bool:
        val = existing.get(key)
        return val is None or val == "" or val == "Не указано"

    missing = [f for f in transport_fields if is_missing(f)]
    if not missing:
        logger.debug("All transport fields already filled, skip Vision LLM")
        return existing

    # Проверяем конфигурацию Vision
    config = get_vision_config()
    if not config.get("enabled") or not config.get("model"):
        logger.debug("Vision LLM not configured, skipping")
        return existing

    try:
        from pdf_extractor import (
            extract_transport_document_with_vision,
            extract_application_with_vision,
        )

        if doc_type == "application":
            vision_data = extract_application_with_vision(pdf_path, page_num)
        else:
            vision_data = extract_transport_document_with_vision(
                pdf_path, page_num
            )

        if not vision_data:
            logger.debug("Vision LLM returned no data")
            return existing

        logger.info(f"Vision LLM extracted: {list(vision_data.keys())}")

        # Маппинг полей Vision → internal
        field_mapping = {
            "driver_name": "driver_name",
            "vehicle_plate": "vehicle_plate",
            "trailer_plate": "trailer_plate",
            "load_date": "load_date",
            "unload_date": "unload_date",
            "load_address": "load_address",
            "unload_address": "unload_address",
            "sender_name": "sender_name",
            "receiver_name": "receiver_name",
            "document_number": "waybill_number",
            "document_date": "waybill_date",
            "application_number": "application_number",
            "application_date": "application_date",
            "customer_name": "customer_name",
            "carrier_name": "carrier_name",
            "cargo_description": "cargo_description",
            "price": "amount",
            "payment_terms": "payment_terms",
        }

        # Мержим только отсутствующие поля
        result = existing.copy()
        for vision_key, internal_key in field_mapping.items():
            if vision_key in vision_data:
                value = vision_data[vision_key]
                if value and is_missing(internal_key):
                    result[internal_key] = value
                    logger.debug(
                        f"Vision filled field: {internal_key}={value}"
                    )

        return result

    except ImportError:
        logger.debug("pdf_extractor not available for Vision LLM")
        return existing
    except Exception as e:
        logger.warning(f"Vision LLM extraction error: {e}")
        return existing


def extract_text_from_image_llm(image_path: str) -> str:
    """
    OCR через vision-модель Ollama. Возвращает текст без форматирования.
    """
    config = get_vision_config()
    if not config.get("enabled"):
        return ""
    if not check_ollama_health(config):
        logger.warning("Ollama not available for vision OCR")
        return ""

    try:
        with open(image_path, "rb") as handle:
            image_b64 = base64.b64encode(handle.read()).decode("utf-8")
    except OSError as exc:
        logger.warning(f"Не удалось прочитать изображение для OCR: {exc}")
        return ""

    prompt = (
        "Извлеки весь читаемый текст с изображения. "
        "Верни только текст без комментариев. "
        "Сохраняй переносы строк, где они очевидны."
    )
    try:
        response = _call_ollama_vision(prompt, image_b64, config)
    except Exception as exc:
        logger.warning(f"Vision OCR failed: {exc}")
        return ""

    return (response or "").strip()


def _build_document_groups_prompt(text: str) -> str:
    schema = {
        "document_groups": [
            {
                "application": "string|null",
                "documents": ["string"],
            }
        ],
        "ungrouped_documents": ["string"],
    }
    return (
        "Ты анализируешь документы по перевозке и группируешь их по заявкам.\n"
        "Верни ТОЛЬКО JSON без комментариев.\n"
        "Если связать документы с заявкой нельзя, помести их в ungrouped_documents.\n"
        "Названия документов возвращай в виде готовых строк, например:\n"
        "- \"Заявка № 123 от 01.01.2025\"\n"
        "- \"Счет № 45 от 03.01.2025\"\n"
        "- \"Акт № 45 от 05.01.2025\"\n"
        "- \"Транспортная накладная № 789 от 06.01.2025\"\n"
        "\n"
        f"Схема: {json.dumps(schema, ensure_ascii=False)}\n"
        "\n"
        "Текст документов:\n"
        f"{text}\n"
        "\n"
        "Верни JSON:\n"
    )


def _sanitize_document_groups(payload: Dict[str, Any]) -> Dict[str, Any]:
    groups_raw = payload.get("document_groups") if isinstance(payload, dict) else None
    ungrouped_raw = payload.get("ungrouped_documents") if isinstance(payload, dict) else None

    groups_clean: List[Dict[str, Any]] = []
    if isinstance(groups_raw, list):
        for group in groups_raw:
            if not isinstance(group, dict):
                continue
            application = _clean_str(group.get("application"))
            documents = _clean_list(group.get("documents"))
            documents = [doc for doc in documents if doc]
            if not application and not documents:
                continue
            groups_clean.append({
                "application": application,
                "documents": documents,
            })

    ungrouped_clean = _clean_list(ungrouped_raw)
    ungrouped_clean = [doc for doc in ungrouped_clean if doc]

    return {
        "document_groups": groups_clean,
        "ungrouped_documents": ungrouped_clean,
    }


def extract_document_groups_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    Извлекает группы документов по заявкам через LLM.
    """
    config = get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        logger.debug("LLM document groups disabled or no base_url configured")
        return None

    if not check_ollama_health(config):
        logger.warning(
            f"Ollama API not available at {config['base_url']}, "
            "skipping document groups extraction"
        )
        return None

    trimmed = text[: config["max_chars"]] if config["max_chars"] else text
    prompt = _build_document_groups_prompt(trimmed)
    try:
        raw_response = _call_ollama(prompt, config)
        logger.info(
            "LLM document groups response received: %s chars",
            len(raw_response or "")
        )
    except Exception as exc:
        logger.warning("LLM document groups extraction failed: %s", exc)
        return None

    parsed = _extract_json(raw_response or "")
    if not parsed:
        logger.warning("LLM document groups returned no JSON")
        return None

    sanitized = _sanitize_document_groups(parsed)
    if not sanitized.get("document_groups") and not sanitized.get("ungrouped_documents"):
        logger.warning("LLM document groups returned empty result")
        return None

    return sanitized
