"""
Основной модуль Telegram-бота для
автоматизации расчёта процентов по ст. 395 ГК РФ.
"""

import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt
from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
                      Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

from cal import calculate_duty
from calc_395 import calculate_full_395, get_key_rates_from_395gk
from llm_fallback import apply_llm_fallback
from sliding_window_parser import parse_documents_with_sliding_window

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.info("Bot script started")

# Состояния диалога
(ASK_JURISDICTION, ASK_CUSTOM_COURT, ASK_CLAIM_STATUS,
 ASK_TRACK, ASK_RECEIVE_DATE, ASK_SEND_DATE) = range(6)


def get_court_by_address(defendant_address: str) -> Tuple[str, str]:
    """
    Определяет суд по адресу ответчика.

    Args:
        defendant_address: Адрес ответчика

    Returns:
        Кортеж (название суда, адрес суда)
    """
    from courts_code import ARBITRATION_COURTS, CITY_TO_REGION

    defendant_address_lower = defendant_address.lower()

    # Сначала ищем по названию региона
    for region, court_info in ARBITRATION_COURTS.items():
        if region.lower() in defendant_address_lower:
            return court_info["name"], court_info["address"]

    # Если регион не найден, ищем по городам
    for city, region in CITY_TO_REGION.items():
        if city in defendant_address_lower:
            if region in ARBITRATION_COURTS:
                court_info = ARBITRATION_COURTS[region]
                return court_info["name"], court_info["address"]

    # Если ничего не найдено, возвращаем общий ответ
    return (
        "Арбитражный суд по месту нахождения ответчика",
        "Адрес суда не определен"
    )


def insert_interest_table(doc, details, total_interest: Optional[float] = None):
    """
    Вставляет таблицу процентов в документ
    Word вместо маркера {interest_table}.
    """
    placeholders = ['{{interest_table}}', '{interest_table}']
    headers = [
        'Сумма', 'Дата начала', 'Дата окончания', 'Дни',
        'Ставка', 'Формула', 'Проценты'
    ]
    for i, paragraph in enumerate(doc.paragraphs):
        if any(ph in paragraph.text for ph in placeholders):
            table = doc.add_table(rows=1, cols=len(headers))
            for col, header in enumerate(headers):
                cell = table.cell(0, col)
                cell.text = header
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(10)
                        run.font.name = 'Times New Roman'
                        run._element.rPr.rFonts.set(
                            qn('w:eastAsia'), 'Times New Roman'
                        )
            for row in details:
                cells = table.add_row().cells
                row_sum = row.get('sum', 0.0)
                date_from = row.get('date_from', '')
                date_to = row.get('date_to', '')
                if isinstance(date_from, datetime):
                    date_from = date_from.strftime('%d.%m.%Y')
                if isinstance(date_to, datetime):
                    date_to = date_to.strftime('%d.%m.%Y')
                cells[0].text = f"{row_sum:,.2f}".replace(',', ' ')
                cells[1].text = f"{date_from} г." if date_from else ''
                cells[2].text = f"{date_to} г." if date_to else ''
                cells[3].text = str(row.get('days', ''))
                cells[4].text = str(row.get('rate', ''))
                cells[5].text = str(row.get('formula', ''))
                cells[6].text = f"{row.get('interest', 0.0):,.2f}".replace(',', ' ')
                for cell in cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            run.font.size = Pt(10)
                            run.font.name = 'Times New Roman'
                            run._element.rPr.rFonts.set(
                                qn('w:eastAsia'), 'Times New Roman'
                            )
            if total_interest is None:
                total_interest = sum(
                    float(row.get('interest', 0.0) or 0.0)
                    for row in details
                )
            if details or total_interest:
                total_row = table.add_row().cells
                label_cell = total_row[0].merge(total_row[5])
                label_cell.text = 'Итого процентов'
                total_row[6].text = f"{total_interest:,.2f}".replace(',', ' ')
                for cell in total_row:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            run.font.size = Pt(10)
                            run.font.name = 'Times New Roman'
                            run.bold = True
                            run._element.rPr.rFonts.set(
                                qn('w:eastAsia'), 'Times New Roman'
                            )
            p = paragraph._element
            p.addnext(table._element)
            for placeholder in placeholders:
                paragraph.text = paragraph.text.replace(placeholder, '')
            break


def generate_claim_paragraph(user_data: dict) -> str:
    claim_date = user_data.get('claim_date', '').strip()
    claim_number = user_data.get('claim_number', '').strip()
    receive_date = user_data.get('postal_receive_date', '').strip()
    claim_status = user_data.get('claim_status', '')

    if claim_status == 'claim_received':
        return (
            f"{claim_date} Истцом в адрес Ответчика была направлена "
            "досудебная претензия с требованием погасить "
            "образовавшуюся задолженность. "
            f"Претензия была отправлена почтовым отправлением "
            f"с трек-номером {claim_number}. "
            f"{receive_date} Ответчик получил данное отправление."
        )
    elif claim_status == 'claim_not_received':
        return (
            f"{claim_date}. Истцом в адрес Ответчика была направлена "
            "досудебная претензия с требованием погасить "
            "образовавшуюся задолженность. "
            f"Претензия была отправлена почтовым отправлением "
            f"с трек-номером {claim_number}. "
            "В соответствии с п. 1 ст. 165.1 ГК РФ Истец считает, "
            "что претензия была получена Ответчиком. "
            "У Ответчика имелось достаточно времени для получения "
            "почтового отправления. "
            "Таким образом, Истцом был соблюден обязательный "
            "претензионный порядок (досудебный порядок урегулирования "
            "споров) в строгом соответствии с действующим "
            "законодательством РФ."
        )
    else:
        return (
            "Не указано г. Истцом в адрес Ответчика была направлена "
            "досудебная претензия."
        )


class RussianPostTrackingError(Exception):
    pass


def get_russian_post_config() -> Dict[str, object]:
    login = os.getenv("RUSSIAN_POST_LOGIN", "").strip()
    password = os.getenv("RUSSIAN_POST_PASSWORD", "").strip()
    return {
        "enabled": bool(login and password),
        "login": login,
        "password": password,
        "endpoint": os.getenv(
            "RUSSIAN_POST_ENDPOINT",
            "https://tracking.russianpost.ru/rtm34"
        ).strip(),
        "language": os.getenv("RUSSIAN_POST_LANGUAGE", "RUS").strip() or "RUS",
        "message_type": os.getenv("RUSSIAN_POST_MESSAGE_TYPE", "0").strip() or "0",
        "timeout": int(os.getenv("RUSSIAN_POST_TIMEOUT", "30") or "30"),
    }


def normalize_tracking_number(value: str) -> str:
    return re.sub(r'[^0-9A-Za-z]', '', value or '').upper()


def is_valid_tracking_number(value: str) -> bool:
    if not value:
        return False
    if re.fullmatch(r'\d{10,20}', value):
        return True
    return bool(re.fullmatch(r'[A-Z]{2}\d{9}[A-Z]{2}', value))


def build_russian_post_request(barcode: str, config: Dict[str, object]) -> str:
    language = config.get("language", "RUS")
    message_type = config.get("message_type", "0")
    login = config.get("login", "")
    password = config.get("password", "")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:oper="http://russianpost.org/operationhistory" '
        'xmlns:data="http://russianpost.org/operationhistory/data" '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soap:Header/>'
        '<soap:Body>'
        '<oper:getOperationHistory>'
        '<data:OperationHistoryRequest>'
        f'<data:Barcode>{barcode}</data:Barcode>'
        f'<data:MessageType>{message_type}</data:MessageType>'
        f'<data:Language>{language}</data:Language>'
        '</data:OperationHistoryRequest>'
        '<data:AuthorizationHeader soapenv:mustUnderstand="1">'
        f'<data:login>{login}</data:login>'
        f'<data:password>{password}</data:password>'
        '</data:AuthorizationHeader>'
        '</oper:getOperationHistory>'
        '</soap:Body>'
        '</soap:Envelope>'
    )


def parse_russian_post_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip()
    try:
        return datetime.fromisoformat(cleaned.replace('Z', '+00:00'))
    except ValueError:
        match = re.search(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',
            cleaned
        )
        if match:
            try:
                return datetime.strptime(
                    match.group(0),
                    "%Y-%m-%dT%H:%M:%S"
                )
            except ValueError:
                return None
    return None


