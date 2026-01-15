# IskBot v2 - Инструкции по доработке

## Обзор проекта

**IskBot** — Telegram-бот для автоматического преобразования претензий в исковые заявления по делам о грузоперевозках.

**Входные данные:** Претензия в формате .docx
**Выходные данные:** Исковое заявление в формате .docx

---

## Структура проекта

```
iskbot/
├── main.py                    # Telegram-бот (aiogram 3.x)
├── parser.py                  # Парсер претензий (OpenAI API)
├── calc_395.py               # Расчёт процентов по ст. 395 ГК РФ
├── cb_rates.py               # Получение ставок ЦБ РФ
├── courts_db.py              # База арбитражных судов
├── duty_calculator.py        # Расчёт госпошлины
├── doc_generator.py          # Генерация DOCX
├── template_isk.docx         # Шаблон искового заявления
└── requirements.txt
```

---

## КРИТИЧНО: Структура данных

### Данные извлекаемые из претензии

Парсер должен извлечь следующую структуру:

```python
@dataclass
class ParsedClaim:
    # Истец (раздел "От кого:")
    plaintiff: Party
    
    # Ответчик (раздел "Кому:")
    defendant: Party
    
    # Документы по перевозке
    documents: List[Document]
    
    # Финансовая информация
    debt: Decimal                    # Сумма долга
    payment_terms: str               # Условия оплаты (напр. "5 бд по ОТТН")
    
    # Отправка документов
    docs_track_number: str           # Трек-номер отправки оригиналов
    docs_received_date: str          # Дата получения документов
    
    # Юридические услуги
    legal_contract_number: str       # Номер договора на юр.услуги
    legal_contract_date: str         # Дата договора
    legal_fee: Decimal               # Сумма юр.услуг
    legal_payment_number: str        # Номер платёжного поручения
    legal_payment_date: str          # Дата оплаты
    
    # Таблица процентов (если есть в претензии)
    interest_table: List[InterestRow]


@dataclass
class Party:
    name: str                        # Полное наименование
    name_short: str                  # Сокращённое (для подписи)
    inn: str                         # ИНН (10 или 12 цифр)
    kpp: str | None                  # КПП (9 цифр, только для ООО)
    ogrn: str                        # ОГРН (13 цифр) или ОГРНИП (15 цифр)
    ogrn_type: str                   # "ОГРН" или "ОГРНИП"
    address: str                     # Полный адрес
    # Для ИП дополнительно:
    birth_date: str | None           # Дата рождения
    birth_place: str | None          # Место рождения


@dataclass
class Document:
    doc_type: str                    # Тип: "Заявка", "Счет", "УПД", "Акт", "ТН", "Счет-фактура"
    number: str                      # Номер документа
    date: str                        # Дата документа
    amount: Decimal | None           # Сумма (если указана)


@dataclass
class InterestRow:
    debt: Decimal                    # Сумма долга
    date_from: str                   # Начало периода
    date_to: str                     # Конец периода
    days: int                        # Дней в периоде
    rate: Decimal                    # Ставка (в процентах)
    interest: Decimal                # Сумма процентов
```

---

## Парсер претензий (parser.py)

### Промпт для OpenAI API

