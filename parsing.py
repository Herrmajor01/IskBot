"""
Модуль для парсинга данных из .docx файлов (исковые требования, периоды).
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from docx import Document


def parse_periods_from_docx(
    docx_path: str,
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Парсит таблицу из .docx и возвращает периоды для расчета процентов.
    """
    doc = Document(docx_path)
    if not doc.tables:
        raise ValueError("В файле .docx отсутствует таблица")
    table = doc.tables[0]

    if len(table.rows) == 0:
        raise ValueError("Таблица пуста")

    header_cells = table.rows[0].cells
    if len(header_cells) < 7:
        raise ValueError(
            f"Таблица должна содержать минимум 7 столбцов, найдено: "
            f"{len(header_cells)}"
        )

    logging.info(
        f"Найдена таблица с {len(header_cells)} столбцами и "
        f"{len(table.rows)} строками"
    )

    periods = []
    current_sum = 0.0

    for row in table.rows[1:]:
        cells = row.cells
        if len(cells) < 7:
            logging.warning(
                f"Некорректная строка таблицы: {len(cells)} столбцов"
            )
            continue
        try:
            amount_text = cells[0].text.strip()
            logging.debug(f"Оригинальная сумма: '{amount_text}'")
            if not amount_text:
                logging.debug("Сумма пустая, пропускаем строку")
                continue
            cleaned_for_check = re.sub(r'[^\d.,+\-]', '', amount_text)
            if not cleaned_for_check:
                logging.debug(f"Строка не содержит чисел: '{amount_text}'")
                continue
            if re.search(r'[А-Яа-яA-Za-z]', amount_text):
                if any(word in amount_text.lower()
                       for word in ["сумма", "задолж", "процент"]):
                    logging.debug(
                        f"Строка пропущена по ключевому слову: "
                        f"'{amount_text}'"
                    )
                    continue
            amount_text = re.sub(r'[^\d.,+\-]', '', amount_text)
            logging.debug(f"После очистки: '{amount_text}'")
            if not amount_text:
                logging.debug("Сумма пустая после очистки, пропускаем строку")
                continue
            amount_text = amount_text.rstrip('.').rstrip(',').rstrip()
            logging.debug(f"После rstrip: '{amount_text}'")
            if not amount_text:
                logging.debug("Сумма пустая после rstrip, пропускаем строку")
                continue
            if not re.match(r'^[\+\-]?\d+([.,]\d+)?$', amount_text):
                logging.debug(f"Сумма не является числом: '{amount_text}'")
                continue
            try:
                if amount_text.startswith('+'):
                    amount = float(amount_text[1:].replace(',', '.'))
                    current_sum += amount
                    logging.debug(
                        f"Добавлена сумма: {amount}, текущая: {current_sum}"
                    )
                else:
                    current_sum = float(amount_text.replace(',', '.'))
                    logging.debug(f"Установлена сумма: {current_sum}")
            except ValueError as e:
                logging.debug(f"Ошибка конвертации суммы '{amount_text}': {e}")
                continue
            date_from_text = cells[1].text.strip()
            date_to_text = cells[2].text.strip()
            if not date_from_text or not date_to_text:
                continue
            if date_to_text.lower() == "новая задолженность":
                continue
            if (
                not re.match(r'\d{2}\.\d{2}\.\d{4}', date_from_text)
                or not re.match(r'\d{2}\.\d{2}\.\d{4}', date_to_text)
            ):
                continue
            date_from = datetime.strptime(date_from_text, '%d.%m.%Y')
            date_to = datetime.strptime(date_to_text, '%d.%m.%Y')
            days_text = cells[3].text.strip()
            if not days_text or not days_text.isdigit():
                continue
            days = int(days_text)
            rate_text = cells[4].text.strip()
            if not rate_text:
                continue
            rate = float(rate_text.replace(',', '.'))
            interest_text = cells[6].text.strip()
            if not interest_text:
                continue
            interest_text = re.sub(r'[^\d.,]', '', interest_text)
            interest_text = interest_text.rstrip('.').rstrip(',').rstrip()
            if not interest_text:
                continue
            if not re.match(r'^\d+([.,]\d+)?$', interest_text):
                logging.debug(
                    f"Проценты не являются числом: '{interest_text}'"
                )
                continue
            try:
                interest = float(interest_text.replace(',', '.'))
            except ValueError as e:
                logging.debug(
                    f"Ошибка конвертации процентов '{interest_text}': {e}"
                )
                continue
            periods.append({
                'sum': current_sum,
                'date_from': date_from,
                'date_to': date_to,
                'days': days,
                'rate': rate,
                'interest': interest,
                'formula': cells[5].text.strip() if len(cells) > 5 else ""
            })
        except Exception as e:
            logging.warning(
                f"Ошибка парсинга строки: {e}"
            )
            logging.warning(
                f"Содержимое ячеек: {[cell.text.strip() for cell in cells]}"
            )
            continue
    if not periods:
        raise ValueError("Не удалось распарсить ни одной строки из таблицы")
    return periods, current_sum


