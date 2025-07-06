"""
Конфигурационный файл для IskBot.
"""

import os
from typing import List, Tuple

# Настройки бота
BOT_CONFIG = {
    'max_file_size_mb': 10,
    'supported_formats': ['.docx'],
    'temp_dir': 'uploads',
    'log_file': 'bot.log',
    'log_level': 'INFO'
}

# Настройки парсинга
PARSING_CONFIG = {
    'max_paragraphs_per_section': 10,
    'date_formats': ['%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d'],
    'number_formats': {
        'decimal_separator': '.',
        'thousands_separator': ' ',
        'currency_symbols': ['руб', 'рублей', '₽', 'р.']
    }
}

# Настройки расчета
CALCULATION_CONFIG = {
    'rates_url': 'https://395gk.ru/svedcb.htm',
    'cache_file': 'key_rates_cache.json',
    'cache_ttl_hours': 24,
    'fallback_rate': 21.0,
    'request_timeout': 10
}

# База судов (можно расширить)
COURTS_DATABASE = {
    'Москва': {
        'name': 'Арбитражный суд города Москвы',
        'address': '115191, г. Москва, ул. Большая Тульская, д. 17'
    },
    'Санкт-Петербург': {
        'name': 'Арбитражный суд города Санкт-Петербурга и Ленинградской области',
        'address': '190000, г. Санкт-Петербург, ул. Марата, д. 72'
    },
    'Челябинск': {
        'name': 'Арбитражный суд Челябинской области',
        'address': '454091, г. Челябинск, ул. Воровского, д. 2'
    },
    'Волгоград': {
        'name': 'Арбитражный суд Волгоградской области',
        'address': '400005, г. Волгоград, ул. 7-й Гвардейской, д. 2'
    },
    'Петрозаводск': {
        'name': 'Арбитражный суд Республики Карелия',
        'address': '185035, г. Петрозаводск, пр. Ленина, д. 21'
    },
    'Екатеринбург': {
        'name': 'Арбитражный суд Свердловской области',
        'address': '620075, г. Екатеринбург, ул. Шарташская, д. 4'
    },
    'Новосибирск': {
        'name': 'Арбитражный суд Новосибирской области',
        'address': '630099, г. Новосибирск, ул. Советская, д. 5'
    },
    'Краснодар': {
        'name': 'Арбитражный суд Краснодарского края',
        'address': '350000, г. Краснодар, ул. Красная, д. 139'
    }
}

# Паттерны для парсинга
PARSING_PATTERNS = {
    'entity': {
        'inn': r'ИНН[:\s]*(\d{10}|\d{12})',
        'kpp': r'КПП[:\s]*(\d{9})',
        'ogrn': r'ОГРН[:\s]*(\d{13}|\d{15})',
        'address': r'адрес[:\s]*(.+?)(?=\n|ИНН|КПП|ОГРН|$)',
    },
    'financial': {
        'debt': [
            r'долг[а]?\s*[:\-]?\s*(\d[\d\s,]*\.?\d*)',
            r'сумм[аы]\s*[:\-]?\s*(\d[\d\s,]*\.?\d*)',
            r'задолженност[ьи]\s*[:\-]?\s*(\d[\d\s,]*\.?\d*)'
        ],
        'contract': r'договор[а]?\s*№?\s*([A-Za-zА-Яа-я0-9\-_/]+)',
        'invoice': r'счет[а]?\s*№?\s*([A-Za-zА-Яа-я0-9\-_/]+)',
        'upd': r'упд\s*№?\s*([A-Za-zА-Яа-я0-9\-_/]+)',
    },
    'dates': {
        'date': r'(\d{2}[\.\/]\d{2}[\.\/]\d{4})',
        'postal': r'№\s*(\d+)[^\d]{0,40}?об отправке и получении[^\d]{0,40}?(\d{2}\.\d{2}\.\d{4})'
    }
}

# Сообщения об ошибках
ERROR_MESSAGES = {
    'file_not_found': 'Файл не найден',
    'invalid_format': 'Поддерживаются только файлы .docx',
    'file_too_large': 'Размер файла превышает {max_size}MB',
    'empty_file': 'Файл пустой',
    'no_content': 'Документ не содержит текста или таблиц',
    'parsing_error': 'Ошибка чтения документа: {error}',
    'validation_error': 'Ошибки в данных: {errors}',
    'network_error': 'Ошибка сети при получении данных',
    'calculation_error': 'Ошибка расчета: {error}'
}

# Сообщения для пользователя
USER_MESSAGES = {
    'welcome': 'Добро пожаловать в IskBot! Загрузите документ .docx для начала работы.',
    'file_uploaded': '✅ Файл "{filename}" успешно загружен и проверен.\nРазмер: {size:.1f} KB\nТеперь ответьте на несколько вопросов для формирования иска.',
    'processing': '⏳ Обрабатываю документ...',
    'success': '✅ Исковое заявление успешно сформировано!',
    'error': '❌ Ошибка: {message}',
    'help': 'Для получения помощи обратитесь к администратору.'
}


def get_court_by_city(city: str) -> Tuple[str, str]:
    """
    Получает информацию о суде по городу.

    Args:
        city: Название города

    Returns:
        Кортеж (название суда, адрес суда)
    """
    city_lower = city.lower()

    for court_city, court_info in COURTS_DATABASE.items():
        if city_lower in court_city.lower() or court_city.lower() in city_lower:
            return court_info['name'], court_info['address']

    # Если город не найден, возвращаем общий суд
    return (
        "Арбитражный суд по месту нахождения ответчика",
        "Адрес суда не определен"
    )


def validate_config() -> List[str]:
    """
    Валидирует конфигурацию.

    Returns:
        Список ошибок конфигурации
    """
    errors = []

    # Проверяем обязательные директории
    if not os.path.exists(BOT_CONFIG['temp_dir']):
        try:
            os.makedirs(BOT_CONFIG['temp_dir'], exist_ok=True)
        except Exception as e:
            errors.append(
                f"Не удалось создать директорию {BOT_CONFIG['temp_dir']}: {e}")

    # Проверяем настройки
    if BOT_CONFIG['max_file_size_mb'] <= 0:
        errors.append("max_file_size_mb должно быть больше нуля")

    if not BOT_CONFIG['supported_formats']:
        errors.append("Должен быть указан хотя бы один поддерживаемый формат")

    return errors
