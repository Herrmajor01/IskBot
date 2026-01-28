#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный модуль извлечения данных из PDF.

Гибридный подход:
1. pdfplumber для структурированных документов (таблицы, формы)
2. PyMuPDF как fallback для простого текста
3. Vision LLM (Qwen3-VL) для сложных случаев (сканы, плохое качество)
"""

import base64
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Пробуем импортировать библиотеки
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    logger.warning("pdfplumber не установлен. pip install pdfplumber")

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF не установлен. pip install pymupdf")


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


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

    # Модель для Vision (по умолчанию qwen3-vl:8b)
    vision_model = _get_env("OLLAMA_VISION_MODEL", "qwen3-vl:8b").strip()

    enabled_raw = os.getenv("VISION_LLM_ENABLED")
    if enabled_raw is None:
        enabled = bool(base_url)
    else:
        enabled = enabled_raw.lower() in ("1", "true", "yes", "on")

    timeout = int(_get_env("OLLAMA_VISION_TIMEOUT", "120"))  # Vision модели медленнее

    return {
        "enabled": enabled,
        "base_url": base_url.rstrip("/"),
        "model": vision_model,
        "timeout": timeout,
    }


def check_vision_model_available(config: Dict[str, Any]) -> bool:
    """
    Проверяет доступность Vision модели в Ollama.
    """
    if not config["base_url"]:
        return False

    try:
        response = requests.get(
            f"{config['base_url']}/api/tags",
            timeout=5
        )
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            # Проверяем есть ли наша модель или её вариации
            target = config["model"].split(":")[0]  # qwen3-vl
            for name in model_names:
                if target in name:
                    return True
        return False
    except Exception as e:
        logger.debug(f"Vision model check failed: {e}")
        return False


# ============================================================
# Извлечение текста с помощью pdfplumber
# ============================================================

def extract_with_pdfplumber(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Извлекает текст и таблицы из PDF с помощью pdfplumber.

    Returns:
        Список словарей с данными по каждой странице:
        {
            "page_num": int,
            "text": str,
            "tables": List[List[List[str]]],  # Таблицы
            "text_quality": float  # Оценка качества 0-1
        }
    """
    if not HAS_PDFPLUMBER:
        raise ImportError("pdfplumber не установлен")

    pages_data = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_data = {
                "page_num": i + 1,
                "text": "",
                "tables": [],
                "text_quality": 1.0
            }

            # Извлекаем текст
            text = page.extract_text() or ""
            page_data["text"] = text

            # Извлекаем таблицы
            tables = page.extract_tables() or []
            page_data["tables"] = tables

            # Оцениваем качество текста
            page_data["text_quality"] = _estimate_text_quality(text)

            # Если есть таблицы, конвертируем их в текст и добавляем
            if tables:
                table_text = _tables_to_text(tables)
                page_data["text"] = text + "\n\n" + table_text

            pages_data.append(page_data)

    return pages_data


def _tables_to_text(tables: List[List[List[str]]]) -> str:
    """
    Конвертирует таблицы в читаемый текст.
    """
    result = []
    for table in tables:
        for row in table:
            # Фильтруем None и пустые ячейки
            cells = [str(c).strip() if c else "" for c in row]
            if any(cells):
                result.append(" | ".join(cells))
        result.append("")  # Пустая строка между таблицами
    return "\n".join(result)


def _estimate_text_quality(text: str) -> float:
    """
    Оценивает качество извлечённого текста (0-1).
    Низкое качество может указывать на скан или плохой OCR.
    """
    if not text:
        return 0.0

    # Подсчёт нечитаемых символов
    total_chars = len(text)
    if total_chars == 0:
        return 0.0

    # Считаем "хорошие" символы (буквы, цифры, пунктуация, пробелы)
    good_chars = sum(1 for c in text if c.isalnum() or c.isspace() or c in '.,;:!?()-"\'№@')

    # Проверяем наличие типичных OCR-артефактов
    ocr_artifacts = [
        r'[^\x00-\x7FА-Яа-яЁё\s\d.,;:!?()\-"\'№@/\\]',  # Странные символы
        r'\b[А-Я]{10,}\b',  # Длинные последовательности заглавных
        r'[\^~`|]',  # Редкие символы
    ]
    artifact_count = sum(len(re.findall(p, text)) for p in ocr_artifacts)

    quality = good_chars / total_chars
    quality -= min(0.3, artifact_count / total_chars * 10)  # Штраф за артефакты

    return max(0.0, min(1.0, quality))