def extract_fault_message(root: ET.Element) -> Optional[str]:
    for fault_name in ("AuthorizationFault", "OperationHistoryFault", "LanguageFault"):
        fault_elem = root.find(f".//{{*}}{fault_name}")
        if fault_elem is not None:
            for tag in ("Description", "Message", "FaultString"):
                text = fault_elem.findtext(f".//{{*}}{tag}")
                if text:
                    return text.strip()
            return fault_name
    fault_elem = root.find(".//{*}Fault")
    if fault_elem is not None:
        text = fault_elem.findtext(".//{*}Text") or fault_elem.findtext(".//{*}faultstring")
        if text:
            return text.strip()
        return "Ошибка сервиса отслеживания"
    return None


def fetch_russian_post_operations(barcode: str) -> List[Dict[str, object]]:
    config = get_russian_post_config()
    if not config.get("enabled"):
        raise RussianPostTrackingError("Не настроен доступ к API Почты России.")

    payload = build_russian_post_request(barcode, config)
    try:
        response = requests.post(
            config.get("endpoint"),
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            timeout=config.get("timeout", 30),
        )
    except requests.RequestException as exc:
        raise RussianPostTrackingError(
            "Сервис отслеживания недоступен. Попробуйте позже."
        ) from exc

    if response.status_code != 200:
        raise RussianPostTrackingError(
            f"Сервис отслеживания вернул код {response.status_code}."
        )

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise RussianPostTrackingError(
            "Не удалось разобрать ответ сервиса отслеживания."
        ) from exc

    fault_message = extract_fault_message(root)
    if fault_message:
        raise RussianPostTrackingError(fault_message)

    records = []
    for record in root.findall(".//{*}historyRecord"):
        oper_date_raw = record.findtext(".//{*}OperDate")
        oper_date = parse_russian_post_date(oper_date_raw)
        if not oper_date:
            continue
        oper_type = record.findtext(".//{*}OperType/{*}Name") or ""
        oper_attr = record.findtext(".//{*}OperAttr/{*}Name") or ""
        records.append({
            "date": oper_date,
            "oper_type": oper_type.strip(),
            "oper_attr": oper_attr.strip(),
        })

    return sorted(records, key=lambda item: item["date"])


def extract_tracking_dates(records: List[Dict[str, object]]) -> Tuple[Optional[str], Optional[str]]:
    if not records:
        return None, None

    def normalize(text: str) -> str:
        return (text or "").lower().replace("ё", "е")

    send_date = None
    for record in records:
        if "прием" in normalize(str(record.get("oper_type", ""))):
            send_date = record["date"]
            break

    if not send_date:
        send_date = records[0]["date"]

    receive_date = None
    for record in records:
        combined = normalize(
            f"{record.get('oper_type', '')} {record.get('oper_attr', '')}"
        )
        if "неудач" in combined or "отправител" in combined:
            continue
        if "вруч" in combined or "получен" in combined:
            receive_date = record["date"]

    send_str = send_date.strftime("%d.%m.%Y") if send_date else None
    receive_str = receive_date.strftime("%d.%m.%Y") if receive_date else None
    return send_str, receive_str


def format_header_paragraph(paragraph, label, value, postfix=None):
    paragraph.clear()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if label:
        run_label = paragraph.add_run(label)
        run_label.bold = True
        run_label.font.name = 'Times New Roman'
        run_label.font.size = Pt(12)
    run_value = paragraph.add_run(value)
    run_value.bold = True
    run_value.font.name = 'Times New Roman'
    run_value.font.size = Pt(12)
    if postfix:
        run_post = paragraph.add_run(postfix)
        run_post.bold = True
        run_post.font.name = 'Times New Roman'
        run_post.font.size = Pt(12)


def format_header_address(paragraph, value):
    paragraph.clear()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = paragraph.add_run(value)
    run.bold = False
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)


def format_placeholder_paragraph(paragraph, placeholder, value, bold=False):
    text = ''.join(run.text for run in paragraph.runs)
    idx = text.find(placeholder)
    if idx == -1:
        return
    before = text[:idx]
    after = text[idx+len(placeholder):]
    paragraph.clear()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if before:
        run_before = paragraph.add_run(before)
        run_before.bold = bold
        run_before.font.name = 'Times New Roman'
        run_before.font.size = Pt(12)
    run_value = paragraph.add_run(value)
    run_value.bold = bold
    run_value.font.name = 'Times New Roman'
    run_value.font.size = Pt(12)
    if after:
        run_after = paragraph.add_run(after)
        run_after.bold = bold
        run_after.font.name = 'Times New Roman'
        run_after.font.size = Pt(12)


def format_placeholder_paragraph_plain(paragraph, value):
    paragraph.clear()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = paragraph.add_run(value)
    run.bold = False
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)


def format_document_list(document_string: str) -> str:
    """
    Форматирует список документов с переносами строк.

    Args:
        document_string: Строка с документами, разделенными точкой с запятой

    Returns:
        Отформатированная строка с переносами строк
    """
    if not document_string or document_string == 'Не указано':
        return document_string

    # Разделяем по точке с запятой и убираем пустые строки
    documents = [doc.strip()
                 for doc in document_string.split(';') if doc.strip()]

    if not documents:
        return document_string

    # Форматируем каждый документ без маркера
    formatted_docs = []
    for doc in documents:
        # Убираем лишние пробелы
        formatted_docs.append(doc.strip())

    # Соединяем документы с переносами строк
    return ';\n'.join(formatted_docs) + ';'


def normalize_str(value: Optional[str], default: str = 'Не указано') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def parse_amount(value: Optional[str], default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r'\s+', '', str(value)).replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return default


def normalize_payment_terms(text: str) -> str:
    if not text:
        return text
    normalized = re.sub(r'\s+', ' ', str(text)).strip()
    lower = normalized.lower()
    if 'условия оплаты' in lower or 'оплаты по договор' in lower:
        dash_match = re.search(r'\s[–\-]\s*', normalized)
        colon_index = normalized.find(':')
        if colon_index != -1 and (dash_match is None or colon_index < dash_match.start()):
            split_match = re.split(r':\s*', normalized, maxsplit=1)
        else:
            split_match = re.split(r'\s[–\-]\s*', normalized, maxsplit=1)
        if len(split_match) == 2:
            normalized = split_match[1].strip()
    if re.search(r'\bг\.$', normalized):
        return normalized
    normalized = re.sub(r'[.;:]+$', '', normalized).strip()
    if normalized.startswith(('«', '"')) and normalized.endswith(('»', '"')):
        normalized = normalized[1:-1].strip()
    if '«' in normalized or '»' in normalized or '"' in normalized:
        normalized = normalized.replace('«', '').replace('»', '').replace('"', '')
    return normalized


def get_ogrn_label(name: str, inn_value: str) -> str:
    inn_clean = re.sub(r'[^\d]', '', inn_value or '')
    if (
        'ИП' in name
        or 'Индивидуальный предприниматель' in name
        or len(inn_clean) == 12
    ):
        return 'ОГРНИП'
    return 'ОГРН'


def get_first_list_value(values) -> str:
    if not values:
        return ''
    for val in values:
        if val and str(val).strip():
            return str(val).strip()
    return ''


def join_list_values(values) -> str:
    if not values:
        return ''
    if isinstance(values, str):
        return values.strip()
    cleaned = [str(val).strip() for val in values if str(val).strip()]
    return ', '.join(cleaned)


def normalize_document_item(value: str) -> str:
    normalized = re.sub(r'\s+', ' ', str(value)).strip()
    if normalized.endswith(';'):
        normalized = normalized[:-1].strip()
    return normalized


def format_document_item(value: str) -> str:
    normalized = normalize_document_item(value)
    if not normalized:
        return ''
    if re.match(r'^\s*(\d+[\.\)]|-)\s+', normalized):
        return normalized
    return f"- {normalized}"


def build_documents_list(claim_data: dict) -> str:
    items = []
    for key in [
        'contract_applications',
        'invoice_blocks',
        'upd_blocks',
        'cargo_docs',
        'contracts'
    ]:
        value = claim_data.get(key, '')
        if not value or value == 'Не указано':
            continue
        parts = [part.strip() for part in str(value).split(';') if part.strip()]
        items.extend(parts)
    if not items:
        attachments = claim_data.get('attachments', [])
        if isinstance(attachments, str):
            attachments = [attachments]
        for item in attachments:
            cleaned = normalize_document_item(item)
            if cleaned and cleaned != 'Не указано':
                items.append(cleaned)
    if not items:
        return 'Не указано'
    unique = []
    seen = set()
    for item in items:
        cleaned = normalize_document_item(item)
        if not cleaned or cleaned == 'Не указано':
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    if not unique:
        return 'Не указано'
    if len(unique) == 1:
        return unique[0]
    formatted = [format_document_item(item) for item in unique]
    return '\n'.join([item for item in formatted if item])


