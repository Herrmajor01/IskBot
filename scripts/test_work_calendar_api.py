#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import date, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as m


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    # Базовая проверка доступности API и корректности статусов
    holiday = date(2024, 1, 1)
    workday = date(2024, 1, 9)

    holiday_status = m.fetch_calendar_day_status(holiday)
    workday_status = m.fetch_calendar_day_status(workday)

    print(f"{holiday} working: {holiday_status}")
    print(f"{workday} working: {workday_status}")

    expect(holiday_status is False, "Ожидали выходной для 2024-01-01")
    expect(workday_status is True, "Ожидали рабочий день для 2024-01-09")

    # Проверка add_working_days с календарём API
    calendar = m.load_work_calendar(2024)
    due = m.add_working_days(datetime(2024, 1, 9), 1, calendar)
    print(f"2024-01-09 +1 working day => {due.date()}")
    expect(due.date() == date(2024, 1, 10), "Неверный расчёт add_working_days")

    print("OK")


if __name__ == "__main__":
    main()