```python
SYSTEM_PROMPT = """Ты — юридический ассистент для парсинга претензий по грузоперевозкам.

Извлеки данные из претензии и верни JSON со следующей структурой:

{
  "plaintiff": {
    "name": "полное наименование истца",
    "name_short": "сокращённое имя для подписи (напр. 'Иванов И.И.')",
    "inn": "ИНН без пробелов",
    "kpp": "КПП или null для ИП",
    "ogrn": "ОГРН или ОГРНИП без пробелов",
    "ogrn_type": "ОГРН" или "ОГРНИП",
    "address": "полный адрес",
    "birth_date": "дата рождения для ИП или null",
    "birth_place": "место рождения для ИП или null"
  },
  "defendant": {
    "name": "полное наименование ответчика",
    "name_short": "сокращённое имя",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН",
    "ogrn_type": "ОГРН",
    "address": "адрес"
  },
  "documents": [
    {
      "doc_type": "Заявка|Счет|УПД|Акт|ТН|Счет-фактура",
      "number": "номер",
      "date": "DD.MM.YYYY",
      "amount": число или null
    }
  ],
  "debt": число,
  "payment_terms": "условия оплаты из договора-заявки",
  "docs_track_number": "трек-номер отправки документов",
  "docs_received_date": "DD.MM.YYYY",
  "legal_contract_number": "номер договора юр.услуг",
  "legal_contract_date": "DD.MM.YYYY",
  "legal_fee": число,
  "legal_payment_number": "номер платёжки",
  "legal_payment_date": "DD.MM.YYYY",
  "interest_table": [
    {
      "debt": число,
      "date_from": "DD.MM.YYYY",
      "date_to": "DD.MM.YYYY",
      "days": число,
      "rate": число (процент, напр. 17),
      "interest": число
    }
  ]
}

ПРАВИЛА ПАРСИНГА:

1. ИСТЕЦ — ищи в разделе "От кого:" или "Исполнитель:"
2. ОТВЕТЧИК — ищи в разделе "Кому:" или "Заказчик:"
3. ИНН:
   - 10 цифр = юр.лицо (ООО, АО)
   - 12 цифр = ИП или физ.лицо
4. ОГРН vs ОГРНИП:
   - 13 цифр = ОГРН (юр.лицо)
   - 15 цифр = ОГРНИП (ИП)
5. КПП есть только у юр.лиц (9 цифр)
6. ДОКУМЕНТЫ — ищи: Заявка, Договор-заявка, Счет, УПД, Акт, ТН, Транспортная накладная, Счет-фактура
7. УСЛОВИЯ ОПЛАТЫ — ищи фразу после "Условия оплаты по договору-заявке"
8. ТРЕК-НОМЕР — 14-значный номер почтового отправления
9. СОКРАЩЁННОЕ ИМЯ:
   - Для ИП: "Фамилия И.О." (напр. "Иванова А.Е.")
   - Для ООО: "ООО «Название»"

Если данные не найдены, используй null.
Верни ТОЛЬКО валидный JSON без markdown-разметки.
"""
```

### Код парсера

```python
import json
from openai import OpenAI
from docx import Document as DocxDocument
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Optional, List

def extract_text_from_docx(file_path: str) -> str:
    """Извлекает текст из DOCX файла включая таблицы"""
    doc = DocxDocument(file_path)
    full_text = []
    
    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text)
    
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            full_text.append(row_text)
    
    return "\n".join(full_text)


def parse_claim(file_path: str, api_key: str) -> dict:
    """Парсит претензию с помощью OpenAI API"""
    client = OpenAI(api_key=api_key)
    
    text = extract_text_from_docx(file_path)
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Распарси эту претензию:\n\n{text}"}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )
    
    result = json.loads(response.choices[0].message.content)
    return validate_parsed_data(result)


def validate_parsed_data(data: dict) -> dict:
    """Валидация извлечённых данных"""
    errors = []
    
    # Проверка ИНН
    if data.get("plaintiff", {}).get("inn"):
        inn = data["plaintiff"]["inn"]
        if not validate_inn(inn):
            errors.append(f"Некорректный ИНН истца: {inn}")
    
    if data.get("defendant", {}).get("inn"):
        inn = data["defendant"]["inn"]
        if not validate_inn(inn):
            errors.append(f"Некорректный ИНН ответчика: {inn}")
    
    # Проверка ОГРН
    if data.get("plaintiff", {}).get("ogrn"):
        ogrn = data["plaintiff"]["ogrn"]
        if len(ogrn) == 15:
            data["plaintiff"]["ogrn_type"] = "ОГРНИП"
        elif len(ogrn) == 13:
            data["plaintiff"]["ogrn_type"] = "ОГРН"
    
    if errors:
        data["_validation_errors"] = errors
    
    return data


def validate_inn(inn: str) -> bool:
    """Проверка контрольной суммы ИНН"""
    if not inn.isdigit():
        return False
    if len(inn) == 10:
        coeffs = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        checksum = sum(int(inn[i]) * coeffs[i] for i in range(9)) % 11 % 10
        return checksum == int(inn[9])
    elif len(inn) == 12:
        coeffs1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        coeffs2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        check1 = sum(int(inn[i]) * coeffs1[i] for i in range(10)) % 11 % 10
        check2 = sum(int(inn[i]) * coeffs2[i] for i in range(11)) % 11 % 10
        return check1 == int(inn[10]) and check2 == int(inn[11])
    return False
```

---

## Плейсхолдеры шаблона искового заявления

Шаблон `template_isk.docx` содержит следующие плейсхолдеры:

