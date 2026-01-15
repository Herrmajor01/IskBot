#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный парсер с многоуровневой стратегией извлечения данных.
Интегрирован с модулями валидации и восстановления данных.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from data_recovery import DataRecovery
from validators import DataValidator, EntityType

logger = logging.getLogger(__name__)


@dataclass
class ParsingResult:
    """Результат парсинга с метаинформацией"""
    data: Dict[str, any]
    confidence: float = 1.0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    extraction_methods: Dict[str, str] = field(default_factory=dict)

    def add_warning(self, message: str):
        """Добавляет предупреждение"""
        self.warnings.append(message)
        logger.warning(f"Parser warning: {message}")

    def add_error(self, message: str):
        """Добавляет ошибку"""
        self.errors.append(message)
        logger.error(f"Parser error: {message}")

    def set_extraction_method(self, field: str, method: str):
        """Сохраняет метод извлечения для поля"""
        self.extraction_methods[field] = method


class EnhancedParser:
    """
    Улучшенный парсер с многоуровневой стратегией извлечения.

    Уровни извлечения:
    1. Прямое извлечение (по точным паттернам)
    2. Контекстное извлечение (по окружающему тексту)
    3. Эвристическое восстановление (через DataRecovery)
    4. Валидация и коррекция (через DataValidator)
    """

    def __init__(self):
        self.validator = DataValidator()
        self.recovery = DataRecovery()

        # Паттерны для ИНН/КПП/ОГРН с различными вариантами написания
        self.inn_patterns = [
            r'ИНН\s*[:\s]*(\d{10,12})',
            r'ИНН:\s*(\d{10,12})',
            r'инн\s+(\d{10,12})',
            r'ИНН\s+ответчика\s*[:\s]*(\d{10,12})',
        ]

        self.kpp_patterns = [
            r'КПП\s*[:\s]*(\d{9})',
            r'КПП:\s*(\d{9})',
            r'кпп\s+(\d{9})',
        ]

        self.ogrn_patterns = [
            r'ОГРН(?:ИП)?\s*[:\s]*(\d{13,15})',
            r'ОГРН:\s*(\d{13,15})',
            r'огрн\s+(\d{13,15})',
            r'ОГРНИП\s*[:\s]*(\d{15})',
        ]

        # Паттерны для организаций
        self.legal_entity_patterns = [
            r'(?:Обществу|Общество)\s+с\s+ограниченной\s+ответственностью\s*[«"]([^»"]+)[»"]',
            r'ООО\s*[«"]([^»"]+)[»"]',
            r'Общество\s+с\s+ограниченной\s+ответственностью\s+([^\n]+)',
        ]

        self.individual_patterns = [
            r'(?:ИП|Индивидуальный\s+предприниматель)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})',
            r'Индивидуальному\s+предпринимателю\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})',
        ]

        # Паттерны для адресов
        self.address_patterns = [
            r'(\d{6},\s*[^,\n]+(?:,[^,\n]+){2,})',
            r'адрес\s*[:\s]*([^,\n]+(?:,[^,\n]+){2,})',
            r'место\s+нахождения\s*[:\s]*([^,\n]+(?:,[^,\n]+){2,})',
        ]

    def parse_with_strategy(self, text: str) -> ParsingResult:
        """
        Основной метод парсинга с многоуровневой стратегией.

        Args:
            text: Текст претензии

        Returns:
            ParsingResult с извлеченными данными
        """
        result = ParsingResult(data={})

        logger.info("=" * 60)
        logger.info("Начало многоуровневого парсинга")
        logger.info("=" * 60)

        # Уровень 1: Прямое извлечение
        self._level1_direct_extraction(text, result)

        # Уровень 2: Контекстное извлечение
        self._level2_contextual_extraction(text, result)

        # Уровень 3: Валидация извлеченных данных
        self._level3_validation(result)

        # Уровень 4: Восстановление недостающих данных
        self._level4_recovery(result)

        # Финальная проверка
        self._final_check(result)

        logger.info("=" * 60)
        logger.info(f"Парсинг завершен. Confidence: {result.confidence:.2f}")
        logger.info(f"Warnings: {len(result.warnings)}, Errors: {len(result.errors)}")
        logger.info("=" * 60)

        return result

    def _level1_direct_extraction(self, text: str, result: ParsingResult):
        """
        Уровень 1: Прямое извлечение данных по паттернам.
        """
        logger.info("Уровень 1: Прямое извлечение данных")

        # Извлечение реквизитов ответчика
        defendant_data = self._extract_entity_data(text, 'defendant')
        if defendant_data:
            result.data.update(defendant_data)
            result.set_extraction_method('defendant', 'direct')
            logger.info(f"Извлечены данные ответчика: {list(defendant_data.keys())}")

        # Извлечение реквизитов истца
        plaintiff_data = self._extract_entity_data(text, 'plaintiff')
        if plaintiff_data:
            result.data.update(plaintiff_data)
            result.set_extraction_method('plaintiff', 'direct')
            logger.info(f"Извлечены данные истца: {list(plaintiff_data.keys())}")

        # Извлечение финансовых данных
        financial_data = self._extract_financial_data(text)
        if financial_data:
            result.data.update(financial_data)
            result.set_extraction_method('financial', 'direct')
            logger.info(f"Извлечены финансовые данные: {list(financial_data.keys())}")

    def _level2_contextual_extraction(self, text: str, result: ParsingResult):
        """
        Уровень 2: Контекстное извлечение с учетом структуры документа.
        """
        logger.info("Уровень 2: Контекстное извлечение")

        # Ищем блок "ТРЕБОВАНИЕ" или "ПРЕТЕНЗИЯ"
        requirement_match = re.search(r'(ТРЕБОВАНИЕ|ПРЕТЕНЗИЯ)', text, re.IGNORECASE)
        if not requirement_match:
            result.add_warning("Не найден блок ТРЕБОВАНИЕ/ПРЕТЕНЗИЯ")
            return

        header_text = text[:requirement_match.start()].strip()

        # Извлечение данных из заголовка
        self._extract_from_header(header_text, result)

    def _extract_from_header(self, header_text: str, result: ParsingResult):
        """
        Извлекает данные из заголовка документа (до слова ТРЕБОВАНИЕ).
        """
        lines = header_text.split('\n')
        current_section = None

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Определение секции
            if line.startswith('Обществу'):
                current_section = 'defendant'
                logger.debug(f"Найдена секция ответчика: {line}")
            elif line.startswith('от'):
                current_section = 'plaintiff'
                logger.debug(f"Найдена секция истца: {line}")

            # Извлечение реквизитов в зависимости от секции
            if current_section == 'defendant':
                self._extract_defendant_from_line(line, result)
            elif current_section == 'plaintiff':
                self._extract_plaintiff_from_line(line, result)

    def _extract_defendant_from_line(self, line: str, result: ParsingResult):
        """Извлекает данные ответчика из строки"""
        # Извлечение ИНН
        if 'defendant_inn' not in result.data:
            for pattern in self.inn_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['defendant_inn'] = match.group(1)
                    logger.debug(f"Извлечен ИНН ответчика: {match.group(1)}")
                    break

        # Извлечение КПП
        if 'defendant_kpp' not in result.data:
            for pattern in self.kpp_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['defendant_kpp'] = match.group(1)
                    logger.debug(f"Извлечен КПП ответчика: {match.group(1)}")
                    break

        # Извлечение ОГРН
        if 'defendant_ogrn' not in result.data:
            for pattern in self.ogrn_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['defendant_ogrn'] = match.group(1)
                    logger.debug(f"Извлечен ОГРН ответчика: {match.group(1)}")
                    break

        # Извлечение адреса
        if 'defendant_address' not in result.data:
            if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                result.data['defendant_address'] = line
                logger.debug(f"Извлечен адрес ответчика: {line}")

    def _extract_plaintiff_from_line(self, line: str, result: ParsingResult):
        """Извлекает данные истца из строки"""
        # Аналогично _extract_defendant_from_line, но для истца
        if 'plaintiff_inn' not in result.data:
            for pattern in self.inn_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['plaintiff_inn'] = match.group(1)
                    logger.debug(f"Извлечен ИНН истца: {match.group(1)}")
                    break

        if 'plaintiff_kpp' not in result.data:
            for pattern in self.kpp_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['plaintiff_kpp'] = match.group(1)
                    logger.debug(f"Извлечен КПП истца: {match.group(1)}")
                    break

        if 'plaintiff_ogrn' not in result.data:
            for pattern in self.ogrn_patterns:
                match = re.search(pattern, line)
                if match:
                    result.data['plaintiff_ogrn'] = match.group(1)
                    logger.debug(f"Извлечен ОГРН истца: {match.group(1)}")
                    break

        if 'plaintiff_address' not in result.data:
            if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                result.data['plaintiff_address'] = line
                logger.debug(f"Извлечен адрес истца: {line}")

    def _extract_entity_data(self, text: str, entity: str) -> Dict[str, str]:
        """
        Извлекает данные организации (истца или ответчика).

        Args:
            text: Текст документа
            entity: 'plaintiff' или 'defendant'

        Returns:
            Словарь с данными организации
        """
        data = {}
        prefix = f'{entity}_'

        # Извлечение ИНН
        for pattern in self.inn_patterns:
            match = re.search(pattern, text)
            if match:
                data[f'{prefix}inn'] = match.group(1)
                break

        # Извлечение КПП
        for pattern in self.kpp_patterns:
            match = re.search(pattern, text)
            if match:
                data[f'{prefix}kpp'] = match.group(1)
                break

        # Извлечение ОГРН
        for pattern in self.ogrn_patterns:
            match = re.search(pattern, text)
            if match:
                data[f'{prefix}ogrn'] = match.group(1)
                break

        return data

    def _extract_financial_data(self, text: str) -> Dict[str, str]:
        """
        Извлекает финансовые данные из текста.

        Returns:
            Словарь с финансовыми данными
        """
        data = {}

        # Извлечение суммы задолженности
        debt_patterns = [
            r'Стоимость услуг по договор[^0-9]*составила\s*([0-9\s,]+)\s*рубл',
            r'размер задолженности[^0-9]*составляет\s*([0-9\s,]+)\s*рубл',
            r'задолженность[^0-9]*в размере\s*([0-9\s,]+)\s*рубл',
        ]

        for pattern in debt_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                debt_str = match.group(1).replace(' ', '').replace(',', '.')
                try:
                    debt = float(debt_str)
                    data['debt'] = f"{debt:,.0f}".replace(',', ' ')
                    logger.debug(f"Извлечена сумма долга: {data['debt']}")
                    break
                except ValueError:
                    continue

        return data

    def _level3_validation(self, result: ParsingResult):
        """
        Уровень 3: Валидация извлеченных данных.
        """
        logger.info("Уровень 3: Валидация данных")

        # Валидация ответчика
        if any(key.startswith('defendant_') for key in result.data.keys()):
            self._validate_entity(result, 'defendant')

        # Валидация истца
        if any(key.startswith('plaintiff_') for key in result.data.keys()):
            self._validate_entity(result, 'plaintiff')

    def _validate_entity(self, result: ParsingResult, entity: str):
        """
        Валидирует данные организации (истца или ответчика).

        Args:
            result: Результат парсинга
            entity: 'plaintiff' или 'defendant'
        """
        prefix = f'{entity}_'
        inn = result.data.get(f'{prefix}inn')
        kpp = result.data.get(f'{prefix}kpp')
        ogrn = result.data.get(f'{prefix}ogrn')

        if not inn:
            result.add_warning(f"ИНН {entity} не найден")
            result.confidence *= 0.7
            return

        # Валидация
        report = self.validator.validate_entity(inn, kpp, ogrn)

        if not report.is_valid:
            result.add_error(f"Данные {entity} не прошли валидацию")
            result.confidence *= 0.5

            # Добавляем детали ошибок
            if not report.inn.is_valid():
                result.add_error(f"ИНН {entity}: {report.inn.error_message}")
            if not report.kpp.is_valid() and report.entity_type == EntityType.LEGAL_ENTITY:
                result.add_error(f"КПП {entity}: {report.kpp.error_message}")
            if not report.ogrn.is_valid():
                result.add_error(f"ОГРН {entity}: {report.ogrn.error_message}")

        # Добавляем предупреждения из валидации
        for warning in report.warnings:
            result.add_warning(f"{entity}: {warning}")

        # Сохраняем тип организации
        result.data[f'{prefix}entity_type'] = report.entity_type.value
        logger.info(f"Тип {entity}: {report.entity_type.value}")

    def _level4_recovery(self, result: ParsingResult):
        """
        Уровень 4: Восстановление недостающих данных.
        """
        logger.info("Уровень 4: Восстановление данных")

        # Восстановление данных ответчика
        if any(key.startswith('defendant_') for key in result.data.keys()):
            self._recover_entity_data(result, 'defendant')

        # Восстановление данных истца
        if any(key.startswith('plaintiff_') for key in result.data.keys()):
            self._recover_entity_data(result, 'plaintiff')

    def _recover_entity_data(self, result: ParsingResult, entity: str):
        """
        Восстанавливает недостающие данные организации.

        Args:
            result: Результат парсинга
            entity: 'plaintiff' или 'defendant'
        """
        prefix = f'{entity}_'

        inn = result.data.get(f'{prefix}inn')
        kpp = result.data.get(f'{prefix}kpp')
        ogrn = result.data.get(f'{prefix}ogrn')
        name = result.data.get(f'{prefix}name')

        # Пытаемся восстановить данные
        recovered = self.recovery.recover_missing_fields(inn, kpp, ogrn, name)

        # Применяем восстановленные данные
        if recovered['kpp'] is not None and f'{prefix}kpp' not in result.data:
            result.data[f'{prefix}kpp'] = recovered['kpp']
            logger.info(f"Восстановлен КПП {entity}: {recovered['kpp']}")

        if recovered['name'] and f'{prefix}name' not in result.data:
            result.data[f'{prefix}name'] = recovered['name']
            logger.info(f"Восстановлено название {entity}: {recovered['name']}")
            result.set_extraction_method(f'{prefix}name', 'recovered')

        if recovered.get('name_short') and f'{prefix}name_short' not in result.data:
            result.data[f'{prefix}name_short'] = recovered['name_short']
            logger.info(f"Восстановлено краткое название {entity}: {recovered['name_short']}")

        # Добавляем предупреждения из восстановления
        for warning in recovered['warnings']:
            result.add_warning(f"{entity}: {warning}")

        # Корректируем уверенность
        result.confidence *= recovered['confidence']

    def _final_check(self, result: ParsingResult):
        """
        Финальная проверка полноты данных.
        """
        logger.info("Финальная проверка данных")

        required_fields = [
            'defendant_inn',
            'defendant_name',
            'plaintiff_inn',
            'plaintiff_name',
        ]

        missing_fields = [field for field in required_fields if field not in result.data]

        if missing_fields:
            result.add_error(f"Отсутствуют обязательные поля: {', '.join(missing_fields)}")
            result.confidence *= 0.6
            logger.warning(f"Не найдены обязательные поля: {missing_fields}")

        # Проверяем финансовые данные
        if 'debt' not in result.data:
            result.add_warning("Не найдена сумма задолженности")
            result.confidence *= 0.8


def parse_with_enhanced_strategy(text: str) -> Tuple[Dict[str, any], ParsingResult]:
    """
    Удобная функция для парсинга с использованием улучшенной стратегии.

    Args:
        text: Текст претензии

    Returns:
        Кортеж (данные, результат парсинга с метаинформацией)
    """
    parser = EnhancedParser()
    result = parser.parse_with_strategy(text)
    return result.data, result