def extract_documents_list_structure(text: str) -> Optional[List[Tuple[int, str]]]:
    lines = [line.strip() for line in str(text).splitlines()]

    start_idx = None
    for i, line in enumerate(lines):
        if re.search(r'основани[яе].*задолж', line, re.IGNORECASE):
            start_idx = i + 1
            break

    if start_idx is None:
        return None

    block = []
    for line in lines[start_idx:]:
        if not line:
            continue
        if re.search(r'итого\s+задолж', line, re.IGNORECASE):
            break
        if re.match(r'^\d+\.\d+', line):
            break
        if re.search(
            r'качество исполнения|отправка оригиналов|расчет процентов|приложен',
            line,
            re.IGNORECASE,
        ):
            break
        block.append(line)

    if not block:
        return None

    def strip_list_prefix(value: str) -> str:
        stripped = re.sub(r'^\s*\d+[\.\)]\s+', '', value)
        stripped = re.sub(r'^\s*[-\u2022\u00B7]\s+', '', stripped)
        return stripped.strip()

    def is_document_line(value: str) -> bool:
        lower = value.lower()
        return '№' in value or 'комплект сопроводительных документов' in lower

    groups = []
    current = None
    for line in block:
        cleaned = strip_list_prefix(line).rstrip(';').strip()
        if not cleaned:
            continue
        if not is_document_line(cleaned):
            continue
        if re.match(r'^(заявк|договор-?заявк)', cleaned, re.IGNORECASE):
            if current:
                groups.append(current)
            current = {'header': cleaned, 'items': []}
            continue
        if current:
            current['items'].append(cleaned)
        else:
            groups.append({'header': cleaned, 'items': []})

    if current:
        groups.append(current)

    if not groups:
        return None

    structured = []
    for index, group in enumerate(groups, 1):
        header = group['header']
        if group['items']:
            structured.append((0, f"{index}. {header}"))
            for item in group['items']:
                structured.append((1, item))
        else:
            structured.append((0, f"{index}. {header}"))

    return structured


def expand_placeholder_map(replacements: dict) -> dict:
    expanded = {}
    for key, value in replacements.items():
        expanded[key] = value
        if key.startswith('{') and key.endswith('}'):
            name = key[1:-1]
            expanded[f"{{{{{name}}}}}"] = value
    return expanded


def generate_debt_text(claim_data: dict) -> str:
    """
    Форматирует сумму задолженности для вставки в шаблон.

    Args:
        claim_data: Данные о требованиях

    Returns:
        Сумма задолженности в формате строки
    """
    debt_amount = parse_amount(claim_data.get('debt', '0'))
    return f"{debt_amount:,.0f}".replace(',', ' ')


def generate_payment_terms(claim_data: dict) -> str:
    """
    Генерирует текст о порядке оплаты по приоритету:
    1. Если в payment_terms есть текст — возвращает payment_terms
    2. Если есть только дата — возвращает строку с датой
    3. Если есть только дни — возвращает строку с днями
    4. Если ничего нет — стандартный текст
    """
    payment_days = claim_data.get('payment_days')
    payment_due_date = claim_data.get('payment_due_date')
    payment_terms = claim_data.get('payment_terms', '')

    # Если в тексте требования явно есть условия — используем их как есть
    if payment_terms:
        return normalize_payment_terms(payment_terms)
    # Если есть только дата
    if payment_due_date and not payment_days:
        return normalize_payment_terms(
            f"Срок оплаты не позднее {payment_due_date} г."
        )
    # Если есть только дни
    if payment_days and not payment_due_date:
        return normalize_payment_terms(
            f"Оплата производится в течение {payment_days} банковских дней "
            "безналичным расчетом после получения оригиналов документов."
        )
    # Если ничего нет — стандарт
    return normalize_payment_terms(
        "Оплата производится безналичным расчетом после получения "
        "оригиналов документов."
    )


def replace_placeholders_robust(doc, replacements):
    """
    Заменяет плейсхолдеры в документе, применяя жирное начертание
    только для указанных полей и строк.
    """
    # Переменная для будущего использования жирных плейсхолдеров
    # bold_placeholders = [
    #     '{plaintiff_name}', '{defendant_name}',
    #     '{total_claim}', '{duty}'
    # ]
    # Ключевые фразы для жирного
    bold_lines = [
        'Арбитражный суд по месту нахождения ответчика',
        'Истец:',
        'Ответчик:',
        'Цена иска:',
        'Государственная пошлина:'
    ]

    is_plaintiff_ip = (
        'ИП' in str(replacements.get('{plaintiff_name}', '')) or
        'Индивидуальный предприниматель' in str(
            replacements.get('{plaintiff_name}', ''))
    )
    is_defendant_ip = (
        'ИП' in str(replacements.get('{defendant_name}', '')) or
        'Индивидуальный предприниматель' in str(
            replacements.get('{defendant_name}', ''))
    )

    def is_missing(value: object) -> bool:
        if value is None:
            return True
        text = str(value).strip()
        return not text or text == 'Не указано'

    def split_list_values(value: object) -> list:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items
        text = str(value).strip()
        if not text or text == 'Не указано':
            return []
        return [part.strip() for part in re.split(r'[;,]', text) if part.strip()]

    def count_list_items(value: object) -> int:
        return len(split_list_values(value))

    def normalize_track_item(item: str) -> str:
        cleaned = re.sub(r'^[№\s]+', '', item)
        return cleaned.strip()

    def format_track_phrase(value: object) -> str:
        items = [normalize_track_item(item) for item in split_list_values(value)]
        if not items:
            return ''
        label = 'трек номерами' if len(items) > 1 else 'трек номером'
        return f"с {label} № {', '.join(items)}"

    def format_received_verb(track_value: object, date_value: object) -> str:
        count = max(
            count_list_items(track_value),
            count_list_items(date_value)
        )
        return 'получены' if count > 1 else 'получен'

    for paragraph in doc.paragraphs:
        full_text = ''.join(run.text for run in paragraph.runs)
        original_alignment = paragraph.alignment
        text_changed = False
        skip_replacements = False

        # Удаляем строку про оригиналы, если нет трек-номера или даты получения
        if 'Оригиналы документов' in full_text:
            track_value = replacements.get(
                '{docs_track_number}',
                replacements.get('{{docs_track_number}}', '')
            )
            date_value = replacements.get(
                '{docs_received_date}',
                replacements.get('{{docs_received_date}}', '')
            )
            if is_missing(track_value) or is_missing(date_value):
                p = paragraph._element
                p.getparent().remove(p)
                continue
            track_items = [
                normalize_track_item(item)
                for item in split_list_values(track_value)
            ]
            date_items = split_list_values(date_value)
            if track_items and date_items and len(track_items) == len(date_items):
                if len(track_items) == 1:
                    full_text = (
                        "Оригиналы документов по перевозкам отправлялись "
                        "почтовым отправлением "
                        f"с трек номером № {track_items[0]} "
                        f"получен {date_items[0]}."
                    )
                else:
                    pairs = [
                        f"№ {track} (получен {date})"
                        for track, date in zip(track_items, date_items)
                    ]
                    full_text = (
                        "Оригиналы документов по перевозкам отправлялись "
                        "почтовым отправлением "
                        f"с трек номерами {', '.join(pairs)}."
                    )
            else:
                full_text = (
                    "Оригиналы документов по перевозкам отправлялись почтовым "
                    f"отправлением {format_track_phrase(track_value)} "
                    f"{format_received_verb(track_value, date_value)} "
                    f"{str(date_value).strip()}."
                )
            text_changed = True
            skip_replacements = True

        # Удаляем строки с КПП для ИП
        if (
            is_plaintiff_ip
            and 'КПП' in full_text
            and ('{plaintiff_kpp}' in full_text or '{{plaintiff_kpp}}' in full_text)
        ):
            before_kpp = full_text
            lines = full_text.split('\n')
            filtered_lines = []
            for line in lines:
                if not (
                    'КПП' in line
                    and ('{plaintiff_kpp}' in line or '{{plaintiff_kpp}}' in line)
                ):
                    filtered_lines.append(line)
            full_text = '\n'.join(filtered_lines)
            if full_text != before_kpp:
                text_changed = True

        if (
            is_defendant_ip
            and 'КПП' in full_text
            and ('{defendant_kpp}' in full_text or '{{defendant_kpp}}' in full_text)
        ):
            before_kpp = full_text
            lines = full_text.split('\n')
            filtered_lines = []
            for line in lines:
                if not (
                    'КПП' in line
                    and ('{defendant_kpp}' in line or '{{defendant_kpp}}' in line)
                ):
                    filtered_lines.append(line)
            full_text = '\n'.join(filtered_lines)
            if full_text != before_kpp:
                text_changed = True

        if not skip_replacements:
            replaced_any = False
            for key in sorted(replacements.keys(), key=len, reverse=True):
                if key in full_text:
                    replaced_any = True
                    value = replacements[key]
                    if key.strip('{}') == 'documents_list':
                        clean_value = str(value).replace('\r', '').strip()
                    else:
                        clean_value = str(value).replace(
                            '\n', ' ').replace('\r', ' ').strip()
                    full_text = full_text.replace(key, clean_value)
            if replaced_any:
                text_changed = True

        if text_changed:
            if not full_text.strip():
                p = paragraph._element
                p.getparent().remove(p)
                continue
            paragraph.clear()
            if original_alignment is not None:
                paragraph.alignment = original_alignment
            # Проверяем, должна ли строка быть жирной
            is_bold = False
            for bold_line in bold_lines:
                if full_text.strip().startswith(bold_line):
                    is_bold = True

            # Специальная обработка для строк с названием суда
            if '{court_name}' in full_text or 'Арбитражный суд' in full_text:
                # Разбиваем текст на части и применяем жирное начертание к названию суда
                parts = full_text.split('Арбитражный суд')
                if len(parts) > 1:
                    # Первая часть (обычно "В")
                    if parts[0].strip():
                        prefix = parts[0]
                        if not prefix.endswith(' '):
                            prefix += ' '
                        run = paragraph.add_run(prefix)
                        run.font.name = 'Times New Roman'
                        run.font.size = Pt(12)
                        run.bold = False

                    # Название суда (жирное)
                    court_part = 'Арбитражный суд' + parts[1]
                    run = paragraph.add_run(court_part)
                    run.font.name = 'Times New Roman'
                    run.font.size = Pt(12)
                    run.bold = True
                else:
                    run = paragraph.add_run(full_text)
                    run.font.name = 'Times New Roman'
                    run.font.size = Pt(12)
                    run.bold = True
            # Специально для "Истец:" и "Ответчик:" — только первая строка жирная
            elif full_text.strip().startswith('Истец:') or full_text.strip().startswith('Ответчик:'):
                lines = full_text.split('\n')
                for i, line in enumerate(lines):
                    run = paragraph.add_run(line)
                    run.bold = (i == 0)
                    run.font.name = 'Times New Roman'
                    run.font.size = Pt(12)
                    if i < len(lines) - 1:
                        paragraph.add_run('\n')
            else:
                run = paragraph.add_run(full_text)
                run.bold = is_bold
                run.font.name = 'Times New Roman'
                run.font.size = Pt(12)


