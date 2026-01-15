# Руководство по валидации и восстановлению данных

## Обзор

Проект включает две связанные системы:

1. **`validators.py`** - Валидация ИНН, КПП, ОГРН по алгоритмам ФНС РФ
2. **`data_recovery.py`** - Восстановление отсутствующих данных

## Модуль validators.py

### Основные функции

#### Валидация ИНН

```python
from validators import DataValidator

validator = DataValidator()

# Валидный ИНН юр.лица (10 цифр)
result = validator.validate_inn("7736207543")
print(result.is_valid())  # True
print(result.entity_type)  # EntityType.LEGAL_ENTITY

# Валидный ИНН ИП (12 цифр)
result = validator.validate_inn("526317984689")
print(result.is_valid())  # True
print(result.entity_type)  # EntityType.INDIVIDUAL

# Невалидный ИНН
result = validator.validate_inn("7736207544")
print(result.is_valid())  # False
print(result.error_message)  # "ИНН не прошел проверку контрольной суммы"
```

#### Валидация КПП

```python
# КПП для юр.лица
result = validator.validate_kpp("773601001", EntityType.LEGAL_ENTITY)
print(result.is_valid())  # True

# КПП не нужен для ИП
result = validator.validate_kpp(None, EntityType.INDIVIDUAL)
print(result.is_valid())  # True
```

#### Валидация ОГРН

```python
# ОГРН юр.лица (13 цифр)
result = validator.validate_ogrn("1027700229193")
print(result.is_valid())  # True
print(result.entity_type)  # EntityType.LEGAL_ENTITY

# ОГРНИП (15 цифр)
result = validator.validate_ogrn("304500116000157")
print(result.is_valid())  # True
print(result.entity_type)  # EntityType.INDIVIDUAL
```

#### Комплексная валидация

```python
# Валидация всех полей организации
report = validator.validate_entity(
    inn="7736207543",
    kpp="773601001",
    ogrn="1027700229193"
)

print(report.is_valid)  # True
print(report.entity_type)  # EntityType.LEGAL_ENTITY
print(report.get_summary())
# Выведет:
# Тип: Юридическое лицо (ООО, АО и т.д.)
#
# ✅ ИНН: 7736207543
# ✅ КПП: 773601001
# ✅ ОГРН: 1027700229193
```

### Быстрые функции

```python
from validators import is_valid_inn, is_valid_ogrn, get_entity_type

# Быстрая проверка
if is_valid_inn("7736207543"):
    print("ИНН валиден")

# Определение типа организации
entity_type = get_entity_type(inn="7736207543")
print(entity_type)  # EntityType.LEGAL_ENTITY
```

## Модуль data_recovery.py

### Восстановление данных

```python
from data_recovery import DataRecovery

recovery = DataRecovery()

# Восстановление недостающих полей
result = recovery.recover_missing_fields(
    inn="7736207543",
    kpp=None,  # Будет определено, что КПП обязателен
    ogrn="1027700229193",
    name="Общество с ограниченной ответственностью \"Яндекс\""
)

print(result['entity_type'])  # EntityType.LEGAL_ENTITY
print(result['name'])  # Отформатированное название
print(result['name_short'])  # ООО «Яндекс»
print(result['warnings'])  # Список предупреждений
```

### Форматирование названий

```python
# Для ИП
result = recovery.recover_missing_fields(
    inn="526317984689",
    kpp=None,
    ogrn="304500116000157",
    name="Индивидуальный предприниматель Иванова Анна Петровна"
)

print(result['name'])  # ИП Иванова Анна Петровна
print(result['name_short'])  # Иванова А.П.
```

### Извлечение региона из адреса

```python
address = "404127, Волгоградская область, г. Волгоград, ул. Советская, 10"
region = recovery.extract_region_from_address(address)
print(region)  # "Волгоградская область"
```

### Комплексная валидация и восстановление

```python
entity_data = {
    'name': 'ООО "Профилог"',
    'inn': '3435143874',
    'kpp': '343501001',
    'ogrn': '1223400010570',
    'address': '404127, Волгоградская область, г. Волгоград'
}

result = recovery.validate_and_recover(entity_data)

print(result['is_valid'])  # True/False
print(result['name_short'])  # ООО «Профилог»
print(result['all_warnings'])  # Все предупреждения
print(result['validation_report'].get_summary())  # Детальный отчет
```

## Интеграция с парсером

### Пример использования в main.py

```python
from validators import DataValidator
from data_recovery import DataRecovery

# После парсинга документа
claim_data = parse_documents_with_sliding_window(text)

# Валидация и восстановление данных истца
recovery = DataRecovery()
plaintiff_data = recovery.validate_and_recover({
    'name': claim_data.get('plaintiff_name'),
    'inn': claim_data.get('plaintiff_inn'),
    'kpp': claim_data.get('plaintiff_kpp'),
    'ogrn': claim_data.get('plaintiff_ogrn'),
    'address': claim_data.get('plaintiff_address')
})

if not plaintiff_data['is_valid']:
    # Показать пользователю предупреждения
    for warning in plaintiff_data['all_warnings']:
        print(f"⚠️ {warning}")

# Использовать восстановленные данные
replacements = {
    '{plaintiff_name}': plaintiff_data['name'],
    '{plaintiff_name_short}': plaintiff_data['name_short'],
    '{plaintiff_inn}': plaintiff_data['inn'],
    # ...
}
```