def estimate_text_quality(text: str) -> float:
    """
    Публичная обёртка для оценки качества текста (0-1).
    """
    return _estimate_text_quality(text)


# ============================================================
# Извлечение текста с помощью PyMuPDF
# ============================================================

def extract_with_pymupdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Извлекает текст из PDF с помощью PyMuPDF.
    """
    if not HAS_PYMUPDF:
        raise ImportError("PyMuPDF не установлен")

    pages_data = []

    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        text = page.get_text()
        pages_data.append({
            "page_num": i + 1,
            "text": text,
            "tables": [],
            "text_quality": _estimate_text_quality(text)
        })
    doc.close()

    return pages_data


def convert_page_to_image(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """
    Конвертирует страницу PDF в изображение PNG.

    Args:
        pdf_path: Путь к PDF
        page_num: Номер страницы (начиная с 0)
        dpi: Разрешение (по умолчанию 150)

    Returns:
        PNG изображение в байтах
    """
    if not HAS_PYMUPDF:
        raise ImportError("PyMuPDF не установлен")

    doc = fitz.open(pdf_path)
    page = doc[page_num]

    # Матрица масштабирования для нужного DPI
    zoom = dpi / 72  # 72 - стандартный DPI PDF
    matrix = fitz.Matrix(zoom, zoom)

    # Рендерим страницу в pixmap
    pix = page.get_pixmap(matrix=matrix)

    # Конвертируем в PNG
    png_bytes = pix.tobytes("png")

    doc.close()
    return png_bytes


# ============================================================
# Vision LLM извлечение
# ============================================================

def extract_with_vision_llm(
    pdf_path: str,
    page_nums: Optional[List[int]] = None,
    extraction_prompt: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Извлекает данные из PDF с помощью Vision LLM (Qwen3-VL).

    Args:
        pdf_path: Путь к PDF файлу
        page_nums: Номера страниц для обработки (None = все)
        extraction_prompt: Промпт для извлечения (None = общий промпт)

    Returns:
        Список словарей с данными по каждой странице
    """
    config = get_vision_config()

    if not config["enabled"] or not config["base_url"]:
        logger.warning("Vision LLM не настроен")
        return []

    if not HAS_PYMUPDF:
        logger.error("PyMuPDF нужен для конвертации PDF в изображения")
        return []

    # Открываем PDF для определения количества страниц
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    if page_nums is None:
        page_nums = list(range(total_pages))

    # Промпт по умолчанию
    if extraction_prompt is None:
        extraction_prompt = _get_default_vision_prompt()

    results = []

    for page_num in page_nums:
        if page_num >= total_pages:
            continue

        try:
            # Конвертируем страницу в изображение
            image_bytes = convert_page_to_image(pdf_path, page_num, dpi=150)
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')

            # Отправляем в Vision LLM
            response = _call_vision_llm(
                config=config,
                image_base64=image_base64,
                prompt=extraction_prompt
            )

            if response:
                results.append({
                    "page_num": page_num + 1,
                    "text": response.get("text", ""),
                    "extracted_data": response.get("data", {}),
                    "text_quality": 1.0  # Vision LLM даёт качественный результат
                })

        except Exception as e:
            logger.error(f"Vision LLM error on page {page_num + 1}: {e}")
            results.append({
                "page_num": page_num + 1,
                "text": "",
                "extracted_data": {},
                "text_quality": 0.0,
                "error": str(e)
            })

    return results