def fix_number_spacing(text: str) -> str:
    """
    Добавляет пробел после '№', если его нет.
    """
    return re.sub(r'№(\d)', r'№ \1', text)


ATTACHMENTS_EXTRA_TOP = [
    "Претензия – копия",
    "Чек и опись об отправке требования – копия",
]
ATTACHMENTS_EXTRA_TAIL = [
    "Квитанция об уплате государственной пошлины",
    "Документы, подтверждающие отправку искового заявления Ответчику - копия",
    "Доверенность на представителя – копия",
]
F107_EXCLUDED_ATTACHMENTS = [
    "Квитанция об уплате государственной пошлины",
    "Документы, подтверждающие отправку искового заявления Ответчику - копия",
    "Доверенность на представителя – копия",
]
F107_MAX_ITEMS = 14


def normalize_attachment_text(value: str) -> str:
    cleaned = str(value).lower().replace('ё', 'е')
    cleaned = re.sub(r'[\s\.,;:–—-]+', ' ', cleaned)
    return cleaned.strip()


def build_isk_attachments_list(attachments) -> List[str]:
    if isinstance(attachments, str):
        attachments = [attachments]
    attachments = attachments or []

    base_attachments = []
    for att in attachments:
        if not att or str(att).strip() == "Не указано":
            continue
        att_clean = fix_number_spacing(normalize_document_item(att))
        if att_clean:
            base_attachments.append(att_clean)

    final_attachments = []
    seen = set()

    def add_unique(item: str) -> None:
        key = normalize_attachment_text(item)
        if not key or key in seen:
            return
        seen.add(key)
        final_attachments.append(item)

    for item in ATTACHMENTS_EXTRA_TOP:
        add_unique(item)
    for item in base_attachments:
        add_unique(item)
    for item in ATTACHMENTS_EXTRA_TAIL:
        add_unique(item)

    return final_attachments


def resolve_defendant_display_name(
    name_short: Optional[str],
    name_full: Optional[str]
) -> str:
    for value in (name_short, name_full):
        if value and str(value).strip() and str(value).strip() != 'Не указано':
            return str(value).strip()
    return "Ответчик"


def build_f107_items(
    attachments,
    defendant_name: str
) -> List[str]:
    items = build_isk_attachments_list(attachments)
    excluded = {
        normalize_attachment_text(item)
        for item in F107_EXCLUDED_ATTACHMENTS
    }
    filtered_items = [
        item for item in items
        if normalize_attachment_text(item) not in excluded
    ]

    final_items = []
    seen = set()

    def add_unique(item: str) -> None:
        normalized = normalize_attachment_text(item)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        final_items.append(item)

    claim_item = normalize_document_item(
        f"Исковое заявление к {defendant_name}"
    )
    add_unique(claim_item)
    for item in filtered_items:
        add_unique(item)

    if len(final_items) > F107_MAX_ITEMS:
        logging.warning(
            "Слишком много приложений для Ф107: %s, используется %s",
            len(final_items),
            F107_MAX_ITEMS
        )
        final_items = final_items[:F107_MAX_ITEMS]

    return final_items


def replace_attachments_with_paragraphs(doc, attachments):
    """
    Заменяет плейсхолдер {attachments} списком приложений с нумерацией и добавляет заголовок 'Приложения:'.
    Ожидается, что в шаблоне только {attachments} на отдельной строке.
    """
    import logging
    idx = None
    add_header = True
    parent = None
    placeholders = ['{attachments}', '{{attachments}}']
    for i, paragraph in enumerate(doc.paragraphs):
        if any(ph in paragraph.text for ph in placeholders):
            idx = i
            parent = paragraph._element.getparent()
            break

    if idx is None:
        for i, paragraph in enumerate(doc.paragraphs):
            if paragraph.text.strip() == "Приложения:":
                idx = i + 1
                parent = paragraph._element.getparent()
                add_header = False
                break

    if idx is not None:
        if add_header:
            # Удаляем параграф с плейсхолдером
            p = doc.paragraphs[idx]._element
            parent.remove(p)

            # Добавляем заголовок "Приложения:"
            new_par = doc.add_paragraph()
            run = new_par.add_run("Приложения:")
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            run.bold = True
            new_par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            parent.insert(idx, new_par._element)
            idx += 1
        else:
            # Удаляем старый список приложений из шаблона
            while idx < len(doc.paragraphs):
                paragraph = doc.paragraphs[idx]
                text = paragraph.text.strip()
                if not text:
                    p = paragraph._element
                    parent.remove(p)
                    continue
                if (
                    "{{plaintiff_name_short}}" in text
                    or "{plaintiff_name_short}" in text
                    or re.match(r"^_+", text)
                ):
                    break
                p = paragraph._element
                parent.remove(p)

        final_attachments = build_isk_attachments_list(attachments)

        # Динамические приложения с нумерацией
        attachment_number = 1
        for att in final_attachments:
            new_par = doc.add_paragraph()
            run = new_par.add_run(f"{attachment_number}. {att};")
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            run.bold = False
            new_par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            parent.insert(idx, new_par._element)
            idx += 1
            attachment_number += 1
    else:
        if attachments and attachments != ["Не указано"]:
            logging.warning(
                "Плейсхолдер {attachments} или блок 'Приложения:' не найден"
            )


def replace_documents_list_with_paragraphs(
    doc,
    structured_items: List[Tuple[int, str]]
) -> bool:
    """
    Заменяет {documents_list} на список параграфов с отступами.
    """
    placeholders = ['{documents_list}', '{{documents_list}}']
    idx = None
    for i, paragraph in enumerate(doc.paragraphs):
        if any(ph in paragraph.text for ph in placeholders):
            idx = i
            break

    if idx is None:
        return False

    p = doc.paragraphs[idx]._element
    parent = p.getparent()
    parent.remove(p)

    for level, text in structured_items:
        line = text.strip()
        if not line:
            continue
        if level > 0 and not line.startswith(('-', '–', '—', '•')):
            line = f"• {line}"
        new_par = doc.add_paragraph()
        run = new_par.add_run(line)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        new_par.alignment = WD_ALIGN_PARAGRAPH.LEFT
        if level > 0:
            new_par.paragraph_format.left_indent = Pt(18)
            new_par.paragraph_format.first_line_indent = Pt(-9)
        parent.insert(idx, new_par._element)
        idx += 1

    return True