## Типы организаций

```python
from validators import EntityType

EntityType.LEGAL_ENTITY  # Юридическое лицо (ООО, АО, ЗАО, ПАО, ОАО)
EntityType.INDIVIDUAL    # Индивидуальный предприниматель
EntityType.UNKNOWN       # Тип не определен
```

## Статусы валидации

```python
from validators import ValidationStatus

ValidationStatus.VALID              # Поле валидно
ValidationStatus.INVALID            # Поле невалидно
ValidationStatus.NOT_PROVIDED       # Поле не предоставлено
ValidationStatus.INVALID_FORMAT     # Неверный формат
ValidationStatus.INVALID_CHECKSUM   # Неверная контрольная сумма
```

## Примеры сценариев

### Сценарий 1: Все данные валидны

```python
validator = DataValidator()
report = validator.validate_entity(
    inn="7736207543",
    kpp="773601001",
    ogrn="1027700229193"
)

if report.is_valid:
    print("✅ Все данные корректны")
    print(f"Тип: {report.entity_type.value}")
```

### Сценарий 2: Отсутствует КПП у юр.лица

```python
report = validator.validate_entity(
    inn="7736207543",  # 10 цифр = юр.лицо
    kpp=None,          # Отсутствует
    ogrn="1027700229193"
)

print(report.is_valid)  # False
print(report.warnings)  # ['КПП обязателен для юридических лиц']
```

### Сценарий 3: Несогласованные данные

```python
report = validator.validate_entity(
    inn="7736207543",        # ИНН юр.лица
    kpp="773601001",
    ogrn="304500116000157"   # ОГРНИП (ИП)
)

print(report.is_valid)  # False
print(report.warnings)
# ['ИНН указывает на legal_entity, а ОГРН на individual']
```

### Сценарий 4: Автоматическое форматирование

```python
recovery = DataRecovery()

# Неправильные кавычки
result = recovery.recover_missing_fields(
    inn="7736207543",
    kpp="773601001",
    ogrn="1027700229193",
    name='ООО "Яндекс"'  # Двойные кавычки
)

print(result['name'])  # ООО «Яндекс» - правильные кавычки
```

## Тестирование

Запуск тестов:

```bash
python3 test_validators.py
```

Все тесты (24 теста) должны пройти успешно:

```
test_valid_inn_10_digits ... ok
test_valid_inn_12_digits ... ok
test_invalid_inn_wrong_checksum ... ok
test_invalid_inn_wrong_length ... ok
...
OK
```

## Логирование

Модули используют стандартное логирование Python:

```python
import logging

# Настройка уровня логирования
logging.basicConfig(level=logging.DEBUG)

# Теперь вы увидите все действия модулей
validator = DataValidator()
result = validator.validate_inn("7736207543")
# DEBUG: Тип определен по ИНН: legal_entity
# INFO: Определен тип организации: legal_entity
```

## Алгоритмы проверки

### ИНН (10 цифр)

Контрольная сумма:
```
checksum = (sum(digit[i] * coeff[i] for i in 0..8) % 11) % 10
```

Коэффициенты: `[2, 4, 10, 3, 5, 9, 4, 6, 8]`

### ИНН (12 цифр)

Две контрольные цифры с разными коэффициентами:
- Коэффициенты 1: `[7, 2, 4, 10, 3, 5, 9, 4, 6, 8]`
- Коэффициенты 2: `[3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]`

### ОГРН (13 цифр)

```
checksum = (number % 11) % 10
```

где `number` - первые 12 цифр.

### ОГРНИП (15 цифр)

```
checksum = (number % 13) % 10
```

где `number` - первые 14 цифр.

## Расширение

### Добавление нового типа организации

Отредактируйте `validators.py`:

```python
class EntityType(Enum):
    LEGAL_ENTITY = "legal_entity"
    INDIVIDUAL = "individual"
    FOREIGN_COMPANY = "foreign_company"  # Новый тип
    UNKNOWN = "unknown"
```

### Добавление новой эвристики

Отредактируйте `data_recovery.py`:

```python
def _is_foreign_company_by_name(self, name: str) -> bool:
    """Проверка иностранной компании"""
    foreign_markers = ['GmbH', 'Inc', 'Ltd', 'LLC']
    return any(marker in name for marker in foreign_markers)
```

## FAQ

**Q: Что делать, если ИНН не проходит проверку контрольной суммы?**
A: Скорее всего, ИНН введен неверно. Попросите пользователя проверить данные.

**Q: Может ли у ИП быть КПП?**
A: Нет, у ИП нет КПП. Если система обнаружит КПП у ИП, она выдаст предупреждение.

**Q: Как определяется тип организации?**
A: По приоритету: 1) ИНН, 2) ОГРН, 3) название (эвристика).

**Q: Можно ли использовать модули отдельно от бота?**
A: Да, модули полностью независимы и могут использоваться в любом проекте.
