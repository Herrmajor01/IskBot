#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер внешних претензий (не по нашей форме).

Извлекает данные из произвольных претензий клиентов для составления исковых заявлений:
- Стороны (истец/ответчик) с реквизитами
- Заявки на перевозку с суммами
- Транспортные накладные
- Почтовые треки и даты получения
- Условия оплаты
- Юридические услуги
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from document_matcher import (
    ApplicationInfo,
    MatchingReport,
    process_pdf as match_documents_in_pdf,
    format_report as format_matching_report,
    ConfidenceLevel,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LLM парсинг через Ollama
# =============================================================================

def _get_llm_config() -> Dict[str, Any]:
    """Получает конфигурацию Ollama из переменных окружения."""
    from dotenv import load_dotenv
    load_dotenv()

    base_url = (
        os.getenv("OLLAMA_BASE_URL", "")
        or os.getenv("OLLAMA_HOST", "")
    ).strip().rstrip("/")

    enabled_raw = os.getenv("LLM_ENABLED")
    enabled = (
        enabled_raw.lower() in ("1", "true", "yes", "on")
        if enabled_raw else bool(base_url)
    )

    # Для парсинга документов лучше использовать 14b модель
    # qwen2.5:14b-instruct - хорошо понимает структуру документов
    model = os.getenv(
        "OLLAMA_MODEL_LARGE",
        os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct")
    ).strip()

    return {
        "enabled": enabled,
        "base_url": base_url,
        "model": model,
        "timeout": int(os.getenv("OLLAMA_TIMEOUT", "180")),  # Увеличен для 14b
        "max_chars": int(os.getenv("OLLAMA_MAX_CHARS", "15000")),
    }


def _call_ollama(prompt: str, config: Dict[str, Any]) -> Optional[str]:
    """Вызывает Ollama API."""
    import requests

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
        logger.error(f"Ollama error: {e}")
        return None


def _extract_json_from_response(text: str) -> Optional[Dict]:
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
                pass
    return None


EXTERNAL_CLAIM_PROMPT = """Ты извлекаешь данные из претензии по грузоперевозкам.

ЗАДАЧА: Извлечь структурированные данные и вернуть JSON.

ФОРМАТ ОТВЕТА (только JSON, без комментариев):
{
  "plaintiff": {
    "name": "Полное название истца (перевозчика)",
    "inn": "10 или 12 цифр",
    "kpp": "9 цифр или null",
    "ogrn": "13 или 15 цифр",
    "address": "Юридический адрес"
  },
  "defendant": {
    "name": "Полное название ответчика (заказчика)",
    "inn": "10 или 12 цифр",
    "kpp": "9 цифр или null",
    "ogrn": "13 или 15 цифр",
    "address": "Юридический адрес"
  },
  "base_contract": {
    "number": "Номер договора",
    "date": "ДД.ММ.ГГГГ"
  },
  "applications": [
    {
      "number": "Номер заявки (например СП139948/1)",
      "date": "ДД.ММ.ГГГГ",
      "amount_with_vat": число (сумма с НДС),
      "amount_without_vat": число (сумма без НДС),
      "route": "Маршрут перевозки",
      "vehicle_plate": "Госномер машины",
      "driver_name": "ФИО водителя",
      "load_date": "Дата загрузки ДД.ММ.ГГГГ",
      "waybill_number": "Номер транспортной накладной",
      "postal_track": "14-значный трек-номер почты для этой заявки"
    }
  ],
  "total_debt": число (общая сумма долга),
  "payment_days": число (срок оплаты в рабочих днях),
  "legal_services": {
    "contract_number": "Номер договора юр.услуг",
    "contract_date": "ДД.ММ.ГГГГ",
    "amount": число
  }
}

ПРАВИЛА:
- Истец (plaintiff) - тот кто НАПРАВЛЯЕТ претензию (Перевозчик)
- Ответчик (defendant) - тот КОМУ направлена претензия (Заказчик)
- ИНН: только цифры, 10 (ООО) или 12 (ИП) цифр
- ОГРН: 13 цифр для ООО, 15 для ИП
- Даты строго в формате ДД.ММ.ГГГГ
- Суммы - числа без пробелов и валюты
- Трек-номер почты - 14 цифр
- Связывай заявки с транспортными накладными и треками

ТЕКСТ ПРЕТЕНЗИИ:
{text}

Верни только JSON:"""


def parse_claim_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    Парсит претензию с помощью LLM (Ollama).

    Returns:
        Dict с извлечёнными данными или None
    """
    config = _get_llm_config()
    if not config["enabled"] or not config["base_url"]:
        logger.info("LLM disabled, using regex parsing")
        return None

    # Ограничиваем текст
    max_chars = config["max_chars"]
    trimmed = text[:max_chars] if len(text) > max_chars else text

    prompt = EXTERNAL_CLAIM_PROMPT.replace("{text}", trimmed)

    logger.info(f"Calling Ollama for claim parsing ({len(trimmed)} chars)...")
    response = _call_ollama(prompt, config)

    if not response:
        logger.warning("LLM returned empty response")
        return None

    result = _extract_json_from_response(response)
    if result:
        logger.info(f"LLM extracted: {list(result.keys())}")
    else:
        logger.warning("Failed to parse LLM response as JSON")
        logger.debug(f"Response: {response[:500]}")

    return result


def apply_llm_data_to_result(
    result: 'ExternalClaimData',
    llm_data: Dict[str, Any]
) -> 'ExternalClaimData':
    """
    Применяет данные из LLM к результату парсинга.
    Заполняет только пустые поля.
    """
    if not llm_data:
        return result

    # Истец
    if 'plaintiff' in llm_data:
        p = llm_data['plaintiff']
        if not result.plaintiff.name and p.get('name'):
            result.plaintiff.name = p['name']
        if not result.plaintiff.inn and p.get('inn'):
            result.plaintiff.inn = str(p['inn'])
        if not result.plaintiff.kpp and p.get('kpp'):
            result.plaintiff.kpp = str(p['kpp'])
        if not result.plaintiff.ogrn and p.get('ogrn'):
            result.plaintiff.ogrn = str(p['ogrn'])
        if not result.plaintiff.address and p.get('address'):
            result.plaintiff.address = p['address']

    # Ответчик
    if 'defendant' in llm_data:
        d = llm_data['defendant']
        if not result.defendant.name and d.get('name'):
            result.defendant.name = d['name']
        if not result.defendant.inn and d.get('inn'):
            result.defendant.inn = str(d['inn'])
        if not result.defendant.kpp and d.get('kpp'):
            result.defendant.kpp = str(d['kpp'])
        if not result.defendant.ogrn and d.get('ogrn'):
            result.defendant.ogrn = str(d['ogrn'])
        if not result.defendant.address and d.get('address'):
            result.defendant.address = d['address']

    # Договор
    if 'base_contract' in llm_data:
        bc = llm_data['base_contract']
        if not result.base_contract_number and bc.get('number'):
            result.base_contract_number = bc['number']
        if not result.base_contract_date and bc.get('date'):
            result.base_contract_date = bc['date']

    # Общий долг
    if not result.total_debt and llm_data.get('total_debt'):
        result.total_debt = Decimal(str(llm_data['total_debt']))

    # Обогащаем заявки данными из LLM
    if 'applications' in llm_data:
        llm_apps = llm_data['applications']
        for llm_app in llm_apps:
            llm_num = llm_app.get('number', '')
            # Ищем соответствующую заявку
            for app in result.applications:
                if _numbers_match(app.number, llm_num):
                    # Заполняем пустые поля
                    if not app.route and llm_app.get('route'):
                        app.route = llm_app['route']
                    if not app.postal_track and llm_app.get('postal_track'):
                        app.postal_track = str(llm_app['postal_track'])
                    if not app.waybill_number and llm_app.get('waybill_number'):
                        app.waybill_number = str(llm_app['waybill_number'])
                    # НЕ перезаписываем payment_days из LLM, если уже есть
                    # данные из пакета документов (они точнее)
                    break

    return result


def _numbers_match(num1: str, num2: str) -> bool:
    """Проверяет совпадение номеров заявок."""
    # Извлекаем базовую часть номера
    def get_base(n):
        match = re.search(r'(\d{5,})', str(n))
        return match.group(1) if match else str(n)

    return get_base(num1) == get_base(num2)


# =============================================================================
# API Почты России
# =============================================================================

# Импортируем функции для работы с API Почты России из main.py
def get_tracking_dates_from_api(track_number: str) -> Tuple[str, str]:
    """
    Получает даты отправки и получения по трек-номеру через API Почты России.

    Returns:
        Tuple[send_date, received_date] в формате ДД.ММ.ГГГГ
    """
    try:
        from main import (
            fetch_russian_post_operations,
            extract_tracking_dates,
            RussianPostTrackingError
        )
        operations = fetch_russian_post_operations(track_number)
        send_date, received_date = extract_tracking_dates(operations)
        return send_date or "", received_date or ""
    except ImportError:
        logger.warning("Не удалось импортировать функции API Почты России")
        return "", ""
    except Exception as e:
        logger.warning(f"Ошибка получения данных по треку {track_number}: {e}")
        return "", ""


# =============================================================================
# Структуры данных
# =============================================================================

@dataclass
class Party:
    """Сторона дела (истец или ответчик)."""
    name: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    bank_account: str = ""
    bank_name: str = ""
    bik: str = ""
    corr_account: str = ""


@dataclass
class TransportApplication:
    """Заявка на перевозку."""
    number: str = ""  # СП 139948/2
    date: str = ""  # 17.06.2025
    route: str = ""  # Екатеринбург → Тюмень
    amount_without_vat: Decimal = Decimal("0")
    amount_with_vat: Decimal = Decimal("0")
    vehicle_plate: str = ""
    trailer_plate: str = ""
    driver_name: str = ""
    driver_inn: str = ""
    load_date: str = ""
    payment_terms: str = ""
    payment_days: int = 0
    # Связанные документы (строковые данные)
    waybill_number: str = ""
    waybill_date: str = ""
    postal_track: str = ""
    docs_received_date: str = ""
    # Связанные объекты документов
    linked_waybill: Optional['Waybill'] = None
    linked_shipment: Optional['PostalShipment'] = None
    # Источник
    source_file: str = ""
    source_page: int = 0


@dataclass
class Waybill:
    """Транспортная накладная."""
    number: str = ""
    date: str = ""
    sender_name: str = ""
    sender_inn: str = ""
    receiver_name: str = ""
    receiver_inn: str = ""
    cargo_description: str = ""
    weight: str = ""
    # Связь с заявкой
    application_number: str = ""
    source_file: str = ""
    source_page: int = 0


@dataclass
class PostalShipment:
    """Почтовое отправление."""
    track_number: str = ""
    send_date: str = ""
    received_date: str = ""
    sender: str = ""
    receiver: str = ""
    # Связь с документами
    related_docs: List[str] = field(default_factory=list)
    source_file: str = ""
    source_page: int = 0


@dataclass
class LegalServices:
    """Юридические услуги."""
    contract_number: str = ""
    contract_date: str = ""
    amount: Decimal = Decimal("0")
    contractor_name: str = ""
    contractor_inn: str = ""
    payment_number: str = ""
    payment_date: str = ""


@dataclass
class ExternalClaimData:
    """Результат парсинга внешней претензии."""
    # Стороны
    plaintiff: Party = field(default_factory=Party)  # Истец (перевозчик)
    defendant: Party = field(default_factory=Party)  # Ответчик (заказчик)

    # Основной договор
    base_contract_number: str = ""
    base_contract_date: str = ""

    # Перевозки
    applications: List[TransportApplication] = field(default_factory=list)
    waybills: List[Waybill] = field(default_factory=list)
    postal_shipments: List[PostalShipment] = field(default_factory=list)

    # Финансы
    total_debt: Decimal = Decimal("0")
    interest_calculated: Decimal = Decimal("0")

    # Юридические услуги
    legal_services: Optional[LegalServices] = None

    # Претензия
    claim_date: str = ""
    claim_number: str = ""

    # Предупреждения
    warnings: List[str] = field(default_factory=list)

    # Источники
    source_files: List[str] = field(default_factory=list)


# =============================================================================
# Вспомогательные функции
# =============================================================================

def _parse_amount(text: str) -> Decimal:
    """Парсит сумму из текста."""
    if not text:
        return Decimal("0")
    # Убираем всё кроме цифр, точки и запятой
    cleaned = re.sub(r'[^\d.,]', '', str(text))
    cleaned = cleaned.replace(',', '.')
    # Убираем лишние точки (оставляем последнюю как десятичный разделитель)
    parts = cleaned.split('.')
    if len(parts) > 2:
        cleaned = ''.join(parts[:-1]) + '.' + parts[-1]
    try:
        return Decimal(cleaned)
    except:
        return Decimal("0")


def _parse_date(text: str) -> str:
    """Парсит дату из текста в формат ДД.ММ.ГГГГ."""
    if not text:
        return ""
    # Ищем дату в формате ДД.ММ.ГГГГ
    match = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', text)
    if match:
        day, month, year = match.groups()
        return f"{int(day):02d}.{int(month):02d}.{year}"
    return ""


def _extract_inn(text: str) -> str:
    """Извлекает ИНН из текста."""
    match = re.search(r'ИНН\s*[:\s]*(\d{10,12})', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _extract_kpp(text: str) -> str:
    """Извлекает КПП из текста."""
    match = re.search(r'КПП\s*[:\s]*(\d{9})', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _extract_ogrn(text: str) -> str:
    """Извлекает ОГРН/ОГРНИП из текста."""
    match = re.search(r'ОГРН(?:ИП)?\s*[:\s]*(\d{13,15})', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _extract_track_number(text: str) -> str:
    """Извлекает трек-номер почты из текста."""
    # Формат: 14 цифр (российская почта)
    match = re.search(r'\b(\d{14})\b', text)
    if match:
        return match.group(1)
    # Формат: 13 цифр
    match = re.search(r'\b(\d{13})\b', text)
    if match:
        return match.group(1)
    return ""


def _normalize_application_number(text: str) -> str:
    """Нормализует номер заявки (СП, № и т.д.)."""
    if not text:
        return ""
    # Убираем лишние символы, оставляем буквы, цифры, слэш
    cleaned = re.sub(r'[^\w/]', '', text.upper())
    # Если начинается с СП - оставляем
    if cleaned.startswith('СП') or cleaned.startswith('SP'):
        return cleaned
    return cleaned


# =============================================================================
# Парсинг сторон из претензии
# =============================================================================

def extract_parties_from_claim(text: str) -> Tuple[Party, Party]:
    """
    Извлекает стороны из текста претензии.

    Returns:
        Tuple[plaintiff, defendant]
    """
    plaintiff = Party()
    defendant = Party()

    # Ищем позицию слова "Претензия" или "ПРЕТЕНЗИЯ"
    pretension_pos = text.lower().find('претензия')
    if pretension_pos == -1:
        pretension_pos = len(text)

    # Всё до "Претензия" - это шапка с реквизитами
    header = text[:pretension_pos]

    # Ищем позицию "Директору" - это разделитель между истцом и ответчиком
    director_patterns = [
        r'Директору\s+',
        r'Генеральному директору\s+',
        r'В\s+(?:ООО|АО)\s+',
    ]

    director_pos = -1
    for pattern in director_patterns:
        match = re.search(pattern, header, re.IGNORECASE)
        if match:
            director_pos = match.start()
            break

    if director_pos > 0:
        # Истец - всё до "Директору"
        sender_text = header[:director_pos]
        # Ответчик - от "Директору" до конца шапки
        receiver_text = header[director_pos:]
    else:
        # Пробуем разделить по ООО/ИП
        org_matches = list(re.finditer(
            r'(?:ООО|Общество с ограниченной|ИП\s+)',
            header, re.IGNORECASE
        ))
        if len(org_matches) >= 2:
            sender_text = header[:org_matches[1].start()]
            receiver_text = header[org_matches[1].start():]
        else:
            sender_text = header
            receiver_text = ""

    # Парсим блоки
    if sender_text:
        plaintiff = _parse_party_block(sender_text)

    if receiver_text:
        defendant = _parse_party_block(receiver_text)

    # Если не нашли по блокам, ищем по паттернам в тексте
    if not plaintiff.name:
        plaintiff = _extract_party_by_patterns(text, is_plaintiff=True)
    if not defendant.name:
        defendant = _extract_party_by_patterns(text, is_plaintiff=False)

    return plaintiff, defendant


def _parse_party_block(text: str) -> Party:
    """Парсит блок реквизитов стороны."""
    party = Party()

    # Название организации
    name_patterns = [
        r'((?:ООО|Общество с ограниченной ответственностью)'
        r'[^И\n]*(?:«[^»]+»|"[^"]+"|[А-Яа-яЁё\-\s]+))',
        r'((?:ИП|Индивидуальный предприниматель)\s+'
        r'[А-ЯЁа-яё\s]+)',
        r'((?:АО|Акционерное общество)\s*[«"]?[^»"]+[»"]?)',
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Убираем лишние кавычки и пробелы
            name = re.sub(r'\s+', ' ', name)
            # Ограничиваем до ИНН или адреса
            for stop_word in ['ИНН', 'Юридический', 'адрес', '\n']:
                stop_pos = name.find(stop_word)
                if stop_pos > 10:
                    name = name[:stop_pos].strip()
            party.name = name
            break

    # ИНН - ищем явно после "ИНН"
    party.inn = _extract_inn(text)

    # КПП
    party.kpp = _extract_kpp(text)

    # ОГРН
    party.ogrn = _extract_ogrn(text)

    # Адрес - ищем после "адрес:" или по индексу
    addr_match = re.search(
        r'(?:юридический\s+)?адрес[:\s]*(\d{6}[^\n]+)',
        text, re.IGNORECASE
    )
    if addr_match:
        party.address = addr_match.group(1).strip()
    else:
        # Ищем адрес по индексу (6-значный индекс)
        addr_match = re.search(r'(\d{6}[,\s]+[^\n]+)', text)
        if addr_match:
            party.address = addr_match.group(1).strip()

    # Email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match:
        party.email = email_match.group(0)

    # Телефон
    phone_match = re.search(
        r'(?:тел\.?|телефон)[:\s]*([\d\s\-\+\(\)]+)',
        text, re.IGNORECASE
    )
    if phone_match:
        party.phone = phone_match.group(1).strip()

    return party


def _extract_party_by_patterns(text: str, is_plaintiff: bool) -> Party:
    """Извлекает сторону по паттернам в тексте."""
    party = Party()

    if is_plaintiff:
        # Истец - ищем "между ... (Перевозчик)" или "Исполнитель"
        match = re.search(
            r'между[,\s]+([^,]+(?:ООО|ИП)[^,]+)[,\s]+\(?Перевозчик\)?',
            text, re.IGNORECASE
        )
        if match:
            party.name = match.group(1).strip()
    else:
        # Ответчик - ищем "(Заказчик)"
        match = re.search(
            r'([^,]+(?:ООО|ИП)[^,]+)[,\s]+\(?Заказчик\)?',
            text, re.IGNORECASE
        )
        if match:
            party.name = match.group(1).strip()

    # Ищем ИНН рядом с названием
    if party.name:
        name_pos = text.find(party.name)
        if name_pos >= 0:
            context = text[name_pos:name_pos + 500]
            party.inn = _extract_inn(context)
            party.kpp = _extract_kpp(context)

    return party


# =============================================================================
# Парсинг заявок на перевозку
# =============================================================================

def extract_applications_from_claim(text: str) -> List[TransportApplication]:
    """
    Извлекает заявки на перевозку из текста претензии.

    Ищет только явные заявки вида "№ СП 139948/1 от 16.06.2025г" с описанием перевозки.
    """
    applications = []
    seen_numbers = set()

    # Строгий паттерн для заявок в претензии
    # Формат: № СП 139948/1 от 16.06.2025г или №СП139948/1 от 16.06.2025г
    app_pattern = re.compile(
        r'№\s*СП\s*(\d+/\d+)\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        re.IGNORECASE
    )

    # Ищем все заявки
    for match in app_pattern.finditer(text):
        number = match.group(1)

        # Пропускаем дубликаты
        if number in seen_numbers:
            continue
        seen_numbers.add(number)

        app = TransportApplication()
        app.number = f"СП{number}"
        app.date = _parse_date(match.group(2))

        # Ищем контекст вокруг заявки (1000 символов после)
        start = match.end()
        end = min(start + 1000, len(text))
        context = text[start:end]

        # Извлекаем маршрут - ищем текст между "по маршруту" и "автомобиль"
        route_match = re.search(
            r'маршрут[у]?\s+([^,]+(?:[\-–]\s*[^,]+)+)',
            context, re.IGNORECASE
        )
        if route_match:
            route = route_match.group(1).strip()
            # Ограничиваем до "автомобиль"
            auto_pos = route.lower().find('автомобиль')
            if auto_pos > 0:
                route = route[:auto_pos].strip().rstrip(',')
            app.route = route

        # Извлекаем сумму с НДС
        # с учетом ндс (руб.) 40 000,00
        amount_match = re.search(
            r'с\s+учет(?:о|ё)м\s+ндс\s*\([^)]*\)\s*(\d[\d\s,\.]+)',
            context, re.IGNORECASE
        )
        if amount_match:
            app.amount_with_vat = _parse_amount(amount_match.group(1))

        # Сумма без НДС
        no_vat_match = re.search(
            r'без\s+учет(?:а|ё)\s+ндс\s*\([^)]*\)\s*(\d[\d\s,\.]+)',
            context, re.IGNORECASE
        )
        if no_vat_match:
            app.amount_without_vat = _parse_amount(no_vat_match.group(1))

        # Госномер - ищем после слова "автомобиль"
        plate_match = re.search(
            r'автомобиль\s+\S+\s+([А-ЯA-Z]\s*\d{3}\s*[А-ЯA-Z]{2}\s*\d{2,3})',
            context, re.IGNORECASE
        )
        if plate_match:
            app.vehicle_plate = plate_match.group(1).replace(' ', '')

        # Прицеп - ищем "(прицеп XX 1234 66)"
        trailer_match = re.search(
            r'\(прицеп\s+([А-ЯA-Z]{2}\s*\d{4}\s*\d{2,3})\)',
            context, re.IGNORECASE
        )
        if trailer_match:
            trailer = trailer_match.group(1).replace(' ', '').replace('\n', '').replace('\r', '')
            app.trailer_plate = trailer

        # Водитель
        driver_match = re.search(
            r'водитель\s+([А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)',
            context, re.IGNORECASE
        )
        if driver_match:
            driver = driver_match.group(1).replace('\n', ' ').replace('\r', ' ')
            app.driver_name = " ".join(driver.split())

        # Дата загрузки
        load_match = re.search(
            r'(?:дата\s+)?загрузк[иа]\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
            context, re.IGNORECASE
        )
        if load_match:
            app.load_date = _parse_date(load_match.group(1))

        # Номер накладной (ТН № XXXXX или транспортная накладная №)
        # Номер должен содержать цифры
        waybill_match = re.search(
            r'(?:ТН|транспортн[аоую]+\s+накладн[аоую]+)\s*№?\s*'
            r'(\d[\dA-Za-z/\-]+)',
            context, re.IGNORECASE
        )
        if waybill_match:
            app.waybill_number = waybill_match.group(1).strip()

        # Почтовый трек (14 цифр или формат с дефисами)
        track_match = re.search(
            r'(?:трек|идентификатор|почт[а-я]*\s+отправлени[ея])\s*'
            r'[№:]?\s*(\d{14})',
            context, re.IGNORECASE
        )
        if track_match:
            app.postal_track = track_match.group(1)

        applications.append(app)

    return applications


def extract_waybills_from_claim(text: str) -> List[Waybill]:
    """
    Извлекает транспортные накладные из текста претензии.
    """
    waybills = []

    # Паттерн для накладных
    # Транспортная накладная № 55130926 от 16.06.2025г
    waybill_pattern = re.compile(
        r'(?:транспортн\w+\s+накладн\w+|ТН|ТТН)\s*№?\s*'
        r'([A-Za-zА-Яа-я\d/\-]+)\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        re.IGNORECASE
    )

    for match in waybill_pattern.finditer(text):
        wb = Waybill()
        wb.number = match.group(1).strip()
        wb.date = _parse_date(match.group(2))
        waybills.append(wb)

    return waybills


def extract_postal_shipments_from_claim(text: str) -> List[PostalShipment]:
    """
    Извлекает почтовые отправления из текста претензии.
    """
    shipments = []
    seen_tracks = set()

    # Паттерн: почтовой квитанции 80514110186166 от 18.06.2025г
    postal_pattern = re.compile(
        r'(?:почтов\w+\s+(?:квитанц\w+|отправлен\w+))\s*'
        r'(\d{14})\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        re.IGNORECASE
    )

    for match in postal_pattern.finditer(text):
        track = match.group(1)
        if track in seen_tracks:
            continue
        seen_tracks.add(track)

        shipment = PostalShipment()
        shipment.track_number = track
        shipment.send_date = _parse_date(match.group(2))
        shipments.append(shipment)

    return shipments


def extract_document_links_from_claim(text: str) -> List[dict]:
    """
    Извлекает связи между заявками, накладными и почтовыми отправлениями.

    В претензии есть блоки вида:
    "Транспортная накладная № 55130926 от 16.06.2025г (приложение 6),
    которые получены Вами, согласно почтовой квитанции 80514110186166
    от 18.06.2025г. (приложение 7)"

    Returns:
        List[dict] с ключами: waybill_numbers, track_number, send_date
    """
    links = []

    # Паттерн для блока с накладными и квитанцией
    # Ищем накладные, за которыми следует "получены... почтовой квитанции"
    block_pattern = re.compile(
        r'((?:Транспортная накладная|ТТН|товарно[­-]?\s*транспортная накладная)'
        r'[^;]+?)'
        r'получен\w*\s+(?:Вами,?\s*)?согласно\s+почтов\w+\s+квитанци\w*\s*'
        r'(\d{14})\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        re.IGNORECASE | re.DOTALL
    )

    for match in block_pattern.finditer(text):
        waybills_text = match.group(1)
        track = match.group(2)
        send_date = _parse_date(match.group(3))

        # Извлекаем номера накладных из текста
        waybill_numbers = []
        wb_pattern = re.compile(
            r'(?:накладная|ТТН)\s*№?\s*([A-Za-zА-Яа-я0-9/\-]+)\s+от',
            re.IGNORECASE
        )
        for wb_match in wb_pattern.finditer(waybills_text):
            wb_num = wb_match.group(1).strip()
            if wb_num and len(wb_num) > 2:
                waybill_numbers.append(wb_num)

        if waybill_numbers and track:
            links.append({
                'waybill_numbers': waybill_numbers,
                'track_number': track,
                'send_date': send_date
            })

    return links


# =============================================================================
# Парсинг комплекта документов (СП файл)
# =============================================================================

def parse_document_package(pdf_path: str) -> Dict[str, Any]:
    """
    Парсит комплект документов из PDF файла (заявка + накладная + трек).

    Returns:
        Dict с данными: application, waybill, postal, invoice
    """
    result = {
        "application": None,
        "waybills": [],
        "postal_shipments": [],
        "invoice": None,
        "source_file": os.path.basename(pdf_path)
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_text = ""
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                all_text += f"\n\n=== PAGE {i+1} ===\n{page_text}"

                # Определяем тип страницы и парсим
                page_lower = page_text.lower()

                # Заявка (берём только первую найденную)
                if result["application"] is None:
                    if 'реквизиты заявки' in page_lower:
                        app = _parse_application_page(page_text, i + 1)
                        if app and app.number:
                            result["application"] = app
                            app.source_file = result["source_file"]
                            app.source_page = i + 1

                # Транспортная накладная
                if 'транспортная накладная' in page_lower and 'грузоотправитель' in page_lower:
                    wb = _parse_waybill_page(page_text, i + 1)
                    if wb and wb.number:
                        wb.source_file = result["source_file"]
                        wb.source_page = i + 1
                        result["waybills"].append(wb)

                # Почтовое отслеживание
                if 'почт' in page_lower and ('отслеживан' in page_lower or 'идентификатор' in page_lower):
                    postal = _parse_postal_tracking_page(page_text, i + 1)
                    if postal and postal.track_number:
                        postal.source_file = result["source_file"]
                        postal.source_page = i + 1
                        result["postal_shipments"].append(postal)

                # Счёт на оплату
                if 'счет на оплату' in page_lower or 'счёт на оплату' in page_lower:
                    invoice = _parse_invoice_page(page_text, i + 1)
                    if invoice:
                        result["invoice"] = invoice

            result["full_text"] = all_text

    except Exception as e:
        logger.error(f"Error parsing document package {pdf_path}: {e}")

    return result


def parse_document_packages(pdf_paths: List[str]) -> List[Dict[str, Any]]:
    """
    Парсит список пакетов документов.

    Args:
        pdf_paths: Список путей к PDF файлам

    Returns:
        Список распарсенных пакетов документов
    """
    packages = []
    for pdf_path in pdf_paths:
        try:
            package = parse_document_package(pdf_path)
            packages.append(package)
        except Exception as e:
            logger.error(f"Error parsing document package {pdf_path}: {e}")
    return packages


def _parse_application_page(text: str, page_num: int) -> Optional[TransportApplication]:
    """Парсит страницу с заявкой."""
    app = TransportApplication()
    app.source_page = page_num

    # Номер и дата заявки
    # Реквизиты заявки СП139948/2 от 17.06.2025 08:28
    match = re.search(
        r'(?:заявк[иа]|реквизиты заявки)\s*(?:№?\s*)?(?:СП\s*)?(\d+[/\d]*)\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        text, re.IGNORECASE
    )
    if match:
        app.number = f"СП{match.group(1)}"
        app.date = _parse_date(match.group(2))

    # Сумма
    amount_match = re.search(
        r'(?:стоимость|сумма)[^\d]*(?:без[^\d]*ндс)?[^\d]*(\d[\d\s,\.]+)',
        text, re.IGNORECASE
    )
    if amount_match:
        app.amount_without_vat = _parse_amount(amount_match.group(1))
        # Рассчитываем с НДС (20%)
        app.amount_with_vat = app.amount_without_vat * Decimal("1.2")

    # Срок оплаты
    # Оплата не позднее 20 рабочих дней с даты получения документов
    payment_match = re.search(
        r'(?:оплата|срок оплаты)[^\d]*(\d+)\s*(?:рабочих|банковских)?\s*дн',
        text, re.IGNORECASE
    )
    if payment_match:
        app.payment_days = int(payment_match.group(1))
        app.payment_terms = f"{app.payment_days} рабочих дней с даты получения документов"

    # Госномер ТС
    plate_match = re.search(
        r'(?:машин[ыа]|номер)\s*([А-ЯA-Z]+\s*\d+\s*[А-ЯA-Z]+\s*\d+)',
        text, re.IGNORECASE
    )
    if plate_match:
        app.vehicle_plate = plate_match.group(1).replace(' ', '')

    # Прицеп
    trailer_match = re.search(
        r'прицеп[а]?\s*([А-ЯA-Z]+\s*\d+\s*\d+)',
        text, re.IGNORECASE
    )
    if trailer_match:
        app.trailer_plate = trailer_match.group(1).replace(' ', '')

    # Водитель
    driver_match = re.search(
        r'(?:Ф\.?И\.?О\.?\s*водителя|водител[ья])[,:\s]+([А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)',
        text, re.IGNORECASE
    )
    if driver_match:
        app.driver_name = driver_match.group(1).strip()

    # ИНН водителя
    driver_inn_match = re.search(
        r'(?:водител[ья]|Ф\.?И\.?О)[^\n]*ИНН\s*(\d{12})',
        text, re.IGNORECASE
    )
    if driver_inn_match:
        app.driver_inn = driver_inn_match.group(1)

    return app if app.number else None


def _parse_waybill_page(text: str, page_num: int) -> Optional[Waybill]:
    """Парсит страницу с транспортной накладной."""
    wb = Waybill()
    wb.source_page = page_num

    # Номер и дата
    # Транспортная накладная | N0 : 55130926 Дата: 16.06.2025
    match = re.search(
        r'(?:транспортная накладная|ТН)[^\n]*N[оo0]?\s*[:\s]*([A-Za-zА-Яа-я\d/\-]+)[^\n]*'
        r'(?:дата|от)[:\s]*(\d{1,2}[./]\d{1,2}[./]\d{4})',
        text, re.IGNORECASE
    )
    if match:
        wb.number = match.group(1).strip()
        wb.date = _parse_date(match.group(2))
    else:
        # Альтернативный формат
        match = re.search(
            r'N[оo0]?\s*[:\s]*([A-Za-zА-Яа-я\d/\-]+)\s+Дата[:\s]*(\d{1,2}[./]\d{1,2}[./]\d{4})',
            text, re.IGNORECASE
        )
        if match:
            wb.number = match.group(1).strip()
            wb.date = _parse_date(match.group(2))

    # Грузоотправитель
    sender_match = re.search(
        r'грузоотправитель[^\n]*\n?([^\n]+)',
        text, re.IGNORECASE
    )
    if sender_match:
        wb.sender_name = sender_match.group(1).strip()[:200]

    # Грузополучатель
    receiver_match = re.search(
        r'грузополучатель[^\n]*\n?([^\n]+)',
        text, re.IGNORECASE
    )
    if receiver_match:
        wb.receiver_name = receiver_match.group(1).strip()[:200]

    # Груз
    cargo_match = re.search(
        r'(?:груз|наименование груза)[:\s]*([^\n]+)',
        text, re.IGNORECASE
    )
    if cargo_match:
        wb.cargo_description = cargo_match.group(1).strip()[:200]

    return wb if wb.number else None


def _parse_postal_tracking_page(text: str, page_num: int) -> Optional[PostalShipment]:
    """Парсит страницу с отслеживанием почтового отправления."""
    shipment = PostalShipment()
    shipment.source_page = page_num

    # Трек-номер
    # с почтовым идентификатором 80514110186166
    track_match = re.search(
        r'(?:идентификатор|трек)[^\d]*(\d{14})',
        text, re.IGNORECASE
    )
    if track_match:
        shipment.track_number = track_match.group(1)
    else:
        # Ищем просто 14-значный номер в начале текста
        track_match = re.search(r'\b(\d{14})\b', text[:500])
        if track_match:
            shipment.track_number = track_match.group(1)

    # Отправитель
    sender_match = re.search(
        r'отправитель[:\s]*([^\n]+)',
        text, re.IGNORECASE
    )
    if sender_match:
        shipment.sender = sender_match.group(1).strip()

    # Получатель
    receiver_match = re.search(
        r'получатель[:\s]*([^\n]+)',
        text, re.IGNORECASE
    )
    if receiver_match:
        shipment.receiver = receiver_match.group(1).strip()

    # Дата отправки (первая запись - "Присвоен трек-номер")
    send_match = re.search(
        r'(\d{1,2}\s+[а-яё]+\s+\d{4})[,\s]+[\d:]+\s+'
        r'(?:присвоен|приём|прием)',
        text, re.IGNORECASE
    )
    if send_match:
        shipment.send_date = _parse_russian_date(send_match.group(1))

    # Дата получения - ищем "Адресату по ОК коду" или "Вручение"
    # Формат: "23 июня 2025, 12:07 Адресату по ОК коду"
    receive_patterns = [
        r'(\d{1,2}\s+[а-яё]+\s+\d{4})[,\s]+[\d:]+\s+Адресату',
        r'(\d{1,2}\s+[а-яё]+\s+\d{4})[,\s]+[\d:]+\s+[Вв]ручен',
        r'(\d{1,2}\s+[а-яё]+\s+\d{4})[,\s]+[\d:]+\s+[Пп]олучен',
    ]
    for pattern in receive_patterns:
        receive_match = re.search(pattern, text)
        if receive_match:
            shipment.received_date = _parse_russian_date(receive_match.group(1))
            break

    return shipment if shipment.track_number else None


def _parse_russian_date(text: str) -> str:
    """Парсит дату в русском формате (23 июня 2025) в ДД.ММ.ГГГГ."""
    months = {
        'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04',
        'мая': '05', 'июня': '06', 'июля': '07', 'августа': '08',
        'сентября': '09', 'октября': '10', 'ноября': '11', 'декабря': '12'
    }

    match = re.search(r'(\d{1,2})\s+([а-яё]+)\s+(\d{4})', text, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = match.group(3)

        month = months.get(month_name, '01')
        return f"{day:02d}.{month}.{year}"

    return ""


def _parse_invoice_page(text: str, page_num: int) -> Optional[Dict[str, Any]]:
    """Парсит страницу со счётом на оплату."""
    invoice = {}

    # Номер и дата счёта
    match = re.search(
        r'счет[^\n]*№\s*(\d+)\s+от\s+(\d{1,2}[./]?\s*[а-яё]*\s*\d{4})',
        text, re.IGNORECASE
    )
    if match:
        invoice["number"] = match.group(1)
        invoice["date"] = _parse_date(match.group(2)) or match.group(2)

    # Сумма
    total_match = re.search(
        r'(?:всего к оплате|итого)[:\s]*(\d[\d\s,\.]+)',
        text, re.IGNORECASE
    )
    if total_match:
        invoice["amount"] = str(_parse_amount(total_match.group(1)))

    return invoice if invoice else None


# =============================================================================
# Связывание документов
# =============================================================================

def link_documents(
    applications: List[TransportApplication],
    waybills: List[Waybill],
    postal_shipments: List[PostalShipment],
    document_packages: List[Dict[str, Any]]
) -> List[TransportApplication]:
    """
    Связывает документы между собой:
    - Заявка ↔ Накладная (по дате, номеру)
    - Заявка ↔ Почтовый трек (по файлу-источнику)

    Returns:
        Обновлённый список заявок с заполненными связями
    """
    # Собираем payment_days из пакетов (обычно одинаковый для всех)
    common_payment_days = 0
    for package in document_packages:
        pkg_app = package.get("application")
        if pkg_app and pkg_app.payment_days > 0:
            common_payment_days = pkg_app.payment_days
            break

    # Обогащаем заявки данными из пакетов документов
    for package in document_packages:
        pkg_app = package.get("application")
        if not pkg_app:
            continue

        # Ищем соответствующую заявку
        for app in applications:
            if _applications_match(app, pkg_app):
                # Обновляем данные из пакета
                _merge_applications(app, pkg_app)

                # Связываем накладные
                for wb in package.get("waybills", []):
                    if wb.number and not app.waybill_number:
                        app.waybill_number = wb.number
                        app.waybill_date = wb.date

                # Связываем почтовые отправления
                for postal in package.get("postal_shipments", []):
                    if postal.track_number and not app.postal_track:
                        app.postal_track = postal.track_number
                        app.docs_received_date = postal.received_date

                break

    # Применяем общий payment_days ко всем заявкам
    if common_payment_days > 0:
        for app in applications:
            if app.payment_days == 0:
                app.payment_days = common_payment_days
                app.payment_terms = (
                    f"{common_payment_days} рабочих дней "
                    "с даты получения документов"
                )

    # Если заявка из пакета не найдена в списке - добавляем
    for package in document_packages:
        pkg_app = package.get("application")
        if not pkg_app:
            continue

        found = any(_applications_match(app, pkg_app) for app in applications)
        if not found:
            # Добавляем новую заявку из пакета
            for wb in package.get("waybills", []):
                if wb.number:
                    pkg_app.waybill_number = wb.number
                    pkg_app.waybill_date = wb.date
                    break
            for postal in package.get("postal_shipments", []):
                if postal.track_number:
                    pkg_app.postal_track = postal.track_number
                    pkg_app.docs_received_date = postal.received_date
                    break
            applications.append(pkg_app)

    return applications


def link_documents_full(
    claim_data: 'ExternalClaimData',
    document_packages: List[Dict[str, Any]]
) -> 'ExternalClaimData':
    """
    Высокоуровневая функция для связывания документов.
    Принимает ExternalClaimData и пакеты документов, возвращает обновлённый ExternalClaimData.
    """
    # Собираем все накладные и почтовые отправления из пакетов
    all_waybills = []
    all_postal = []
    for pkg in document_packages:
        all_waybills.extend(pkg.get("waybills", []))
        all_postal.extend(pkg.get("postal_shipments", []))

    # Связываем документы
    linked_apps = link_documents(
        claim_data.applications or [],
        all_waybills,
        all_postal,
        document_packages
    )

    # Обновляем данные
    claim_data.applications = linked_apps

    # Обновляем linked_waybill и linked_shipment для каждой заявки
    for app in claim_data.applications:
        # Связываем накладную
        if app.waybill_number and not app.linked_waybill:
            for wb in all_waybills:
                if wb.number == app.waybill_number:
                    app.linked_waybill = wb
                    break
            else:
                # Создаём объект накладной из данных заявки
                app.linked_waybill = Waybill(
                    number=app.waybill_number,
                    date=app.waybill_date
                )

        # Связываем почтовое отправление
        if app.postal_track and not app.linked_shipment:
            for postal in all_postal:
                if postal.track_number == app.postal_track:
                    app.linked_shipment = postal
                    break
            else:
                # Создаём объект из данных заявки
                app.linked_shipment = PostalShipment(
                    track_number=app.postal_track,
                    received_date=app.docs_received_date
                )

    return claim_data


def match_documents_with_applications(
    pdf_paths: List[str],
    applications: List[TransportApplication]
) -> Dict[str, List[MatchingReport]]:
    """
    Сопоставляет документы из PDF файлов с заявками используя document_matcher.

    Реализует правила определения принадлежности документов:
    - По ТС + прицеп + дата (high confidence)
    - По ТС + дата (high confidence)
    - По водителю + дата (high confidence)
    - По номеру документа (medium confidence)
    - По пакетной логике (low confidence)

    Args:
        pdf_paths: Список путей к PDF файлам
        applications: Список заявок для сопоставления

    Returns:
        Словарь {app_number: [MatchingReport, ...]} - документы по каждой заявке
    """
    # Конвертируем заявки в формат document_matcher
    app_infos = []
    for app in applications:
        app_info = ApplicationInfo(
            number=app.number,
            date=app.date,
            vehicle_plate=app.vehicle_plate,
            trailer_plate=app.trailer_plate,
            driver_name=app.driver_name,
            load_date=app.load_date,
            route=app.route,
            amount=float(app.amount_with_vat) if app.amount_with_vat else 0.0
        )
        app_infos.append(app_info)

    # Обрабатываем каждый PDF
    results_by_app: Dict[str, List[MatchingReport]] = {}
    all_reports = []

    for pdf_path in pdf_paths:
        try:
            report = match_documents_in_pdf(pdf_path, app_infos)
            all_reports.append(report)

            # Группируем результаты по заявкам
            for match_result in report.results:
                if match_result.is_matched and match_result.application:
                    app_num = match_result.application.number
                    if app_num not in results_by_app:
                        results_by_app[app_num] = []
                    # Добавляем отчёт (для отслеживания источника)
                    results_by_app[app_num].append(report)

        except Exception as e:
            logger.error(f"Error matching documents in {pdf_path}: {e}")

    return results_by_app


def enrich_applications_from_matched_documents(
    applications: List[TransportApplication],
    pdf_paths: List[str]
) -> List[TransportApplication]:
    """
    Обогащает заявки данными из сопоставленных документов.

    Использует document_matcher для определения, какие документы
    относятся к каждой заявке, и извлекает из них:
    - Номера накладных
    - ТС и прицепы (если не заполнены)
    - Водителей (если не заполнены)
    - Даты документов
    """
    if not pdf_paths or not applications:
        return applications

    # Конвертируем заявки в формат document_matcher
    app_infos = []
    for app in applications:
        app_info = ApplicationInfo(
            number=app.number,
            date=app.date,
            vehicle_plate=app.vehicle_plate,
            trailer_plate=app.trailer_plate,
            driver_name=app.driver_name,
            load_date=app.load_date,
            route=app.route,
            amount=float(app.amount_with_vat) if app.amount_with_vat else 0.0
        )
        app_infos.append(app_info)

    # Обрабатываем каждый PDF
    for pdf_path in pdf_paths:
        try:
            report = match_documents_in_pdf(pdf_path, app_infos)

            # Обогащаем заявки данными из документов
            for match_result in report.results:
                if not match_result.is_matched:
                    continue
                if match_result.confidence == ConfidenceLevel.LOW:
                    # Низкая уверенность - не обогащаем, только логируем
                    logger.info(
                        f"Документ {match_result.document.page_range} "
                        f"({match_result.document.doc_type.value}) отнесён к заявке "
                        f"{match_result.application.number} с низкой уверенностью: "
                        f"{match_result.reason_details}"
                    )
                    continue

                # Находим соответствующую заявку
                matched_app_num = match_result.application.number
                for app in applications:
                    if app.number != matched_app_num:
                        continue

                    doc_ids = match_result.document.identifiers

                    # Функция очистки от переносов строк
                    def clean_value(val: str) -> str:
                        if not val:
                            return ""
                        return " ".join(val.replace("\n", " ").replace("\r", " ").split())

                    # Обогащаем ТС (если не заполнен)
                    if not app.vehicle_plate and doc_ids.vehicle_plate:
                        app.vehicle_plate = clean_value(doc_ids.vehicle_plate)

                    # Обогащаем прицеп (если не заполнен)
                    if not app.trailer_plate and doc_ids.trailer_plate:
                        app.trailer_plate = clean_value(doc_ids.trailer_plate)

                    # Обогащаем водителя (если не заполнен)
                    if not app.driver_name and doc_ids.driver_name:
                        app.driver_name = clean_value(doc_ids.driver_name)

                    # Обогащаем номер накладной (если не заполнен)
                    if not app.waybill_number and doc_ids.document_number:
                        from document_matcher import DocumentType
                        if match_result.document.doc_type in (
                            DocumentType.TRANSPORT_WAYBILL,
                            DocumentType.CARGO_WAYBILL
                        ):
                            app.waybill_number = doc_ids.document_number
                            if doc_ids.document_date:
                                app.waybill_date = doc_ids.document_date

                    break

        except Exception as e:
            logger.error(f"Error enriching from {pdf_path}: {e}")

    return applications


def _applications_match(app1: TransportApplication, app2: TransportApplication) -> bool:
    """Проверяет, совпадают ли заявки."""
    # Нормализуем номера - извлекаем основную часть (до слэша)
    def get_base_number(num: str) -> str:
        # СП139948/1 -> 139948, СП139948/2 -> 139948
        match = re.search(r'(\d{5,})', num)
        return match.group(1) if match else ""

    base1 = get_base_number(app1.number)
    base2 = get_base_number(app2.number)

    # Совпадение по базовому номеру (139948)
    if base1 and base2 and base1 == base2:
        return True

    # Проверяем по дате загрузки (если одна дата - та же перевозка)
    if app1.load_date and app2.load_date and app1.load_date == app2.load_date:
        return True

    # Проверяем по дате и сумме
    if (app1.date and app2.date and
        app1.amount_with_vat > 0 and
        app1.amount_with_vat == app2.amount_with_vat):
        return True

    return False


def _merge_applications(target: TransportApplication, source: TransportApplication):
    """Объединяет данные заявок (заполняет пустые поля в target из source)."""
    for field in [
        'route', 'vehicle_plate', 'trailer_plate', 'driver_name', 'driver_inn',
        'load_date', 'payment_terms', 'waybill_number', 'waybill_date',
        'postal_track', 'docs_received_date'
    ]:
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))

    if target.payment_days == 0 and source.payment_days > 0:
        target.payment_days = source.payment_days

    if target.amount_without_vat == 0 and source.amount_without_vat > 0:
        target.amount_without_vat = source.amount_without_vat

    if target.amount_with_vat == 0 and source.amount_with_vat > 0:
        target.amount_with_vat = source.amount_with_vat


def enrich_with_postal_api(
    applications: List[TransportApplication],
    postal_shipments: List[PostalShipment],
    doc_links: List[dict]
) -> List[TransportApplication]:
    """
    Обогащает заявки датами получения документов через API Почты России.

    Args:
        applications: Список заявок
        postal_shipments: Список почтовых отправлений
        doc_links: Связи документов из претензии (накладные -> трек)
    """
    # Собираем все трек-номера для запроса
    tracks_to_fetch = set()

    for app in applications:
        if app.postal_track and not app.docs_received_date:
            tracks_to_fetch.add(app.postal_track)

    for shipment in postal_shipments:
        if shipment.track_number and not shipment.received_date:
            tracks_to_fetch.add(shipment.track_number)

    for link in doc_links:
        if link.get('track_number'):
            tracks_to_fetch.add(link['track_number'])

    # Получаем даты по трекам через API
    track_dates = {}
    for track in tracks_to_fetch:
        try:
            send_date, received_date = get_tracking_dates_from_api(track)
            if send_date or received_date:
                track_dates[track] = {
                    'send_date': send_date,
                    'received_date': received_date
                }
                logger.info(
                    f"Трек {track}: отправлено {send_date}, "
                    f"получено {received_date}"
                )
        except Exception as e:
            logger.warning(f"Ошибка получения данных по треку {track}: {e}")

    # Обновляем даты в заявках
    for app in applications:
        if app.postal_track and app.postal_track in track_dates:
            dates = track_dates[app.postal_track]
            if not app.docs_received_date and dates.get('received_date'):
                app.docs_received_date = dates['received_date']

    # Обновляем даты в почтовых отправлениях
    for shipment in postal_shipments:
        if shipment.track_number in track_dates:
            dates = track_dates[shipment.track_number]
            if not shipment.send_date and dates.get('send_date'):
                shipment.send_date = dates['send_date']
            if not shipment.received_date and dates.get('received_date'):
                shipment.received_date = dates['received_date']

    # Связываем заявки с треками через doc_links (по номерам накладных)
    for app in applications:
        if app.docs_received_date:
            continue

        # Ищем трек для накладной этой заявки
        for link in doc_links:
            if app.waybill_number in link.get('waybill_numbers', []):
                track = link.get('track_number')
                if track:
                    if not app.postal_track:
                        app.postal_track = track
                    if track in track_dates:
                        dates = track_dates[track]
                        if dates.get('received_date'):
                            app.docs_received_date = dates['received_date']
                break

    # Fallback: связываем заявки с треками по порядку, если не удалось по накладным
    unlinked_apps = [app for app in applications if not app.postal_track]
    if unlinked_apps and doc_links:
        for i, app in enumerate(unlinked_apps):
            if i < len(doc_links):
                link = doc_links[i]
                track = link.get('track_number')
                if track:
                    app.postal_track = track
                    if track in track_dates:
                        dates = track_dates[track]
                        if dates.get('received_date'):
                            app.docs_received_date = dates['received_date']
                    # Также берём первую накладную из связи
                    waybills = link.get('waybill_numbers', [])
                    if waybills and not app.waybill_number:
                        app.waybill_number = waybills[0]

    return applications


# =============================================================================
# Главная функция парсинга
# =============================================================================

def parse_external_claim(
    claim_pdf_path: str,
    document_pdf_paths: List[str] = None,
    legal_services_pdf_path: str = None
) -> ExternalClaimData:
    """
    Парсит внешнюю претензию и связанные документы.

    Args:
        claim_pdf_path: Путь к PDF с претензией
        document_pdf_paths: Пути к PDF с документами по перевозкам
        legal_services_pdf_path: Путь к PDF с договором юр.услуг

    Returns:
        ExternalClaimData со всеми извлечёнными данными
    """
    result = ExternalClaimData()
    result.source_files.append(os.path.basename(claim_pdf_path))

    # Парсим претензию
    claim_text = ""
    try:
        with pdfplumber.open(claim_pdf_path) as pdf:
            for page in pdf.pages:
                claim_text += (page.extract_text() or "") + "\n\n"
    except Exception as e:
        logger.error(f"Error reading claim PDF: {e}")
        result.warnings.append(f"Ошибка чтения претензии: {e}")
        return result

    # Извлекаем стороны
    result.plaintiff, result.defendant = extract_parties_from_claim(claim_text)

    # Извлекаем базовый договор
    contract_match = re.search(
        r'договор[а]?\s*№?\s*([A-Za-zА-Яа-я\d\-/]+)\s+от\s+(\d{1,2}[./]\d{1,2}[./]\d{4})',
        claim_text, re.IGNORECASE
    )
    if contract_match:
        result.base_contract_number = contract_match.group(1)
        result.base_contract_date = _parse_date(contract_match.group(2))

    # Извлекаем заявки
    result.applications = extract_applications_from_claim(claim_text)

    # Извлекаем накладные
    result.waybills = extract_waybills_from_claim(claim_text)

    # Извлекаем почтовые отправления
    result.postal_shipments = extract_postal_shipments_from_claim(claim_text)

    # Извлекаем общую сумму долга
    debt_match = re.search(
        r'задолженност[ьи]\s+(?:в сумме\s+)?(\d[\d\s]+)',
        claim_text, re.IGNORECASE
    )
    if debt_match:
        result.total_debt = _parse_amount(debt_match.group(1))

    # Извлекаем дату претензии
    claim_date_match = re.search(
        r'(\d{1,2}[./]\d{1,2}[./]\d{4})\s*г?\.?\s*$',
        claim_text, re.MULTILINE
    )
    if claim_date_match:
        result.claim_date = _parse_date(claim_date_match.group(1))

    # Парсим пакеты документов
    document_packages = []
    if document_pdf_paths:
        for pdf_path in document_pdf_paths:
            result.source_files.append(os.path.basename(pdf_path))
            package = parse_document_package(pdf_path)
            if package:
                document_packages.append(package)

    # Извлекаем связи между документами из текста претензии
    doc_links = extract_document_links_from_claim(claim_text)

    # Связываем документы
    if document_packages:
        result.applications = link_documents(
            result.applications,
            result.waybills,
            result.postal_shipments,
            document_packages
        )

    # Обогащаем данными о почтовых отправлениях через API
    result.applications = enrich_with_postal_api(
        result.applications,
        result.postal_shipments,
        doc_links
    )

    # Дополняем данные с помощью LLM (Ollama)
    llm_data = parse_claim_with_llm(claim_text)
    if llm_data:
        result = apply_llm_data_to_result(result, llm_data)
        logger.info("Данные дополнены с помощью LLM")

    # Парсим юридические услуги
    if legal_services_pdf_path:
        result.source_files.append(os.path.basename(legal_services_pdf_path))
        result.legal_services = _parse_legal_services(legal_services_pdf_path)

    # Проверяем полноту данных
    _validate_data(result)

    return result


def _parse_legal_services(pdf_path: str) -> Optional[LegalServices]:
    """Парсит договор юридических услуг."""
    legal = LegalServices()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n\n"
    except Exception as e:
        logger.error(f"Error reading legal services PDF: {e}")
        return None

    # Номер и дата договора
    contract_match = re.search(
        r'договор[^\n]*№\s*(\d+)[^\n]*(\d{1,2}[./]\d{1,2}[./]\d{4})',
        text, re.IGNORECASE
    )
    if contract_match:
        legal.contract_number = contract_match.group(1)
        legal.contract_date = _parse_date(contract_match.group(2))

    # Сумма
    amount_match = re.search(
        r'стоимость[^\d]*(\d[\d\s,\.]+)\s*(?:руб|рублей)',
        text, re.IGNORECASE
    )
    if amount_match:
        legal.amount = _parse_amount(amount_match.group(1))

    # Исполнитель
    contractor_match = re.search(
        r'(?:исполнитель|ИП)[:\s]+([А-ЯЁа-яё\s\.]+)',
        text, re.IGNORECASE
    )
    if contractor_match:
        legal.contractor_name = contractor_match.group(1).strip()

    # ИНН исполнителя
    inn_match = re.search(r'ИНН\s*(\d{10,12})', text)
    if inn_match:
        legal.contractor_inn = inn_match.group(1)

    # Платёжное поручение
    payment_match = re.search(
        r'(?:платежн\w+\s+поруч\w+|п/п)\s*№?\s*(\d+)[^\n]*(\d{1,2}[./]\d{1,2}[./]\d{4})',
        text, re.IGNORECASE
    )
    if payment_match:
        legal.payment_number = payment_match.group(1)
        legal.payment_date = _parse_date(payment_match.group(2))

    return legal if legal.contract_number else None


def _validate_data(data: ExternalClaimData):
    """Проверяет полноту данных и добавляет предупреждения."""

    if not data.plaintiff.name:
        data.warnings.append("Не удалось определить истца (перевозчика)")
    if not data.plaintiff.inn:
        data.warnings.append("Не найден ИНН истца")

    if not data.defendant.name:
        data.warnings.append("Не удалось определить ответчика (заказчика)")
    if not data.defendant.inn:
        data.warnings.append("Не найден ИНН ответчика")

    if not data.applications:
        data.warnings.append("Не найдены заявки на перевозку")
    else:
        for app in data.applications:
            if not app.amount_with_vat or app.amount_with_vat == 0:
                data.warnings.append(f"Не найдена сумма для заявки {app.number}")
            if not app.docs_received_date:
                data.warnings.append(
                    f"Не найдена дата получения документов для заявки {app.number}"
                )

    # Сверяем общую сумму
    if data.applications:
        calc_total = sum(app.amount_with_vat for app in data.applications)
        if data.total_debt and abs(calc_total - data.total_debt) > 1:
            data.warnings.append(
                f"Сумма заявок ({calc_total}) не совпадает с суммой долга ({data.total_debt})"
            )


# =============================================================================
# Преобразование в формат для искового
# =============================================================================

def convert_to_claim_data(external_data: ExternalClaimData) -> Dict[str, Any]:
    """
    Преобразует данные внешней претензии в формат для генерации искового.
    """
    claim_data = {
        # Истец
        "plaintiff_name": external_data.plaintiff.name,
        "plaintiff_inn": external_data.plaintiff.inn,
        "plaintiff_kpp": external_data.plaintiff.kpp,
        "plaintiff_ogrn": external_data.plaintiff.ogrn,
        "plaintiff_address": external_data.plaintiff.address,

        # Ответчик
        "defendant_name": external_data.defendant.name,
        "defendant_inn": external_data.defendant.inn,
        "defendant_kpp": external_data.defendant.kpp,
        "defendant_ogrn": external_data.defendant.ogrn,
        "defendant_address": external_data.defendant.address,

        # Договор
        "base_contract_number": external_data.base_contract_number,
        "base_contract_date": external_data.base_contract_date,

        # Финансы
        "debt": str(external_data.total_debt) if external_data.total_debt else "",

        # Претензия
        "claim_date": external_data.claim_date,

        # Заявки в формате групп
        "pretension_groups": [],

        # Источники
        "source_files": external_data.source_files,
        "warnings": external_data.warnings,
    }

    # Преобразуем заявки в группы
    for app in external_data.applications:
        group = {
            "application": f"Заявка № {app.number} от {app.date}",
            "application_number": app.number,
            "application_date": app.date,
            "amount": float(app.amount_with_vat),
            "amount_without_vat": float(app.amount_without_vat),
            "route": app.route,
            "vehicle_plate": app.vehicle_plate,
            "trailer_plate": app.trailer_plate,
            "driver_name": app.driver_name,
            "driver_inn": app.driver_inn,
            "load_date": app.load_date,
            "payment_days": app.payment_days,
            "payment_terms": app.payment_terms,
            "waybill_number": app.waybill_number,
            "waybill_date": app.waybill_date,
            "docs_track_number": app.postal_track,
            "docs_received_date": app.docs_received_date,
            "cargo_docs": [
                f"ТН № {app.waybill_number} от {app.waybill_date}"
            ] if app.waybill_number else [],
        }
        claim_data["pretension_groups"].append(group)

    # Юридические услуги
    if external_data.legal_services:
        ls = external_data.legal_services
        claim_data["legal_fees"] = str(ls.amount) if ls.amount else ""
        claim_data["legal_contract_number"] = ls.contract_number
        claim_data["legal_contract_date"] = ls.contract_date
        claim_data["legal_payment_number"] = ls.payment_number
        claim_data["legal_payment_date"] = ls.payment_date

    return claim_data


# =============================================================================
# CLI для тестирования
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python external_claim_parser.py <claim.pdf> [doc1.pdf doc2.pdf ...] [--legal legal.pdf]")
        sys.exit(1)

    claim_path = sys.argv[1]
    doc_paths = []
    legal_path = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--legal":
            legal_path = sys.argv[i + 1]
            i += 2
        else:
            doc_paths.append(sys.argv[i])
            i += 1

    print(f"\n=== Парсинг претензии: {claim_path} ===")
    print(f"Документы: {doc_paths}")
    print(f"Юр.услуги: {legal_path}")

    result = parse_external_claim(claim_path, doc_paths, legal_path)

    print(f"\n=== Результат ===")
    print(f"Истец: {result.plaintiff.name} (ИНН: {result.plaintiff.inn})")
    print(f"Ответчик: {result.defendant.name} (ИНН: {result.defendant.inn})")
    print(f"Договор: {result.base_contract_number} от {result.base_contract_date}")
    print(f"Общий долг: {result.total_debt}")
    print(f"\nЗаявки ({len(result.applications)}):")
    for app in result.applications:
        print(f"  - {app.number} от {app.date}: {app.amount_with_vat} руб.")
        print(f"    Накладная: {app.waybill_number}, Трек: {app.postal_track}")
        print(f"    Получено: {app.docs_received_date}, Срок: {app.payment_days} дн.")

    if result.legal_services:
        ls = result.legal_services
        print(f"\nЮр.услуги: {ls.amount} руб. (договор {ls.contract_number})")

    if result.warnings:
        print(f"\n⚠️ Предупреждения:")
        for w in result.warnings:
            print(f"  - {w}")

    # Выводим данные для искового
    print("\n=== Данные для искового ===")
    claim_data = convert_to_claim_data(result)
    print(json.dumps(claim_data, ensure_ascii=False, indent=2, default=str))