| Плейсхолдер | Описание | Пример |
|-------------|----------|--------|
| `{{court_name}}` | Название суда | Арбитражный Суд Волгоградской области |
| `{{court_address}}` | Адрес суда | 400005, Волгоградская область... |
| `{{plaintiff_name}}` | Полное имя истца | ИП Иванова Анна Петровна |
| `{{plaintiff_inn}}` | ИНН истца | 523802089213 |
| `{{plaintiff_ogrn_type}}` | ОГРН или ОГРНИП | ОГРНИП |
| `{{plaintiff_ogrn}}` | Номер ОГРН | 322527500002284 |
| `{{plaintiff_birth_info}}` | Дата и место рождения (для ИП) | Дата рождения 16.04.1990... |
| `{{plaintiff_address}}` | Адрес истца | 607700, Нижегородская область... |
| `{{defendant_name}}` | Полное имя ответчика | ООО «Профилог» |
| `{{defendant_inn}}` | ИНН ответчика | 3435143874 |
| `{{defendant_kpp}}` | КПП ответчика | 343501001 |
| `{{defendant_ogrn}}` | ОГРН ответчика | 1223400010570 |
| `{{defendant_address}}` | Адрес ответчика | 404127, Волгоградская область... |
| `{{claim_total}}` | Общая сумма иска | 129 045,53 |
| `{{duty}}` | Госпошлина | 11 452 |
| `{{documents_list}}` | Список документов | 1. Договор-заявка № 270... |
| `{{docs_track_number}}` | Трек отправки документов | 60770009004737 |
| `{{docs_received_date}}` | Дата получения документов | 10.09.2025 |
| `{{payment_terms}}` | Условия оплаты | 5 бд по ОТТН |
| `{{debt}}` | Основной долг | 123 000 |
| `{{interest_table}}` | Таблица расчёта процентов | [Таблица] |
| `{{claim_date}}` | Дата отправки претензии | 26.11.2025 |
| `{{claim_track_number}}` | Трек претензии | 6077009018215 |
| `{{legal_contract_number}}` | № договора юр.услуг | 2074 |
| `{{legal_contract_date}}` | Дата договора юр.услуг | 06.11.2025 |
| `{{legal_fee}}` | Сумма юр.услуг | 20 000 |
| `{{legal_payment_number}}` | № платёжного поручения | 120 |
| `{{legal_payment_date}}` | Дата оплаты юр.услуг | 18.11.2025 |
| `{{total_interest}}` | Итого процентов | 6 045,53 |
| `{{total_expenses}}` | Судебные расходы (пошлина + юр.услуги) | 31 452 |
| `{{plaintiff_name_short}}` | Сокращённое имя для подписи | Шипилова Я.С. |

---

## Генератор документов (doc_generator.py)

Используй библиотеку `python-docx-template` (docxtpl) для заполнения шаблона:

