"""
Модуль для расчета процентов по ст. 395 ГК РФ.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)


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
        ("09.06.2025", "20.00"),
    ]
    key_rates = []
    for date_str, rate_str in rates_data:
        try:
            date_from = datetime.strptime(date_str, "%d.%m.%Y")
            rate = float(rate_str.replace(",", "."))
            key_rates.append((date_from, rate))
        except Exception as e:
            logging.warning(
                f"Ошибка парсинга ставки: {date_str}, {rate_str}, {e}")
            continue

    result = []
    for i in range(len(key_rates)):
        date_from, rate = key_rates[i]
        date_to = (
            key_rates[i + 1][0] - timedelta(days=1)
            if i + 1 < len(key_rates) else datetime.max
        )
        result.append((date_from, date_to, rate))

    if not result:
        logging.warning(
            "Не удалось получить ключевые ставки, используется резервная ставка 21%"
        )
        return [(datetime(2025, 1, 1), datetime.max, 21.0)]
    return result


def split_period_by_key_rate(
    start: datetime, end: datetime, key_rates: List[Tuple[datetime, datetime, float]]
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
    Считает проценты по ст. 395 ГК РФ для каждого подпериода.
    """
    total = 0
    details = []
    for date_from, date_to, rate in periods:
        days = (date_to - date_from).days + 1
        year_days = 366 if date_from.year % 4 == 0 and (
            date_from.year % 100 != 0 or date_from.year % 400 == 0) else 365
        interest = base_sum * rate / 100 / year_days * days
        total += interest
        details.append({
            'date_from': date_from.strftime('%d.%m.%Y'),
            'date_to': date_to.strftime('%d.%m.%Y'),
            'rate': rate,
            'days': days,
            'interest': round(interest, 2),
            'sum': base_sum,
            'formula': f"{base_sum:,.2f} × {days} × {rate}% / {year_days}".replace(',', ' ')
        })
    return round(total, 2), details


def calculate_full_395(
    docx_path: str, today: Optional[datetime] = None, key_rates: Optional[List[Tuple[datetime, datetime, float]]] = None
) -> Dict[str, Any]:
    """
    Полный расчет процентов по ст. 395 ГК РФ с учетом всех периодов и ставок.
    """
    from parsing import parse_periods_from_docx

    if today is None:
        today = datetime.today()
    logging.info(f"Текущая дата: {today.strftime('%d.%m.%Y')}")
    if key_rates is None:
        key_rates = get_key_rates_from_395gk()
    periods, base_sum = parse_periods_from_docx(docx_path)
    total_interest = 0
    details = []

    for p in periods:
        year_days = 366 if p['date_from'].year % 4 == 0 and (
            p['date_from'].year % 100 != 0 or p['date_from'].year % 400 == 0) else 365
        interest = p['sum'] * p['days'] * p['rate'] / 100 / year_days
        details.append({
            'date_from': p['date_from'].strftime('%d.%m.%Y'),
            'date_to': p['date_to'].strftime('%d.%m.%Y'),
            'rate': p['rate'],
            'days': p['days'],
            'interest': round(interest, 2),
            'sum': p['sum'],
            'formula': f"{p['sum']:,.2f} × {p['days']} × {p['rate']}% / {year_days}".replace(',', ' ')
        })
        total_interest += interest

    if periods:
        last = periods[-1]
        logging.info(
            f"Последняя дата в таблице: {last['date_to'].strftime('%d.%m.%Y')}")
        if last['date_to'] < today:
            actual_start = last['date_to'] + timedelta(days=1)
            actual_end = today
            logging.info(
                f"Дополнительный период: {actual_start.strftime('%d.%m.%Y')} - {actual_end.strftime('%d.%m.%Y')}")
            actual_periods = split_period_by_key_rate(
                actual_start, actual_end, key_rates)
            if not actual_periods:
                logging.warning(
                    "Не найдены ключевые ставки для дополнительного периода, используется ставка 21%")
                actual_periods = [(actual_start, actual_end, 21.0)]
            total_actual, details_actual = calc_395_on_periods(
                last['sum'], actual_periods)
            details.extend(details_actual)
            total_interest += total_actual

    return {
        'total_interest': round(total_interest, 2),
        'details': details,
        'base_sum': base_sum
    }