def _get_default_vision_prompt() -> str:
    """
    Возвращает промпт для извлечения данных из документа.
    """
    return """Ты — эксперт по обработке документов. Извлеки текст и структурированные данные из этого изображения документа.

Это может быть:
- Транспортная накладная (ТН)
- Товарно-транспортная накладная (ТТН)
- Заявка на перевозку
- УПД (универсальный передаточный документ)
- Акт

Извлеки и верни JSON со следующими полями (если они есть в документе):

{
    "document_type": "тип документа",
    "document_number": "номер документа",
    "document_date": "дата документа в формате ДД.ММ.ГГГГ",
    "sender_name": "грузоотправитель",
    "sender_inn": "ИНН отправителя",
    "receiver_name": "грузополучатель",
    "receiver_inn": "ИНН получателя",
    "carrier_name": "перевозчик",
    "carrier_inn": "ИНН перевозчика",
    "driver_name": "ФИО водителя",
    "vehicle_plate": "госномер транспортного средства",
    "trailer_plate": "госномер прицепа",
    "load_date": "дата погрузки",
    "load_address": "адрес погрузки",
    "unload_date": "дата разгрузки",
    "unload_address": "адрес разгрузки",
    "cargo_description": "описание груза",
    "cargo_weight": "вес груза",
    "amount": "сумма",
    "full_text": "весь текст документа как есть"
}

ВАЖНО:
- Извлекай только то, что реально видишь в документе
- Если поле не найдено, не включай его в ответ
- Даты должны быть в формате ДД.ММ.ГГГГ
- ИНН должен содержать только цифры (10 или 12)
- Госномера в формате А123БВ77 или АБ1234 77

Верни ТОЛЬКО валидный JSON без markdown-разметки."""


def _get_claim_vision_prompt() -> str:
    """
    Промпт для извлечения ключевых данных, важных для претензии/иска.
    """
    return """Ты анализируешь изображение документа для подготовки претензии/иска.

Сначала определи ТИП ДОКУМЕНТА. Используй один из вариантов:
- application (заявка на перевозку)
- transport_doc (транспортная/товарная накладная, ТТН, экспедиторская расписка)
- invoice (счет, счет на оплату)
- upd (УПД)
- act (акт выполненных работ/оказанных услуг)
- contract (договор)
- payment_order (платежное поручение)
- postal_receipt (квитанция Почты России, отчет об отслеживании, РПО)
- cdek_receipt (СДЭК накладная/квитанция)
- guarantee_letter (гарантийное письмо)
- other

Верни ТОЛЬКО JSON без markdown. Если поле не найдено — не включай его.

JSON поля:
{
  "document_type": "один из вариантов выше",
  "document_type_raw": "как написано в документе",
  "document_number": "номер документа",
  "document_date": "дата документа ДД.ММ.ГГГГ",
  "application_number": "номер заявки",
  "application_date": "дата заявки ДД.ММ.ГГГГ",
  "invoice_number": "номер счета",
  "invoice_date": "дата счета ДД.ММ.ГГГГ",
  "upd_number": "номер УПД",
  "upd_date": "дата УПД ДД.ММ.ГГГГ",
  "contract_number": "номер договора",
  "contract_date": "дата договора ДД.ММ.ГГГГ",
  "payment_order_number": "номер платежного поручения",
  "payment_order_date": "дата платежного поручения ДД.ММ.ГГГГ",
  "amount": "сумма",
  "payment_terms": "условия оплаты (если есть)",
  "track_number": "РПО/трек-номер (если есть)",
  "sender_name": "грузоотправитель/продавец/исполнитель",
  "sender_inn": "ИНН отправителя/исполнителя",
  "receiver_name": "грузополучатель/покупатель/заказчик",
  "receiver_inn": "ИНН получателя/заказчика",
  "carrier_name": "перевозчик",
  "carrier_inn": "ИНН перевозчика",
  "driver_name": "ФИО водителя",
  "vehicle_plate": "госномер ТС",
  "trailer_plate": "госномер прицепа",
  "load_date": "дата погрузки ДД.ММ.ГГГГ",
  "unload_date": "дата разгрузки ДД.ММ.ГГГГ",
  "load_address": "адрес погрузки",
  "unload_address": "адрес разгрузки",
  "full_text": "полный текст документа"
}

ВАЖНО:
- Извлекай только то, что реально видишь в документе
- Даты строго в формате ДД.ММ.ГГГГ
- ИНН только цифры (10 или 12)
- Трек-номер — только символы без пробелов
"""