def iter_table_paragraphs(table):
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                yield paragraph
            for nested in cell.tables:
                yield from iter_table_paragraphs(nested)


def iter_document_paragraphs(doc):
    for paragraph in doc.paragraphs:
        yield paragraph
    for table in doc.tables:
        yield from iter_table_paragraphs(table)


def replace_placeholders_simple(doc, replacements: Dict[str, str]) -> None:
    for paragraph in iter_document_paragraphs(doc):
        if not paragraph.runs:
            continue
        full_text = ''.join(run.text for run in paragraph.runs)
        if not full_text:
            continue
        updated = full_text
        for key, value in replacements.items():
            if key in updated:
                updated = updated.replace(key, value)
        if updated != full_text:
            paragraph.runs[0].text = updated
            for run in paragraph.runs[1:]:
                run.text = ''


def create_f107_document(
    items: List[str],
    sender_name: str,
    sender_company: str,
    output_path: Optional[str] = None
) -> str:
    template_dir = os.path.dirname(__file__)
    candidate_templates = [
        os.path.join(template_dir, 'templates', 'F107.docx'),
        os.path.join(template_dir, 'F107.docx'),
    ]
    template_path = next(
        (path for path in candidate_templates if os.path.exists(path)),
        None
    )
    if template_path is None:
        raise FileNotFoundError("Шаблон Ф107 не найден.")

    doc = Document(template_path)
    replacements: Dict[str, str] = {}
    total_quantity = 0
    total_value = 0

    for index in range(F107_MAX_ITEMS):
        suffix = '' if index == 0 else str(index)
        if index < len(items):
            item_text = normalize_document_item(items[index])
            replacements[f"${{predmet{suffix}}}"] = item_text
            replacements[f"${{kolich_predm{suffix}}}"] = "1"
            replacements[f"${{sum_predm{suffix}}}"] = "0"
            total_quantity += 1
        else:
            replacements[f"${{predmet{suffix}}}"] = ""
            replacements[f"${{kolich_predm{suffix}}}"] = ""
            replacements[f"${{sum_predm{suffix}}}"] = ""

    replacements["${sum_predmetov}"] = str(total_quantity) if total_quantity else ""
    replacements["${sum_kolich}"] = str(total_value) if total_quantity else ""
    replacements["${namef107}"] = ""
    replacements["${company}"] = sender_company or ""

    replace_placeholders_simple(doc, replacements)

    if output_path is None:
        output_name = f"Опись_вложения_F107_{uuid.uuid4().hex}.docx"
        output_path = os.path.join(os.path.dirname(__file__), output_name)
    doc.save(output_path)
    return output_path


def format_poa_date(value: Optional[datetime] = None) -> str:
    target = value or datetime.today()
    months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ]
    month_name = months[target.month - 1]
    return f"«{target:%d}» {month_name} {target.year} г."


def create_power_of_attorney_document(
    replacements: Dict[str, str],
    output_path: Optional[str] = None
) -> str:
    template_dir = os.path.dirname(__file__)
    candidate_templates = [
        os.path.join(template_dir, 'templates', 'ДОВЕРЕННОСТЬ.docx'),
        os.path.join(template_dir, 'ДОВЕРЕННОСТЬ.docx'),
    ]
    template_path = next(
        (path for path in candidate_templates if os.path.exists(path)),
        None
    )
    if template_path is None:
        raise FileNotFoundError("Шаблон доверенности не найден.")

    doc = Document(template_path)
    replace_placeholders_simple(doc, replacements)

    if output_path is None:
        output_name = f"Доверенность_{uuid.uuid4().hex}.docx"
        output_path = os.path.join(os.path.dirname(__file__), output_name)
    doc.save(output_path)
    return output_path


def number_attachments_section(doc):
    """
    Добавляет нумерацию к списку приложений в шаблоне,
    если он указан как отдельные строки без номеров.
    """
    start_idx = None
    for i, paragraph in enumerate(doc.paragraphs):
        if paragraph.text.strip() == "Приложения:":
            start_idx = i + 1
            break
    if start_idx is None:
        return

    number = 1
    for i in range(start_idx, len(doc.paragraphs)):
        paragraph = doc.paragraphs[i]
        text = paragraph.text.strip()
        if not text:
            break
        if (
            "{{plaintiff_name_short}}" in text
            or "{plaintiff_name_short}" in text
            or re.match(r"^_+", text)
        ):
            break
        if re.match(r"^\d+[.)]\s+", text):
            number += 1
            continue

        cleaned = re.sub(r"^[-–—]\s*", "", text).strip()
        original_alignment = paragraph.alignment
        paragraph.clear()
        if original_alignment is not None:
            paragraph.alignment = original_alignment
        run = paragraph.add_run(f"{number}. {cleaned}")
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        run.bold = False
        number += 1


def format_organization_name_short(full_name: str) -> str:
    """
    Форматирует полное название организации/ИП в краткий формат.

    Args:
        full_name: Полное название организации или ИП

    Returns:
        Краткий формат названия в кавычках
    """
    if not full_name or full_name == 'Не указано':
        return 'Не указано'

    # Для ИП
    if 'Индивидуальный предприниматель' in full_name:
        # Извлекаем ФИО после "Индивидуальный предприниматель"
        fio = full_name.replace('Индивидуальный предприниматель', '').strip()
        # Форматируем ФИО в формат "Фамилия И.О."
        parts = fio.split()
        if len(parts) >= 2:
            surname = parts[0]
            # Берем первые 2 инициала
            initials = '.'.join([part[0] for part in parts[1:3]]) + '.'
            return f'ИП «{surname} {initials}»'
        else:
            return f'ИП «{fio}»'

    # Для ООО
    elif 'Общество с ограниченной ответственностью' in full_name:
        # Извлекаем название в кавычках
        match = re.search(r'«(.+?)»', full_name)
        if match:
            return f'ООО «{match.group(1)}»'
        else:
            # Если нет кавычек, берем все после ООО
            name = full_name.replace(
                'Общество с ограниченной ответственностью',
                '').strip()
            return f'ООО «{name}»'

    # Для ЗАО
    elif 'Закрытое акционерное общество' in full_name:
        match = re.search(r'«(.+?)»', full_name)
        if match:
            return f'ЗАО «{match.group(1)}»'
        else:
            name = full_name.replace(
                'Закрытое акционерное общество',
                '').strip()
            return f'ЗАО «{name}»'

    # Для ПАО
    elif 'Публичное акционерное общество' in full_name:
        match = re.search(r'«(.+?)»', full_name)
        if match:
            return f'ПАО «{match.group(1)}»'
        else:
            name = full_name.replace(
                'Публичное акционерное общество', '').strip()
            return f'ПАО «{name}»'

    # Для ОАО
    elif 'Открытое акционерное общество' in full_name:
        match = re.search(r'«(.+?)»', full_name)
        if match:
            return f'ОАО «{match.group(1)}»'
        else:
            name = full_name.replace(
                'Открытое акционерное общество', '').strip()
            return f'ОАО «{name}»'

    # Для АО
    elif 'Акционерное общество' in full_name:
        match = re.search(r'«(.+?)»', full_name)
        if match:
            return f'АО «{match.group(1)}»'
        else:
            name = full_name.replace('Акционерное общество', '').strip()
            return f'АО «{name}»'

    # Если уже в кратком формате (содержит аббревиатуру)
    elif any(abbr in full_name for abbr in ['ООО', 'ИП', 'ЗАО', 'ПАО', 'ОАО', 'АО']):
        # Проверяем, есть ли уже кавычки
        if '«' in full_name and '»' in full_name:
            return full_name
        else:
            # Добавляем кавычки если их нет
            match = re.search(r'(ООО|ИП|ЗАО|ПАО|ОАО|АО)\s+(.+)', full_name)
            if match:
                return f'{match.group(1)} «{match.group(2).strip()}»'
            else:
                return full_name

    # Для остальных случаев возвращаем как есть
    return full_name


