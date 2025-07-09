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
from typing import Tuple

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
from sliding_window_parser import parse_documents_with_sliding_window

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан в .env")

logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.info("Bot script started")

ASK_CLAIM_STATUS, ASK_TRACK, ASK_RECEIVE_DATE, ASK_SEND_DATE = range(4)


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


def insert_interest_table(doc, details):
    """
    Вставляет таблицу процентов в документ
    Word вместо маркера {interest_table}.
    """
    headers = [
        'Сумма', 'Дата начала', 'Дата окончания', 'Дни',
        'Ставка', 'Формула', 'Проценты'
    ]
    for i, paragraph in enumerate(doc.paragraphs):
        if '{interest_table}' in paragraph.text:
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
                cells[0].text = f"{row['sum']:,.2f}".replace(',', ' ')
                cells[1].text = row['date_from'] + ' г.'
                cells[2].text = row['date_to'] + ' г.'
                cells[3].text = str(row['days'])
                cells[4].text = str(row['rate'])
                cells[5].text = row['formula']
                cells[6].text = f"{row['interest']:,.2f}".replace(',', ' ')
                for cell in cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            run.font.size = Pt(10)
                            run.font.name = 'Times New Roman'
                            run._element.rPr.rFonts.set(
                                qn('w:eastAsia'), 'Times New Roman'
                            )
            p = paragraph._element
            p.addnext(table._element)
            paragraph.text = paragraph.text.replace('{interest_table}', '')
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


def generate_debt_text(claim_data: dict) -> str:
    """
    Генерирует текст о стоимости услуг.

    Args:
        claim_data: Данные о требованиях

    Returns:
        Текст о стоимости услуг
    """
    debt_amount = claim_data.get('debt', '0')
    return f"Стоимость услуг по Договору составила {debt_amount} рублей."