def extract_claim_document_with_vision(
    pdf_path: str,
    page_num: int
) -> Dict[str, Any]:
    """
    Извлекает ключевые данные по документу через Vision LLM
    (для претензии/иска).
    """
    config = get_vision_config()
    if not config.get("enabled"):
        return {}

    try:
        image_bytes = convert_page_to_image(pdf_path, page_num, dpi=200)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = _get_claim_vision_prompt()
        result = _call_vision_llm(config, image_base64, prompt)
        if not result:
            return {}
        data = result.get("data", {}) or {}
        if "full_text" not in data and result.get("text"):
            data["full_text"] = result.get("text")
        return data
    except Exception as exc:
        logger.error(f"Vision claim extraction error: {exc}")
        return {}


def _extract_data_from_text(text: str) -> Dict[str, Any]:
    """
    Извлекает структурированные данные из текстового ответа Vision LLM.
    Используется когда модель возвращает описание вместо JSON.

    Vision LLM возвращает данные в формате:
    - Поле: значение
    или
    **Поле**: значение
    """
    data = {}

    # Убираем markdown-форматирование
    clean_text = text.replace('**', '')

    # Простой парсер формата "- Поле: значение"
    field_mappings = {
        'номер документа': 'document_number',
        'дата документа': 'document_date',
        'фио водителя': 'driver_name',
        'инн водителя': 'driver_inn',
        'инн перевозчика': 'driver_inn',
        'госномер тс': 'vehicle_plate',
        'регистрационный номер': 'vehicle_plate',
        'госномер прицепа': 'trailer_plate',
        'грузоотправитель': 'sender_name',
        'грузополучатель': 'receiver_name',
        'адрес погрузки': 'load_address',
        'адрес разгрузки': 'unload_address',
        'дата погрузки': 'load_date',
        'дата разгрузки': 'unload_date',
        'описание груза': 'cargo_description',
        'номер заявки': 'application_number',
        'дата заявки': 'application_date',
        'заказчик': 'customer_name',
        'перевозчик': 'carrier_name',
        'исполнитель': 'carrier_name',
        'телефон водителя': 'driver_phone',
        'тип тс': 'vehicle_type',
        'стоимость': 'price',
        'условия оплаты': 'payment_terms',
        'температурный режим': 'temperature',
    }

    # Парсим формат "- Поле: значение" или "Поле: значение"
    for line in clean_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Убираем маркер списка
        if line.startswith('-'):
            line = line[1:].strip()

        # Ищем двоеточие
        if ':' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                field_name = parts[0].strip().lower()
                value = parts[1].strip()

                # Пропускаем пустые значения и "не указано"
                if not value or value.lower() in ('не указано', 'не указана',
                                                   'нет данных', '-', 'н/д'):
                    continue

                # Ищем соответствие в маппинге
                for key, mapped_field in field_mappings.items():
                    if key in field_name:
                        # Извлекаем ИНН из комбинированных полей
                        # "ООО "ЛЕНТА", ИНН 7814148471"
                        if mapped_field in ('sender_name', 'receiver_name',
                                           'customer_name', 'carrier_name'):
                            if 'инн' in value.lower():
                                inn_match = re.search(r'ИНН\s*(\d{10,12})',
                                                      value, re.IGNORECASE)
                                if inn_match:
                                    inn_field = mapped_field.replace(
                                        '_name', '_inn')
                                    data[inn_field] = inn_match.group(1)
                                    # Убираем ИНН из названия
                                    value = re.sub(
                                        r',?\s*ИНН\s*\d{10,12}', '', value
                                    ).strip()

                        if value and len(value) > 1:
                            data[mapped_field] = value
                        break

    # Fallback на regex-паттерны для полей, которые не найдены
    if not data.get('document_number'):
        match = re.search(r'№\s*([A-Za-zА-Яа-я]?\s*\d+[/\d-]+)', clean_text)
        if match:
            data['document_number'] = match.group(1).strip()

    if not data.get('document_date'):
        match = re.search(r'(\d{2}\.\d{2}\.\d{4})', clean_text)
        if match:
            data['document_date'] = match.group(1)

    if not data.get('vehicle_plate'):
        # Госномер: "E 015 ВК 11" или "Е015ВК11"
        match = re.search(
            r'\b([АВЕКМНОРСТУХABEKMHOPCTYX]\s*\d{3}\s*'
            r'[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s*\d{2,3})\b',
            clean_text
        )
        if match:
            data['vehicle_plate'] = match.group(1)

    if not data.get('driver_inn'):
        # ИНН перевозчика (12 цифр)
        match = re.search(r'ИНН[:\s]*(\d{12})', clean_text)
        if match:
            data['driver_inn'] = match.group(1)

    return data