def parse_contract_applications(text: str) -> str:
    """
    Парсит договоры-заявки на перевозку груза целиком.
    Возвращает полную фразу для подстановки в шаблон.
    """
    # Паттерны для поиска договоров-заявок
    patterns = [
        # Договор-заявка (единственное число)
        (r'договор-заявка[а-яё\s\-]*на перевозку груза[^;\n]*?'
         r'(?:№\s*\d+[^;\n]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?)[^;\n]*'),
        # Договор - заявки (множественное число)
        (r'договор\s*-\s*заявки[а-яё\s\-]*на перевозку груза[^;\n]*?'
         r'(?:№\s*\d+[^;\n]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?)[^;\n]*'),
        # Договоры-заявки (множественное число)
        (r'договоры-заявки[а-яё\s\-]*на перевозку груза[^;\n]*?'
         r'(?:№\s*\d+[^;\n]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?)[^;\n]*'),
        # Договор-заявка без дефиса
        (r'договор\s+заявка[а-яё\s\-]*на перевозку груза[^;\n]*?'
         r'(?:№\s*\d+[^;\n]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n]*?г?\.?)[^;\n]*'),
    ]

    found_contracts = []

    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            contract_text = match.group(0).strip()
            # Очищаем от лишних пробелов и переносов строк
            contract_text = re.sub(r'\s+', ' ', contract_text)
            if contract_text not in found_contracts:
                found_contracts.append(contract_text)

    if found_contracts:
        return '; '.join(found_contracts)
    else:
        return "Не указано"


def parse_cargo_documents(text: str) -> str:
    """
    Парсит грузосопроводительные документы.
    Возвращает только реальные документы и 'Комплектом сопроводительных документов на груз'.
    """
    found_documents = []

    # 1. Комплект сопроводительных документов - только эту фразу
    komplekt_match = re.search(
        r'Комплектом сопроводительных документов на груз', text, re.IGNORECASE)
    if komplekt_match:
        found_documents.append(
            "Комплектом сопроводительных документов на груз")

    # 2. Транспортная накладная - название + номер + дата
    transport_matches = re.finditer(
        r'([Тт]ранспортн[а-яё]* накладн[а-яё]*)\s*'
        r'(?:№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})|'
        r'от\s*(\d{2}\.\d{2}\.\d{4}))',
        text, re.IGNORECASE
    )
    for match in transport_matches:
        name = match.group(1)
        if match.group(2) and match.group(3):  # есть номер и дата
            doc_text = f"{name} № {match.group(2)} от {match.group(3)}"
        elif match.group(4):  # только дата
            doc_text = f"{name} от {match.group(4)}"
        else:
            doc_text = name
        if doc_text not in found_documents:
            found_documents.append(doc_text)

    # 3. Товарно-транспортная накладная
    ttn_matches = re.finditer(
        r'([Тт]оварно-транспортн[а-яё]* накладн[а-яё]*)\s*'
        r'(?:№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})|'
        r'от\s*(\d{2}\.\d{2}\.\d{4}))',
        text, re.IGNORECASE
    )
    for match in ttn_matches:
        name = match.group(1)
        if match.group(2) and match.group(3):  # есть номер и дата
            doc_text = f"{name} № {match.group(2)} от {match.group(3)}"
        elif match.group(4):  # только дата
            doc_text = f"{name} от {match.group(4)}"
        else:
            doc_text = name
        if doc_text not in found_documents:
            found_documents.append(doc_text)

    # 4. Товарная накладная
    tovarnaya_matches = re.finditer(
        r'([Тт]оварн[а-яё]* накладн[а-яё]*)\s*'
        r'(?:№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})|'
        r'от\s*(\d{2}\.\d{2}\.\d{4}))',
        text, re.IGNORECASE
    )
    for match in tovarnaya_matches:
        name = match.group(1)
        if match.group(2) and match.group(3):  # есть номер и дата
            doc_text = f"{name} № {match.group(2)} от {match.group(3)}"
        elif match.group(4):  # только дата
            doc_text = f"{name} от {match.group(4)}"
        else:
            doc_text = name
        if doc_text not in found_documents:
            found_documents.append(doc_text)

    # 5. Счет-фактура
    sf_matches = re.finditer(
        r'([Сс]чет-фактур[а-яё]*)\s*'
        r'(?:№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})|'
        r'от\s*(\d{2}\.\d{2}\.\d{4}))',
        text, re.IGNORECASE
    )
    for match in sf_matches:
        name = match.group(1)
        if match.group(2) and match.group(3):  # есть номер и дата
            doc_text = f"{name} № {match.group(2)} от {match.group(3)}"
        elif match.group(4):  # только дата
            doc_text = f"{name} от {match.group(4)}"
        else:
            doc_text = name
        if doc_text not in found_documents:
            found_documents.append(doc_text)

    # Исключаем любые фразы вида 'Грузосопроводительными документами', 'Грузосопроводительный документ' и т.п.
    found_documents = [d for d in found_documents if not re.match(
        r'Грузосопроводительн', d, re.IGNORECASE)]

    if found_documents:
        return '; '.join(found_documents)
    else:
        return "Не указано"