def create_isk_document(
    data: dict,
    interest_data: dict,
    duty_data: dict,
    replacements: dict,
    documents_list_structured: Optional[List[Tuple[int, str]]] = None,
    output_path: Optional[str] = None
) -> str:
    template_dir = os.path.dirname(__file__)
    candidate_templates = [
        os.path.join(template_dir, 'templates', 'template_isk.docx'),
        os.path.join(template_dir, 'template_isk.docx'),
    ]
    fallback_templates = [
        os.path.join(template_dir, 'templates', 'template.docx'),
        os.path.join(template_dir, 'template.docx'),
    ]
    template_path = next(
        (path for path in candidate_templates if os.path.exists(path)),
        None
    )
    if template_path is None:
        template_path = next(
            (path for path in fallback_templates if os.path.exists(path)),
            None
        )
        logging.warning(
            "Шаблон template_isk.docx не найден, используется template.docx"
        )
    if template_path is None:
        raise FileNotFoundError(
            "Шаблон искового заявления не найден."
        )
    doc = Document(template_path)
    replacements = replacements.copy()
    if documents_list_structured:
        inserted = replace_documents_list_with_paragraphs(
            doc,
            documents_list_structured
        )
        if inserted:
            replacements.pop('{documents_list}', None)
            replacements.pop('{{documents_list}}', None)
    replace_placeholders_robust(doc, expand_placeholder_map(replacements))
    attachment_placeholders = ['{attachments}', '{{attachments}}']
    has_attachment_placeholder = any(
        ph in paragraph.text
        for paragraph in doc.paragraphs
        for ph in attachment_placeholders
    )
    has_attachments_header = any(
        paragraph.text.strip() == "Приложения:"
        for paragraph in doc.paragraphs
    )
    if has_attachment_placeholder or has_attachments_header:
        replace_attachments_with_paragraphs(
            doc,
            data.get('attachments', [])
        )
    number_attachments_section(doc)
    insert_interest_table(
        doc,
        interest_data.get('detailed_calc', []),
        interest_data.get('total_interest')
    )
    if output_path is None:
        output_name = f"Исковое_заявление_{uuid.uuid4().hex}.docx"
        output_path = os.path.join(os.path.dirname(__file__), output_name)
    doc.save(output_path)
    return output_path


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /start.

    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    if update.effective_user:
        logging.info(
            f"Received /start command from user {update.effective_user.id}"
        )
    if update.message:
        await update.message.reply_text(
            'Отправь .docx файл с досудебным требованием — '
            'я верну исковое заявление в формате Word.'
        )


