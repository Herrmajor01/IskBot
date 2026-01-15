"""
Модуль для восстановления отсутствующих данных.
Использует эвристики для определения недостающей информации.
"""

import logging
import re
from typing import Dict, Optional, Tuple
from validators import DataValidator, EntityType, get_entity_type

logger = logging.getLogger(__name__)


class DataRecovery:
    """Класс для восстановления отсутствующих данных организаций"""

    def __init__(self):
        self.validator = DataValidator()

    def recover_missing_fields(
        self,
        inn: Optional[str],
        kpp: Optional[str],
        ogrn: Optional[str],
        name: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Восстанавливает отсутствующие поля.

        Args:
            inn: ИНН
            kpp: КПП
            ogrn: ОГРН/ОГРНИП
            name: Название организации

        Returns:
            Словарь с восстановленными данными и предупреждениями
        """
        result = {
            'inn': inn,
            'kpp': kpp,
            'ogrn': ogrn,
            'name': name,
            'entity_type': EntityType.UNKNOWN,
            'recovered': [],
            'warnings': [],
            'confidence': 1.0
        }

        # Шаг 1: Определяем тип организации
        entity_type = self._determine_entity_type(inn, ogrn, name)
        result['entity_type'] = entity_type

        logger.info(f"Определен тип организации: {entity_type.value}")

        # Шаг 2: Проверяем необходимость КПП
        if entity_type == EntityType.INDIVIDUAL:
            if kpp and kpp.strip():
                result['warnings'].append(
                    "У ИП не должно быть КПП, поле будет очищено"
                )
                result['kpp'] = None
                logger.warning("КПП удален для ИП")
        elif entity_type == EntityType.LEGAL_ENTITY:
            if not kpp or not kpp.strip():
                result['warnings'].append(
                    "КПП отсутствует, но обязателен для юридических лиц"
                )
                result['confidence'] *= 0.7
                logger.warning("Отсутствует обязательный КПП для юр.лица")

        # Шаг 3: Форматируем название
        if name:
            formatted_name = self._format_name(name, entity_type)
            if formatted_name != name:
                result['name'] = formatted_name
                result['recovered'].append('name')
                logger.info(f"Название отформатировано: {name} -> {formatted_name}")

        # Шаг 4: Генерируем краткое название для подписи
        if name:
            result['name_short'] = self._generate_short_name(name, entity_type)
            result['recovered'].append('name_short')

        return result

    def _determine_entity_type(
        self,
        inn: Optional[str],
        ogrn: Optional[str],
        name: Optional[str]
    ) -> EntityType:
        """
        Определяет тип организации по доступным данным.

        Приоритет:
        1. ИНН (самый надежный)
        2. ОГРН
        3. Название (эвристика)

        Args:
            inn: ИНН
            ogrn: ОГРН
            name: Название

        Returns:
            EntityType
        """
        # Попытка 1: По ИНН
        if inn:
            entity_type = get_entity_type(inn=inn)
            if entity_type != EntityType.UNKNOWN:
                logger.debug(f"Тип определен по ИНН: {entity_type.value}")
                return entity_type

        # Попытка 2: По ОГРН
        if ogrn:
            entity_type = get_entity_type(ogrn=ogrn)
            if entity_type != EntityType.UNKNOWN:
                logger.debug(f"Тип определен по ОГРН: {entity_type.value}")
                return entity_type

        # Попытка 3: По названию (эвристика)
        if name:
            if self._is_individual_by_name(name):
                logger.debug("Тип определен по названию: ИП")
                return EntityType.INDIVIDUAL
            elif self._is_legal_entity_by_name(name):
                logger.debug("Тип определен по названию: Юр.лицо")
                return EntityType.LEGAL_ENTITY

        logger.warning("Не удалось определить тип организации")
        return EntityType.UNKNOWN

    def _is_individual_by_name(self, name: str) -> bool:
        """Проверяет, является ли название названием ИП"""
        name_lower = name.lower()
        return (
            name_lower.startswith('ип ') or
            name_lower.startswith('и.п. ') or
            name_lower.startswith('и. п. ') or
            'индивидуальный предприниматель' in name_lower
        )

    def _is_legal_entity_by_name(self, name: str) -> bool:
        """Проверяет, является ли название названием юр.лица"""
        name_lower = name.lower()
        legal_markers = [
            'ооо', 'о.о.о.', 'зао', 'з.а.о.', 'пао', 'п.а.о.',
            'оао', 'о.а.о.', 'ао', 'а.о.',
            'общество с ограниченной ответственностью',
            'закрытое акционерное общество',
            'публичное акционерное общество',
            'открытое акционерное общество',
            'акционерное общество'
        ]
        return any(marker in name_lower for marker in legal_markers)

    def _format_name(self, name: str, entity_type: EntityType) -> str:
        """
        Форматирует название организации.

        Args:
            name: Исходное название
            entity_type: Тип организации

        Returns:
            Отформатированное название
        """
        name = name.strip()

        if entity_type == EntityType.INDIVIDUAL:
            # Для ИП: "ИП Фамилия Имя Отчество"
            if name.lower().startswith('индивидуальный предприниматель'):
                name = 'ИП' + name[len('индивидуальный предприниматель'):]
            return name.strip()

        elif entity_type == EntityType.LEGAL_ENTITY:
            # Для юр.лиц: убираем лишние пробелы, нормализуем кавычки
            # Заменяем " на «»
            name = re.sub(r'"([^"]+)"', r'«\1»', name)
            return name

        return name

    def _generate_short_name(self, name: str, entity_type: EntityType) -> str:
        """
        Генерирует краткое название для подписи.

        Args:
            name: Полное название
            entity_type: Тип организации

        Returns:
            Краткое название
        """
        if entity_type == EntityType.INDIVIDUAL:
            return self._generate_ip_short_name(name)
        elif entity_type == EntityType.LEGAL_ENTITY:
            return self._generate_legal_short_name(name)
        else:
            return name

    def _generate_ip_short_name(self, name: str) -> str:
        """
        Генерирует краткое имя для ИП: "Фамилия И.О."

        Args:
            name: Полное имя ИП

        Returns:
            Краткое имя
        """
        # Убираем "ИП" и "Индивидуальный предприниматель"
        name = re.sub(
            r'^(ИП|И\.П\.|И\. П\.|Индивидуальный предприниматель)\s*',
            '',
            name,
            flags=re.IGNORECASE
        ).strip()

        # Разбиваем на части
        parts = name.split()

        if len(parts) >= 2:
            # Фамилия + инициалы
            surname = parts[0]
            initials = '.'.join([p[0].upper() for p in parts[1:3]]) + '.'
            return f"{surname} {initials}"
        else:
            return name

    def _generate_legal_short_name(self, name: str) -> str:
        """
        Генерирует краткое название для юр.лица: "ООО «Название»"

        Args:
            name: Полное название

        Returns:
            Краткое название
        """
        # Ищем сокращение и название в кавычках
        match = re.search(
            r'(ООО|ЗАО|ПАО|ОАО|АО)\s*[«"]([^»"]+)[»"]',
            name,
            re.IGNORECASE
        )
        if match:
            org_type = match.group(1).upper()
            org_name = match.group(2)
            return f'{org_type} «{org_name}»'

        # Если не нашли - пытаемся извлечь из полного названия
        replacements = {
            'Общество с ограниченной ответственностью': 'ООО',
            'Закрытое акционерное общество': 'ЗАО',
            'Публичное акционерное общество': 'ПАО',
            'Открытое акционерное общество': 'ОАО',
            'Акционерное общество': 'АО'
        }

        for full, short in replacements.items():
            if full in name:
                company_name = name.replace(full, '').strip()
                # Добавляем кавычки если их нет
                if '«' not in company_name:
                    company_name = f'«{company_name}»'
                return f'{short} {company_name}'

        return name

    def extract_region_from_address(self, address: str) -> Optional[str]:
        """
        Извлекает регион из адреса.

        Args:
            address: Полный адрес

        Returns:
            Название региона или None
        """
        if not address:
            return None

        # Паттерны для регионов
        patterns = [
            r'([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)?)\s+област[ьи]',
            r'([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)?)\s+кра[йя]',
            r'Республика\s+([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)?)',
            r'г\.\s*(Москва|Санкт-Петербург)',
        ]

        for pattern in patterns:
            match = re.search(pattern, address)
            if match:
                region = match.group(1) if 'Республика' in pattern else match.group(0)
                logger.debug(f"Извлечен регион из адреса: {region}")
                return region.strip()

        # Если ничего не нашли, пытаемся найти по ключевым словам
        if 'Москва' in address:
            return 'Москва'
        elif 'Санкт-Петербург' in address or 'С-Петербург' in address:
            return 'Санкт-Петербург'

        logger.debug("Не удалось извлечь регион из адреса")
        return None

    def validate_and_recover(
        self,
        entity_data: Dict[str, any]
    ) -> Dict[str, any]:
        """
        Валидирует и восстанавливает данные организации.

        Args:
            entity_data: Словарь с данными организации
                {
                    'name': str,
                    'inn': str,
                    'kpp': str,
                    'ogrn': str,
                    'address': str
                }

        Returns:
            Обновленный словарь с восстановленными данными
        """
        # Валидация
        report = self.validator.validate_entity(
            inn=entity_data.get('inn'),
            kpp=entity_data.get('kpp'),
            ogrn=entity_data.get('ogrn')
        )

        # Восстановление
        recovered = self.recover_missing_fields(
            inn=entity_data.get('inn'),
            kpp=entity_data.get('kpp'),
            ogrn=entity_data.get('ogrn'),
            name=entity_data.get('name')
        )

        # Объединение результатов
        result = {
            **entity_data,
            **recovered,
            'validation_report': report,
            'is_valid': report.is_valid,
            'all_warnings': report.warnings + recovered['warnings']
        }

        logger.info(
            f"Валидация и восстановление завершены. "
            f"Валидность: {result['is_valid']}, "
            f"Предупреждений: {len(result['all_warnings'])}"
        )

        return result


# Удобная функция для быстрого использования
def quick_recover(
    inn: Optional[str] = None,
    kpp: Optional[str] = None,
    ogrn: Optional[str] = None,
    name: Optional[str] = None
) -> Dict[str, any]:
    """
    Быстрое восстановление данных организации.

    Args:
        inn: ИНН
        kpp: КПП
        ogrn: ОГРН
        name: Название

    Returns:
        Словарь с восстановленными данными
    """
    recovery = DataRecovery()
    return recovery.recover_missing_fields(inn, kpp, ogrn, name)
