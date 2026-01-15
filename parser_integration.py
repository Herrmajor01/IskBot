#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Интеграционный модуль для объединения старого и нового парсеров.
Обеспечивает обратную совместимость и постепенный переход.
"""

import logging
from typing import Dict, List, Optional

from data_recovery import DataRecovery
from enhanced_parser import EnhancedParser, ParsingResult
from sliding_window_parser import (SlidingWindowParser,
                                     parse_documents_with_sliding_window)
from validators import DataValidator

logger = logging.getLogger(__name__)


class IntegratedParser:
    """
    Интегрированный парсер, объединяющий старый и новый подходы.

    Стратегия работы:
    1. Запускает оба парсера параллельно
    2. Объединяет результаты, выбирая лучшие данные
    3. Применяет валидацию и восстановление
    4. Возвращает максимально полный результат
    """

    def __init__(self):
        self.legacy_parser = SlidingWindowParser()
        self.enhanced_parser = EnhancedParser()
        self.validator = DataValidator()
        self.recovery = DataRecovery()

    def parse(self, text: str, use_legacy_only: bool = False) -> Dict[str, any]:
        """
        Основной метод парсинга.

        Args:
            text: Текст претензии
            use_legacy_only: Использовать только старый парсер (для обратной совместимости)

        Returns:
            Словарь с извлеченными данными
        """
        if use_legacy_only:
            logger.info("Используется только legacy парсер")
            return parse_documents_with_sliding_window(text)

        logger.info("=" * 70)
        logger.info("Начало интегрированного парсинга")
        logger.info("=" * 70)

        # Запускаем legacy парсер
        logger.info("Шаг 1: Запуск legacy парсера")
        legacy_data = self._safe_parse_legacy(text)
        logger.info(f"Legacy парсер извлек {len(legacy_data)} полей")

        # Запускаем enhanced парсер
        logger.info("Шаг 2: Запуск enhanced парсера")
        enhanced_result = self._safe_parse_enhanced(text)
        logger.info(
            f"Enhanced парсер извлек {len(enhanced_result.data)} полей, "
            f"confidence: {enhanced_result.confidence:.2f}"
        )

        # Объединяем результаты
        logger.info("Шаг 3: Объединение результатов")
        merged_data = self._merge_results(legacy_data, enhanced_result)
        logger.info(f"После объединения: {len(merged_data)} полей")

        # Применяем финальную валидацию и восстановление
        logger.info("Шаг 4: Финальная валидация и восстановление")
        final_data = self._apply_final_processing(merged_data)

        logger.info("=" * 70)
        logger.info(f"Парсинг завершен. Итоговых полей: {len(final_data)}")
        logger.info("=" * 70)

        return final_data

    def _safe_parse_legacy(self, text: str) -> Dict[str, any]:
        """
        Безопасно запускает legacy парсер.

        Args:
            text: Текст документа

        Returns:
            Результат legacy парсинга или пустой словарь при ошибке
        """
        try:
            return parse_documents_with_sliding_window(text)
        except Exception as e:
            logger.error(f"Ошибка в legacy парсере: {e}", exc_info=True)
            return {}

    def _safe_parse_enhanced(self, text: str) -> ParsingResult:
        """
        Безопасно запускает enhanced парсер.

        Args:
            text: Текст документа

        Returns:
            ParsingResult или пустой результат при ошибке
        """
        try:
            return self.enhanced_parser.parse_with_strategy(text)
        except Exception as e:
            logger.error(f"Ошибка в enhanced парсере: {e}", exc_info=True)
            return ParsingResult(data={}, confidence=0.0)

    def _merge_results(
        self,
        legacy_data: Dict[str, any],
        enhanced_result: ParsingResult
    ) -> Dict[str, any]:
        """
        Объединяет результаты двух парсеров.

        Стратегия выбора:
        1. Для реквизитов (ИНН/КПП/ОГРН) - приоритет enhanced (валидированные данные)
        2. Для документов - приоритет legacy (проверенный функционал)
        3. Для названий организаций - приоритет enhanced (восстановленные данные)
        4. Для финансовых данных - приоритет legacy (проверенные паттерны)

        Args:
            legacy_data: Результат legacy парсера
            enhanced_result: Результат enhanced парсера

        Returns:
            Объединенный словарь данных
        """
        merged = legacy_data.copy()

        # Поля с приоритетом enhanced парсера (валидированные данные)
        enhanced_priority_fields = [
            'defendant_inn', 'defendant_kpp', 'defendant_ogrn',
            'defendant_entity_type', 'defendant_name', 'defendant_name_short',
            'plaintiff_inn', 'plaintiff_kpp', 'plaintiff_ogrn',
            'plaintiff_entity_type', 'plaintiff_name', 'plaintiff_name_short',
        ]

        # Применяем данные из enhanced парсера
        for field in enhanced_priority_fields:
            if field in enhanced_result.data:
                value = enhanced_result.data[field]
                # Проверяем, что значение не пустое
                if value and (not isinstance(value, str) or value.strip()):
                    merged[field] = value
                    logger.debug(f"Использованы данные enhanced для {field}: {value}")

        # Поля с приоритетом legacy парсера (проверенный функционал)
        legacy_priority_fields = [
            'contract_applications', 'upd_blocks', 'invoice_blocks',
            'cargo_docs', 'postal_block', 'debt', 'legal_fees',
            'total_interest', 'payment_days', 'payment_terms',
            'signatory', 'attachments',
        ]

        # Данные legacy уже в merged, дополняем только если их там нет
        for field in legacy_priority_fields:
            if field not in merged or not merged[field]:
                if field in enhanced_result.data:
                    value = enhanced_result.data[field]
                    if value and (not isinstance(value, str) or value.strip()):
                        merged[field] = value
                        logger.debug(f"Дополнены данные из enhanced для {field}: {value}")

        return merged

    def _apply_final_processing(self, data: Dict[str, any]) -> Dict[str, any]:
        """
        Применяет финальную обработку: валидацию и восстановление.

        Args:
            data: Объединенные данные

        Returns:
            Обработанные данные
        """
        result = data.copy()

        # Обработка ответчика
        if 'defendant_inn' in result:
            result = self._process_entity(result, 'defendant')

        # Обработка истца
        if 'plaintiff_inn' in result:
            result = self._process_entity(result, 'plaintiff')

        return result

    def _process_entity(self, data: Dict[str, any], entity: str) -> Dict[str, any]:
        """
        Обрабатывает данные организации (валидация + восстановление).

        Args:
            data: Данные документа
            entity: 'plaintiff' или 'defendant'

        Returns:
            Обновленные данные
        """
        prefix = f'{entity}_'

        inn = data.get(f'{prefix}inn')
        kpp = data.get(f'{prefix}kpp')
        ogrn = data.get(f'{prefix}ogrn')
        name = data.get(f'{prefix}name')

        if not inn:
            logger.warning(f"Нет ИНН для {entity}, пропускаем обработку")
            return data

        # Валидация
        report = self.validator.validate_entity(inn, kpp, ogrn)

        if not report.is_valid:
            logger.warning(f"Данные {entity} не прошли валидацию")
            for warning in report.warnings:
                logger.warning(f"  - {warning}")
        else:
            logger.info(f"Данные {entity} валидны: {report.entity_type.value}")

        # Восстановление
        recovered = self.recovery.recover_missing_fields(inn, kpp, ogrn, name)

        # Применяем восстановленные данные
        if recovered['kpp'] is not None:
            data[f'{prefix}kpp'] = recovered['kpp']
            logger.debug(f"Применен восстановленный КПП для {entity}")

        if recovered['name']:
            # Применяем только если текущее название пустое или отсутствует
            if not data.get(f'{prefix}name') or data[f'{prefix}name'] == 'Не указано':
                data[f'{prefix}name'] = recovered['name']
                logger.debug(f"Применено восстановленное название для {entity}")

        if recovered.get('name_short'):
            data[f'{prefix}name_short'] = recovered['name_short']
            logger.debug(f"Применено краткое название для {entity}")

        # Сохраняем тип организации
        data[f'{prefix}entity_type'] = report.entity_type.value

        return data

    def get_parsing_report(self, text: str) -> Dict[str, any]:
        """
        Возвращает детальный отчет о парсинге.

        Args:
            text: Текст претензии

        Returns:
            Отчет с метаинформацией
        """
        # Запускаем оба парсера
        legacy_data = self._safe_parse_legacy(text)
        enhanced_result = self._safe_parse_enhanced(text)

        # Формируем отчет
        report = {
            'legacy': {
                'fields_extracted': len(legacy_data),
                'data': legacy_data,
            },
            'enhanced': {
                'fields_extracted': len(enhanced_result.data),
                'confidence': enhanced_result.confidence,
                'warnings': enhanced_result.warnings,
                'errors': enhanced_result.errors,
                'extraction_methods': enhanced_result.extraction_methods,
                'data': enhanced_result.data,
            },
            'merged': self._merge_results(legacy_data, enhanced_result),
        }

        return report


# Удобная функция для использования в main.py
def parse_document_integrated(
    text: str,
    use_legacy_only: bool = False
) -> Dict[str, any]:
    """
    Парсит документ с использованием интегрированного парсера.

    Args:
        text: Текст претензии
        use_legacy_only: Использовать только старый парсер

    Returns:
        Словарь с извлеченными данными
    """
    parser = IntegratedParser()
    return parser.parse(text, use_legacy_only=use_legacy_only)


# Функция для получения детального отчета
def get_parsing_report(text: str) -> Dict[str, any]:
    """
    Получает детальный отчет о парсинге.

    Args:
        text: Текст претензии

    Returns:
        Отчет с метаинформацией о парсинге
    """
    parser = IntegratedParser()
    return parser.get_parsing_report(text)
