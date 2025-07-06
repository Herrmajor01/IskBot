#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Исправленный парсер документов с использованием скользящего окна
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DocumentBlock:
    """Структура для хранения блока документа"""
    header: str  # Оригинальный заголовок
    pairs: List[Tuple[Optional[str], Optional[str]]]  # [(номер, дата), ...]
    clarifications: List[str]
    raw_text: str


class SlidingWindowParser:
    """
    Исправленный парсер документов с использованием скользящего окна
    """

    def __init__(self, window_size: int = 15):
        """
        Инициализация парсера

        Args:
            window_size: Размер скользящего окна (увеличен до 15 токенов)
        """
        self.window_size = window_size
        self.tokens = []
        self.current_pos = 0

        # Словари документов (все падежи и числа)
        self.document_headers = {
            'contract_applications': [
                'заявка', 'заявки', 'заявку', 'заявкой', 'заявок', 'заявкам', 'заявками', 'заявках',
                'заявкой на перевозку груза', 'заявка на перевозку груза',
                'договор-заявка', 'договор-заявки', 'договор-заявку', 'договор-заявкой',
                'договоры-заявки', 'договоров-заявок', 'договорам-заявкам', 'договорами-заявками',
                'транспортная заявка', 'транспортные заявки', 'транспортной заявкой',
                'приложение к заявке', 'приложение к договору-заявке'
            ],
            'invoice_blocks': [
                'счет', 'счета', 'счету', 'счетом', 'счете', 'счетов', 'счетам', 'счетами', 'счетах',
                'счет на оплату', 'счета на оплату', 'счету на оплату', 'счетом на оплату',
                'счет-фактура', 'счета-фактуры', 'счет-фактуру', 'счет-фактуре', 'счет-фактурой'
            ],
            'upd_blocks': [
                'упд', 'универсальный передаточный документ', 'универсального передаточного документа',
                'универсальному передаточному документу', 'универсальным передаточным документом',
                'упд выполненных работ', 'упд оказанных услуг',
                'акт выполненных работ', 'акта выполненных работ', 'акту выполненных работ',
                'актом выполненных работ', 'акте выполненных работ', 'акты выполненных работ',
                'актов выполненных работ', 'актам выполненных работ', 'актами выполненных работ',
                'акт оказанных услуг', 'акта оказанных услуг', 'акту оказанных услуг',
                'актом оказанных услуг', 'акте оказанных услуг', 'акты оказанных услуг',
                'актов оказанных услуг', 'актам оказанных услуг', 'актами оказанных услуг'
            ],
            'cargo_docs': [
                'накладная', 'накладные', 'накладной', 'накладную', 'накладными', 'накладных',
                'товарная накладная', 'товарные накладные', 'товарной накладной', 'товарную накладную',
                'товарна-транспортна накладная', 'товарно-транспортная накладная',
                'транспортная накладная', 'транспортные накладные', 'транспортной накладной',
                'товарно-транспортные накладные',
                'комплект сопроводительных документов', 'комплекты сопроводительных документов',
                'комплектом сопроводительных документов', 'комплекта сопроводительных документов',
                'коносамент', 'коносаменты', 'коносамента', 'коносаментов',
                'экспедиторская расписка', 'экспедиторские расписки'
            ],
            'postal_block': [
                'почтовое уведомление', 'почтовые уведомления', 'почтового уведомления',
                'почтовым уведомлением', 'почтовыми уведомлениями',
                'курьерское уведомление', 'курьерские уведомления', 'курьерского уведомления',
                'почтовая квитанция', 'почтовые квитанции', 'почтовой квитанции'
            ]
        }

        # Улучшенные паттерны для поиска (поддержка "No" и "№")
        self.number_date_pattern = re.compile(
            r'(?:№|No|N)\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})(?:\s*г?\.?|)', re.IGNORECASE)
        self.number_pattern = re.compile(r'(?:№|No|N)\s*(\d+)', re.IGNORECASE)
        self.date_pattern = re.compile(r'(\d{2}\.\d{2}\.\d{4})(?:\s*г?\.?|)')
        self.clarification_pattern = re.compile(
            r'(г\.|оригинал|отправке|получении|подписан|подписана)', re.IGNORECASE)

        # Нормализация заголовков
        self.normalization_map = {
            'contract_applications': 'заявка',
            'invoice_blocks': 'счет на оплату',
            'upd_blocks': 'УПД',
            'cargo_docs': 'накладная',
            'postal_block': 'почтовое уведомление'
        }

    def tokenize_text(self, text: str) -> List[str]:
        """
        Улучшенная токенизация текста
        """
        # Нормализация символов номера
        text = re.sub(r'\bNo\b', '№', text)
        text = re.sub(r'\bN\b', '№', text)

        # Защищаем даты от разбиения
        date_placeholders = {}
        date_counter = 0

        def replace_date(match):
            nonlocal date_counter
            placeholder = f"__DATE_{date_counter}__"
            date_placeholders[placeholder] = match.group(0)
            date_counter += 1
            return placeholder

        # Защищаем даты в различных форматах
        text = re.sub(r'\d{2}\.\d{2}\.\d{4}(?:\s*г?\.?)?', replace_date, text)

        # Защищаем номера документов
        text = re.sub(r'№\s*\d+', lambda m: m.group(0).replace(' ', '_'), text)

        # Разбиваем на токены
        tokens = re.findall(r'\b\w+\b|[^\w\s]', text)
        tokens = [token.strip() for token in tokens if token.strip()]

        # Восстанавливаем даты и номера
        result = []
        for token in tokens:
            if token in date_placeholders:
                result.append(date_placeholders[token])
            elif '_' in token and '№' in token:
                result.append(token.replace('_', ' '))
            else:
                result.append(token)

        return result

    def find_document_patterns(self, text: str) -> List[Dict]:
        """
        Находит все документы в тексте с использованием улучшенных паттернов
        """
        results = []

        # Паттерны для разных типов документов: (паттерн, тип, заголовок)
        patterns = [
            # Заявки на перевозку груза
            (r'заявк[а-я]*\s+на\s+перевозку\s+груза\s+((?:(?:№|No|N)\s*\d+\s+от\s+\d{2}\.\d{2}\.\d{4}[^,]*[,;]?\s*)+)',
             'contract_applications', 'Заявка на перевозку груза'),
            # Счета на оплату
            (r'счет[а-я]*\s+на\s+оплату\s+((?:(?:№|No|N)\s*\d+\s+от\s+\d{2}\.\d{2}\.\d{4}[^,]*[,;]?\s*)+)',
             'invoice_blocks', 'Счет на оплату'),
            # Акты выполненных работ
            (r'акт[а-я]*\s+выполненных\s+работ\s+((?:(?:№|No|N)\s*\d+\s+от\s+\d{2}\.\d{2}\.\d{4}[^,]*[,;]?\s*)+)', 'upd_blocks', 'Акт выполненных работ'),
            # Договоры
            (r'договор[а-я]*\s+((?:(?:№|No|N)\s*\d+\s+от\s+\d{2}\.\d{2}\.\d{4}[^,]*[,;]?\s*)+)', 'contracts', 'Договор'),
        ]

        # Находим документы по паттернам
        for pattern, doc_type, header in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                numbers_dates = match.group(1)
                for m in re.finditer(r'(?:№|No|N)\s*(\d+)\s+от\s+(\d{2}\.\d{2}\.\d{4})', numbers_dates, re.IGNORECASE):
                    number = m.group(1)
                    date = m.group(2)
                    results.append({
                        'type': doc_type,
                        'header': header,
                        'number': number,
                        'date': date,
                        'start': match.start() + m.start(),
                        'end': match.start() + m.end()
                    })

        # Специальная обработка актов выполненных работ (приоритет над УПД)
        act_pattern = r'акт[а-я]*\s+выполненных\s+работ\s+((?:(?:№|No|N)\s*\d+\s+от\s+\d{2}\.\d{2}\.\d{4}[^,]*[,;]?\s*)+)'
        for match in re.finditer(act_pattern, text, re.IGNORECASE):
            act_block = match.group(1)
            # Извлекаем все пары "№ ... от ..."
            for m in re.finditer(r'(?:№|No|N)\s*(\d+)\s+от\s+(\d{2}\.\d{2}\.\d{4})', act_block, re.IGNORECASE):
                number = m.group(1)
                date = m.group(2)
                results.append({
                    'type': 'upd_blocks',
                    'header': 'Акт выполненных работ',
                    'number': number,
                    'date': date,
                    'start': match.start() + m.start(),
                    'end': match.start() + m.end()
                })

        # Специальная обработка почтовых уведомлений
        postal_pattern = r'почтов[а-я]*\s+уведомлени[а-я]*\s+((?:(?:№|No|N)\s*\d+\s+(?:от\s+\d{2}\.\d{2}\.\d{4}|дата\s+получения\s+\d{2}\.\d{2}\.\d{4}|об\s+отправке\s+и\s+получении\s+\d{2}\.\d{2}\.\d{4}|об\s+отправке\s+и\s+получения\s+\d{2}\.\d{2}\.\d{4})[^,]*[,;]?\s*)+)'
        for match in re.finditer(postal_pattern, text, re.IGNORECASE):
            postal_block = match.group(1)
            # Извлекаем все пары "№ ... от ..." или "№ ... дата получения ..." или "№ ... об отправке и получении ..." или "№ ... об отправке и получения ..."
            for m in re.finditer(r'(?:№|No|N)\s*(\d+)\s+(?:от\s+(\d{2}\.\d{2}\.\d{4})|дата\s+получения\s+(\d{2}\.\d{2}\.\d{4})|об\s+отправке\s+и\s+получении\s+(\d{2}\.\d{2}\.\d{4})|об\s+отправке\s+и\s+получения\s+(\d{2}\.\d{2}\.\d{4}))', postal_block, re.IGNORECASE):
                number = m.group(1)
                date = m.group(2) or m.group(3) or m.group(
                    4) or m.group(5)  # Берем дату из любой группы
                results.append({
                    'type': 'postal_block',
                    'header': 'почтовое уведомление',
                    'number': number,
                    'date': date,
                    'start': match.start() + m.start(),
                    'end': match.start() + m.end()
                })

        # Специальная обработка комплектов сопроводительных документов без номеров
        cargo_pattern = r'комплект[а-я]*\s+сопроводительных\s+документов[^,]*[,;]?'
        for match in re.finditer(cargo_pattern, text, re.IGNORECASE):
            # Проверяем, не найден ли уже этот блок с номером
            already_found = False
            for result in results:
                if (result['type'] == 'cargo_docs' and
                    'комплект' in result['header'].lower() and
                        abs(result['start'] - match.start()) < 50):  # Если близко к найденному
                    already_found = True
                    break

            if not already_found:
                results.append({
                    'type': 'cargo_docs',
                    'header': 'комплект сопроводительных документов',
                    'number': None,
                    'date': None,
                    'start': match.start(),
                    'end': match.end()
                })

        results.sort(key=lambda x: x['start'])
        return results

    def parse_parties_info(self, text: str) -> Dict[str, str]:
        """
        Парсинг информации об истце и ответчике из заголовка требования
        """
        parties = {'plaintiff_name': '',
                   'defendant_name': '',
                   'contract_parties': '',
                   'contract_parties_short': '',
                   'plaintiff_inn': '',
                   'plaintiff_kpp': '',
                   'plaintiff_ogrn': '',
                   'plaintiff_address': '',
                   'defendant_inn': '',
                   'defendant_kpp': '',
                   'defendant_ogrn': '',
                   'defendant_address': ''}

        # Ищем заголовок "ТРЕБОВАНИЕ"
        requirement_match = re.search(r'ТРЕБОВАНИЕ', text, re.IGNORECASE)
        if not requirement_match:
            return parties

        # Берем текст до "ТРЕБОВАНИЕ"
        header_text = text[:requirement_match.start()].strip()

        # Ищем ответчика (первая организация в заголовке)
        defendant_match = re.search(
            r'^([^,\n]+(?:ООО|ОАО|ЗАО|ПАО|АО)[^,\n]*)',
            header_text, re.IGNORECASE | re.MULTILINE
        )
        if defendant_match:
            defendant_name = defendant_match.group(1).strip()
            # Приводим к именительному падежу
            defendant_name = self.convert_to_nominative(defendant_name)
            parties['defendant_name'] = defendant_name

        # Если не нашли через регулярные выражения, попробуем другой подход
        if not parties['defendant_name']:
            # Ищем строку, начинающуюся с "Обществу"
            for line in header_text.split('\n'):
                if line.strip().startswith('Обществу'):
                    defendant_name = line.strip()
                    defendant_name = self.convert_to_nominative(defendant_name)
                    parties['defendant_name'] = defendant_name
                    break

        # Если все еще не нашли, ищем строку с "Ответчик:"
        if not parties['defendant_name']:
            for line in header_text.split('\n'):
                if line.strip().startswith('Ответчик:'):
                    defendant_name = line.strip().replace('Ответчик:', '').strip()
                    defendant_name = self.convert_to_nominative(defendant_name)
                    parties['defendant_name'] = defendant_name
                    break

        # Ищем истца (после "от")
        plaintiff_match = re.search(
            r'от\s+([^,\n]+(?:ИП|ООО|ОАО|ЗАО|ПАО|АО)[^,\n]*)',
            header_text, re.IGNORECASE
        )
        if plaintiff_match:
            plaintiff_name = plaintiff_match.group(1).strip()
            # Приводим к именительному падежу
            plaintiff_name = self.convert_to_nominative(plaintiff_name)
            parties['plaintiff_name'] = plaintiff_name

        # Если не нашли через регулярные выражения, попробуем другой подход
        if not parties['plaintiff_name']:
            # Ищем строку, начинающуюся с "Обществу"
            for line in header_text.split('\n'):
                if line.strip().startswith('Обществу'):
                    defendant_name = line.strip()
                    defendant_name = self.convert_to_nominative(defendant_name)
                    parties['defendant_name'] = defendant_name
                    break

        if not parties['plaintiff_name']:
            lines = header_text.split('\n')
            for i, line in enumerate(lines):
                # Новый случай: строка "от Индивидуального предпринимателя" и ФИО на следующей
                if line.strip() == 'от Индивидуального предпринимателя':
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line and not any(x in next_line for x in ['ИНН', 'ОГРНИП', 'ОГРН', 'КПП']):
                            fio_parts = next_line.split()
                            if 2 <= len(fio_parts) <= 4 and fio_parts[0][0].isupper():
                                parties['plaintiff_name'] = f"Индивидуальный предприниматель {next_line}"
                                break
                # Новый случай: строка "Истец: Индивидуальный предприниматель" и ФИО в ближайших строках
                if line.strip().startswith('Истец: Индивидуальный предприниматель'):
                    # Ищем ФИО в ближайших строках (до 5 строк вперед)
                    for j in range(i + 1, min(i + 6, len(lines))):
                        next_line = lines[j].strip()
                        if next_line and not any(x in next_line for x in ['ИНН', 'ОГРНИП', 'ОГРН', 'КПП', '456200', 'г.', 'ул.', 'д.', 'офис']):
                            fio_parts = next_line.split()
                            if 2 <= len(fio_parts) <= 4 and fio_parts[0][0].isupper():
                                parties['plaintiff_name'] = f"Индивидуальный предприниматель {next_line}"
                                break
                    if parties['plaintiff_name']:
                        break
                # Новый случай: строка "Индивидуальный предприниматель" и ФИО на следующей
                if line.strip() == 'Индивидуальный предприниматель':
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line and not any(x in next_line for x in ['ИНН', 'ОГРНИП', 'ОГРН', 'КПП']):
                            fio_parts = next_line.split()
                            if 2 <= len(fio_parts) <= 4 and fio_parts[0][0].isupper():
                                parties['plaintiff_name'] = f"Индивидуальный предприниматель {next_line}"
                                break
                # Старый случай: "Индивидуальный предприниматель" в строке
                if 'Индивидуальный предприниматель' in line:
                    parts = line.split('Индивидуальный предприниматель')
                    if len(parts) > 1 and parts[1].strip():
                        plaintiff_name = f"Индивидуальный предприниматель {parts[1].strip()}"
                        parties['plaintiff_name'] = plaintiff_name
                    else:
                        if i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            if next_line and not any(x in next_line for x in ['ИНН', 'ОГРНИП', 'ОГРН', 'КПП']):
                                fio_parts = next_line.split()
                                if 2 <= len(fio_parts) <= 4 and fio_parts[0][0].isupper():
                                    plaintiff_name = f"Индивидуальный предприниматель {next_line}"
                                    parties['plaintiff_name'] = plaintiff_name
                    break
            # Если всё равно не нашли — ищем первую строку с ИП или ФИО
            if not parties['plaintiff_name']:
                for line in lines:
                    if 'Индивидуальный предприниматель' in line and len(line.split()) > 1:
                        parties['plaintiff_name'] = line.strip()
                        break
                    fio_parts = line.strip().split()
                    if 2 <= len(fio_parts) <= 4 and fio_parts[0][0].isupper():
                        parties['plaintiff_name'] = f"Индивидуальный предприниматель {line.strip()}"
                        break

        # Извлекаем дополнительные данные (ИНН, КПП, ОГРН, адрес)
        lines = header_text.split('\n')

        # Ищем данные истца
        plaintiff_section = False
        for i, line in enumerate(lines):
            line = line.strip()

            # Определяем начало секции истца
            if 'Истец:' in line or 'от Индивидуального предпринимателя' in line:
                plaintiff_section = True
                continue

            # Определяем конец секции истца (начало секции ответчика)
            if 'Ответчик:' in line or 'Обществу' in line:
                plaintiff_section = False
                continue

            if plaintiff_section and line:
                # ИНН истца
                inn_match = re.search(r'ИНН\s+(\d+)', line)
                if inn_match and not parties['plaintiff_inn']:
                    parties['plaintiff_inn'] = inn_match.group(1)

                # КПП истца (только для ООО, для ИП обычно пусто)
                kpp_match = re.search(r'КПП\s+(\d+)', line)
                if kpp_match and not parties['plaintiff_kpp']:
                    parties['plaintiff_kpp'] = kpp_match.group(1)

                # ОГРН/ОГРНИП истца
                ogrn_match = re.search(r'ОГРН(?:ИП)?\s+(\d+)', line)
                if ogrn_match and not parties['plaintiff_ogrn']:
                    parties['plaintiff_ogrn'] = ogrn_match.group(1)

                # Адрес истца (строка с почтовым индексом и адресом)
                if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                    parties['plaintiff_address'] = line

        # Ищем данные ответчика
        defendant_section = False
        for i, line in enumerate(lines):
            line = line.strip()

            # Определяем начало секции ответчика
            if 'Ответчик:' in line or 'Обществу' in line:
                defendant_section = True
                continue

            # Определяем конец секции ответчика (начало ТРЕБОВАНИЕ)
            if 'ТРЕБОВАНИЕ' in line:
                defendant_section = False
                break

            if defendant_section and line:
                # ИНН ответчика
                inn_match = re.search(r'ИНН\s+(\d+)', line)
                if inn_match and not parties['defendant_inn']:
                    parties['defendant_inn'] = inn_match.group(1)

                # КПП ответчика
                kpp_match = re.search(r'КПП\s+(\d+)', line)
                if kpp_match and not parties['defendant_kpp']:
                    parties['defendant_kpp'] = kpp_match.group(1)

                # ОГРН ответчика
                ogrn_match = re.search(r'ОГРН\s+(\d+)', line)
                if ogrn_match and not parties['defendant_ogrn']:
                    parties['defendant_ogrn'] = ogrn_match.group(1)

                # Адрес ответчика (строка с почтовым индексом и адресом)
                if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                    parties['defendant_address'] = line

        # Ищем договорную часть
        contract_match = re.search(
            r'Между\s+([^,]+)\s+и\s+([^,]+)\s+были\s+заключены',
            text, re.IGNORECASE
        )
        if contract_match:
            party1 = contract_match.group(1).strip()
            party2 = contract_match.group(2).strip()
            # Нормализуем кавычки
            party1 = self.normalize_quotes(party1)
            party2 = self.normalize_quotes(party2)
            parties['contract_parties'] = f"Между {party1} и {party2}"

            # Создаем короткую версию для использования в тексте
            party1_short = party1
            party2_short = party2

            if 'Индивидуальный предприниматель' in party1:
                party1_short = party1.replace(
                    'Индивидуальный предприниматель', 'ИП')
            if 'Общество с ограниченной ответственностью' in party2:
                party2_short = party2.replace(
                    'Общество с ограниченной ответственностью', 'ООО')

            parties['contract_parties_short'] = f"Между {party1_short} и {party2_short}"

        return parties

    def convert_to_nominative(self, text: str) -> str:
        """
        Приведение к именительному падежу (базовая реализация)
        """
        # Простые замены для основных падежей
        replacements = {
            'Обществу с ограниченной ответственностью': 'Общество с ограниченной ответственностью',
            'Индивидуальному предпринимателю': 'Индивидуальный предприниматель',
            'ООО': 'Общество с ограниченной ответственностью',
            'ИП': 'Индивидуальный предприниматель'
        }

        result = text
        for old, new in replacements.items():
            result = result.replace(old, new)

        return result

    def normalize_quotes(self, text: str) -> str:
        """
        Нормализация кавычек к единому виду
        """
        # Заменяем все виды кавычек на стандартные
        text = re.sub(r'[«»]', '"', text)
        return text

    def extract_documents_advanced(self, text: str) -> Dict[str, List[DocumentBlock]]:
        """
        Улучшенное извлечение документов с группировкой по типам
        """
        results = {doc_type: [] for doc_type in self.document_headers.keys()}

        # Находим все документы
        documents = self.find_document_patterns(text)

        # Группируем по типам документов
        grouped_docs = {}
        for doc in documents:
            doc_type = doc['type']
            if doc_type not in grouped_docs:
                grouped_docs[doc_type] = []
            grouped_docs[doc_type].append(doc)

        # Создаем блоки документов, группируя по типам
        for doc_type, docs in grouped_docs.items():
            if not docs:
                continue

            # Получаем оригинальный заголовок из первого документа этого типа
            header = self.convert_to_nominative(docs[0]['header'])

            # Собираем все пары номер-дата для этого типа документа
            pairs = []
            for doc in docs:
                if doc['number'] and doc['date']:
                    pairs.append((f"№ {doc['number']}", doc['date']))
                elif doc['number']:
                    pairs.append((f"№ {doc['number']}", None))

            # Удаляем дубликаты
            unique_pairs = list(set(pairs))
            unique_pairs.sort(key=lambda x: (x[0] or '', x[1] or ''))

            # Используем оригинальный заголовок
            block = DocumentBlock(
                header=header,
                pairs=unique_pairs,
                clarifications=[],
                raw_text=docs[0]['header']
            )

            results[doc_type].append(block)

        return results

    def get_normalized_header(self, header: str, doc_type: str) -> str:
        """
        Нормализация заголовка документа
        """
        header_lower = header.lower()

        if doc_type == 'contract_applications':
            return 'Заявка на перевозку груза'
        elif doc_type == 'invoice_blocks':
            return 'Счет на оплату'
        elif doc_type == 'upd_blocks':
            if 'акт' in header_lower:
                return 'Акт выполненных работ'
            else:
                return 'УПД'
        elif doc_type == 'cargo_docs':
            if 'комплект' in header_lower:
                return 'Комплект сопроводительных документов'
            elif 'товарн' in header_lower:
                return 'Товарно-транспортная накладная'
            else:
                return 'Транспортная накладная'
        elif doc_type == 'postal_block':
            return 'Почтовое уведомление'

        return header

    def parse_text(self, text: str) -> Dict[str, List[DocumentBlock]]:
        """
        Основной метод парсинга текста
        """
        logger.info("Начинаем парсинг текста")

        # Используем улучшенный алгоритм
        results = self.extract_documents_advanced(text)

        # Логируем результаты
        for doc_type, blocks in results.items():
            if blocks:
                logger.info(
                    f"Найдено {len(blocks)} документов типа {doc_type}")
                for block in blocks:
                    logger.info(f"  - {block.header}: {block.pairs}")

        return results

    def format_results(self, parsed_blocks: Dict[str, List[DocumentBlock]]) -> Dict[str, str]:
        """
        Форматирование результатов для вывода
        """
        formatted_results = {}

        for doc_type, blocks in parsed_blocks.items():
            if not blocks:
                continue

            # Форматируем результат
            formatted_blocks = []
            for block in blocks:
                # Используем нормализованный заголовок
                normalized_header = self.get_normalized_header(
                    block.header, doc_type)

                # Форматируем пары
                formatted_pairs = []
                for number, date in block.pairs:
                    if number and date:
                        if doc_type == 'postal_block':
                            # Для почтовых уведомлений используем "дата получения"
                            formatted_pairs.append(
                                f"{number} дата получения {date}")
                        else:
                            # Для остальных документов используем "от"
                            formatted_pairs.append(f"{number} от {date}")
                    elif number:
                        formatted_pairs.append(number)
                    elif date:
                        if doc_type == 'postal_block':
                            formatted_pairs.append(f"дата получения {date}")
                        else:
                            formatted_pairs.append(f"от {date}")

                if formatted_pairs:
                    formatted_blocks.append(
                        f"{normalized_header} {'; '.join(formatted_pairs)}")
                else:
                    # Если нет пар, но есть заголовок (например, комплект документов без номера)
                    formatted_blocks.append(normalized_header)

            if formatted_blocks:
                formatted_results[doc_type] = '; '.join(formatted_blocks)

        return formatted_results


def parse_documents_with_sliding_window(text: str, debug: bool = False) -> Dict[str, str]:
    """
    Основная функция для парсинга документов.

    Args:
        text: Текст для парсинга
        debug: Включить отладочную информацию

    Returns:
        Словарь с извлеченными данными
    """
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    parser = SlidingWindowParser()

    # Парсим документы
    parsed_blocks = parser.parse_text(text)
    formatted_results = parser.format_results(parsed_blocks)

    # Парсим информацию о сторонах
    parties_info = parser.parse_parties_info(text)

    # Объединяем результаты
    result = formatted_results.copy()
    result.update(parties_info)

    return result
