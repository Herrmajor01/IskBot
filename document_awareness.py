#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль "осознанности" документов.

Обнаруживает нетипичные случаи в документах:
- Частичные оплаты
- Гарантийные письма
- Акты сверки
- Письма о признании долга
- Претензии контрагента

И корректирует данные иска соответственно.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)


# =============================================================================
# Типы особых документов
# =============================================================================

@dataclass
class PartialPayment:
    """Частичная оплата."""
    amount: Decimal
    date: Optional[str] = None
    payment_number: Optional[str] = None  # Номер платёжного поручения
    payer: Optional[str] = None
    purpose: Optional[str] = None  # Назначение платежа
    source_page: Optional[int] = None


@dataclass
class GuaranteeLetter:
    """Гарантийное письмо."""
    date: Optional[str] = None
    number: Optional[str] = None
    promised_amount: Optional[Decimal] = None
    promised_date: Optional[str] = None  # Дата обещанной оплаты
    debtor_name: Optional[str] = None
    source_page: Optional[int] = None


@dataclass
class DebtAcknowledgment:
    """Акт сверки или письмо о признании долга."""
    date: Optional[str] = None
    acknowledged_amount: Optional[Decimal] = None
    document_type: str = "акт сверки"  # или "письмо о признании долга"
    parties: Optional[List[str]] = None
    source_page: Optional[int] = None


@dataclass
class CounterClaim:
    """Претензия от контрагента (встречная)."""
    date: Optional[str] = None
    number: Optional[str] = None
    amount: Optional[Decimal] = None
    claimant: Optional[str] = None
    source_page: Optional[int] = None


@dataclass
class DocumentAwarenessResult:
    """Результат анализа документов на особые случаи."""
    partial_payments: List[PartialPayment] = field(default_factory=list)
    guarantee_letters: List[GuaranteeLetter] = field(default_factory=list)
    debt_acknowledgments: List[DebtAcknowledgment] = field(default_factory=list)
    counter_claims: List[CounterClaim] = field(default_factory=list)

    # Итоговые данные
    total_partial_payments: Decimal = Decimal("0")
    adjusted_debt: Optional[Decimal] = None
    original_debt: Optional[Decimal] = None

    # Флаги для генерации текста
    has_partial_payments: bool = False
    has_guarantee_letters: bool = False
    has_debt_acknowledgment: bool = False
    has_counter_claims: bool = False

    # Предупреждения для пользователя
    warnings: List[str] = field(default_factory=list)


# =============================================================================
# Конфигурация LLM
# =============================================================================

def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def get_llm_config() -> Dict[str, Any]:
    """Загружает конфигурацию LLM из переменных окружения."""
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
    timeout = int(_get_env("OLLAMA_TIMEOUT", "60"))
    return {
        "enabled": enabled,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout": timeout,
    }