async def ask_claim_status(update, context):
    keyboard = [
        [
            InlineKeyboardButton("✅ Да", callback_data='claim_received'),
            InlineKeyboardButton("❌ Нет", callback_data='claim_not_received'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Ответчик получил требование?",
        reply_markup=reply_markup
    )
    return ASK_CLAIM_STATUS


async def claim_status_chosen(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data['claim_status'] = query.data

    await query.edit_message_text("Введите дату отправления (ДД.ММ.ГГГГ):")
    return ASK_SEND_DATE


async def ask_send_date(update, context):
    context.user_data['claim_date'] = update.message.text.strip()
    if context.user_data.get('claim_status') == 'claim_received':
        await update.message.reply_text("Введите трек-номер отправления:")
        return ASK_TRACK
    else:
        await update.message.reply_text("Введите трек-номер отправления:")
        return ASK_TRACK


async def ask_track(update, context):
    raw_track = update.message.text.strip()
    track_number = normalize_tracking_number(raw_track)

    if context.user_data.get('use_tracking_api'):
        if not is_valid_tracking_number(track_number):
            await update.message.reply_text(
                "Трек-номер некорректен. Введите номер из 10-20 цифр "
                "или формат S10 (например RA123456789RU)."
            )
            return ASK_TRACK
        try:
            records = fetch_russian_post_operations(track_number)
        except RussianPostTrackingError as exc:
            await update.message.reply_text(
                f"Не удалось получить данные по трек-номеру. {exc} "
                "Проверьте номер и попробуйте снова."
            )
            return ASK_TRACK

        send_date, receive_date = extract_tracking_dates(records)
        if not send_date:
            await update.message.reply_text(
                "Не удалось определить дату отправления по треку. "
                "Проверьте номер и попробуйте снова."
            )
            return ASK_TRACK

        context.user_data['claim_number'] = track_number
        context.user_data['claim_date'] = send_date
        context.user_data['postal_receive_date'] = receive_date or ''
        context.user_data['claim_status'] = (
            'claim_received' if receive_date else 'claim_not_received'
        )

        if receive_date:
            await update.message.reply_text(
                f"Найдены даты: отправление {send_date}, получение {receive_date}."
            )
        else:
            await update.message.reply_text(
                f"Найдена дата отправления {send_date}. "
                "Вручение адресату не найдено."
            )

        await finish_claim(update, context)
        return ConversationHandler.END

    context.user_data['claim_number'] = raw_track
    if context.user_data.get('claim_status') == 'claim_received':
        await update.message.reply_text("Введите дату получения (ДД.ММ.ГГГГ):")
        return ASK_RECEIVE_DATE
    else:
        await finish_claim(update, context)
        return ConversationHandler.END


async def ask_receive_date(update, context):
    context.user_data['postal_receive_date'] = update.message.text.strip()
    await finish_claim(update, context)
    return ConversationHandler.END


async def finish_claim(update, context):
    file_path = context.user_data.get('file_path')
    logging.info(
        "Trying to process file_path from user_data: %s",
        file_path
    )
    if not file_path or not os.path.exists(file_path):
        await update.message.reply_text(
            f'Ошибка: файл {file_path} не найден на диске.'
        )
        logging.error(
            "File for processing not found: %s",
            file_path
        )
        return

    # Извлекаем текст из документа для нового парсера
    doc = Document(file_path)
    text = "\n".join(p.text for p in doc.paragraphs)

    # Используем sliding window парсер
    claim_data = parse_documents_with_sliding_window(text)
    claim_data = apply_llm_fallback(text, claim_data)

    claim_data['claim_number'] = context.user_data.get('claim_number', '')
    claim_data['claim_date'] = context.user_data.get('claim_date', '')
    key_rates = get_key_rates_from_395gk()
    try:
        interest_data = calculate_full_395(
            file_path, key_rates=key_rates
        )
    except Exception as exc:
        logging.error(
            "Ошибка расчета процентов: %s",
            exc,
            exc_info=True
        )
        interest_data = {
            'total_interest': 0.0,
            'detailed_calc': [],
            'error': str(exc)
        }
    if interest_data.get('error') and update.message:
        await update.message.reply_text(
            "⚠️ Не удалось найти таблицу расчета процентов. "
            "Продолжаю без процентов."
        )

    # Получаем сумму долга из нового парсера
    debt_amount = parse_amount(claim_data.get('debt', '0'))
    total_interest = parse_amount(interest_data.get('total_interest', 0.0))
    total_claim = debt_amount + total_interest

    duty_data = calculate_duty(total_claim)
    if 'error' in duty_data:
        await update.message.reply_text(str(duty_data['error']))
        return
    for key in ['claim_status', 'claim_date', 'claim_number', 'postal_receive_date']:
        if key not in context.user_data:
            context.user_data[key] = ''

    # Используем данные из нового парсера для истца и ответчика
    plaintiff_name = normalize_str(claim_data.get('plaintiff_name'))
    defendant_name = normalize_str(claim_data.get('defendant_name'))
    contract_parties = claim_data.get('contract_parties', '')
    contract_parties_short = claim_data.get('contract_parties_short', '')

    # Проверяем, является ли истец ИП
    is_plaintiff_ip = 'ИП' in plaintiff_name or 'Индивидуальный предприниматель' in plaintiff_name
    is_defendant_ip = 'ИП' in defendant_name or 'Индивидуальный предприниматель' in defendant_name

    # Форматируем имена для использования в тексте (короткие названия)
    plaintiff_name_short = format_organization_name_short(plaintiff_name)
    defendant_name_short = format_organization_name_short(defendant_name)
    plaintiff_ogrn_type = get_ogrn_label(
        plaintiff_name,
        claim_data.get('plaintiff_inn', '')
    )

    # Получаем информацию о подсудности из контекста
    jurisdiction_info = context.user_data.get('jurisdiction_info')
    if jurisdiction_info:
        court_name = jurisdiction_info.court_name
        court_address = jurisdiction_info.court_address
    else:
        # Fallback на старую логику
        court_name, court_address = get_court_by_address(
            claim_data.get('defendant_address', 'Не указано')
        )

    legal_fees_value = parse_amount(claim_data.get('legal_fees', '0'))
    docs_track_number = join_list_values(
        claim_data.get('postal_numbers', [])
    ) or context.user_data.get('claim_number', '')
    docs_received_date = join_list_values(
        claim_data.get('postal_dates', [])
    ) or context.user_data.get('postal_receive_date', '')
    documents_list_structured = extract_documents_list_structure(text)
    documents_list = build_documents_list(claim_data)
    plaintiff_birth_info = normalize_str(
        claim_data.get('plaintiff_birth_info'),
        default='' if not is_plaintiff_ip else 'Не указано'
    )
    replacements = {
        '{claim_paragraph}': generate_claim_paragraph(
            context.user_data
        ),
        '{postal_block}': format_document_list(
            claim_data.get('postal_block', 'Не указано')
        ),
        '{postal_numbers_all}': (
            ', '.join(claim_data.get('postal_numbers', []))
            or 'Не указано'
        ),
        '{postal_dates_all}': (
            ', '.join(claim_data.get('postal_dates', []))
            or 'Не указано'
        ),
        '{court_name}': court_name,
        '{court_address}': court_address,
        '{plaintiff_name}': plaintiff_name,
        '{plaintiff_name_short}': plaintiff_name_short,
        '{plaintiff_name_formatted}': plaintiff_name_short,
        '{plaintiff_inn}': normalize_str(claim_data.get('plaintiff_inn')),
        '{plaintiff_kpp}': '' if is_plaintiff_ip else normalize_str(claim_data.get('plaintiff_kpp')),
        '{plaintiff_ogrn}': normalize_str(claim_data.get('plaintiff_ogrn')),
        '{plaintiff_address}': normalize_str(
            claim_data.get('plaintiff_address')
        ).replace('\n', ' ').strip(),
        '{defendant_name}': defendant_name,
        '{defendant_name_short}': defendant_name_short,
        '{defendant_inn}': normalize_str(claim_data.get('defendant_inn')),
        '{defendant_kpp}': '' if is_defendant_ip else normalize_str(claim_data.get('defendant_kpp')),
        '{defendant_ogrn}': normalize_str(claim_data.get('defendant_ogrn')),
        '{defendant_address}': normalize_str(
            claim_data.get('defendant_address')
        ).replace('\n', ' ').strip(),
        '{contract_parties}': contract_parties,
        '{contract_parties_short}': contract_parties_short,
        '{total_claim}': f"{total_claim:,.2f}".replace(',', ' '),
        '{claim_total}': f"{total_claim:,.2f}".replace(',', ' '),
        '{duty}': f"{duty_data['duty']:,.0f}".replace(',', ' '),
        '{debt}': generate_debt_text(claim_data),
        '{payment_terms}': generate_payment_terms(claim_data),
        '{contracts}': format_document_list(claim_data.get('contracts', '')),
        '{contract_applications}': format_document_list(claim_data.get('contract_applications', '')),
        '{cargo_docs}': format_document_list(claim_data.get('cargo_docs', '')),
        '{invoice_blocks}': format_document_list(claim_data.get('invoice_blocks', '')),
        '{upd_blocks}': format_document_list(claim_data.get('upd_blocks', '')),
        '{invoices}': claim_data.get('invoice_blocks', 'Не указано'),
        '{upds}': claim_data.get('upd_blocks', 'Не указано'),
        '{claim_date}': normalize_str(context.user_data.get('claim_date', '')),
        '{claim_number}': normalize_str(context.user_data.get('claim_number', '')),
        '{claim_track_number}': normalize_str(
            context.user_data.get('claim_number', '')
        ),
        '{docs_track_number}': normalize_str(docs_track_number),
        '{docs_received_date}': normalize_str(docs_received_date),
        '{documents_list}': documents_list,
        '{total_interest}': f"{total_interest:,.2f}".replace(',', ' '),
        '{legal_fees}': f"{legal_fees_value:,.2f}".replace(',', ' '),
        '{legal_fee}': f"{legal_fees_value:,.2f}".replace(',', ' '),
        '{legal_contract_number}': normalize_str(
            claim_data.get('legal_contract_number')
        ),
        '{legal_contract_date}': normalize_str(
            claim_data.get('legal_contract_date')
        ),
        '{legal_payment_number}': normalize_str(
            claim_data.get('legal_payment_number')
        ),
        '{legal_payment_date}': normalize_str(
            claim_data.get('legal_payment_date')
        ),
        '{total_expenses}': (
            f"{float(str(duty_data['duty'])) + legal_fees_value:,.0f}"
            .replace(',', ' ')
        ),
        '{calculation_date}': datetime.today().strftime('%d.%m.%Y г.'),
        '{signatory}': normalize_str(
            claim_data.get('signatory')
        ).replace('\n', ' ').strip(),
        '{signature_block}': normalize_str(claim_data.get('signature_block')),
        '{postal_numbers}': normalize_str(
            context.user_data.get('claim_number', '')
        ),
        '{postal_receive_date}': normalize_str(
            context.user_data.get('postal_receive_date', '')
        ),
        '{payment_days}': claim_data.get('payment_days', 'Не указано'),
        '{plaintiff_ogrn_type}': plaintiff_ogrn_type,
        '{plaintiff_birth_info}': plaintiff_birth_info,
    }
    result_docx = create_isk_document(
        claim_data,
        interest_data,
        duty_data,
        replacements,
        documents_list_structured=documents_list_structured
    )
    f107_path = None
    poa_path = None
    try:
        defendant_display_name = resolve_defendant_display_name(
            defendant_name_short,
            defendant_name
        )
        f107_items = build_f107_items(
            claim_data.get('attachments', []),
            defendant_display_name
        )
        sender_name = normalize_str(
            claim_data.get('signatory'),
            default=''
        )
        if sender_name == 'Не указано':
            sender_name = ''
        if not sender_name:
            sender_name = (
                plaintiff_name_short
                if plaintiff_name_short != 'Не указано'
                else ''
            )
        sender_company = (
            plaintiff_name
            if plaintiff_name != 'Не указано'
            else plaintiff_name_short
        )
        f107_path = create_f107_document(
            f107_items,
            sender_name,
            sender_company
        )
    except FileNotFoundError as exc:
        logging.warning("Не удалось сформировать Ф107: %s", exc)
    except Exception as exc:
        logging.error("Ошибка формирования Ф107: %s", exc, exc_info=True)
    try:
        poa_replacements = {
            '{poa_date}': format_poa_date(),
            '{plaintiff_name}': normalize_str(plaintiff_name),
            '{plaintiff_inn}': normalize_str(claim_data.get('plaintiff_inn')),
            '{plaintiff_ogrn}': normalize_str(claim_data.get('plaintiff_ogrn')),
            '{plaintiff_address}': normalize_str(
                claim_data.get('plaintiff_address')
            ).replace('\n', ' ').strip(),
        }
        poa_path = create_power_of_attorney_document(poa_replacements)
    except FileNotFoundError as exc:
        logging.warning("Не удалось сформировать доверенность: %s", exc)
    except Exception as exc:
        logging.error(
            "Ошибка формирования доверенности: %s",
            exc,
            exc_info=True
        )
    with open(result_docx, 'rb') as f:
        await update.message.reply_document(
            InputFile(f, filename="Исковое_заявление.docx"),
            caption="Исковое заявление по ст. 395 ГК РФ"
        )
    if f107_path:
        with open(f107_path, 'rb') as f:
            await update.message.reply_document(
                InputFile(f, filename="Опись_вложения_F107.docx"),
                caption="Опись вложения (форма Ф107)"
            )
    if poa_path:
        with open(poa_path, 'rb') as f:
            await update.message.reply_document(
                InputFile(f, filename="Доверенность.docx"),
                caption="Доверенность на представителя"
            )
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(result_docx):
            os.remove(result_docx)
        if f107_path and os.path.exists(f107_path):
            os.remove(f107_path)
        if poa_path and os.path.exists(poa_path):
            os.remove(poa_path)
    except Exception as e:
        logging.warning(
            f"Не удалось удалить файл {file_path}: {e}"
        )


async def ask_jurisdiction(update, context):
    """
    Спрашивает пользователя о подсудности спора.
    """
    from jurisdiction import JurisdictionDetector, format_jurisdiction_for_user

    file_path = context.user_data.get('file_path')
    if not file_path or not os.path.exists(file_path):
        await update.message.reply_text('Ошибка: файл не найден.')
        return ConversationHandler.END

    # Извлекаем текст из документа
    doc = Document(file_path)
    text = "\n".join(p.text for p in doc.paragraphs)

    # Парсим базовые данные для определения адресов
    claim_data = parse_documents_with_sliding_window(text)
    claim_data = apply_llm_fallback(text, claim_data)
    defendant_address = claim_data.get('defendant_address', 'Не указано')

    # Определяем подсудность
    detector = JurisdictionDetector()
    jurisdiction_info = detector.detect_jurisdiction(
        text=text,
        defendant_address=defendant_address
    )

    # Сохраняем в контекст
    context.user_data['jurisdiction_info'] = jurisdiction_info
    context.user_data['claim_data'] = claim_data

    # Формируем сообщение для пользователя
    info_text = format_jurisdiction_for_user(jurisdiction_info)

    # Кнопки для выбора
    keyboard = []

    if jurisdiction_info.confidence > 0.7:
        # Высокая уверенность - предлагаем подтвердить
        keyboard.append([
            InlineKeyboardButton("✅ Верно", callback_data='jurisdiction_confirm')
        ])

    keyboard.extend([
        [InlineKeyboardButton("📝 Указать другой суд", callback_data='jurisdiction_custom')],
        [InlineKeyboardButton("❓ По месту ответчика (по умолчанию)", callback_data='jurisdiction_default')]
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        "🏛 *Определение подсудности*\n\n"
        f"{info_text}\n\n"
        "Подсудность определена верно?"
    )

    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

    return ASK_JURISDICTION


async def jurisdiction_chosen(update, context):
    """
    Обрабатывает выбор пользователя по подсудности.
    """
    query = update.callback_query
    await query.answer()

    choice = query.data

    if choice == 'jurisdiction_confirm':
        # Подтверждено - переходим к вопросам о претензии
        await query.edit_message_text(
            "✅ Подсудность подтверждена.\n\nТеперь ответьте на вопросы о претензии."
        )
        return await ask_claim_status_after_jurisdiction(query, context)

    elif choice == 'jurisdiction_default':
        # Используем подсудность по умолчанию
        from jurisdiction import JurisdictionDetector

        claim_data = context.user_data.get('claim_data', {})
        defendant_address = claim_data.get('defendant_address', 'Не указано')

        detector = JurisdictionDetector()
        jurisdiction_info = detector._get_default_jurisdiction(defendant_address)
        context.user_data['jurisdiction_info'] = jurisdiction_info

        await query.edit_message_text(
            f"✅ Используется подсудность по месту ответчика.\n\n"
            f"Суд: {jurisdiction_info.court_name}\n\n"
            "Теперь ответьте на вопросы о претензии."
        )
        return await ask_claim_status_after_jurisdiction(query, context)

    elif choice == 'jurisdiction_custom':
        # Запрашиваем ручной ввод суда
        await query.edit_message_text(
            "Введите название суда (например: Арбитражный суд Московской области):"
        )
        return ASK_CUSTOM_COURT


async def handle_custom_court(update, context):
    """
    Обрабатывает ручной ввод названия суда.
    """
    from jurisdiction import JurisdictionDetector, JurisdictionInfo, JurisdictionType

    custom_court = update.message.text.strip()
    detector = JurisdictionDetector()

    # Пытаемся найти суд в базе
    region = custom_court.replace('Арбитражный суд', '').strip()
    court_info_dict = detector._find_court_by_region(region)

    if court_info_dict:
        jurisdiction_info = JurisdictionInfo(
            type=JurisdictionType.CUSTOM,
            court_name=court_info_dict['name'],
            court_address=court_info_dict['address'],
            confidence=1.0
        )
        context.user_data['jurisdiction_info'] = jurisdiction_info

        await update.message.reply_text(
            f"✅ Суд установлен:\n{court_info_dict['name']}\n\n"
            "Теперь ответьте на вопросы о претензии."
        )
    else:
        # Не нашли в базе - сохраняем как есть
        jurisdiction_info = JurisdictionInfo(
            type=JurisdictionType.CUSTOM,
            court_name=custom_court,
            court_address="Уточните адрес суда",
            confidence=0.5
        )
        context.user_data['jurisdiction_info'] = jurisdiction_info

        await update.message.reply_text(
            f"⚠️ Суд не найден в базе. Использую введенное название:\n{custom_court}\n\n"
            "Не забудьте проверить адрес суда в готовом документе!\n\n"
            "Теперь ответьте на вопросы о претензии."
        )

    return await ask_claim_status_after_jurisdiction(update, context)


async def ask_claim_status_after_jurisdiction(update_or_query, context):
    """
    Переход к вопросам о претензии после определения подсудности.
    """
    config = get_russian_post_config()
    if config.get("enabled"):
        context.user_data['use_tracking_api'] = True
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(
                "Введите трек-номер отправления претензии. "
                "Я сам определю дату отправки и получения."
            )
        else:
            await update_or_query.message.reply_text(
                "Введите трек-номер отправления претензии. "
                "Я сам определю дату отправки и получения."
            )
        return ASK_TRACK

    context.user_data['use_tracking_api'] = False
    keyboard = [
        [
            InlineKeyboardButton("✅ Да", callback_data='claim_received'),
            InlineKeyboardButton("❌ Нет", callback_data='claim_not_received'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Определяем, откуда пришел вызов
    if hasattr(update_or_query, 'message') and update_or_query.message:
        # Это обычный update с message
        await update_or_query.message.reply_text(
            "Ответчик получил требование?",
            reply_markup=reply_markup
        )
    else:
        # Это callback query
        await update_or_query.message.reply_text(
            "Ответчик получил требование?",
            reply_markup=reply_markup
        )

    return ASK_CLAIM_STATUS


async def handle_doc_entry(update, context):
    try:
        if update.effective_user:
            logging.info(
                "Received document from user %s",
                update.effective_user.id
            )
        if not update.message:
            return
        doc = update.message.document
        if not doc or not doc.file_name:
            logging.warning(
                "Invalid document"
            )
            await update.message.reply_text(
                'Пожалуйста, отправь файл Word (.docx).'
            )
            return
        if not doc.file_name.lower().endswith('.docx'):
            logging.warning(
                "Invalid file format"
            )
            await update.message.reply_text(
                'Пожалуйста, отправь файл Word (.docx).'
            )
            return
        os.makedirs('uploads', exist_ok=True)
        unique_name = f"{uuid.uuid4()}_{doc.file_name}"
        file_path = os.path.join('uploads', unique_name)
        telegram_file = await doc.get_file()
        await telegram_file.download_to_drive(file_path)
        logging.info(
            "Downloaded file: %s",
            file_path
        )
        context.user_data['file_path'] = file_path
        logging.info(
            "Saved file_path in user_data: %s",
            file_path
        )
        # ИЗМЕНЕНО: теперь сначала спрашиваем о подсудности
        return await ask_jurisdiction(update, context)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Ошибка: {e}\n{tb}")
        if update.message:
            await update.message.reply_text(
                f'Ошибка обработки: {e}. Проверьте формат файла.'
            )


conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.Document.ALL, handle_doc_entry)],
    states={
        ASK_JURISDICTION: [CallbackQueryHandler(jurisdiction_chosen)],
        ASK_CUSTOM_COURT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_court)
        ],
        ASK_CLAIM_STATUS: [CallbackQueryHandler(claim_status_chosen)],
        ASK_TRACK: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_track)
        ],
        ASK_RECEIVE_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_receive_date)
        ],
        ASK_SEND_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_send_date)
        ],
    },
    fallbacks=[],
    per_message=False,
)


