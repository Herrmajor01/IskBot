import logging
import tempfile
import os
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from calc_395 import get_key_rates_from_395gk, parse_claim_data, calculate_full_395
from cal import calculate_duty
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from dotenv import load_dotenv
from datetime import datetime

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


def get_court_by_address(defendant_address):
    courts = {
        "Москва": ("Арбитражный суд города Москвы", "115191, г. Москва, ул. Большая Тульская, д. 17"),
        "Челябинск": ("Арбитражный суд Челябинской области", "454091, г. Челябинск, ул. Воровского, д. 2"),
        "Волгоград": ("Арбитражный суд Волгоградской области", "400005, г. Волгоград, ул. 7-й Гвардейской, д. 2")
    }
    for city, (court_name, court_address) in courts.items():
        if city.lower() in defendant_address.lower():
            return court_name, court_address
    return "Арбитражный суд", "Адрес суда не определен"


def create_isk_document(data, interest_data, duty_data):
    doc = Document('template.docx')

    # Подготовка данных для подстановки
    total_claim = data['debt'] + \
        interest_data['total_interest'] + data['legal_fees']
    total_expenses = duty_data['duty'] + data['legal_fees']
    court_name, court_address = get_court_by_address(
        data['defendant']['address'])

    # Формирование таблицы процентов
    interest_table = ""
    for detail in interest_data['details']:
        interest_table += (
            f"{detail['sum']:,.2f} р. | {detail['date_from']} | {detail['date_to']} | {detail['days']} | "
            f"{detail['rate']} | {detail['formula']} | {detail['interest']:,.2f} р.\n"
        ).replace(',', ' ')

    # Обработка списков договоров, счетов и УПД
    contracts_str = ", ".join(
        data['contracts']) if data['contracts'] else "Не указано"
    invoices_str = ", ".join(
        data['invoices']) if data['invoices'] else "Не указано"
    upds_str = ", ".join(data['upds']) if data['upds'] else "Не указано"

    # Словарь замен
    replacements = {
        '{court_name}': court_name,
        '{court_address}': court_address,
        '{plaintiff_name}': data['plaintiff']['name'],
        '{plaintiff_inn}': data['plaintiff']['inn'],
        '{plaintiff_kpp}': data['plaintiff']['kpp'],
        '{plaintiff_ogrn}': data['plaintiff']['ogrn'],
        '{plaintiff_address}': data['plaintiff']['address'],
        '{defendant_name}': data['defendant']['name'],
        '{defendant_inn}': data['defendant']['inn'],
        '{defendant_kpp}': data['defendant']['kpp'],
        '{defendant_ogrn}': data['defendant']['ogrn'],
        '{defendant_address}': data['defendant']['address'],
        '{total_claim}': f"{total_claim:,.2f}".replace(',', ' '),
        '{duty}': f"{duty_data['duty']:,.0f}".replace(',', ' '),
        '{debt}': f"{data['debt']:,.2f}".replace(',', ' '),
        '{contracts}': contracts_str,
        '{invoices}': invoices_str,
        '{upds}': upds_str,
        '{claim_date}': data['claim_date'],
        '{claim_number}': data['claim_number'],
        '{interest_table}': interest_table.strip(),
        '{total_interest}': f"{interest_data['total_interest']:,.2f}".replace(',', ' '),
        '{legal_fees}': f"{data['legal_fees']:,.2f}".replace(',', ' '),
        '{total_expenses}': f"{total_expenses:,.0f}".replace(',', ' '),
        '{calculation_date}': datetime.today().strftime('%d.%m.%Y'),
        '{signatory}': data['signatory']
    }

    # Замена заполнителей в параграфах
    for paragraph in doc.paragraphs:
        for key, value in replacements.items():
            if key in paragraph.text:
                paragraph.text = paragraph.text.replace(key, str(value))
                paragraph.style.font.name = 'Times New Roman'
                paragraph.style.font.size = Pt(14)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                if 'Исковое заявление' in paragraph.text or 'ПРОШУ' in paragraph.text:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    paragraph.style.font.bold = True
                if 'Генеральный директор' in paragraph.text:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Замена заполнителей в таблице
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, value in replacements.items():
                    if key in cell.text:
                        cell.text = cell.text.replace(key, str(value))
                        for paragraph in cell.paragraphs:
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            paragraph.style.font.name = 'Times New Roman'
                            paragraph.style.font.size = Pt(14)

    result_docx = os.path.join(tempfile.gettempdir(), 'Исковое_заявление.docx')
    doc.save(result_docx)
    return result_docx


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(
        f"Received /start command from user {update.effective_user.id}")
    await update.message.reply_text(
        'Отправь .docx файл с досудебным требованием — я верну исковое заявление в формате Word.'
    )


async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logging.info(f"Received document from user {update.effective_user.id}")
        doc = update.message.document
        if not doc.file_name.lower().endswith('.docx'):
            logging.warning("Invalid file format")
            await update.message.reply_text('Пожалуйста, отправь файл Word (.docx).')
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, doc.file_name)
            telegram_file = await doc.get_file()
            await telegram_file.download_to_drive(file_path)
            logging.info(f"Downloaded file: {file_path}")

            # Извлечение данных
            claim_data = parse_claim_data(file_path)
            logging.info("Extracted claim data")

            # Расчет процентов
            key_rates = get_key_rates_from_395gk()
            interest_data = calculate_full_395(file_path, key_rates=key_rates)
            logging.info(
                f"Calculated interest: {interest_data['total_interest']}")

            # Расчет госпошлины
            total_claim = claim_data['debt'] + \
                interest_data['total_interest'] + claim_data['legal_fees']
            duty_data = calculate_duty(total_claim)
            if 'error' in duty_data:
                logging.error(f"Duty calculation error: {duty_data['error']}")
                await update.message.reply_text(duty_data['error'])
                return
            logging.info(f"Calculated duty: {duty_data['duty']}")

            # Создание искового
            result_docx = create_isk_document(
                claim_data, interest_data, duty_data)
            logging.info(f"Saved isk to {result_docx}")

            with open(result_docx, 'rb') as f:
                await update.message.reply_document(
                    InputFile(f, filename="Исковое_заявление.docx"),
                    caption="Исковое заявление по ст. 395 ГК РФ"
                )
                logging.info("Sent isk document to user")
    except ValueError as ve:
        logging.error(f"Ошибка в данных файла: {ve}")
        await update.message.reply_text(f'Ошибка в данных файла: {ve}. Проверьте таблицу в файле.')
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Ошибка: {e}\n{tb}")
        await update.message.reply_text(f'Ошибка обработки: {e}. Проверьте формат файла.')


def main():
    logging.info("Starting bot...")
    app = Application.builder().token(TOKEN).build()
    logging.info("Bot initialized")
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    logging.info("Handlers added")
    app.run_polling()
    logging.info("Bot is polling")


if __name__ == '__main__':
    main()