```python
from docxtpl import DocxTemplate
from decimal import Decimal

def generate_lawsuit(template_path: str, output_path: str, data: dict):
    """Генерирует исковое заявление из шаблона"""
    doc = DocxTemplate(template_path)
    
    # Подготовка данных
    context = prepare_context(data)
    
    # Заполнение шаблона
    doc.render(context)
    doc.save(output_path)


def prepare_context(data: dict) -> dict:
    """Подготавливает контекст для шаблона"""
    plaintiff = data["plaintiff"]
    defendant = data["defendant"]
    
    # Определение суда по адресу ответчика
    court = get_court_by_address(defendant["address"])
    
    # Расчёт госпошлины
    claim_total = Decimal(str(data["debt"])) + Decimal(str(data.get("total_interest", 0)))
    duty = calculate_duty(claim_total)
    
    # Формирование списка документов
    documents_list = format_documents_list(data["documents"])
    
    # Информация о рождении для ИП
    birth_info = ""
    if plaintiff.get("birth_date"):
        birth_info = f"Дата рождения {plaintiff['birth_date']}"
        if plaintiff.get("birth_place"):
            birth_info += f"\nМесто рождения {plaintiff['birth_place']}"
    
    # Судебные расходы
    legal_fee = Decimal(str(data.get("legal_fee", 0)))
    total_expenses = duty + legal_fee
    
    return {
        "court_name": court["name"],
        "court_address": court["address"],
        "plaintiff_name": plaintiff["name"],
        "plaintiff_inn": plaintiff["inn"],
        "plaintiff_ogrn_type": plaintiff.get("ogrn_type", "ОГРН"),
        "plaintiff_ogrn": plaintiff["ogrn"],
        "plaintiff_birth_info": birth_info,
        "plaintiff_address": plaintiff["address"],
        "defendant_name": defendant["name"],
        "defendant_inn": defendant["inn"],
        "defendant_kpp": defendant.get("kpp", ""),
        "defendant_ogrn": defendant["ogrn"],
        "defendant_address": defendant["address"],
        "claim_total": format_money(claim_total),
        "duty": format_money(duty),
        "documents_list": documents_list,
        "docs_track_number": data.get("docs_track_number", ""),
        "docs_received_date": data.get("docs_received_date", ""),
        "payment_terms": data.get("payment_terms", ""),
        "debt": format_money(data["debt"]),
        "interest_table": data.get("interest_table", []),
        "claim_date": data.get("claim_date", ""),
        "claim_track_number": data.get("claim_track_number", ""),
        "legal_contract_number": data.get("legal_contract_number", ""),
        "legal_contract_date": data.get("legal_contract_date", ""),
        "legal_fee": format_money(legal_fee),
        "legal_payment_number": data.get("legal_payment_number", ""),
        "legal_payment_date": data.get("legal_payment_date", ""),
        "total_interest": format_money(data.get("total_interest", 0)),
        "total_expenses": format_money(total_expenses),
        "plaintiff_name_short": plaintiff.get("name_short", ""),
    }


def format_documents_list(documents: list) -> str:
    """Форматирует список документов"""
    lines = []
    for i, doc in enumerate(documents, 1):
        line = f"{i}. {doc['doc_type']} № {doc['number']} от {doc['date']}"
        if doc.get("amount"):
            line += f" на сумму {format_money(doc['amount'])} руб."
        lines.append(line)
    return "\n".join(lines)


def format_money(value) -> str:
    """Форматирует денежную сумму"""
    if isinstance(value, str):
        value = Decimal(value)
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")
```

---

## Расчёт госпошлины (duty_calculator.py)

```python
from decimal import Decimal, ROUND_UP

def calculate_duty(claim_amount: Decimal) -> Decimal:
    """
    Расчёт госпошлины по ст. 333.21 НК РФ
    для имущественных исков в арбитражный суд
    """
    amount = Decimal(str(claim_amount))
    
    if amount <= 100_000:
        duty = amount * Decimal("0.04")
        duty = max(duty, Decimal("2000"))
    elif amount <= 200_000:
        duty = Decimal("4000") + (amount - Decimal("100000")) * Decimal("0.03")
    elif amount <= 1_000_000:
        duty = Decimal("7000") + (amount - Decimal("200000")) * Decimal("0.02")
    elif amount <= 2_000_000:
        duty = Decimal("23000") + (amount - Decimal("1000000")) * Decimal("0.01")
    else:
        duty = Decimal("33000") + (amount - Decimal("2000000")) * Decimal("0.005")
        duty = min(duty, Decimal("200000"))
    
    return duty.quantize(Decimal("1"), rounding=ROUND_UP)
```

---

## Получение ставок ЦБ (cb_rates.py)

```python
import httpx
from datetime import date, datetime
from decimal import Decimal
from bs4 import BeautifulSoup
import json
from pathlib import Path

CACHE_FILE = Path("cb_rates_cache.json")

def get_key_rate(target_date: date) -> Decimal:
    """Получает ключевую ставку ЦБ на указанную дату"""
    rates = load_rates()
    
    # Находим ставку, действующую на указанную дату
    for rate_info in sorted(rates, key=lambda x: x["date"], reverse=True):
        if datetime.strptime(rate_info["date"], "%Y-%m-%d").date() <= target_date:
            return Decimal(str(rate_info["rate"]))
    
    raise ValueError(f"Ставка на дату {target_date} не найдена")


def load_rates() -> list:
    """Загружает ставки из кэша или с сайта ЦБ"""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            cache = json.load(f)
            # Обновляем раз в день
            if cache.get("updated") == date.today().isoformat():
                return cache["rates"]
    
    rates = fetch_rates_from_cbr()
    save_cache(rates)
    return rates


def fetch_rates_from_cbr() -> list:
    """Парсит ставки с сайта ЦБ РФ"""
    url = "https://www.cbr.ru/hd_base/KeyRate/"
    response = httpx.get(url, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    
    rates = []
    table = soup.find("table", class_="data")
    if table:
        for row in table.find_all("tr")[1:]:  # Пропускаем заголовок
            cells = row.find_all("td")
            if len(cells) >= 2:
                date_str = cells[0].text.strip()
                rate_str = cells[1].text.strip().replace(",", ".").replace("%", "")
                try:
                    dt = datetime.strptime(date_str, "%d.%m.%Y")
                    rate = float(rate_str)
                    rates.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "rate": rate
                    })
                except (ValueError, AttributeError):
                    continue
    
    return rates


def save_cache(rates: list):
    """Сохраняет ставки в кэш"""
    with open(CACHE_FILE, "w") as f:
        json.dump({
            "updated": date.today().isoformat(),
            "rates": rates
        }, f, ensure_ascii=False, indent=2)
```

