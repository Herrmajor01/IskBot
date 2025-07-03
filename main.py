import logging
import tempfile
import os
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from calc_395 import get_key_rates_from_395gk, calculate_full_395, write_calc_to_docx

TOKEN = ''

logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Отправь .docx файл с таблицей расчета процентов по ст. 395 ГК РФ — я верну актуальный расчет до текущей даты в формате Word.'
    )


async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.lower().endswith('.docx'):
            await update.message.reply_text('Пожалуйста, отправь файл Word (.docx).')
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, doc.file_name)
            telegram_file = await doc.get_file()
            await telegram_file.download_to_drive(file_path)

            key_rates = get_key_rates_from_395gk()
            total, details = calculate_full_395(file_path, key_rates=key_rates)

            result_docx = os.path.join(tmpdir, 'Расчет_процентов_395ГК.docx')
            write_calc_to_docx(result_docx, total, details)

            with open(result_docx, 'rb') as f:
                await update.message.reply_document(
                    InputFile(f, filename="Расчет_процентов_395ГК.docx"),
                    caption="Актуальный расчет процентов по ст. 395 ГК РФ"
                )
    except ValueError as ve:
        logging.error(f"Ошибка в данных файла: {ve}")
        await update.message.reply_text(f'Ошибка в данных файла: {ve}. Проверьте таблицу в файле.')
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Ошибка: {e}\n{tb}")
        await update.message.reply_text(f'Ошибка обработки: {e}. Проверьте формат таблицы и данные в файле.')


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.run_polling()


if __name__ == '__main__':
    main()
