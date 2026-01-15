"""
Модуль для расчета процентов по ст. 395 ГК РФ.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def parse_periods_from_docx(
    docx_path: str
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Парсит таблицу из .docx и возвращает периоды для расчета процентов.
    Восстановленная версия из старого parsing.py.
    """
    from docx import Document

    doc = Document(docx_path)
    if not doc.tables:
        raise ValueError("В файле .docx отсутствует таблица")
    table = doc.tables[0]
    if len(table.rows) == 0:
        raise ValueError("Таблица пуста")
    max_cells = max(len(row.cells) for row in table.rows)
    if max_cells < 6:
        raise ValueError(
            "Таблица должна содержать минимум 6 столбцов в строках данных"
        )
    periods = []
    current_sum = 0.0
    for row in table.rows[1:]:
        cells = row.cells
        if len(cells) < 6:
            continue
        has_formula = len(cells) >= 7
        try:
            amount_text = cells[0].text.strip()
            # Пропуск строк с текстом, а не суммой
            if not amount_text:
                continue
            cleaned_for_check = re.sub(r'[^\d.,+\-]', '', amount_text)
            if not cleaned_for_check:
                continue
            if re.search(r'[А-Яа-яA-Za-z]', amount_text):
                skip_words = ["сумма", "задолж", "процент"]
                if any(word in amount_text.lower() for word in skip_words):
                    continue
            amount_text = re.sub(r'[^\d.,+\-]', '', amount_text)
            if not amount_text:
                continue
            amount_text = amount_text.rstrip('.').rstrip(',').rstrip()
            if not amount_text:
                continue
            if not re.match(r'^[\+\-]?\d+([.,]\d+)?$', amount_text):
                continue
            try:
                if amount_text.startswith('+'):
                    amount = float(amount_text[1:].replace(',', '.'))
                    current_sum += amount
                else:
                    current_sum = float(amount_text.replace(',', '.'))
            except ValueError:
                continue
            date_from_text = cells[1].text.strip()
            date_to_text = cells[2].text.strip()
            if not date_from_text or not date_to_text:
                continue
            if date_to_text.lower() == "новая задолженность":
                continue
            date_pattern = r'\d{2}\.\d{2}\.\d{4}'
            if (
                not re.match(date_pattern, date_from_text)
                or not re.match(date_pattern, date_to_text)
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
            rate_text = re.sub(r'[^\d.,]', '', rate_text)
            if not rate_text:
                continue
            rate = float(rate_text.replace(',', '.'))
            year_days = None
            formula_text = ""
            if has_formula:
                formula_text = cells[5].text.strip()
                year_days_text = cells[5].text.strip()
                year_days_clean = re.sub(r'[^\d]', '', year_days_text)
                if year_days_clean:
                    year_days = int(year_days_clean)
            interest_index = 6 if has_formula else 5
            interest_text = cells[interest_index].text.strip()
            if not interest_text:
                continue
            interest_text = re.sub(r'[^\d.,]', '', interest_text)
            interest_text = interest_text.rstrip('.').rstrip(',').rstrip()
            if not interest_text:
                continue
            if not re.match(r'^\d+([.,]\d+)?$', interest_text):
                continue
            try:
                interest = float(interest_text.replace(',', '.'))
            except ValueError:
                continue
            periods.append({
                'sum': current_sum,
                'date_from': date_from,
                'date_to': date_to,
                'days': days,
                'rate': rate,
                'interest': interest,
                'year_days': year_days,
                'formula': formula_text
            })
        except Exception as e:
            logging.warning(f"Ошибка парсинга строки: {e}")
            continue
    if not periods:
        raise ValueError("Не удалось распарсить ни одной строки из таблицы")
    return periods, current_sum


def _load_cached_rates(
    cache_path: str,
    ttl_hours: int
) -> Optional[List[Tuple[datetime, float]]]:
    if not cache_path or ttl_hours <= 0:
        return None
    try:
        if not os.path.exists(cache_path):
            return None
        cache_age = datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(cache_path)
        )
        if cache_age > timedelta(hours=ttl_hours):
            return None
        with open(cache_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            rates_payload = payload.get('rates', payload)
        elif isinstance(payload, list):
            rates_payload = payload
        else:
            return None
        if not isinstance(rates_payload, list):
            return None
        parsed = []
        for item in rates_payload:
            date_str = None
            rate_value = None
            if isinstance(item, dict):
                date_str = item.get('date') or item.get('date_from')
                rate_value = item.get('rate')
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                date_str = item[0]
                rate_value = item[1]
            if not date_str or rate_value is None:
                continue
            try:
                date_from = datetime.strptime(str(date_str), "%d.%m.%Y")
                rate = float(str(rate_value).replace(',', '.'))
            except Exception:
                continue
            parsed.append((date_from, rate))
        return parsed or None
    except Exception as exc:
        logging.warning("Ошибка чтения кэша ставок: %s", exc)
        return None


def _save_cached_rates(
    cache_path: str,
    rates: List[Tuple[datetime, float]]
) -> None:
    if not cache_path:
        return
    try:
        payload = {
            'rates': [
                {'date': date_from.strftime("%d.%m.%Y"), 'rate': rate}
                for date_from, rate in rates
            ]
        }
        with open(cache_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.warning("Ошибка сохранения кэша ставок: %s", exc)


def _fetch_rates_from_395gk(
    url: str,
    timeout: int
) -> List[Tuple[datetime, float]]:
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception as exc:
        raise RuntimeError(
            f"Не удалось импортировать зависимости для загрузки ставок: {exc}"
        ) from exc

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    header_cell = soup.find(
        string=re.compile(r'Дата начала применения', re.IGNORECASE)
    )
    table = header_cell.find_parent('table') if header_cell else None
    if not table:
        raise ValueError("Не найдена таблица ставок на странице")

    rates = []
    for row in table.find_all('tr'):
        cells = [c.get_text(strip=True) for c in row.find_all('td')]
        if len(cells) < 2:
            continue
        date_match = re.search(r'\d{2}\.\d{2}\.\d{4}', cells[0])
        rate_match = re.search(r'\d+(?:[.,]\d+)?', cells[1])
        if not date_match or not rate_match:
            continue
        date_from = datetime.strptime(date_match.group(0), "%d.%m.%Y")
        rate = float(rate_match.group(0).replace(',', '.'))
        rates.append((date_from, rate))

    if not rates:
        raise ValueError("Не удалось извлечь ставки из таблицы")

    unique = {}
    for date_from, rate in rates:
        unique[date_from] = rate
    return sorted(unique.items(), key=lambda x: x[0])


def _build_rate_periods(
    rates: List[Tuple[datetime, float]]
) -> List[Tuple[datetime, datetime, float]]:
    if not rates:
        return []
    ordered = sorted(rates, key=lambda x: x[0])
    result = []
    for i, (date_from, rate) in enumerate(ordered):
        date_to = (
            ordered[i + 1][0] - timedelta(days=1)
            if i + 1 < len(ordered) else datetime.max
        )
        result.append((date_from, date_to, rate))
    return result


def get_key_rates_from_395gk() -> List[Tuple[datetime, datetime, float]]:
    """
    Возвращает список ключевых ставок ЦБ РФ с датами действия.
    """
    rates_data = [
        ("01.08.2016", "10.50"), ("19.09.2016", "10.00"),
        ("27.03.2017", "9.75"), ("02.05.2017", "9.25"),
        ("19.06.2017", "9.00"), ("18.09.2017", "8.50"),
        ("30.10.2017", "8.25"), ("18.12.2017", "7.75"),
        ("12.02.2018", "7.50"), ("26.03.2018", "7.25"),
        ("17.09.2018", "7.50"), ("17.12.2018", "7.75"),
        ("17.06.2019", "7.50"), ("29.07.2019", "7.25"),
        ("09.09.2019", "7.00"), ("28.10.2019", "6.50"),
        ("16.12.2019", "6.25"), ("10.02.2020", "6.00"),
        ("27.04.2020", "5.50"), ("22.06.2020", "4.50"),
        ("27.07.2020", "4.25"), ("22.03.2021", "4.50"),
        ("26.04.2021", "5.00"), ("15.06.2021", "5.50"),
        ("26.07.2021", "6.50"), ("13.09.2021", "6.75"),
        ("25.10.2021", "7.50"), ("20.12.2021", "8.50"),
        ("14.02.2022", "9.50"), ("28.02.2022", "20.00"),
        ("11.04.2022", "17.00"), ("04.05.2022", "14.00"),
        ("27.05.2022", "11.00"), ("14.06.2022", "9.50"),
        ("25.07.2022", "8.00"), ("19.09.2022", "7.50"),
        ("24.07.2023", "8.50"), ("15.08.2023", "12.00"),
        ("18.09.2023", "13.00"), ("30.10.2023", "15.00"),
        ("18.12.2023", "16.00"), ("29.07.2024", "18.00"),
        ("16.09.2024", "19.00"), ("28.10.2024", "21.00"),
        ("09.06.2025", "20.00"), ("28.07.2025", "18.00"),
        ("15.09.2025", "17.00"), ("27.10.2025", "16.50"),
        ("22.12.2025", "16.00"),
    ]
    fetched_rates = None
    try:
        from config import CALCULATION_CONFIG
        cache_path = CALCULATION_CONFIG.get('cache_file')
        ttl_hours = int(CALCULATION_CONFIG.get('cache_ttl_hours', 24))
        rates_url = CALCULATION_CONFIG.get(
            'rates_url',
            'https://395gk.ru/svedcb.htm'
        )
        timeout = int(CALCULATION_CONFIG.get('request_timeout', 10))
        cached = _load_cached_rates(cache_path, ttl_hours)
        if cached:
            fetched_rates = cached
        else:
            fetched_rates = _fetch_rates_from_395gk(rates_url, timeout)
            _save_cached_rates(cache_path, fetched_rates)
    except Exception as exc:
        logging.warning("Не удалось загрузить ставки с 395gk: %s", exc)

    key_rates = []
    if fetched_rates:
        key_rates = fetched_rates
    else:
        for date_str, rate_str in rates_data:
            try:
                date_from = datetime.strptime(date_str, "%d.%m.%Y")
                rate = float(rate_str.replace(",", "."))
                key_rates.append((date_from, rate))
            except Exception as e:
                logging.warning(
                    "Ошибка парсинга ставки: %s, %s, %s",
                    date_str, rate_str, e
                )
                continue

    result = _build_rate_periods(key_rates)

    if not result:
        logging.warning(
            "Не удалось получить ключевые ставки, "
            "используется резервная ставка 21%"
        )
        return [(datetime(2025, 1, 1), datetime.max, 21.0)]
    return result


def split_period_by_key_rate(
    start: datetime,
    end: datetime,
    key_rates: List[Tuple[datetime, datetime, float]]
) -> List[Tuple[datetime, datetime, float]]:
    """
    Делит период на подпериоды по ключевым ставкам.
    """
    periods = []
    for k_start, k_end, rate in key_rates:
        actual_start = max(start, k_start)
        actual_end = min(end, k_end)
        if actual_start <= actual_end:
            periods.append((actual_start, actual_end, rate))
    return sorted(periods, key=lambda x: x[0])


def calc_395_on_periods(
    base_sum: float, periods: List[Tuple[datetime, datetime, float]]
) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Рассчитывает проценты по ст. 395 ГК РФ для списка периодов.
    """
    total_interest = 0.0
    detailed_calc = []

    for start, end, rate in periods:
        days = (end - start).days + 1
        year_days = 366 if (
            start.year % 4 == 0 and
            start.year % 100 != 0 or
            start.year % 400 == 0
        ) else 365

        interest = base_sum * days * rate / 100 / year_days
        total_interest += interest

        detailed_calc.append({
            'period': f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}",
            'date_from': start.strftime('%d.%m.%Y'),
            'date_to': end.strftime('%d.%m.%Y'),
            'sum': base_sum,
            'days': days,
            'rate': rate,
            'interest': interest,
            'formula': (
                f"{base_sum:,.2f} × {days} × {rate}% / {year_days}"
                .replace(',', ' ')
            )
        })

    return total_interest, detailed_calc


def calculate_full_395(
    docx_path: str,
    today: Optional[datetime] = None,
    key_rates: Optional[List[Tuple[datetime, datetime, float]]] = None
) -> Dict[str, Any]:
    """
    Полный расчет процентов по ст. 395 ГК РФ с парсингом из .docx.
    """
    if today is None:
        today = datetime.now()

    if key_rates is None:
        key_rates = get_key_rates_from_395gk()

    try:
        periods, base_sum = parse_periods_from_docx(docx_path)
    except Exception as exc:
        logging.warning(
            "Ошибка парсинга таблицы процентов из %s: %s",
            docx_path,
            exc
        )
        return {
            'total_interest': 0.0,
            'detailed_calc': [],
            'base_sum': 0.0,
            'periods_count': 0,
            'error': str(exc)
        }

    if not periods:
        return {
            'total_interest': 0.0,
            'detailed_calc': [],
            'base_sum': base_sum,
            'error': 'Не найдены периоды для расчета'
        }

    # Расчет для каждого периода из таблицы
    total_interest = 0.0
    detailed_calc = []

    for p in periods:
        year_days = p.get('year_days')
        if not year_days:
            year_days = 366 if (
                p['date_from'].year % 4 == 0 and
                p['date_from'].year % 100 != 0 or
                p['date_from'].year % 400 == 0
            ) else 365

        interest = p['sum'] * p['days'] * p['rate'] / 100 / year_days
        total_interest += interest

        detailed_calc.append({
            'period': (
                f"{p['date_from'].strftime('%d.%m.%Y')} - "
                f"{p['date_to'].strftime('%d.%m.%Y')}"
            ),
            'date_from': p['date_from'].strftime('%d.%m.%Y'),
            'date_to': p['date_to'].strftime('%d.%m.%Y'),
            'sum': p['sum'],
            'days': p['days'],
            'rate': p['rate'],
            'interest': interest,
            'formula': (
                f"{p['sum']:,.2f} × {p['days']} × "
                f"{p['rate']}% / {year_days}"
            ).replace(',', ' ')
        })

    # Проверяем, есть ли дополнительный период после последней даты
    last = periods[-1]
    logging.info(
        f"Последняя дата в таблице: {last['date_to'].strftime('%d.%m.%Y г.')}")

    if last['date_to'] < today:
        actual_start = last['date_to'] + timedelta(days=1)
        actual_end = today

        logging.info(
            f"Дополнительный период: "
            f"{actual_start.strftime('%d.%m.%Y г.')} - "
            f"{actual_end.strftime('%d.%m.%Y г.')}")

        # Находим подходящие ставки для дополнительного периода
        additional_periods = split_period_by_key_rate(
            actual_start, actual_end, key_rates
        )

        if not additional_periods:
            logging.warning(
                "Не найдены ключевые ставки для дополнительного периода, "
                "используется ставка 21%")
            additional_periods = [(actual_start, actual_end, 21.0)]

        # Расчет для дополнительного периода
        additional_interest, additional_calc = calc_395_on_periods(
            base_sum, additional_periods
        )
        total_interest += additional_interest
        detailed_calc.extend(additional_calc)

    return {
        'total_interest': total_interest,
        'detailed_calc': detailed_calc,
        'base_sum': base_sum,
        'periods_count': len(periods)
    }
