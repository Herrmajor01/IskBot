#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль сопоставления документов с заявками на перевозку.

Реализует алгоритм определения, к какой заявке относится каждый документ
на основе идентификаторов: госномер ТС, прицеп, водитель, даты, номера документов.

Правила приоритета:
1. ТС + прицеп + дата (high confidence)
2. ТС + дата (high confidence)
3. Водитель + дата (high confidence)
4. Номер документа в пакете (medium confidence)
5. Пакетная логика - соседство с якорным документом (low confidence)
6. Дата + вторичные признаки (low confidence)
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

logger = logging.getLogger(__name__)


# =============================================================================
# Перечисления и константы
# =============================================================================

class DocumentType(Enum):
    """Типы документов грузоперевозки."""
    TRANSPORT_WAYBILL = "transport_waybill"  # Транспортная накладная (ТН/ТрН)
    CARGO_WAYBILL = "cargo_waybill"  # Товарно-транспортная накладная (ТТН)
    TORG12 = "torg12"  # Товарная накладная ТОРГ-12
    EXPEDITOR_RECEIPT = "expeditor_receipt"  # Экспедиторская расписка
    IDLE_SHEET = "idle_sheet"  # Лист простоя
    ACT = "act"  # Акт (приема, простоя, выполненных работ)
    REGISTRY = "registry"  # Реестр сопроводительных документов
    APPENDIX = "appendix"  # Приложение к накладной
    INVOICE = "invoice"  # Счёт-фактура
    UPD = "upd"  # УПД
    POWER_OF_ATTORNEY = "power_of_attorney"  # Доверенность
    OTHER = "other"  # Иные документы
    UNKNOWN = "unknown"  # Не определён


class ConfidenceLevel(Enum):
    """Уровень уверенности в сопоставлении."""
    HIGH = "high"  # ТС+прицеп+дата, ТС+дата, водитель+дата
    MEDIUM = "medium"  # Номер документа, дата+вторичные признаки
    LOW = "low"  # Только пакет/соседство


class MatchReason(Enum):
    """Основание привязки документа к заявке."""
    VEHICLE_TRAILER_DATE = "vehicle_trailer_date"  # ТС + прицеп + дата
    VEHICLE_DATE = "vehicle_date"  # ТС + дата
    DRIVER_DATE = "driver_date"  # Водитель + дата
    DOCUMENT_NUMBER = "document_number"  # Номер перевозочного документа
    PACKAGE = "package"  # Пакет (между якорными документами)
    DATE_SECONDARY = "date_secondary"  # Дата + вторичные признаки
    MANUAL = "manual"  # Ручное назначение
    UNMATCHED = "unmatched"  # Не сопоставлен


# Паттерны для определения типа документа (заголовки)
DOCUMENT_TYPE_PATTERNS = {
    DocumentType.TRANSPORT_WAYBILL: [
        r'транспортная\s+накладная',
        r'ТрН\s*№',
        r'ТН\s*№',
    ],
    DocumentType.CARGO_WAYBILL: [
        r'товарно[\s\-]*транспортная\s+накладная',
        r'ТТН\s*№',
    ],
    DocumentType.TORG12: [
        r'торг[\s\-]*12',
        r'товарная\s+накладная',
        r'унифицированная\s+форма\s+№\s*торг',
    ],
    DocumentType.EXPEDITOR_RECEIPT: [
        r'экспедиторская\s+расписка',
    ],
    DocumentType.IDLE_SHEET: [
        r'лист\s+простоя',
        r'акт\s+простоя',
    ],
    DocumentType.ACT: [
        r'акт\s+(?:приема|приёма|выполненных|сверки|об\s+оказании)',
    ],
    DocumentType.REGISTRY: [
        r'реестр\s+(?:сопроводительных|документов|накладных)',
    ],
    DocumentType.APPENDIX: [
        r'приложение\s*№?\s*\d*\s*к\s+(?:транспортной|товарной)',
        r'перечень\s+.*к\s+(?:транспортной|товарной)',
    ],
    DocumentType.INVOICE: [
        r'счёт[\s\-]*фактура',
        r'счет[\s\-]*фактура',
    ],
    DocumentType.UPD: [
        r'универсальный\s+передаточный\s+документ',
        r'УПД\s*№',
    ],
    DocumentType.POWER_OF_ATTORNEY: [
        r'доверенность',
    ],
}