def _call_vision_llm(
    config: Dict[str, Any],
    image_base64: str,
    prompt: str
) -> Optional[Dict[str, Any]]:
    """
    Вызывает Vision LLM через Ollama API.
    """
    url = f"{config['base_url']}/api/generate"

    payload = {
        "model": config["model"],
        "prompt": prompt,
        "images": [image_base64],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 4096,
        }
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=config["timeout"]
        )
        response.raise_for_status()

        result = response.json()
        response_text = result.get("response", "")

        # Пытаемся распарсить JSON из ответа
        try:
            # Убираем возможные markdown-обёртки
            json_text = response_text
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0]
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0]

            data = json.loads(json_text.strip())
            return {
                "text": data.get("full_text", response_text),
                "data": data
            }
        except json.JSONDecodeError:
            # Если не JSON, пытаемся извлечь данные из текста
            extracted = _extract_data_from_text(response_text)
            return {
                "text": response_text,
                "data": extracted
            }

    except requests.exceptions.Timeout:
        logger.error(f"Vision LLM timeout after {config['timeout']}s")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Vision LLM request error: {e}")
        return None


# ============================================================
# Гибридный экстрактор
# ============================================================

def extract_pdf_hybrid(
    pdf_path: str,
    quality_threshold: float = 0.7,
    use_vision_fallback: bool = True
) -> List[Dict[str, Any]]:
    """
    Гибридное извлечение данных из PDF.

    Алгоритм:
    1. Пробуем pdfplumber (лучше для таблиц)
    2. Если качество низкое, пробуем PyMuPDF
    3. Если качество всё ещё низкое и включен vision, используем Vision LLM

    Args:
        pdf_path: Путь к PDF файлу
        quality_threshold: Порог качества для vision fallback (0-1)
        use_vision_fallback: Использовать Vision LLM для плохих страниц

    Returns:
        Список данных по страницам
    """
    results = []
    low_quality_pages = []

    # Шаг 1: pdfplumber
    if HAS_PDFPLUMBER:
        try:
            results = extract_with_pdfplumber(pdf_path)
            logger.info(f"pdfplumber extracted {len(results)} pages")
        except Exception as e:
            logger.warning(f"pdfplumber failed: {e}")

    # Шаг 2: PyMuPDF fallback
    if not results and HAS_PYMUPDF:
        try:
            results = extract_with_pymupdf(pdf_path)
            logger.info(f"PyMuPDF extracted {len(results)} pages")
        except Exception as e:
            logger.warning(f"PyMuPDF failed: {e}")

    if not results:
        logger.error("No PDF extraction method available")
        return []

    # Определяем страницы с низким качеством
    for page_data in results:
        if page_data["text_quality"] < quality_threshold:
            low_quality_pages.append(page_data["page_num"] - 1)  # 0-indexed

    # Шаг 3: Vision LLM для страниц с низким качеством
    if low_quality_pages and use_vision_fallback:
        config = get_vision_config()
        if config["enabled"] and check_vision_model_available(config):
            logger.info(f"Using Vision LLM for {len(low_quality_pages)} low-quality pages")

            vision_results = extract_with_vision_llm(
                pdf_path,
                page_nums=low_quality_pages
            )

            # Заменяем результаты для этих страниц
            for vision_page in vision_results:
                page_idx = vision_page["page_num"] - 1
                if page_idx < len(results):
                    # Объединяем данные
                    results[page_idx]["text"] = vision_page.get("text", results[page_idx]["text"])
                    results[page_idx]["extracted_data"] = vision_page.get("extracted_data", {})
                    results[page_idx]["text_quality"] = vision_page.get("text_quality", 0.0)
                    results[page_idx]["vision_processed"] = True

    return results