---

## Расчёт процентов по ст. 395 ГК РФ (calc_395.py)

```python
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from cb_rates import get_key_rate

def calculate_interest(
    debt: Decimal,
    start_date: date,
    end_date: date
) -> tuple[list[dict], Decimal]:
    """
    Рассчитывает проценты по ст. 395 ГК РФ
    
    Returns:
        tuple: (таблица расчёта, итоговая сумма процентов)
    """
    rows = []
    total_interest = Decimal("0")
    current_date = start_date
    
    while current_date <= end_date:
        rate = get_key_rate(current_date)
        
        # Находим конец периода с этой ставкой
        period_end = current_date
        while period_end < end_date:
            next_day = period_end + timedelta(days=1)
            try:
                next_rate = get_key_rate(next_day)
                if next_rate != rate:
                    break
                period_end = next_day
            except ValueError:
                break
        
        if period_end > end_date:
            period_end = end_date
        
        # Расчёт процентов за период
        days = (period_end - current_date).days + 1
        days_in_year = 366 if is_leap_year(current_date.year) else 365
        
        interest = (debt * days * rate / Decimal("100") / days_in_year).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        
        rows.append({
            "debt": float(debt),
            "date_from": current_date.strftime("%d.%m.%Y"),
            "date_to": period_end.strftime("%d.%m.%Y"),
            "days": days,
            "rate": float(rate),
            "days_in_year": days_in_year,
            "interest": float(interest)
        })
        
        total_interest += interest
        current_date = period_end + timedelta(days=1)
    
    return rows, total_interest


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
```

---

## Дополнительные требования

### Запрашиваемые данные от пользователя

Бот должен запросить у пользователя данные, которых нет в претензии:

1. **Дата получения претензии ответчиком** — для расчёта срока ответа
2. **Трек-номер отправки претензии** — если не указан в документе
3. **Дата расчёта процентов** — обычно дата подачи иска

### Определение суда

По адресу ответчика определяется арбитражный суд субъекта РФ. База судов в файле `courts_db.py`:

```python
COURTS = {
    "Волгоградская область": {
        "name": "Арбитражный суд Волгоградской области",
        "address": "400005, Волгоградская область, город Волгоград, 7-й Гвардейской ул., д.2"
    },
    "Московская область": {
        "name": "Арбитражный суд Московской области",
        "address": "107053, г. Москва, проспект Академика Сахарова, д. 18"
    },
    # ... остальные регионы
}
```

### requirements.txt

```
aiogram>=3.0
python-docx>=0.8.11
docxtpl>=0.16.0
openai>=1.0
httpx>=0.24.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
pydantic>=2.0
```

---

## Порядок выполнения задач

1. [x] Создать шаблон `template_isk.docx` с плейсхолдерами
2. [ ] Реализовать парсер `parser.py` с валидацией
3. [ ] Реализовать `cb_rates.py` для получения ставок ЦБ
4. [ ] Реализовать `calc_395.py` для расчёта процентов
5. [ ] Реализовать `doc_generator.py` для генерации DOCX
6. [ ] Обновить `main.py` для интеграции всех модулей
7. [ ] Добавить логирование
8. [ ] Написать тесты

---

## Пример использования

```python
# 1. Парсим претензию
data = parse_claim("претензия.docx", api_key="...")

# 2. Запрашиваем недостающие данные
data["claim_date"] = "26.11.2025"  # от пользователя
data["claim_track_number"] = "6077009018215"  # от пользователя

# 3. Рассчитываем проценты
from datetime import date
interest_table, total_interest = calculate_interest(
    debt=Decimal(str(data["debt"])),
    start_date=date(2025, 9, 19),  # дата начала просрочки
    end_date=date.today()
)
data["interest_table"] = interest_table
data["total_interest"] = total_interest

# 4. Генерируем исковое
generate_lawsuit("template_isk.docx", "исковое.docx", data)
```