def parse_claim_data(docx_path: str) -> Dict[str, Any]:
    """
    Извлекает данные иска из .docx:
     истец, ответчик, суммы, договоры, счета, УПД и др.
    """
    doc = Document(docx_path)
    text = "\n".join(p.text for p in doc.paragraphs)

    plaintiff_match = re.search(
        r"((?:Обществ[оа] с ограниченной ответственностью|Индивидуальный предприниматель|Закрытое акционерное общество|Публичное акционерное общество|Открытое акционерное общество|Акционерное общество|АО|ООО|ИП|ЗАО|ПАО)[^«]*«.+?»)\s+ИНН\s+(\d+)\s+КПП\s+(\d+)?\s+ОГРН\s+(\d+)?\s+(.+?)\s*\n",
        text, re.DOTALL
    )
    plaintiff = {
        'name': (
            plaintiff_match.group(1).strip(
            ) if plaintiff_match else "Не указано"
        ),
        'inn': (
            plaintiff_match.group(2).strip(
            ) if plaintiff_match else "Не указано"
        ),
        'kpp': (
            plaintiff_match.group(3).strip(
            ) if plaintiff_match else "Не указано"
        ),
        'ogrn': (
            plaintiff_match.group(4).strip(
            ) if plaintiff_match else "Не указано"
        ),
        'address': (
            plaintiff_match.group(5).strip(
            ) if plaintiff_match else "Не указано"
        ),
    }
    # Сокращаем длинные формы до аббревиатуры
    plaintiff['name'] = re.sub(
        r"Обществ[оа] с ограниченной ответственностью", "ООО", plaintiff['name'])
    plaintiff['name'] = re.sub(
        r"Индивидуальный предприниматель", "ИП", plaintiff['name'])
    plaintiff['name'] = re.sub(
        r"Закрытое акционерное общество", "ЗАО", plaintiff['name'])
    plaintiff['name'] = re.sub(
        r"Публичное акционерное общество", "ПАО", plaintiff['name'])
    plaintiff['name'] = re.sub(
        r"Открытое акционерное общество", "ОАО", plaintiff['name'])
    plaintiff['name'] = re.sub(
        r"Акционерное общество", "АО", plaintiff['name'])

    defendant_match = re.search(
        r"(Обществ[оа] с ограниченной ответственностью|Индивидуальному предпринимателю|Закрытому акционерному обществу|Публичному акционерному обществу|Открытому акционерному обществу|Акционерному обществу|АО|ООО|ИП|ЗАО|ПАО)\s+«(.+?)»\s+"
        r"ИНН\s+(\d+)\s+КПП\s+(\d+)\s+ОГРН\s+(\d+)\s+(.+?)\s*\n",
        text, re.DOTALL
    )
    defendant = {
        'name': (
            (defendant_match.group(1).strip() + ' «' +
             defendant_match.group(2).strip() + '»') if defendant_match else "Не указано"
        ),
        'inn': (
            defendant_match.group(3).strip(
            ) if defendant_match else "Не указано"
        ),
        'kpp': (
            defendant_match.group(4).strip(
            ) if defendant_match else "Не указано"
        ),
        'ogrn': (
            defendant_match.group(5).strip(
            ) if defendant_match else "Не указано"
        ),
        'address': (
            defendant_match.group(6).strip(
            ) if defendant_match else "Не указано"
        ),
    }
    # Сокращаем длинные формы до аббревиатуры для defendant
    for long, short in [
        (r"Обществ[оа] с ограниченной ответственностью", "ООО"),
        (r"Индивидуальный предприниматель", "ИП"),
        (r"Индивидуальному предпринимателю", "ИП"),
        (r"Закрытое акционерное общество", "ЗАО"),
        (r"Закрытому акционерному обществу", "ЗАО"),
        (r"Публичное акционерное общество", "ПАО"),
        (r"Публичному акционерному обществу", "ПАО"),
        (r"Открытое акционерное общество", "ОАО"),
        (r"Открытому акционерному обществу", "ОАО"),
        (r"Акционерное общество", "АО"),
        (r"Акционерному обществу", "АО"),
    ]:
        defendant['name'] = re.sub(long, short, defendant['name'])
    debt_match = re.search(
        r"Сумма\s*основного\s*долга\s*:?\s*([\d\s,.]+)\s*р?\.?",
        text,
        re.IGNORECASE
    )
    if debt_match:
        try:
            debt = float(
                debt_match.group(1).replace(' ', '').replace(',', '.')
            )
        except ValueError:
            debt = 0.0
    else:
        debt = 0.0
        try:
            if doc.tables:
                table = doc.tables[0]
                for row in table.rows:
                    cells = row.cells
                    if len(cells) >= 1:
                        cell_text = cells[0].text.strip()
                        if "Сумма основного долга:" in cell_text:
                            debt_match = re.search(
                                r'(\d[\d\s,.]*)\s*р?\.?', cell_text)
                            if debt_match:
                                debt_text = debt_match.group(1).replace(
                                    ' ', '').replace(',', '.')
                                debt = float(debt_text)
                                break
        except Exception as e:
            logging.warning(f"Ошибка при поиске суммы долга в таблице: {e}")
            debt = 0.0
    legal_fees_match = re.search(
        r"юридические\s+услуги.*?([\d\s,.]+)\s*рубл", text, re.IGNORECASE)
    if legal_fees_match:
        try:
            legal_fees = float(legal_fees_match.group(
                1).replace(' ', '').replace(',', '.'))
        except ValueError:
            legal_fees = 0.0
    else:
        legal_fees = 0.0
    contracts_match = re.findall(
        r"(?:договор[а-я\- ]*|заявка на перевозку груза)[^\n\r№]*"
        r"№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})",
        text, re.IGNORECASE)
    contracts = [f"№ {num} от {date}" for num, date in contracts_match]
    # --- Парсинг счетов на оплату ---
    invoices = []
    for paragraph in text.split('\n'):
        for match in re.finditer(r'Счет(?:ом)? на оплату([^;\n]*)', paragraph, re.IGNORECASE):
            group = match.group(1)
            pairs = re.findall(
                r'№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})', group)
            invoices.extend([f"№ {num} от {date}" for num, date in pairs])
    # --- Парсинг УПД ---
    upds = []
    for paragraph in text.split('\n'):
        for match in re.finditer(r'УПД([^;\n]*)', paragraph, re.IGNORECASE):
            group = match.group(1)
            pairs = re.findall(
                r'№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})', group)
            upds.extend([f"№ {num} от {date}" for num, date in pairs])
    claim_date_match = re.search(
        r"(\d{2}\.\d{2}\.\d{4})\s+Истцом.*претензия", text)
    claim_date = claim_date_match.group(
        1).strip() if claim_date_match else "Не указано"
    claim_number_match = re.search(r"трек\s*номером\s*(\d+)", text)
    claim_number = (
        claim_number_match.group(1).strip()
        if claim_number_match else "66407402018576"
    )
    # --- Новый парсинг подписанта ---
    signatory_match = re.search(
        r"_{3,}\s*/([^/\n]+)", text)
    signatory = signatory_match.group(
        1).strip() if signatory_match else "Не указано"
    # --- Новый парсинг почтовых уведомлений ---
    postal_numbers = []
    postal_dates = []
    for paragraph in text.split('\n'):
        if re.search(r'почтов[а-яё ]*уведомлени[еия]?', paragraph, re.IGNORECASE):
            matches = re.findall(
                r'№\s*(\d+)[^\d]{0,40}?об отправке и получении[^\d]{0,40}?(\d{2}\.\d{2}\.\d{4})',
                paragraph,
                re.IGNORECASE
            )
            for num, date in matches:
                if (num, date) not in zip(postal_numbers, postal_dates):
                    postal_numbers.append(num)
                    postal_dates.append(date)
    # --- Парсинг договоров-заявок целиком ---
    contract_applications = parse_contract_applications(text)

    # --- Парсинг грузосопроводительных документов ---
    cargo_documents = parse_cargo_documents(text)

    # --- Парсинг счетов на оплату целиком ---
    invoice_blocks = parse_invoice_blocks(text)

    # --- Парсинг УПД целиком ---
    upd_blocks = parse_upd_blocks(text)

    # --- конец нового парсинга ---
    payment_days_match = re.search(
        r'оплата производится в течение\s+(\d+)\s+банковск', text, re.IGNORECASE)
    payment_days = payment_days_match.group(
        1) if payment_days_match else "Не указано"

    return {
        'plaintiff': plaintiff,
        'defendant': defendant,
        'debt': debt,
        'legal_fees': legal_fees,
        'contracts': contracts,
        'invoices': invoices,
        'upds': upds,
        'claim_date': claim_date,
        'claim_number': claim_number,
        'signatory': signatory,
        'postal_numbers': postal_numbers,
        'postal_dates': postal_dates,
        'contract_applications': contract_applications,
        'cargo_documents': cargo_documents,
        'invoice_blocks': invoice_blocks,
        'upd_blocks': upd_blocks,
        'payment_days': payment_days,
    }