def _call_ollama(prompt: str, config: Dict[str, Any]) -> Optional[str]:
    """Вызывает Ollama API."""
    if not config["base_url"] or not config["model"]:
        return None

    url = f"{config['base_url']}/api/generate"
    payload = {
        "model": config["model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        response = requests.post(url, json=payload, timeout=config["timeout"])
        response.raise_for_status()
        return response.json().get("response")
    except Exception as e:
        logger.warning(f"LLM request failed: {e}")
        return None


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Извлекает JSON из ответа LLM."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
        cleaned = cleaned.rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


# =============================================================================
# Паттерны для обнаружения особых документов (regex)
# =============================================================================

# Частичная оплата
PARTIAL_PAYMENT_PATTERNS = [
    # Платёжные поручения
    r'платёжн\w*\s+поруч\w*\s*№?\s*(\d+)',
    r'п/п\s*№?\s*(\d+)',
    r'оплат[аы]\s+.*?(\d[\d\s]*[.,]?\d*)\s*руб',
    r'перечислен[оа]?\s+.*?(\d[\d\s]*[.,]?\d*)\s*руб',
    r'поступил[оа]?\s+.*?(\d[\d\s]*[.,]?\d*)\s*руб',
    # Банковские выписки
    r'выписк[аи]\s+.*?банк',
    r'списан[оа]?\s+со\s+счёт',
]

INVOICE_PAGE_MARKERS = [
    "счет на оплату",
    "счёт на оплату",
    "универсальн",
    "передаточн",
    "упд",
    "поставщик",
    "покупатель",
]

PAYMENT_ORDER_KEYWORDS = [
    "платежн",
    "платёжн",
    "п/п",
    "перечисл",
    "поступил",
    "списан",
    "выписк",
]

# Гарантийные письма
GUARANTEE_LETTER_PATTERNS = [
    r'гарантийн\w*\s+письм',
    r'гарантиру\w+\s+оплат',
    r'обязу\w+\s+оплатить',
    r'обязу\w+\s+погасить',
    r'обещ\w+\s+оплат',
]

# Акты сверки / признание долга
DEBT_ACKNOWLEDGMENT_PATTERNS = [
    r'акт\s+сверк[иа]',
    r'сверк[аи]\s+взаиморасчёт',
    r'признан\w*\s+долг',
    r'подтвержда\w+\s+задолженност',
    r'согласн[оы]\s+с\s+задолженност',
]

# Встречные претензии
COUNTER_CLAIM_PATTERNS = [
    r'встречн\w*\s+претенз',
    r'претензи[яю]\s+от\s+',
    r'требован\w+\s+.*?к\s+нам',
]


def _parse_amount(text: str) -> Optional[Decimal]:
    """Парсит сумму из текста."""
    if not text:
        return None
    # Убираем пробелы и заменяем запятую на точку
    cleaned = re.sub(r'\s+', '', str(text)).replace(',', '.')
    # Убираем "руб", "р" и прочее
    cleaned = re.sub(r'[а-яА-Яa-zA-Z\.]+$', '', cleaned)
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _parse_date(text: str) -> Optional[str]:
    """Парсит дату из текста в формат ДД.ММ.ГГГГ."""
    if not text:
        return None
    match = re.search(r'(\d{2})[./](\d{2})[./](\d{4})', text)
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return None


# =============================================================================
# Обнаружение особых документов с помощью regex
# =============================================================================

def detect_partial_payments_regex(text: str, page_num: int = 0) -> List[PartialPayment]:
    """Обнаруживает частичные оплаты с помощью regex."""
    payments = []
    text_lower = text.lower()

    if (
        any(marker in text_lower for marker in INVOICE_PAGE_MARKERS)
        and not any(keyword in text_lower for keyword in PAYMENT_ORDER_KEYWORDS)
    ):
        return payments

    has_payment_order_context = any(
        keyword in text_lower for keyword in PAYMENT_ORDER_KEYWORDS
    )
    has_sverka_context = "сверк" in text_lower or "акт сверки" in text_lower
    if not has_payment_order_context and not has_sverka_context:
        return payments

    # Проверяем наличие признаков оплаты
    has_payment_signs = any(
        re.search(p, text_lower) for p in PARTIAL_PAYMENT_PATTERNS
    )
    if not has_payment_signs:
        return payments

    # Ищем платёжные поручения
    pp_pattern = r'(?:платёжн\w*\s+поруч\w*|п/п)\s*№?\s*(\d+)(?:\s+от\s+(\d{2}[./]\d{2}[./]\d{4}))?'
    for match in re.finditer(pp_pattern, text_lower):
        payment_number = match.group(1)
        payment_date = _parse_date(match.group(2)) if match.group(2) else None

        # Ищем сумму рядом
        context = text[max(0, match.start() - 100):match.end() + 200]
        amount_match = re.search(r'(\d[\d\s]*[.,]?\d*)\s*(?:руб|р\.)', context)
        amount = _parse_amount(amount_match.group(1)) if amount_match else None

        if amount and amount > 0:
            payments.append(PartialPayment(
                amount=amount,
                date=payment_date,
                payment_number=payment_number,
                source_page=page_num
            ))

    # Ищем упоминания оплаты с суммой
    payment_amount_pattern = r'(?:оплат[аы]|перечислен[оа]?|поступил[оа]?|внесен[оа]?)\s+.*?(\d[\d\s]*[.,]?\d*)\s*(?:руб|р\.)'
    for match in re.finditer(payment_amount_pattern, text_lower):
        amount = _parse_amount(match.group(1))
        if amount and amount > 0:
            # Ищем дату рядом
            context = text[max(0, match.start() - 50):match.end() + 50]
            date = _parse_date(context)
            payments.append(PartialPayment(
                amount=amount,
                date=date,
                source_page=page_num
            ))

    return payments


def detect_guarantee_letters_regex(text: str, page_num: int = 0) -> List[GuaranteeLetter]:
    """Обнаруживает гарантийные письма с помощью regex."""
    letters = []
    text_lower = text.lower()

    if not re.search(r'гарантийн\w*\s+письм', text_lower):
        return letters

    # Если есть признаки гарантийного письма
    letter = GuaranteeLetter(source_page=page_num)

    # Ищем дату документа
    date_match = re.search(r'от\s+(\d{2}[./]\d{2}[./]\d{4})', text_lower)
    if date_match:
        letter.date = _parse_date(date_match.group(1))

    # Ищем номер
    number_match = re.search(r'№\s*([A-Za-zА-Яа-я\d/-]+)', text)
    if number_match:
        letter.number = number_match.group(1)

    # Ищем обещанную сумму
    amount_match = re.search(
        r'(?:гарантиру\w+|обязу\w+|обещ\w+)\s+.*?(\d[\d\s]*[.,]?\d*)\s*(?:руб|р\.)',
        text_lower
    )
    if amount_match:
        letter.promised_amount = _parse_amount(amount_match.group(1))

    # Ищем дату обещанной оплаты
    promise_date_match = re.search(
        r'(?:до|не\s+позднее|в\s+срок\s+до)\s+(\d{2}[./]\d{2}[./]\d{4})',
        text_lower
    )
    if promise_date_match:
        letter.promised_date = _parse_date(promise_date_match.group(1))

    letters.append(letter)
    return letters


def detect_debt_acknowledgment_regex(text: str, page_num: int = 0) -> List[DebtAcknowledgment]:
    """Обнаруживает акты сверки и признание долга."""
    acknowledgments = []
    text_lower = text.lower()

    has_ack_signs = any(
        re.search(p, text_lower) for p in DEBT_ACKNOWLEDGMENT_PATTERNS
    )
    if not has_ack_signs:
        return acknowledgments

    # Определяем тип документа
    if 'акт сверк' in text_lower:
        doc_type = "акт сверки"
    else:
        doc_type = "письмо о признании долга"

    ack = DebtAcknowledgment(
        document_type=doc_type,
        source_page=page_num
    )

    # Ищем дату
    date_match = re.search(r'(?:от|по состоянию на)\s+(\d{2}[./]\d{2}[./]\d{4})', text_lower)
    if date_match:
        ack.date = _parse_date(date_match.group(1))

    # Ищем сумму задолженности
    amount_match = re.search(
        r'(?:задолженност\w*|долг\w*|сальдо)\s*[:\s]+(\d[\d\s]*[.,]?\d*)\s*(?:руб|р\.)?',
        text_lower
    )
    if amount_match:
        ack.acknowledged_amount = _parse_amount(amount_match.group(1))

    acknowledgments.append(ack)
    return acknowledgments


# =============================================================================
# LLM-анализ для сложных случаев
# =============================================================================

def _build_awareness_prompt(text: str) -> str:
    """Строит промпт для LLM-анализа особых случаев."""
    schema = {
        "partial_payments": [{
            "amount": "число - сумма оплаты",
            "date": "ДД.ММ.ГГГГ или null",
            "payment_number": "номер платёжки или null",
            "purpose": "назначение платежа или null"
        }],
        "guarantee_letters": [{
            "date": "дата письма ДД.ММ.ГГГГ или null",
            "promised_amount": "обещанная сумма или null",
            "promised_date": "дата обещанной оплаты или null"
        }],
        "debt_acknowledgments": [{
            "date": "дата документа или null",
            "acknowledged_amount": "признанная сумма или null",
            "document_type": "акт сверки | письмо о признании долга"
        }],
        "document_type_detected": "тип обнаруженного документа"
    }

    return f"""Проанализируй текст документа и определи, содержит ли он:
1. Частичные оплаты (платёжные поручения, банковские выписки об оплате)
2. Гарантийные письма (обещания оплатить в будущем)
3. Акты сверки или признания долга

Верни ТОЛЬКО JSON без комментариев.
Если категория не обнаружена, верни пустой массив [].

Важно:
- Суммы указывай числами без пробелов
- Даты в формате ДД.ММ.ГГГГ
- Если данных нет, указывай null

Схема ответа: {json.dumps(schema, ensure_ascii=False)}

Текст документа:
{text[:8000]}

Верни JSON:
"""


def analyze_document_with_llm(
    text: str,
    page_num: int = 0
) -> Dict[str, Any]:
    """
    Анализирует документ с помощью LLM для обнаружения особых случаев.
    """
    config = get_llm_config()
    if not config["enabled"]:
        return {}

    prompt = _build_awareness_prompt(text)
    response = _call_ollama(prompt, config)
    if not response:
        return {}

    parsed = _extract_json(response)
    if not parsed:
        logger.warning("LLM awareness analysis returned no JSON")
        return {}

    return parsed


# =============================================================================
# Основная функция анализа
# =============================================================================

def analyze_documents_for_special_cases(
    pages: List[str],
    original_debt: Optional[Decimal] = None,
    use_llm: bool = True
) -> DocumentAwarenessResult:
    """
    Анализирует все страницы документов на наличие особых случаев.

    Args:
        pages: Список текстов страниц
        original_debt: Исходная сумма долга (для корректировки)
        use_llm: Использовать LLM для сложных случаев

    Returns:
        DocumentAwarenessResult с обнаруженными особенностями
    """
    result = DocumentAwarenessResult()
    result.original_debt = original_debt

    # Объединяем текст для анализа
    full_text = "\n\n".join(pages)
    sverka_pages = [
        page for page in pages
        if "сверк" in page.lower()
        or "акт сверки" in page.lower()
    ]
    llm_text = "\n\n".join(sverka_pages) if sverka_pages else full_text

    # Сначала пробуем regex для быстрого обнаружения
    for i, page_text in enumerate(pages):
        # Частичные оплаты
        payments = detect_partial_payments_regex(page_text, i)
        result.partial_payments.extend(payments)

        # Гарантийные письма
        letters = detect_guarantee_letters_regex(page_text, i)
        result.guarantee_letters.extend(letters)

        # Акты сверки
        acks = detect_debt_acknowledgment_regex(page_text, i)
        result.debt_acknowledgments.extend(acks)

    # Если regex нашёл что-то или нужен более глубокий анализ — используем LLM
    if use_llm and (
        result.partial_payments or
        result.guarantee_letters or
        result.debt_acknowledgments or
        # Проверяем ключевые слова для LLM-анализа
        any(kw in full_text.lower() for kw in [
            'платежн', 'платёжн', 'п/п', 'перечисл', 'поступил',
            'выписк', 'списан', 'сверк', 'признан'
        ])
    ):
        logger.info("Running LLM awareness analysis...")
        llm_result = analyze_document_with_llm(llm_text[:12000])

        # Обрабатываем результат LLM
        if llm_result.get("partial_payments"):
            for p in llm_result["partial_payments"]:
                amount = _parse_amount(str(p.get("amount", 0)))
                if amount and amount > 0:
                    # Проверяем на дубли
                    is_duplicate = any(
                        existing.amount == amount and existing.date == p.get("date")
                        for existing in result.partial_payments
                    )
                    if not is_duplicate:
                        result.partial_payments.append(PartialPayment(
                            amount=amount,
                            date=p.get("date"),
                            payment_number=p.get("payment_number"),
                            purpose=p.get("purpose")
                        ))

        if llm_result.get("guarantee_letters"):
            has_explicit_guarantee = bool(
                re.search(r'гарантийн\w*\s+письм', full_text.lower())
            )
            if not has_explicit_guarantee:
                llm_result["guarantee_letters"] = []
            for g in llm_result["guarantee_letters"]:
                promised = _parse_amount(str(g.get("promised_amount", 0)))
                # Проверяем на дубли
                is_duplicate = any(
                    existing.date == g.get("date") and
                    existing.promised_amount == promised
                    for existing in result.guarantee_letters
                )
                if not is_duplicate:
                    result.guarantee_letters.append(GuaranteeLetter(
                        date=g.get("date"),
                        promised_amount=promised,
                        promised_date=g.get("promised_date")
                    ))

        if llm_result.get("debt_acknowledgments"):
            for a in llm_result["debt_acknowledgments"]:
                ack_amount = _parse_amount(str(a.get("acknowledged_amount", 0)))
                is_duplicate = any(
                    existing.date == a.get("date") and
                    existing.acknowledged_amount == ack_amount
                    for existing in result.debt_acknowledgments
                )
                if not is_duplicate:
                    result.debt_acknowledgments.append(DebtAcknowledgment(
                        date=a.get("date"),
                        acknowledged_amount=ack_amount,
                        document_type=a.get("document_type", "акт сверки")
                    ))

    # Подсчитываем итоги
    result.total_partial_payments = sum(
        p.amount for p in result.partial_payments if p.amount
    )

    # Корректируем сумму долга
    if original_debt and result.total_partial_payments > 0:
        result.adjusted_debt = original_debt - result.total_partial_payments
        if result.adjusted_debt < 0:
            result.warnings.append(
                f"⚠️ Сумма частичных оплат ({result.total_partial_payments} руб.) "
                f"превышает сумму долга ({original_debt} руб.)!"
            )
            result.adjusted_debt = Decimal("0")

    # Устанавливаем флаги
    result.has_partial_payments = len(result.partial_payments) > 0
    result.has_guarantee_letters = len(result.guarantee_letters) > 0
    result.has_debt_acknowledgment = len(result.debt_acknowledgments) > 0
    result.has_counter_claims = len(result.counter_claims) > 0

    # Генерируем предупреждения
    if result.has_partial_payments:
        result.warnings.append(
            f"📋 Обнаружены частичные оплаты на общую сумму "
            f"{result.total_partial_payments:,.2f} руб."
        )

    if result.has_guarantee_letters:
        dates = [g.promised_date for g in result.guarantee_letters if g.promised_date]
        if dates:
            result.warnings.append(
                f"📝 Обнаружены гарантийные письма с обещанием оплаты до: "
                f"{', '.join(dates)}"
            )
        else:
            result.warnings.append("📝 Обнаружены гарантийные письма")

    if result.has_debt_acknowledgment:
        amounts = [
            f"{a.acknowledged_amount:,.2f}"
            for a in result.debt_acknowledgments
            if a.acknowledged_amount
        ]
        if amounts:
            result.warnings.append(
                f"✅ Обнаружены документы о признании долга: {', '.join(amounts)} руб."
            )

    return result


# =============================================================================
# Генерация текстовых блоков для документа
# =============================================================================

def generate_partial_payment_text(
    payments: List[PartialPayment],
    style: str = "претензия"
) -> str:
    """
    Генерирует текст о частичных оплатах для вставки в документ.

    Args:
        payments: Список частичных оплат
        style: Стиль документа ("претензия" или "иск")

    Returns:
        Отформатированный текст
    """
    if not payments:
        return ""

    total = sum(p.amount for p in payments if p.amount)

    if style == "претензия":
        if len(payments) == 1:
            p = payments[0]
            date_str = f" от {p.date}" if p.date else ""
            pp_str = f" (п/п № {p.payment_number})" if p.payment_number else ""
            return (
                f"Вместе с тем, Заказчиком была произведена частичная оплата{date_str}"
                f"{pp_str} в размере {p.amount:,.2f} руб. "
                f"Таким образом, с учётом частичной оплаты, сумма задолженности "
                f"составляет {{adjusted_debt}} руб."
            )
        else:
            payments_list = []
            for p in payments:
                date_str = f" от {p.date}" if p.date else ""
                pp_str = f" (п/п № {p.payment_number})" if p.payment_number else ""
                payments_list.append(f"- {p.amount:,.2f} руб.{date_str}{pp_str}")

            return (
                f"Вместе с тем, Заказчиком были произведены частичные оплаты:\n"
                f"{chr(10).join(payments_list)}\n"
                f"Итого оплачено: {total:,.2f} руб.\n"
                f"Таким образом, с учётом частичных оплат, сумма задолженности "
                f"составляет {{adjusted_debt}} руб."
            )

    else:  # иск
        if len(payments) == 1:
            p = payments[0]
            date_str = f" {p.date}" if p.date else ""
            pp_str = f" (платёжное поручение № {p.payment_number})" if p.payment_number else ""
            return (
                f"В процессе взыскания задолженности Ответчиком была произведена "
                f"частичная оплата{date_str}{pp_str} в размере {p.amount:,.2f} руб. "
                f"С учётом указанной оплаты, сумма основного долга составляет "
                f"{{adjusted_debt}} руб."
            )
        else:
            payments_list = []
            for i, p in enumerate(payments, 1):
                date_str = f" от {p.date}" if p.date else ""
                pp_str = f" (п/п № {p.payment_number})" if p.payment_number else ""
                payments_list.append(f"{i}) {p.amount:,.2f} руб.{date_str}{pp_str}")

            return (
                f"В процессе взыскания задолженности Ответчиком были произведены "
                f"следующие частичные оплаты:\n"
                f"{chr(10).join(payments_list)}\n"
                f"Всего оплачено: {total:,.2f} руб.\n"
                f"С учётом произведённых оплат, сумма основного долга составляет "
                f"{{adjusted_debt}} руб."
            )


def generate_guarantee_letter_text(
    letters: List[GuaranteeLetter],
    style: str = "претензия"
) -> str:
    """
    Генерирует текст о гарантийных письмах.
    """
    if not letters:
        return ""

    if style == "претензия":
        if len(letters) == 1:
            g = letters[0]
            date_str = f" от {g.date}" if g.date else ""
            promise_str = ""
            if g.promised_date:
                promise_str = f" с обязательством оплаты до {g.promised_date}"
            return (
                f"Ранее от Заказчика было получено гарантийное письмо{date_str}"
                f"{promise_str}. Однако обязательства по оплате исполнены не были."
            )
        else:
            return (
                f"Ранее от Заказчика были получены гарантийные письма "
                f"({len(letters)} шт.) с обещаниями оплаты. "
                f"Однако взятые на себя обязательства исполнены не были."
            )

    else:  # иск
        if len(letters) == 1:
            g = letters[0]
            date_str = f" от {g.date}" if g.date else ""
            return (
                f"Ответчиком было направлено гарантийное письмо{date_str}, "
                f"в котором он обязался погасить задолженность. "
                f"Между тем, данные обязательства исполнены не были, "
                f"что свидетельствует о недобросовестности Ответчика."
            )
        else:
            return (
                f"Ответчиком неоднократно ({len(letters)} раз(а)) направлялись "
                f"гарантийные письма с обязательствами погасить задолженность. "
                f"Однако данные обязательства систематически не исполнялись, "
                f"что свидетельствует о недобросовестном поведении Ответчика."
            )


def generate_debt_acknowledgment_text(
    acknowledgments: List[DebtAcknowledgment],
    style: str = "претензия"
) -> str:
    """
    Генерирует текст о признании долга.
    """
    if not acknowledgments:
        return ""

    ack = acknowledgments[0]  # Берём первый (обычно самый актуальный)
    date_str = f" от {ack.date}" if ack.date else ""
    amount_str = f" в размере {ack.acknowledged_amount:,.2f} руб." if ack.acknowledged_amount else ""

    if style == "претензия":
        return (
            f"Согласно {ack.document_type}{date_str}, Заказчик признал "
            f"задолженность{amount_str} перед Исполнителем."
        )
    else:  # иск
        return (
            f"Факт наличия задолженности подтверждается {ack.document_type}{date_str}, "
            f"подписанным обеими сторонами{amount_str}. "
            f"Таким образом, Ответчик признал задолженность перед Истцом."
        )


def generate_awareness_text_block(
    result: DocumentAwarenessResult,
    style: str = "претензия"
) -> str:
    """
    Генерирует полный текстовый блок с учётом всех особых случаев.

    Args:
        result: Результат анализа документов
        style: Стиль документа

    Returns:
        Готовый текстовый блок для вставки в документ
    """
    blocks = []

    # Частичные оплаты
    if result.has_partial_payments:
        text = generate_partial_payment_text(result.partial_payments, style)
        if text:
            blocks.append(text)

    # Гарантийные письма
    if result.has_guarantee_letters:
        text = generate_guarantee_letter_text(result.guarantee_letters, style)
        if text:
            blocks.append(text)

    # Признание долга
    if result.has_debt_acknowledgment:
        text = generate_debt_acknowledgment_text(result.debt_acknowledgments, style)
        if text:
            blocks.append(text)

    return "\n\n".join(blocks)


# =============================================================================
# Корректировка данных иска
# =============================================================================

def adjust_claim_data(
    claim_data: Dict[str, Any],
    awareness_result: DocumentAwarenessResult
) -> Dict[str, Any]:
    """
    Корректирует данные иска с учётом обнаруженных особенностей.

    Args:
        claim_data: Исходные данные иска
        awareness_result: Результат анализа документов

    Returns:
        Скорректированные данные иска
    """
    adjusted = claim_data.copy()

    # Корректируем сумму долга при частичных оплатах
    if awareness_result.has_partial_payments and awareness_result.adjusted_debt is not None:
        adjusted["original_debt"] = claim_data.get("debt")
        adjusted["debt"] = str(awareness_result.adjusted_debt)
        adjusted["partial_payments_total"] = str(awareness_result.total_partial_payments)
        adjusted["partial_payments_info"] = [
            {
                "amount": str(p.amount),
                "date": p.date,
                "payment_number": p.payment_number
            }
            for p in awareness_result.partial_payments
        ]

    # Добавляем информацию о гарантийных письмах
    if awareness_result.has_guarantee_letters:
        adjusted["guarantee_letters"] = [
            {
                "date": g.date,
                "promised_amount": str(g.promised_amount) if g.promised_amount else None,
                "promised_date": g.promised_date
            }
            for g in awareness_result.guarantee_letters
        ]

    # Добавляем информацию о признании долга
    if awareness_result.has_debt_acknowledgment:
        adjusted["debt_acknowledgments"] = [
            {
                "date": a.date,
                "amount": str(a.acknowledged_amount) if a.acknowledged_amount else None,
                "type": a.document_type
            }
            for a in awareness_result.debt_acknowledgments
        ]

    # Добавляем сгенерированный текст
    awareness_text = generate_awareness_text_block(awareness_result, "претензия")
    if awareness_text:
        adjusted["awareness_text"] = awareness_text

    # Добавляем предупреждения
    if awareness_result.warnings:
        adjusted["awareness_warnings"] = awareness_result.warnings

    return adjusted