def generate_postal_block(postal_numbers, postal_dates):
    if not postal_numbers or not postal_dates or len(postal_numbers) != len(postal_dates):
        return "Не указано"
    if len(postal_numbers) == 1:
        return (
            f"Почтовым уведомлением № {postal_numbers[0]} об отправке и получении "
            f"{postal_dates[0]} оригиналов документов Заказчиком."
        )
    else:
        pairs = [
            f"№ {num} от {date}" for num, date in zip(postal_numbers, postal_dates)
        ]
        return (
            f"Почтовыми уведомлениями {', '.join(pairs)} "
            f"об отправке и получении оригиналов документов Заказчиком."
        )


def clean_uploads_folder():
    """Удаляет все файлы из папки uploads при запуске бота."""
    uploads_dir = os.path.join(os.path.dirname(__file__), 'uploads')
    if not os.path.exists(uploads_dir):
        return
    for filename in os.listdir(uploads_dir):
        file_path = os.path.join(uploads_dir, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                logging.info(f"Удален файл из uploads: {file_path}")
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
                logging.info(f"Удалена папка из uploads: {file_path}")
        except Exception as e:
            logging.warning(f"Не удалось удалить {file_path}: {e}")


def main() -> None:
    """Запускает Telegram бота."""
    logging.info("Starting bot...")
    clean_uploads_folder()  # Очищаем uploads при запуске
    if not TOKEN:
        logging.error(
            "TOKEN is not set. Please provide a valid Telegram bot token."
        )
        raise ValueError(
            "TOKEN is not set. Please provide a valid Telegram bot token.")
    app = Application.builder().token(TOKEN).build()
    logging.info("Bot initialized")
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    logging.info("Handlers added")
    app.run_polling()
    logging.info("Bot is polling")


if __name__ == '__main__':
    main()
