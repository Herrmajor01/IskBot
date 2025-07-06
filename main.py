"""
Основной модуль Telegram-бота для
автоматизации расчёта процентов по ст. 395 ГК РФ.
"""

import logging
import os
import tempfile
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
from parsing import parse_claim_data
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
    courts = {
        "Москва": (
            "Арбитражный суд города Москвы",
            "115191, г. Москва, ул. Большая Тульская, д. 17"
        ),
        "Челябинск": (
            "Арбитражный суд Челябинской области",
            "454091, г. Челябинск, ул. Воровского, д. 2"
        ),
        "Волгоград": (
            "Арбитражный суд Волгоградской области",
            "400005, г. Волгоград, ул. 7-й Гвардейской, д. 2"
        ),
        "Петрозаводск": (
            "Арбитражный суд Республики Карелия",
            "185035, г. Петрозаводск, пр. Ленина, д. 21"
        ),
    }
    for city, (court_name, court_address) in courts.items():
        if city.lower() in defendant_address.lower():
            return court_name, court_address
    return "Арбитражный суд по месту нахождения ответчика", "Адрес суда не определен"


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
                cells[1].text = row['date_from']
                cells[2].text = row['date_to']
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
            "досудебная претензия с требованием погасить образовавшуюся задолженность. "
            f"Претензия была отправлена почтовым отправлением с трек-номером {claim_number}. "
            f"{receive_date} Ответчик получил данное отправление."
        )
    elif claim_status == 'claim_not_received':
        return (
            f"{claim_date}. Истцом в адрес Ответчика была направлена "
            "досудебная претензия с требованием погасить образовавшуюся задолженность. "
            f"Претензия была отправлена почтовым отправлением с трек-номером {claim_number}. "
            "В соответствии с п. 1 ст. 165.1 ГК РФ Истец считает, что претензия была получена Ответчиком. "
            "У Ответчика имелось достаточно времени для получения почтового отправления. "
            "Таким образом, Истцом был соблюден обязательный претензионный порядок (досудебный порядок урегулирования споров) "
            "в строгом соответствии с действующим законодательством РФ."
        )
    else:
        return "Не указано г. Истцом в адрес Ответчика была направлена досудебная претензия."


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


def replace_placeholders_robust(doc, replacements):
    """
    Заменяет плейсхолдеры в документе, применяя жирное начертание только для указанных полей.
    """
    bold_placeholders = [
        '{court_name}', '{plaintiff_name}', '{defendant_name}',
        '{total_claim}', '{duty}'
    ]

    # Проверяем, является ли истец или ответчик ИП
    is_plaintiff_ip = 'ИП' in str(replacements.get(
        '{plaintiff_name}', '')) or 'Индивидуальный предприниматель' in str(replacements.get('{plaintiff_name}', ''))
    is_defendant_ip = 'ИП' in str(replacements.get(
        '{defendant_name}', '')) or 'Индивидуальный предприниматель' in str(replacements.get('{defendant_name}', ''))

    for paragraph in doc.paragraphs:
        full_text = ''.join(run.text for run in paragraph.runs)

        # Удаляем строки с КПП для ИП
        if is_plaintiff_ip and 'КПП' in full_text and '{plaintiff_kpp}' in full_text:
            # Удаляем только строку с КПП истца, а не весь параграф
            lines = full_text.split('\n')
            filtered_lines = []
            for line in lines:
                if not ('КПП' in line and '{plaintiff_kpp}' in line):
                    filtered_lines.append(line)
            full_text = '\n'.join(filtered_lines)

        if is_defendant_ip and 'КПП' in full_text and '{defendant_kpp}' in full_text:
            # Удаляем только строку с КПП ответчика, а не весь параграф
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
            # Проверяем, содержит ли параграф один из bold_placeholders
            is_bold = any(
                placeholder in full_text for placeholder in bold_placeholders)
            run = paragraph.add_run(full_text)
            run.bold = is_bold
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    full_text = ''.join(run.text for run in paragraph.runs)
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
                        is_bold = any(
                            placeholder in full_text for placeholder in bold_placeholders)
                        run = paragraph.add_run(full_text)
                        run.bold = is_bold
                        run.font.name = 'Times New Roman'
                        run.font.size = Pt(12)