def generate_payment_terms(claim_data: dict) -> str:
    """
    Генерирует текст о порядке оплаты по приоритету:
    1. Если в payment_terms есть и дни, и дата — возвращает payment_terms
    2. Если есть только дата — возвращает строку с датой
    3. Если есть только дни — возвращает строку с днями
    4. Если ничего нет — стандартный текст
    """
    payment_days = claim_data.get('payment_days')
    payment_due_date = claim_data.get('payment_due_date')
    payment_terms = claim_data.get('payment_terms', '')

    # Если в тексте требования явно есть оба — используем их как есть
    if payment_terms and payment_days and payment_due_date:
        return payment_terms
    # Если есть только дата
    if payment_due_date and not payment_days:
        return f"Срок оплаты не позднее {payment_due_date} г."
    # Если есть только дни
    if payment_days and not payment_due_date:
        return (
            f"Оплата производится в течение {payment_days} банковских дней "
            "безналичным расчетом после получения оригиналов документов."
        )
    # Если ничего нет — стандарт
    return (
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

    for paragraph in doc.paragraphs:
        full_text = ''.join(run.text for run in paragraph.runs)

        # Удаляем строки с КПП для ИП
        if is_plaintiff_ip and 'КПП' in full_text and '{plaintiff_kpp}' in full_text:
            lines = full_text.split('\n')
            filtered_lines = []
            for line in lines:
                if not ('КПП' in line and '{plaintiff_kpp}' in line):
                    filtered_lines.append(line)
            full_text = '\n'.join(filtered_lines)

        if is_defendant_ip and 'КПП' in full_text and '{defendant_kpp}' in full_text:
            lines = full_text.split('\n')
            filtered_lines = []
            for line in lines:
                if not ('КПП' in line and '{defendant_kpp}' in line):
                    filtered_lines.append(line)
            full_text = '\n'.join(filtered_lines)

        replaced = False
        for key, value in replacements.items():
            if key in full_text:
                replaced = True
                clean_value = str(value).replace(
                    '\n', ' ').replace('\r', ' ').strip()
                full_text = full_text.replace(key, clean_value)
        if replaced:
            paragraph.clear()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
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
                        run = paragraph.add_run(parts[0].strip())
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


def replace_attachments_with_paragraphs(doc, attachments):
    """
    Заменяет плейсхолдер {attachments} списком приложений с нумерацией и добавляет заголовок 'Приложения:'.
    Ожидается, что в шаблоне только {attachments} на отдельной строке.
    """
    import logging
    idx = None
    for i, paragraph in enumerate(doc.paragraphs):
        if '{attachments}' in paragraph.text:
            idx = i
            break

    if idx is not None:
        # Удаляем параграф с плейсхолдером
        p = doc.paragraphs[idx]._element
        parent = p.getparent()
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

        # Ключевые слова для жирного
        bold_keywords = [
            "Заявка", "Счет", "УПД", "Акт", "Комплект сопроводительных документов"
        ]

        # Динамические приложения с нумерацией
        attachment_number = 1
        for att in attachments:
            att_clean = fix_number_spacing(att.rstrip(';').strip())
            new_par = doc.add_paragraph()
            # Проверяем, нужно ли выделять жирным
            is_bold = any(att_clean.startswith(word) for word in bold_keywords)
            run = new_par.add_run(f"{attachment_number}. {att_clean};")
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            run.bold = is_bold
            new_par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            parent.insert(idx, new_par._element)
            idx += 1
            attachment_number += 1

        # Статические приложения
        static_attachments = [
            "Документы, подтверждающие отправку искового заявления Ответчику – копия",
            "Документы, подтверждающие оплату государственной пошлины – копия",
            "Выписка из ЕГРЮЛ на Истца – электронная версия",
            "Выписка из ЕГРЮЛ на Ответчика – электронная версия"
        ]
        for static_att in static_attachments:
            static_att = fix_number_spacing(static_att)
            new_par = doc.add_paragraph()
            run = new_par.add_run(f"{attachment_number}. {static_att};")
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            run.bold = False
            new_par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            parent.insert(idx, new_par._element)
            idx += 1
            attachment_number += 1
    else:
        logging.warning("Плейсхолдер {attachments} не найден в документе")


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
    replacements: dict
) -> str:
    doc = Document('template.docx')
    replace_placeholders_robust(doc, replacements)
    replace_attachments_with_paragraphs(doc, data.get('attachments', []))
    insert_interest_table(doc, interest_data['details'])
    result_docx = os.path.join(
        os.path.dirname(__file__), 'Исковое_заявление.docx'
    )
    doc.save(result_docx)
    return result_docx


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
    context.user_data['claim_number'] = update.message.text.strip()
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

    # Используем только новый парсер
    claim_data = parse_documents_with_sliding_window(text)

    claim_data['claim_number'] = context.user_data.get('claim_number', '')
    claim_data['claim_date'] = context.user_data.get('claim_date', '')
    key_rates = get_key_rates_from_395gk()
    interest_data = calculate_full_395(
        file_path, key_rates=key_rates
    )

    # Получаем сумму долга из нового парсера
    debt_amount = float(claim_data.get(
        'debt', '0').replace(' ', '').replace(',', '.'))
    total_claim = debt_amount + interest_data['total_interest']

    duty_data = calculate_duty(total_claim)
    if 'error' in duty_data:
        await update.message.reply_text(str(duty_data['error']))
        return
    for key in ['claim_status', 'claim_date', 'claim_number', 'postal_receive_date']:
        if key not in context.user_data:
            context.user_data[key] = ''

    # Используем данные из нового парсера для истца и ответчика
    plaintiff_name = claim_data.get('plaintiff_name', 'Не указано')
    defendant_name = claim_data.get('defendant_name', 'Не указано')
    contract_parties = claim_data.get('contract_parties', '')
    contract_parties_short = claim_data.get('contract_parties_short', '')

    # Проверяем, является ли истец ИП
    is_plaintiff_ip = 'ИП' in plaintiff_name or 'Индивидуальный предприниматель' in plaintiff_name
    is_defendant_ip = 'ИП' in defendant_name or 'Индивидуальный предприниматель' in defendant_name

    # Форматируем имена для использования в тексте (короткие названия)
    plaintiff_name_short = format_organization_name_short(plaintiff_name)
    defendant_name_short = format_organization_name_short(defendant_name)

    replacements = {
        '{claim_paragraph}': generate_claim_paragraph(
            context.user_data
        ),
        '{postal_block}': format_document_list(claim_data.get('postal_block', '')),
        '{postal_numbers_all}': (
            ', '.join(claim_data.get('postal_numbers', []))
            or 'Не указано'
        ),
        '{postal_dates_all}': (
            ', '.join(claim_data.get('postal_dates', []))
            or 'Не указано'
        ),
        '{court_name}': get_court_by_address(claim_data.get('defendant_address', 'Не указано'))[0],
        '{court_address}': get_court_by_address(claim_data.get('defendant_address', 'Не указано'))[1],
        '{plaintiff_name}': plaintiff_name,
        '{plaintiff_name_short}': plaintiff_name_short,
        '{plaintiff_name_formatted}': plaintiff_name_short,
        '{plaintiff_inn}': claim_data.get('plaintiff_inn', 'Не указано'),
        '{plaintiff_kpp}': '' if is_plaintiff_ip else claim_data.get('plaintiff_kpp', 'Не указано'),
        '{plaintiff_ogrn}': claim_data.get('plaintiff_ogrn', 'Не указано'),
        '{plaintiff_address}': claim_data.get('plaintiff_address', 'Не указано').replace('\n', ' ').strip(),
        '{defendant_name}': defendant_name,
        '{defendant_name_short}': defendant_name_short,
        '{defendant_inn}': claim_data.get('defendant_inn', 'Не указано'),
        '{defendant_kpp}': '' if is_defendant_ip else claim_data.get('defendant_kpp', 'Не указано'),
        '{defendant_ogrn}': claim_data.get('defendant_ogrn', 'Не указано'),
        '{defendant_address}': claim_data.get('defendant_address', 'Не указано').replace('\n', ' ').strip(),
        '{contract_parties}': contract_parties,
        '{contract_parties_short}': contract_parties_short,
        '{total_claim}': f"{total_claim:,.2f}".replace(',', ' '),
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
        '{claim_date}': context.user_data.get('claim_date', ''),
        '{claim_number}': context.user_data.get('claim_number', ''),
        '{total_interest}': f"{interest_data['total_interest']:,.2f}".replace(',', ' '),
        '{legal_fees}': f"{float(claim_data.get('legal_fees', '0').replace(' ', '')):,.2f}".replace(',', ' '),
        '{total_expenses}': (
            f"{float(str(duty_data['duty'])) + float(claim_data.get('legal_fees', '0').replace(' ', '')):,.0f}".replace(
                ',', ' ')
        ),
        '{calculation_date}': datetime.today().strftime('%d.%m.%Y г.'),
        '{signatory}': claim_data.get('signatory', 'Не указано').replace('\n', ' ').strip(),
        '{signature_block}': claim_data.get('signature_block', 'Не указано'),
        '{postal_numbers}': (
            context.user_data.get('claim_number', '') or 'Не указано'
        ),
        '{postal_receive_date}': (
            context.user_data.get('postal_receive_date', '') or 'Не указано'
        ),
        '{payment_days}': claim_data.get('payment_days', 'Не указано'),
    }
    result_docx = create_isk_document(
        claim_data, interest_data, duty_data, replacements
    )
    with open(result_docx, 'rb') as f:
        await update.message.reply_document(
            InputFile(f, filename="Исковое_заявление.docx"),
            caption="Исковое заявление по ст. 395 ГК РФ"
        )
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logging.warning(
            f"Не удалось удалить файл {file_path}: {e}"
        )


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
        return await ask_claim_status(update, context)
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
        ASK_CLAIM_STATUS: [CallbackQueryHandler(claim_status_chosen)],
        ASK_TRACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_track)],
        ASK_RECEIVE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_receive_date)],
        ASK_SEND_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_send_date)],
    },
    fallbacks=[],
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
    if TOKEN is None:
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