def parse_invoice_blocks(text: str) -> str:
    """
    Парсит целые фразы, связанные со счетами на оплату (все формы).
    Возвращает строку для подстановки в шаблон.
    """
    patterns = [
        # Счет на оплату (ед. и мн. число, с номерами и датами)
        (r'(?:счет[а-яё]* на оплату|счета на оплату|счет[а-яё]*|счета)[^;\n\.]*?'
         r'(?:№\s*\d+[^;\n\.]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n\.]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n\.]*?г?\.?|[^;\n\.]*?г?\.?)[^;\n\.]*'),
    ]
    found_blocks = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            block = match.group(0).strip()
            block = re.sub(r'\s+', ' ', block)
            if block not in found_blocks:
                found_blocks.append(block)
    if found_blocks:
        return '; '.join(found_blocks)
    else:
        return "Не указано"


def parse_upd_blocks(text: str) -> str:
    """
    Парсит целые фразы, связанные с УПД (универсальный передаточный документ).
    Возвращает строку для подстановки в шаблон.
    """
    patterns = [
        # УПД и универсальный передаточный документ (ед. и мн. число)
        (r'(?:УПД|универсальн[а-яё ]*передаточн[а-яё ]*документ[а-яё]*)[^;\n\.]*?'
         r'(?:№\s*\d+[^;\n\.]*?от\s*\d{2}\.\d{2}\.\d{4}[^;\n\.]*?г?\.?|'
         r'от\s*\d{2}\.\d{2}\.\d{4}[^;\n\.]*?г?\.?|[^;\n\.]*?г?\.?)[^;\n\.]*'),
    ]
    found_blocks = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            block = match.group(0).strip()
            block = re.sub(r'\s+', ' ', block)
            if block not in found_blocks:
                found_blocks.append(block)
    if found_blocks:
        return '; '.join(found_blocks)
    else:
        return "Не указано"