# Паттерн госномера РФ (с учётом разных форматов)
# Формат: А123БВ77 или А 123 БВ 77 или A123BC77 (латиница)
VEHICLE_PLATE_PATTERN = re.compile(
    r'[АВЕКМНОРСТУХABEKMHOPCTYX]\s*'
    r'(\d{3})\s*'
    r'[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s*'
    r'(\d{2,3})',
    re.IGNORECASE
)

# Паттерн прицепа (формат: АВ1234-77 или АВ 1234 77)
TRAILER_PLATE_PATTERN = re.compile(
    r'[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s*'
    r'(\d{4,6})\s*'
    r'[\-\s]?(\d{2,3})',
    re.IGNORECASE
)

# Паттерн даты
DATE_PATTERNS = [
    r'(\d{1,2})[./](\d{1,2})[./](\d{4})',  # DD.MM.YYYY или DD/MM/YYYY
    r'(\d{1,2})[./](\d{1,2})[./](\d{2})\b',  # DD.MM.YY
    r'(\d{1,2})\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})',
]

MONTH_NAMES = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
    'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
    'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}


# =============================================================================
# Классы данных
# =============================================================================

@dataclass
class DocumentIdentifiers:
    """Идентификаторы, извлечённые из документа."""
    # Первый уровень (сильные)
    vehicle_plate: str = ""  # Госномер тягача
    trailer_plate: str = ""  # Госномер прицепа
    driver_name: str = ""  # ФИО водителя
    document_number: str = ""  # Номер ТН/ТТН/заказа
    document_date: str = ""  # Дата документа

    # Второй уровень (вторичные)
    loading_address: str = ""  # Адрес погрузки
    unloading_address: str = ""  # Адрес выгрузки
    shipper_name: str = ""  # Грузоотправитель
    consignee_name: str = ""  # Грузополучатель
    order_number: str = ""  # Номер заявки/заказа
    amount: float = 0.0  # Сумма


@dataclass
class ParsedDocument:
    """Распарсенный документ из PDF."""
    doc_type: DocumentType = DocumentType.UNKNOWN
    page_start: int = 0  # Начальная страница (1-based)
    page_end: int = 0  # Конечная страница (1-based)
    identifiers: DocumentIdentifiers = field(default_factory=DocumentIdentifiers)
    raw_text: str = ""  # Исходный текст
    source_file: str = ""  # Имя файла-источника

    @property
    def page_range(self) -> str:
        """Диапазон страниц в читаемом формате."""
        if self.page_start == self.page_end:
            return f"стр. {self.page_start}"
        return f"стр. {self.page_start}-{self.page_end}"


@dataclass
class ApplicationInfo:
    """Информация о заявке для сопоставления."""
    number: str = ""  # Номер заявки (СП...)
    date: str = ""  # Дата заявки
    vehicle_plate: str = ""  # Госномер тягача
    trailer_plate: str = ""  # Госномер прицепа
    driver_name: str = ""  # ФИО водителя
    load_date: str = ""  # Дата погрузки
    unload_date: str = ""  # Дата разгрузки
    route: str = ""  # Маршрут
    amount: float = 0.0  # Сумма


@dataclass
class MatchResult:
    """Результат сопоставления документа с заявкой."""
    document: ParsedDocument
    application: Optional[ApplicationInfo] = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    reason: MatchReason = MatchReason.UNMATCHED
    reason_details: str = ""  # Подробности (например, "страница внутри пакета")

    @property
    def is_matched(self) -> bool:
        return self.application is not None


@dataclass
class MatchingReport:
    """Полный отчёт о сопоставлении документов."""
    source_file: str = ""
    total_pages: int = 0
    documents: List[ParsedDocument] = field(default_factory=list)
    results: List[MatchResult] = field(default_factory=list)
    unmatched_documents: List[ParsedDocument] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# =============================================================================
# Нормализация данных
# =============================================================================

