#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM fallback для извлечения полей из претензий через Ollama.
Используется только когда правила не нашли данные.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

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
        "plaintiff_address",
        "defendant_name", "defendant_inn", "defendant_ogrn",
        "defendant_address",
        "debt", "payment_terms", "postal_numbers", "postal_dates",
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