def replace_attachments_with_paragraphs(doc, attachments):
    """
    Заменяет плейсхолдер {attachments} списком приложений, каждое в отдельном параграфе
    без нумерации, сохраняя статические приложения из шаблона.
    """
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

        # Добавляем динамические приложения без нумерации
        for att in attachments:
            # Удаляем точку с запятой и лишние пробелы
            att_clean = att.rstrip(';').strip()
            new_par = doc.add_paragraph()
            run = new_par.add_run(f"{att_clean};")
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            run.bold = False
            new_par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            parent.insert(idx, new_par._element)
            idx += 1
            logging.info(f"Добавлено динамическое приложение: {att_clean}")


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
        tempfile.gettempdir(), 'Исковое_заявление.docx'
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
    claim_data = parse_claim_data(file_path)

    # Извлекаем текст из документа для нового парсера
    doc = Document(file_path)
    text = "\n".join(p.text for p in doc.paragraphs)
    document_blocks = parse_documents_with_sliding_window(text)

    claim_data['claim_number'] = context.user_data.get('claim_number', '')
    claim_data['claim_date'] = context.user_data.get('claim_date', '')
    key_rates = get_key_rates_from_395gk()
    interest_data = calculate_full_395(
        file_path, key_rates=key_rates
    )
    total_claim = (
        claim_data['debt'] + interest_data['total_interest']
    )
    duty_data = calculate_duty(total_claim)
    if 'error' in duty_data:
        await update.message.reply_text(str(duty_data['error']))
        return
    for key in ['claim_status', 'claim_date', 'claim_number', 'postal_receive_date']:
        if key not in context.user_data:
            context.user_data[key] = ''

    # Используем данные из нового парсера для истца и ответчика
    plaintiff_name = document_blocks.get(
        'plaintiff_name', claim_data['plaintiff']['name'].replace('\n', ' ').strip())
    defendant_name = document_blocks.get(
        'defendant_name', claim_data['defendant']['name'].replace('\n', ' ').strip())
    contract_parties = document_blocks.get('contract_parties', '')
    contract_parties_short = document_blocks.get('contract_parties_short', '')

    # Проверяем, является ли истец ИП
    is_plaintiff_ip = 'ИП' in plaintiff_name or 'Индивидуальный предприниматель' in plaintiff_name
    is_defendant_ip = 'ИП' in defendant_name or 'Индивидуальный предприниматель' in defendant_name

    # Форматируем имена для использования в тексте (короткие названия)
    plaintiff_name_short = plaintiff_name
    defendant_name_short = defendant_name

    if 'Индивидуальный предприниматель' in plaintiff_name:
        # Заменяем "Индивидуальный предприниматель Иванов И.И." на "ИП Иванов И.И."
        plaintiff_name_short = plaintiff_name.replace(
            'Индивидуальный предприниматель', 'ИП')

    if 'Общество с ограниченной ответственностью' in defendant_name:
        # Заменяем "Общество с ограниченной ответственностью" на "ООО"
        defendant_name_short = defendant_name.replace(
            'Общество с ограниченной ответственностью', 'ООО')

    replacements = {
        '{claim_paragraph}': generate_claim_paragraph(
            context.user_data
        ),
        '{postal_block}': document_blocks.get('postal_block', ''),
        '{postal_numbers_all}': (
            ', '.join(claim_data.get('postal_numbers', []))
            or 'Не указано'
        ),
        '{postal_dates_all}': (
            ', '.join(claim_data.get('postal_dates', []))
            or 'Не указано'
        ),
        '{court_name}': get_court_by_address(claim_data['defendant']['address'])[0],
        '{court_address}': get_court_by_address(claim_data['defendant']['address'])[1],
        '{plaintiff_name}': plaintiff_name,
        '{plaintiff_name_short}': plaintiff_name_short,
        '{plaintiff_inn}': claim_data['plaintiff']['inn'],
        '{plaintiff_kpp}': '' if is_plaintiff_ip else claim_data['plaintiff']['kpp'],
        '{plaintiff_ogrn}': claim_data['plaintiff']['ogrn'],
        '{plaintiff_address}': claim_data['plaintiff']['address'].replace('\n', ' ').strip(),
        '{defendant_name}': defendant_name,
        '{defendant_name_short}': defendant_name_short,
        '{defendant_inn}': claim_data['defendant']['inn'],
        '{defendant_kpp}': '' if is_defendant_ip else claim_data['defendant']['kpp'],
        '{defendant_ogrn}': claim_data['defendant']['ogrn'],
        '{defendant_address}': claim_data['defendant']['address'].replace('\n', ' ').strip(),
        '{contract_parties}': contract_parties,
        '{contract_parties_short}': contract_parties_short,
        '{total_claim}': f"{total_claim:,.2f}".replace(',', ' '),
        '{duty}': f"{duty_data['duty']:,.0f}".replace(',', ' '),
        '{debt}': f"{claim_data['debt']:,.2f}".replace(',', ' '),
        '{contracts}': document_blocks.get('contracts', ''),
        '{contract_applications}': document_blocks.get('contract_applications', ''),
        '{cargo_docs}': document_blocks.get('cargo_docs', ''),
        '{invoice_blocks}': document_blocks.get('invoice_blocks', ''),
        '{upd_blocks}': document_blocks.get('upd_blocks', ''),
        '{invoices}': ", ".join(claim_data['invoices'])
        if claim_data['invoices'] else 'Не указано',
        '{upds}': ", ".join(claim_data['upds'])
        if claim_data['upds'] else 'Не указано',
        '{claim_date}': context.user_data.get('claim_date', ''),
        '{claim_number}': context.user_data.get('claim_number', ''),
        '{total_interest}': f"{interest_data['total_interest']:,.2f}".replace(',', ' '),
        '{legal_fees}': f"{claim_data['legal_fees']:,.2f}".replace(',', ' '),
        '{total_expenses}': (
            f"{duty_data['duty'] + claim_data['legal_fees']:,.0f}".replace(
                ',', ' ')
        ),
        '{calculation_date}': datetime.today().strftime('%d.%m.%Y'),
        '{signatory}': claim_data['signatory'].replace('\n', ' ').strip(),
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


def main() -> None:
    """Запускает Telegram бота."""
    logging.info("Starting bot...")
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