def get_pages_text(pdf_path: str) -> List[str]:
    """
    Удобная функция для получения текста всех страниц.
    Совместима с существующим кодом в main.py.
    """
    results = extract_pdf_hybrid(pdf_path)
    return [page.get("text", "") for page in results]


# ============================================================
# Специализированные функции извлечения
# ============================================================

def extract_transport_document_with_vision(
    pdf_path: str,
    page_num: int
) -> Dict[str, Any]:
    """
    Извлекает данные транспортного документа с помощью Vision LLM.
    Специализированный промпт для ТН/ТТН.
    """
    prompt = """Опиши данные из этой транспортной накладной.

Перечисли все данные которые видишь:
- Номер документа (№)
- Дата документа
- ФИО водителя (раздел 6 или 7)
- ИНН водителя/перевозчика
- Госномер ТС (регистрационный номер)
- Госномер прицепа
- Грузоотправитель (название, ИНН)
- Грузополучатель (название, ИНН)
- Адрес погрузки
- Адрес разгрузки
- Дата погрузки
- Дата разгрузки
- Описание груза

Формат ответа: перечисли каждое поле с его значением."""

    config = get_vision_config()
    if not config["enabled"]:
        return {}

    try:
        image_bytes = convert_page_to_image(pdf_path, page_num, dpi=200)
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

        result = _call_vision_llm(config, image_base64, prompt)
        if result:
            return result.get("data", {})
    except Exception as e:
        logger.error(f"Vision extraction error: {e}")

    return {}


def extract_application_with_vision(
    pdf_path: str,
    page_num: int
) -> Dict[str, Any]:
    """
    Извлекает данные заявки на перевозку с помощью Vision LLM.
    """
    prompt = """Опиши данные из этой заявки на перевозку груза.

Перечисли все данные которые видишь:
- Номер заявки
- Дата заявки
- Заказчик (название, ИНН)
- Перевозчик/Исполнитель (название, ИНН)
- ФИО водителя
- Телефон водителя
- Госномер ТС
- Госномер прицепа
- Тип ТС (рефрижератор, тент)
- Дата погрузки
- Адрес/город погрузки
- Дата разгрузки
- Адрес/город разгрузки
- Описание груза
- Вес груза
- Стоимость перевозки
- Условия оплаты
- Температурный режим

Формат ответа: перечисли каждое поле с его значением."""

    config = get_vision_config()
    if not config["enabled"]:
        return {}

    try:
        image_bytes = convert_page_to_image(pdf_path, page_num, dpi=200)
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

        result = _call_vision_llm(config, image_base64, prompt)
        if result:
            return result.get("data", {})
    except Exception as e:
        logger.error(f"Vision extraction error: {e}")

    return {}


if __name__ == "__main__":
    # Тест модуля
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <pdf_file>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    print(f"\n=== Тестирование извлечения из {pdf_path} ===\n")

    # Проверяем конфигурацию
    config = get_vision_config()
    print(f"Vision LLM: {config['model']}")
    print(f"Enabled: {config['enabled']}")
    print(f"Available: {check_vision_model_available(config)}")

    # Гибридное извлечение
    print("\n--- Гибридное извлечение ---")
    results = extract_pdf_hybrid(pdf_path)

    for page in results[:3]:  # Первые 3 страницы
        print(f"\nСтраница {page['page_num']}:")
        print(f"  Качество: {page['text_quality']:.2f}")
        print(f"  Vision: {page.get('vision_processed', False)}")
        print(f"  Текст: {page['text'][:200]}...")
        if page.get("extracted_data"):
            print(f"  Данные: {json.dumps(page['extracted_data'], ensure_ascii=False, indent=2)}")