def normalize_vehicle_plate(plate: str) -> str:
    """
    Нормализует госномер ТС.

    Приводит к формату: А123БВ77 (без пробелов, кириллица в верхнем регистре)
    """
    if not plate:
        return ""

    # Удаляем пробелы, переносы и приводим к верхнему регистру
    plate = plate.upper().replace(" ", "").replace("-", "").replace("\n", "").replace("\r", "")

    # Заменяем латиницу на кириллицу (для унификации)
    lat_to_cyr = {
        'A': 'А', 'B': 'В', 'E': 'Е', 'K': 'К', 'M': 'М',
        'H': 'Н', 'O': 'О', 'P': 'Р', 'C': 'С', 'T': 'Т',
        'Y': 'У', 'X': 'Х'
    }
    for lat, cyr in lat_to_cyr.items():
        plate = plate.replace(lat, cyr)

    return plate


def normalize_driver_name(name: str) -> str:
    """
    Нормализует ФИО водителя.

    Приводит к формату: Фамилия Имя Отчество (именительный падеж, если возможно)
    """
    if not name:
        return ""

    # Заменяем переносы на пробелы
    name = name.replace("\n", " ").replace("\r", " ")

    # Удаляем лишние пробелы
    name = " ".join(name.split())

    # Приводим к title case
    name = name.title()

    return name


def parse_date(text: str) -> Optional[datetime]:
    """Парсит дату из текста."""
    if not text:
        return None

    # DD.MM.YYYY
    match = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', text)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)
        except ValueError:
            pass

    # DD.MM.YY
    match = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{2})\b', text)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            year = 2000 + year if year < 50 else 1900 + year
            return datetime(year, month, day)
        except ValueError:
            pass

    # "DD месяца YYYY"
    for month_name, month_num in MONTH_NAMES.items():
        pattern = rf'(\d{{1,2}})\s+{month_name}\s+(\d{{4}})'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                day, year = int(match.group(1)), int(match.group(2))
                return datetime(year, month_num, day)
            except ValueError:
                pass

    return None


def format_date(dt: Optional[datetime]) -> str:
    """Форматирует дату в строку DD.MM.YYYY."""
    if not dt:
        return ""
    return dt.strftime("%d.%m.%Y")


def dates_match(date1: str, date2: str, tolerance_days: int = 1) -> bool:
    """
    Проверяет, совпадают ли даты (с допуском).

    Args:
        date1: Первая дата
        date2: Вторая дата
        tolerance_days: Допустимая разница в днях
    """
    dt1 = parse_date(date1)
    dt2 = parse_date(date2)

    if not dt1 or not dt2:
        return False

    diff = abs((dt1 - dt2).days)
    return diff <= tolerance_days


def date_in_range(date: str, start_date: str, end_date: str, tolerance_days: int = 1) -> bool:
    """
    Проверяет, попадает ли дата в диапазон.

    Args:
        date: Проверяемая дата
        start_date: Начало диапазона
        end_date: Конец диапазона
        tolerance_days: Допуск по краям диапазона
    """
    dt = parse_date(date)
    dt_start = parse_date(start_date)
    dt_end = parse_date(end_date)

    if not dt:
        return False

    # Если нет начала/конца, проверяем только по имеющейся границе
    if dt_start and dt_end:
        start = dt_start - timedelta(days=tolerance_days)
        end = dt_end + timedelta(days=tolerance_days)
        return start <= dt <= end
    elif dt_start:
        start = dt_start - timedelta(days=tolerance_days)
        return dt >= start
    elif dt_end:
        end = dt_end + timedelta(days=tolerance_days)
        return dt <= end

    return False


# =============================================================================
# Извлечение идентификаторов
# =============================================================================

def extract_vehicle_plates(text: str) -> List[str]:
    """Извлекает госномера ТС из текста."""
    plates = []

    # Ищем стандартные госномера
    for match in VEHICLE_PLATE_PATTERN.finditer(text):
        plate = match.group(0)
        normalized = normalize_vehicle_plate(plate)
        if normalized and normalized not in plates:
            plates.append(normalized)

    return plates


