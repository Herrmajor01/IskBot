# Руководство по улучшенной системе парсинга

## 📋 Оглавление

- [Обзор](#обзор)
- [Архитектура](#архитектура)
- [Уровни извлечения данных](#уровни-извлечения-данных)
- [Использование](#использование)
- [Конфигурация](#конфигурация)
- [Интеграция](#интеграция)
- [Примеры](#примеры)

## 🎯 Обзор

Система парсинга IskBot v2 использует **многоуровневую стратегию извлечения данных** с валидацией и автоматическим восстановлением недостающей информации.

### Ключевые особенности

- ✅ **4 уровня извлечения данных** - от прямого до эвристического
- ✅ **Валидация по алгоритмам ФНС РФ** - проверка ИНН, КПП, ОГРН
- ✅ **Автоматическое восстановление данных** - заполнение пропущенных полей
- ✅ **Обратная совместимость** - плавный переход со старого парсера
- ✅ **Конфигурируемые паттерны** - легкая настройка без изменения кода
- ✅ **Детальное логирование** - полная прозрачность процесса

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────┐
│              parser_integration.py                  │
│            Интегрированный парсер                   │
│  (объединяет legacy и enhanced подходы)            │
└──────────────┬──────────────────┬──────────────────┘
               │                  │
      ┌────────┴────────┐  ┌─────┴──────────┐
      │  Legacy Parser  │  │ Enhanced Parser│
      │ (проверенный)   │  │  (улучшенный)  │
      └─────────────────┘  └────────┬───────┘
                                    │
                     ┌──────────────┼──────────────┐
                     │              │              │
              ┌──────┴──────┐ ┌────┴─────┐ ┌─────┴──────┐
              │  Validators │ │  Data    │ │  Parsing   │
              │   (ФНС РФ)  │ │ Recovery │ │   Config   │
              └─────────────┘ └──────────┘ └────────────┘
```

### Компоненты системы

1. **`enhanced_parser.py`** - Улучшенный парсер с 4-уровневой стратегией
2. **`parser_integration.py`** - Интегратор legacy и enhanced парсеров
3. **`parsing_config.py`** - Конфигурация паттернов и настроек
4. **`validators.py`** - Валидация ИНН/КПП/ОГРН
5. **`data_recovery.py`** - Восстановление недостающих данных
6. **`sliding_window_parser.py`** - Legacy парсер (проверенный)

## 📊 Уровни извлечения данных

### Уровень 1: Прямое извлечение

Поиск данных по точным регулярным выражениям.

```python
# Примеры паттернов
INN: r'ИНН\s*[:\s]*(\d{10,12})'
КПП: r'КПП\s*[:\s]*(\d{9})'
ОГРН: r'ОГРН(?:ИП)?\s*[:\s]*(\d{13,15})'
```

**Что извлекается:**
- ИНН, КПП, ОГРН организаций
- Финансовые суммы (долг, проценты, пошлина)
- Даты (договора, оплаты, расчета)

**Confidence:** 1.0 (максимальная уверенность)

### Уровень 2: Контекстное извлечение

Анализ структуры документа и контекста вокруг данных.

```python
# Пример: определение секции документа
if line.startswith('Обществу'):
    current_section = 'defendant'
elif line.startswith('от'):
    current_section = 'plaintiff'
```

**Что извлекается:**
- Названия организаций из заголовка
- Адреса из соответствующих секций
- Реквизиты с учетом контекста (истец/ответчик)

**Confidence:** 0.9 (высокая уверенность)

### Уровень 3: Валидация

Проверка извлеченных данных по алгоритмам ФНС РФ.

```python
validator = DataValidator()
report = validator.validate_entity(inn, kpp, ogrn)

if not report.is_valid:
    # Выявлены ошибки, confidence снижается
    confidence *= 0.5
```

**Что проверяется:**
- Контрольные суммы ИНН (алгоритм ФНС)
- Контрольные цифры ОГРН/ОГРНИП
- Соответствие типов организаций
- Обязательность КПП для юр.лиц

**Результат:** Корректировка confidence на основе валидности

### Уровень 4: Восстановление

Автоматическое заполнение недостающих полей.

```python
recovery = DataRecovery()
recovered = recovery.recover_missing_fields(inn, kpp, ogrn, name)

# Применение восстановленных данных
if recovered['name']:
    data['name'] = recovered['name']
if recovered['name_short']:
    data['name_short'] = recovered['name_short']
```

**Что восстанавливается:**
- Тип организации (ООО/ИП) по ИНН или ОГРН
- Форматирование названий организаций
- Краткие названия для подписи
- КПП устанавливается в None для ИП

**Confidence:** Зависит от успешности восстановления (0.7-1.0)

## 💻 Использование

### Базовое использование (рекомендуется)

```python
from parser_integration import parse_document_integrated

# Парсинг с использованием обоих парсеров
result = parse_document_integrated(text)

# Доступ к данным
defendant_inn = result.get('defendant_inn')
plaintiff_name = result.get('plaintiff_name')
debt = result.get('debt')
```

### Использование только улучшенного парсера

```python
from enhanced_parser import EnhancedParser

parser = EnhancedParser()
parsing_result = parser.parse_with_strategy(text)

# Доступ к данным и метаинформации
data = parsing_result.data
confidence = parsing_result.confidence
warnings = parsing_result.warnings
errors = parsing_result.errors
```

### Использование только legacy парсера

```python
from parser_integration import parse_document_integrated

# Режим обратной совместимости
result = parse_document_integrated(text, use_legacy_only=True)
```

### Получение детального отчета

```python
from parser_integration import get_parsing_report

report = get_parsing_report(text)

print("Legacy извлек:", report['legacy']['fields_extracted'], "полей")
print("Enhanced извлек:", report['enhanced']['fields_extracted'], "полей")
print("Confidence:", report['enhanced']['confidence'])
print("Warnings:", report['enhanced']['warnings'])
```

## ⚙️ Конфигурация

Все паттерны и настройки находятся в `parsing_config.py`.

### Изменение паттернов

```python
# В parsing_config.py
INN_PATTERNS = [
    r'ИНН\s*[:\s]*(\d{10,12})',
    r'ваш_новый_паттерн',  # Добавьте свой паттерн
]
```

### Настройка уверенности

```python
# В parsing_config.py
MIN_CONFIDENCE = 0.5  # Минимальная уверенность для принятия результата
```

### Включение детального логирования

```python
# В parsing_config.py
DEBUG_LOGGING = True

# Или через Python
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

## 🔗 Интеграция в main.py

### Минимальная интеграция

Замените импорт:

```python
# Было:
from sliding_window_parser import parse_documents_with_sliding_window

# Стало:
from parser_integration import parse_document_integrated as parse_documents_with_sliding_window
```

Все остальное продолжит работать без изменений!

### Полная интеграция

```python
from parser_integration import parse_document_integrated, get_parsing_report
from enhanced_parser import ParsingResult

# В функции handle_document
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... извлечение текста ...

    # Парсинг
    claim_data = parse_document_integrated(text)

    # Опционально: получить метаинформацию
    report = get_parsing_report(text)
    confidence = report['enhanced']['confidence']

    if confidence < 0.6:
        await update.message.reply_text(
            "⚠️ Внимание: некоторые данные могут быть извлечены неточно. "
            "Пожалуйста, проверьте результат."
        )

    # ... дальнейшая обработка ...
```

## 📝 Примеры

### Пример 1: Парсинг претензии

```python
text = """
Обществу с ограниченной ответственностью «Рога и Копыта»
ИНН 7736207543
КПП 773601001
ОГРН 1027700229193

от Индивидуального предпринимателя Иванова Ивана Ивановича
ИНН 526317984689
ОГРНИП 304500116000157

ТРЕБОВАНИЕ
...
"""

result = parse_document_integrated(text)

print("Ответчик:", result['defendant_name'])
print("ИНН ответчика:", result['defendant_inn'])
print("Истец:", result['plaintiff_name'])
print("ИНН истца:", result['plaintiff_inn'])
```

**Вывод:**
```
Ответчик: Общество с ограниченной ответственностью «Рога и Копыта»
ИНН ответчика: 7736207543
Истец: Индивидуальный предприниматель Иванов Иван Иванович
ИНН истца: 526317984689
```

### Пример 2: Обработка ошибок

```python
from enhanced_parser import EnhancedParser

parser = EnhancedParser()
result = parser.parse_with_strategy(text)

if result.errors:
    print("❌ Обнаружены ошибки:")
    for error in result.errors:
        print(f"  - {error}")

if result.warnings:
    print("⚠️ Предупреждения:")
    for warning in result.warnings:
        print(f"  - {warning}")

print(f"Уверенность: {result.confidence:.2%}")
```

### Пример 3: Валидация данных

```python
from validators import DataValidator

validator = DataValidator()

# Валидация извлеченных данных
report = validator.validate_entity(
    inn=result.data.get('defendant_inn'),
    kpp=result.data.get('defendant_kpp'),
    ogrn=result.data.get('defendant_ogrn')
)

if report.is_valid:
    print("✅ Все данные валидны")
    print(f"Тип: {report.entity_type.value}")
else:
    print("❌ Данные невалидны:")
    print(report.get_summary())
```

## 🐛 Отладка

### Включение детального логирования

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Теперь все действия парсера будут логироваться
result = parse_document_integrated(text)
```

### Анализ методов извлечения

```python
from enhanced_parser import EnhancedParser

parser = EnhancedParser()
result = parser.parse_with_strategy(text)

# Смотрим, какие методы были использованы
for field, method in result.extraction_methods.items():
    print(f"{field}: {method}")
```

**Вывод:**
```
defendant: direct
plaintiff: direct
defendant_name: recovered
plaintiff_name_short: recovered
```

## 📈 Производительность

- **Legacy парсер**: ~200-300ms на документ
- **Enhanced парсер**: ~400-500ms на документ
- **Integrated парсер**: ~600-800ms на документ

**Рекомендация:** Используйте integrated парсер для максимальной точности. Если важна скорость, используйте `use_legacy_only=True`.

## ❓ FAQ

**Q: Можно ли использовать только новый парсер?**
A: Да, используйте `EnhancedParser` напрямую. Но integrated парсер дает лучшие результаты.

**Q: Как добавить новый паттерн?**
A: Откройте `parsing_config.py` и добавьте паттерн в соответствующий список.

**Q: Почему confidence низкий?**
A: Проверьте логи - там будет указано, какие данные не прошли валидацию.

**Q: Можно ли отключить валидацию?**
A: Да, используйте legacy парсер: `parse_document_integrated(text, use_legacy_only=True)`

---

**IskBot v2** - Интеллектуальный парсинг юридических документов с валидацией и восстановлением данных.