def extract_trailer_plates(text: str) -> List[str]:
    """Извлекает номера прицепов из текста."""
    plates = []

    # Ищем номера прицепов
    for match in TRAILER_PLATE_PATTERN.finditer(text):
        plate = match.group(0)
        normalized = normalize_vehicle_plate(plate)
        if normalized and normalized not in plates:
            plates.append(normalized)

    # Также ищем в контексте слова "прицеп"
    trailer_context = re.findall(
        r'прицеп[а]?\s+([А-ЯA-Z]{2}\s*\d{4,6}\s*[\-\s]?\d{2,3})',
        text, re.IGNORECASE
    )
    for plate in trailer_context:
        normalized = normalize_vehicle_plate(plate)
        if normalized and normalized not in plates:
            plates.append(normalized)

    return plates


def extract_driver_name(text: str) -> str:
    """Извлекает ФИО водителя из текста."""
    # Паттерны для поиска водителя
    patterns = [
        r'водитель[:\s]+([А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)',
        r'ФИО\s+водител[яь]?[:\s]+([А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)',
        r'принял[:\s]+([А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Проверяем, что это похоже на ФИО (минимум 2 слова)
            if len(name.split()) >= 2:
                return normalize_driver_name(name)

    return ""


def extract_document_number(text: str, doc_type: DocumentType) -> str:
    """Извлекает номер документа."""
    patterns = []

    if doc_type in (DocumentType.TRANSPORT_WAYBILL, DocumentType.CARGO_WAYBILL):
        patterns = [
            r'(?:ТН|ТТН|ТрН|транспортная\s+накладная)\s*№\s*([A-Za-z0-9А-Яа-я/\-]+)',
            r'накладная\s*№\s*([A-Za-z0-9А-Яа-я/\-]+)',
        ]
    elif doc_type == DocumentType.TORG12:
        patterns = [
            r'(?:ТОРГ[\s\-]*12|товарная\s+накладная)\s*№\s*([A-Za-z0-9А-Яа-я/\-]+)',
        ]
    elif doc_type == DocumentType.IDLE_SHEET:
        patterns = [
            r'(?:лист\s+простоя|акт\s+простоя)\s*№\s*([A-Za-z0-9А-Яа-я/\-]+)',
        ]
    else:
        patterns = [
            r'№\s*([A-Za-z0-9А-Яа-я/\-]+)',
        ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return ""


def extract_dates(text: str) -> List[str]:
    """Извлекает все даты из текста."""
    dates = []

    # DD.MM.YYYY
    for match in re.finditer(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', text):
        date_str = f"{match.group(1).zfill(2)}.{match.group(2).zfill(2)}.{match.group(3)}"
        if date_str not in dates:
            dates.append(date_str)

    return dates


def extract_identifiers(text: str, doc_type: DocumentType) -> DocumentIdentifiers:
    """
    Извлекает все идентификаторы из текста документа.
    """
    ids = DocumentIdentifiers()

    # ТС
    vehicles = extract_vehicle_plates(text)
    if vehicles:
        ids.vehicle_plate = vehicles[0]

    # Прицеп
    trailers = extract_trailer_plates(text)
    if trailers:
        ids.trailer_plate = trailers[0]

    # Водитель
    ids.driver_name = extract_driver_name(text)

    # Номер документа
    ids.document_number = extract_document_number(text, doc_type)

    # Даты
    dates = extract_dates(text)
    if dates:
        ids.document_date = dates[0]

    # Адреса погрузки/выгрузки
    loading_match = re.search(
        r'(?:погрузк[аи]|отправлени[ея]|грузоотправител[ья])[:\s]+([^\n]{10,100})',
        text, re.IGNORECASE
    )
    if loading_match:
        ids.loading_address = loading_match.group(1).strip()[:100]

    unloading_match = re.search(
        r'(?:выгрузк[аи]|доставк[аи]|грузополучател[ья])[:\s]+([^\n]{10,100})',
        text, re.IGNORECASE
    )
    if unloading_match:
        ids.unloading_address = unloading_match.group(1).strip()[:100]

    return ids


# =============================================================================
# Определение типа и границ документов
# =============================================================================

def detect_document_type(text: str) -> DocumentType:
    """Определяет тип документа по тексту."""
    text_lower = text.lower()

    for doc_type, patterns in DOCUMENT_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return doc_type

    return DocumentType.UNKNOWN


def is_continuation_page(text: str, prev_doc_type: DocumentType) -> bool:
    """
    Проверяет, является ли страница продолжением предыдущего документа.
    """
    text_lower = text.lower()

    # Явные признаки продолжения
    continuation_patterns = [
        r'стр\.?\s*\d+',  # Стр. 2
        r'страница\s+\d+\s+из',  # Страница 2 из 5
        r'продолжение',
        r'лист\s+\d+',
    ]

    for pattern in continuation_patterns:
        if re.search(pattern, text_lower):
            return True

    # Если это ТОРГ-12 и есть табличные данные без заголовка
    if prev_doc_type == DocumentType.TORG12:
        # Проверяем наличие табличных строк без заголовка формы
        has_table_data = bool(re.search(r'\d+\s+\d+[\.,]\d+', text))
        has_header = bool(re.search(r'торг[\s\-]*12|товарная\s+накладная', text_lower))
        if has_table_data and not has_header:
            return True

    return False


def detect_document_boundaries(pages: List[str]) -> List[Tuple[int, int, DocumentType]]:
    """
    Определяет границы документов в списке страниц.

    Returns:
        List of (start_page, end_page, doc_type) - 1-based page numbers
    """
    boundaries = []
    current_start = 1
    current_type = DocumentType.UNKNOWN

    for i, page_text in enumerate(pages):
        page_num = i + 1
        detected_type = detect_document_type(page_text)

        if i == 0:
            # Первая страница
            current_type = detected_type
            current_start = page_num
        elif detected_type != DocumentType.UNKNOWN:
            # Найден заголовок нового документа
            if not is_continuation_page(page_text, current_type):
                # Закрываем предыдущий документ
                boundaries.append((current_start, page_num - 1, current_type))
                current_start = page_num
                current_type = detected_type
        elif is_continuation_page(page_text, current_type):
            # Продолжение текущего документа
            pass
        else:
            # Неопределённая страница - проверяем признаки нового документа
            # Если есть явный новый номер документа с датой
            new_doc_match = re.search(
                r'№\s*[A-Za-z0-9/\-]+\s+от\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}',
                page_text
            )
            if new_doc_match and current_type != DocumentType.UNKNOWN:
                boundaries.append((current_start, page_num - 1, current_type))
                current_start = page_num
                current_type = detected_type

    # Добавляем последний документ
    if pages:
        boundaries.append((current_start, len(pages), current_type))

    return boundaries


# =============================================================================
# Алгоритм сопоставления
# =============================================================================

def match_by_vehicle_and_trailer(
    doc: ParsedDocument,
    applications: List[ApplicationInfo]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ с заявкой по ТС + прицеп + дата.

    Правило 3.1.A: Совпадение тягач + прицеп с ресурсами заявки
    и дата документа попадает в период перевозки.
    """
    if not doc.identifiers.vehicle_plate:
        return None

    doc_vehicle = normalize_vehicle_plate(doc.identifiers.vehicle_plate)
    doc_trailer = normalize_vehicle_plate(doc.identifiers.trailer_plate)
    doc_date = doc.identifiers.document_date

    for app in applications:
        app_vehicle = normalize_vehicle_plate(app.vehicle_plate)
        app_trailer = normalize_vehicle_plate(app.trailer_plate)

        # Проверяем совпадение ТС
        if doc_vehicle != app_vehicle:
            continue

        # Проверяем совпадение прицепа (если есть в обоих)
        trailer_match = True
        if doc_trailer and app_trailer:
            trailer_match = doc_trailer == app_trailer

        # Проверяем дату
        date_match = False
        if doc_date:
            if app.load_date and app.unload_date:
                date_match = date_in_range(doc_date, app.load_date, app.unload_date)
            elif app.load_date:
                date_match = dates_match(doc_date, app.load_date, tolerance_days=2)
            elif app.date:
                date_match = dates_match(doc_date, app.date, tolerance_days=5)

        if trailer_match and date_match:
            confidence = ConfidenceLevel.HIGH
            if doc_trailer and app_trailer:
                reason = f"ТС {doc_vehicle} + прицеп {doc_trailer} + дата {doc_date}"
            else:
                reason = f"ТС {doc_vehicle} + дата {doc_date}"
            return (app, confidence, reason)

    return None


def match_by_vehicle_only(
    doc: ParsedDocument,
    applications: List[ApplicationInfo]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ с заявкой по ТС + дата.

    Правило 3.1.B: Совпадение тягача (без прицепа) и дата попадает в период,
    и нет другой заявки с тем же тягачом в тот же период.
    """
    if not doc.identifiers.vehicle_plate:
        return None

    doc_vehicle = normalize_vehicle_plate(doc.identifiers.vehicle_plate)
    doc_date = doc.identifiers.document_date

    matching_apps = []

    for app in applications:
        app_vehicle = normalize_vehicle_plate(app.vehicle_plate)

        if doc_vehicle != app_vehicle:
            continue

        # Проверяем дату
        date_match = False
        if doc_date:
            if app.load_date:
                date_match = dates_match(doc_date, app.load_date, tolerance_days=2)
            elif app.date:
                date_match = dates_match(doc_date, app.date, tolerance_days=5)
            else:
                date_match = True  # Нет даты в заявке - считаем совпадением

        if date_match:
            matching_apps.append(app)

    # Возвращаем только если одно совпадение
    if len(matching_apps) == 1:
        app = matching_apps[0]
        return (
            app,
            ConfidenceLevel.HIGH,
            f"ТС {doc_vehicle} + дата {doc_date} (единственная заявка)"
        )

    return None


def match_by_driver(
    doc: ParsedDocument,
    applications: List[ApplicationInfo]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ с заявкой по водителю + дата.

    Правило 3.1.C: Совпадение водителя (ФИО) и дата в периоде заявки,
    и нет другой заявки с тем же водителем в тот же период.
    """
    if not doc.identifiers.driver_name:
        return None

    doc_driver = normalize_driver_name(doc.identifiers.driver_name)
    doc_date = doc.identifiers.document_date

    matching_apps = []

    for app in applications:
        app_driver = normalize_driver_name(app.driver_name)

        if not app_driver:
            continue

        # Сравниваем фамилии (первое слово)
        doc_surname = doc_driver.split()[0] if doc_driver else ""
        app_surname = app_driver.split()[0] if app_driver else ""

        if doc_surname.lower() != app_surname.lower():
            continue

        # Проверяем дату
        date_match = False
        if doc_date:
            if app.load_date:
                date_match = dates_match(doc_date, app.load_date, tolerance_days=2)
            elif app.date:
                date_match = dates_match(doc_date, app.date, tolerance_days=5)
            else:
                date_match = True

        if date_match:
            matching_apps.append(app)

    if len(matching_apps) == 1:
        app = matching_apps[0]
        return (
            app,
            ConfidenceLevel.HIGH,
            f"Водитель {doc_driver} + дата {doc_date}"
        )

    return None


def match_by_document_number(
    doc: ParsedDocument,
    applications: List[ApplicationInfo],
    anchor_matches: Dict[str, ApplicationInfo]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ по номеру, если этот номер уже был привязан.

    Правило 3.1.D: Номер ТН/ТТН/заказа совпадает внутри пакета,
    где уже есть якорный документ.
    """
    if not doc.identifiers.document_number:
        return None

    doc_num = doc.identifiers.document_number

    if doc_num in anchor_matches:
        app = anchor_matches[doc_num]
        return (
            app,
            ConfidenceLevel.MEDIUM,
            f"Номер документа {doc_num} совпадает с якорным"
        )

    return None


def match_by_package(
    doc: ParsedDocument,
    doc_index: int,
    all_documents: List[ParsedDocument],
    results: List[MatchResult]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ по принадлежности к пакету.

    Правило 4.1: Документ без идентификаторов привязывается к якорному
    документу, находящемуся рядом.
    """
    # Ищем ближайший якорный документ (с high confidence)
    # Сначала смотрим назад
    for i in range(doc_index - 1, -1, -1):
        if i < len(results) and results[i].is_matched:
            if results[i].confidence == ConfidenceLevel.HIGH:
                return (
                    results[i].application,
                    ConfidenceLevel.LOW,
                    f"Часть пакета после якорного документа {all_documents[i].doc_type.value} "
                    f"({all_documents[i].page_range})"
                )

    # Если не нашли - смотрим вперёд
    for i in range(doc_index + 1, len(all_documents)):
        if i < len(results) and results[i].is_matched:
            if results[i].confidence == ConfidenceLevel.HIGH:
                return (
                    results[i].application,
                    ConfidenceLevel.LOW,
                    f"Часть пакета перед якорным документом {all_documents[i].doc_type.value} "
                    f"({all_documents[i].page_range})"
                )

    return None


def match_by_date_and_secondary(
    doc: ParsedDocument,
    applications: List[ApplicationInfo]
) -> Optional[Tuple[ApplicationInfo, ConfidenceLevel, str]]:
    """
    Сопоставляет документ по дате и вторичным признакам.

    Правило 4.2: Используется, если другие методы не помогли.
    """
    doc_date = doc.identifiers.document_date
    if not doc_date:
        return None

    matching_apps = []

    for app in applications:
        date_match = False
        if app.load_date:
            date_match = dates_match(doc_date, app.load_date, tolerance_days=3)
        elif app.date:
            date_match = dates_match(doc_date, app.date, tolerance_days=5)

        if date_match:
            matching_apps.append(app)

    if len(matching_apps) == 1:
        app = matching_apps[0]
        return (
            app,
            ConfidenceLevel.LOW,
            f"Дата {doc_date} совпадает с единственной заявкой"
        )

    # Если несколько совпадений - пробуем по адресам
    if len(matching_apps) > 1 and doc.identifiers.loading_address:
        for app in matching_apps:
            if app.route and doc.identifiers.loading_address.lower() in app.route.lower():
                return (
                    app,
                    ConfidenceLevel.LOW,
                    f"Дата {doc_date} + адрес совпадает с маршрутом"
                )

    return None


def match_documents_to_applications(
    documents: List[ParsedDocument],
    applications: List[ApplicationInfo]
) -> List[MatchResult]:
    """
    Основной алгоритм сопоставления документов с заявками.

    Выполняет сопоставление в порядке приоритета:
    1. ТС + прицеп + дата
    2. ТС + дата
    3. Водитель + дата
    4. Номер документа (если уже привязан)
    5. Пакетная логика
    6. Дата + вторичные признаки
    """
    results: List[MatchResult] = []
    anchor_matches: Dict[str, ApplicationInfo] = {}  # doc_number -> app

    # Первый проход: сильные привязки (ТС, водитель)
    for doc in documents:
        result = MatchResult(document=doc)

        # Пробуем по ТС + прицеп
        match = match_by_vehicle_and_trailer(doc, applications)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.VEHICLE_TRAILER_DATE
            if doc.identifiers.document_number:
                anchor_matches[doc.identifiers.document_number] = result.application
            results.append(result)
            continue

        # Пробуем по ТС
        match = match_by_vehicle_only(doc, applications)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.VEHICLE_DATE
            if doc.identifiers.document_number:
                anchor_matches[doc.identifiers.document_number] = result.application
            results.append(result)
            continue

        # Пробуем по водителю
        match = match_by_driver(doc, applications)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.DRIVER_DATE
            if doc.identifiers.document_number:
                anchor_matches[doc.identifiers.document_number] = result.application
            results.append(result)
            continue

        results.append(result)

    # Второй проход: привязка по номеру документа
    for i, result in enumerate(results):
        if result.is_matched:
            continue

        doc = result.document
        match = match_by_document_number(doc, applications, anchor_matches)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.DOCUMENT_NUMBER

    # Третий проход: пакетная логика
    for i, result in enumerate(results):
        if result.is_matched:
            continue

        doc = result.document
        match = match_by_package(doc, i, documents, results)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.PACKAGE

    # Четвёртый проход: дата + вторичные признаки
    for i, result in enumerate(results):
        if result.is_matched:
            continue

        doc = result.document
        match = match_by_date_and_secondary(doc, applications)
        if match:
            result.application, result.confidence, result.reason_details = match
            result.reason = MatchReason.DATE_SECONDARY

    return results


# =============================================================================
# Главная функция
# =============================================================================

def process_pdf(
    pdf_path: str,
    applications: List[ApplicationInfo]
) -> MatchingReport:
    """
    Обрабатывает PDF и сопоставляет документы с заявками.

    Args:
        pdf_path: Путь к PDF файлу
        applications: Список заявок для сопоставления

    Returns:
        MatchingReport с результатами сопоставления
    """
    report = MatchingReport()
    report.source_file = os.path.basename(pdf_path)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)

            report.total_pages = len(pages)

            # Определяем границы документов
            boundaries = detect_document_boundaries(pages)

            # Создаём объекты документов
            for start, end, doc_type in boundaries:
                doc = ParsedDocument()
                doc.page_start = start
                doc.page_end = end
                doc.doc_type = doc_type
                doc.source_file = report.source_file

                # Собираем текст всех страниц документа
                doc_text = "\n".join(pages[start-1:end])
                doc.raw_text = doc_text

                # Извлекаем идентификаторы
                doc.identifiers = extract_identifiers(doc_text, doc_type)

                report.documents.append(doc)

            # Сопоставляем с заявками
            report.results = match_documents_to_applications(
                report.documents,
                applications
            )

            # Собираем несопоставленные
            report.unmatched_documents = [
                r.document for r in report.results if not r.is_matched
            ]

            if report.unmatched_documents:
                report.warnings.append(
                    f"Не удалось сопоставить {len(report.unmatched_documents)} "
                    f"документ(ов)"
                )

    except Exception as e:
        logger.error(f"Error processing PDF {pdf_path}: {e}")
        report.warnings.append(f"Ошибка обработки PDF: {e}")

    return report


def format_report(report: MatchingReport) -> str:
    """Форматирует отчёт в читаемый вид."""
    lines = [
        f"=== Отчёт о сопоставлении: {report.source_file} ===",
        f"Всего страниц: {report.total_pages}",
        f"Документов: {len(report.documents)}",
        f"Сопоставлено: {len([r for r in report.results if r.is_matched])}",
        f"Не сопоставлено: {len(report.unmatched_documents)}",
        ""
    ]

    for result in report.results:
        doc = result.document
        lines.append(f"📄 {doc.doc_type.value} ({doc.page_range})")

        if result.is_matched:
            lines.append(f"   ✅ Заявка: {result.application.number}")
            lines.append(f"   Уверенность: {result.confidence.value}")
            lines.append(f"   Основание: {result.reason_details}")
        else:
            lines.append(f"   ❌ Не сопоставлен")

        if doc.identifiers.vehicle_plate:
            lines.append(f"   ТС: {doc.identifiers.vehicle_plate}")
        if doc.identifiers.trailer_plate:
            lines.append(f"   Прицеп: {doc.identifiers.trailer_plate}")
        if doc.identifiers.driver_name:
            lines.append(f"   Водитель: {doc.identifiers.driver_name}")
        if doc.identifiers.document_date:
            lines.append(f"   Дата: {doc.identifiers.document_date}")

        lines.append("")

    if report.warnings:
        lines.append("⚠️ Предупреждения:")
        for warning in report.warnings:
            lines.append(f"   - {warning}")

    return "\n".join(lines)


# =============================================================================
# CLI для тестирования
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Использование: python document_matcher.py <pdf_file> [--apps <apps_json>]")
        sys.exit(1)

    pdf_path = sys.argv[1]

    # Тестовые заявки (если не указан файл)
    test_applications = [
        ApplicationInfo(
            number="СП139948/1",
            date="16.06.2025",
            vehicle_plate="Т461РН196",
            trailer_plate="ВМ228766",
            driver_name="Богданов Сергей Валерьевич",
            load_date="16.06.2025"
        ),
        ApplicationInfo(
            number="СП144280/1",
            date="18.06.2025",
            vehicle_plate="С805РХ196",
            trailer_plate="ЕА707566",
            driver_name="Зайцев Юрий Александрович",
            load_date="19.06.2025"
        ),
    ]

    report = process_pdf(pdf_path, test_applications)
    print(format_report(report))
