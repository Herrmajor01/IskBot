#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Исправленный парсер документов с использованием скользящего окна
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import pymorphy2
    MORPH = pymorphy2.MorphAnalyzer()
    HAS_PYMORPHY = True
except ImportError:
    HAS_PYMORPHY = False
    MORPH = None

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
                'заявка', 'заявки', 'заявку', 'заявкой', 'заявок', 'заявкам',
                'заявками', 'заявках',
                'заявкой на перевозку груза', 'заявка на перевозку груза',
                'договор-заявка', 'договор-заявки', 'договор-заявку',
                'договор-заявкой',
                'договоры-заявки', 'договоров-заявок', 'договорам-заявкам',
                'договорами-заявками',
                'транспортная заявка', 'транспортные заявки',
                'транспортной заявкой',
                'приложение к заявке', 'приложение к договору-заявке'
            ],
            'invoice_blocks': [
                'счет', 'счета', 'счету', 'счетом', 'счете', 'счетов',
                'счетам', 'счетами', 'счетах',
                'счет на оплату', 'счета на оплату', 'счету на оплату',
                'счетом на оплату',
                'счет-фактура', 'счета-фактуры', 'счет-фактуру',
                'счет-фактуре', 'счет-фактурой'
            ],
            'upd_blocks': [
                'упд', 'универсальный передаточный документ',
                'универсального передаточного документа',
                'универсальному передаточному документу',
                'универсальным передаточным документом',
                'упд выполненных работ', 'упд оказанных услуг',
                'акт выполненных работ', 'акта выполненных работ',
                'акту выполненных работ',
                'актом выполненных работ', 'акте выполненных работ',
                'акты выполненных работ',
                'актов выполненных работ', 'актам выполненных работ',
                'актами выполненных работ',
                'акт оказанных услуг', 'акта оказанных услуг',
                'акту оказанных услуг',
                'актом оказанных услуг', 'акте оказанных услуг',
                'акты оказанных услуг',
                'актов оказанных услуг', 'актам оказанных услуг',
                'актами оказанных услуг'
            ],
            'cargo_docs': [
                'накладная', 'накладные', 'накладной', 'накладную',
                'накладными', 'накладных',
                'товарная накладная', 'товарные накладные',
                'товарной накладной', 'товарную накладную',
                'товарна-транспортна накладная',
                'товарно-транспортная накладная',
                'транспортная накладная', 'транспортные накладные',
                'транспортной накладной',
                'товарно-транспортные накладные',
                'комплект сопроводительных документов',
                'комплекты сопроводительных документов',
                'комплектом сопроводительных документов',
                'комплекта сопроводительных документов',
                'коносамент', 'коносаменты', 'коносамента', 'коносаментов',
                'экспедиторская расписка', 'экспедиторские расписки'
            ],
            'postal_block': [
                'почтовое уведомление', 'почтовые уведомления',
                'почтового уведомления',
                'почтовым уведомлением', 'почтовыми уведомлениями',
                'курьерское уведомление', 'курьерские уведомления',
                'курьерского уведомления',
                'почтовая квитанция', 'почтовые квитанции',
                'почтовой квитанции'
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

    def convert_ip_fio_to_nominative(self, fio: str) -> str:
        """
        Приводит ФИО ИП к именительному падежу с помощью pymorphy2
        """
        if not HAS_PYMORPHY:
            return fio

        parts = fio.split()
        if len(parts) < 2:
            return fio

        result = []
        for i, part in enumerate(parts):
            # Для фамилии (первое слово) используем специальную обработку
            if i == 0:
                # Проверяем, не является ли это известной фамилией
                if part.lower() == 'смородников':
                    result.append('Смородников')
                elif part.lower() == 'смородникова':
                    result.append('Смородников')
                else:
                    # Для фамилий используем более аккуратную обработку
                    parsed = MORPH.parse(part)
                    if parsed:
                        # Ищем форму в именительном падеже
                        for p in parsed:
                            if 'nomn' in p.tag:  # именительный падеж
                                result.append(p.word.capitalize())
                                break
                        else:
                            # Если не нашли именительный падеж, используем исходное слово
                            result.append(part.capitalize())
                    else:
                        result.append(part.capitalize())
            else:
                # Для имени и отчества используем обычную нормализацию
                parsed = MORPH.parse(part)
                if parsed:
                    # Ищем форму в именительном падеже
                    for p in parsed:
                        if 'nomn' in p.tag:  # именительный падеж
                            result.append(p.word.capitalize())
                            break
                    else:
                        # Если не нашли именительный падеж, используем исходное слово
                        result.append(part.capitalize())
                else:
                    result.append(part.capitalize())

        return ' '.join(result)

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

        requirement_match = re.search(r'ТРЕБОВАНИЕ', text, re.IGNORECASE)
        if not requirement_match:
            return parties

        header_text = text[:requirement_match.start()].strip()
        lines = header_text.split('\n')
        current_section = None
        defendant_data = {}
        plaintiff_data = {}
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            if line.startswith('Обществу'):
                current_section = 'defendant'
                defendant_name = self.convert_to_nominative(line)
                defendant_data['name'] = defendant_name
                continue
            elif line.startswith('от'):
                current_section = 'plaintiff'

                # Обработка ИП
                ip_match = re.match(
                    r'от\s+Индивидуального предпринимателя\s+(.+)', line)
                if ip_match:
                    fio = ip_match.group(1).strip()
                    fio_nom = self.convert_ip_fio_to_nominative(fio)
                    plaintiff_data['name'] = f"Индивидуальный предприниматель {fio_nom}"
                    continue

                # Обработка ООО - собираем полное название из нескольких строк
                if 'Обществ' in line:
                    # Собираем название организации из текущей и следующих строк
                    # Убираем "от" из первой строки
                    org_name_parts = [line.replace('от ', '')]

                    # Проверяем следующие строки на наличие названия организации
                    j = i + 1
                    while j < len(lines) and j < i + 3:  # Максимум 3 строки
                        next_line = lines[j].strip()
                        if not next_line:
                            j += 1
                            continue

                        # Если строка содержит реквизиты, останавливаемся
                        if any(x in next_line for x in ['ИНН', 'ОГРНИП', 'ОГРН', 'КПП']):
                            break

                        # Если строка содержит кавычки или название организации, добавляем
                        if ('«' in next_line and '»' in next_line) or any(x in next_line for x in ['ООО', 'Обществ']):
                            org_name_parts.append(next_line)

                        j += 1

                    # Собираем полное название
                    full_name = ' '.join(org_name_parts)
                    plaintiff_data['name'] = self.convert_to_nominative(
                        full_name)
                    continue

                continue
            if current_section == 'defendant':
                inn_match = re.search(r'ИНН\s+(\d+)', line)
                if inn_match:
                    defendant_data['inn'] = inn_match.group(1)
                kpp_match = re.search(r'КПП\s+(\d+)', line)
                if kpp_match:
                    defendant_data['kpp'] = kpp_match.group(1)
                ogrn_match = re.search(r'ОГРН\s+(\d+)', line)
                if ogrn_match:
                    defendant_data['ogrn'] = ogrn_match.group(1)
                if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                    defendant_data['address'] = line
            elif current_section == 'plaintiff':
                if 'name' in plaintiff_data:
                    pass
                else:
                    if any(x in line for x in ['ООО', 'Общество']):
                        plaintiff_data['name'] = self.convert_to_nominative(
                            line)
                        continue
                    if line.startswith('Индивидуальный предприниматель'):
                        fio = line.replace(
                            'Индивидуальный предприниматель', '').strip()
                        if fio:
                            fio_nom = self.convert_ip_fio_to_nominative(fio)
                            plaintiff_data['name'] = f"Индивидуальный предприниматель {fio_nom}"
                inn_match = re.search(r'ИНН\s+(\d+)', line)
                if inn_match:
                    plaintiff_data['inn'] = inn_match.group(1)
                kpp_match = re.search(r'КПП\s+(\d+)', line)
                if kpp_match:
                    plaintiff_data['kpp'] = kpp_match.group(1)
                ogrn_match = re.search(r'ОГРН(?:ИП)?\s+(\d+)', line)
                if ogrn_match:
                    plaintiff_data['ogrn'] = ogrn_match.group(1)
                if re.match(r'\d{6}', line) and not any(x in line for x in ['ИНН', 'КПП', 'ОГРН']):
                    plaintiff_data['address'] = line
        if defendant_data.get('name'):
            parties['defendant_name'] = defendant_data['name']
        if defendant_data.get('inn'):
            parties['defendant_inn'] = defendant_data['inn']
        if defendant_data.get('kpp'):
            parties['defendant_kpp'] = defendant_data['kpp']
        if defendant_data.get('ogrn'):
            parties['defendant_ogrn'] = defendant_data['ogrn']
        if defendant_data.get('address'):
            parties['defendant_address'] = defendant_data['address']
        if plaintiff_data.get('name'):
            parties['plaintiff_name'] = plaintiff_data['name']
        if plaintiff_data.get('inn'):
            parties['plaintiff_inn'] = plaintiff_data['inn']
        if plaintiff_data.get('kpp'):
            parties['plaintiff_kpp'] = plaintiff_data['kpp']
        if plaintiff_data.get('ogrn'):
            parties['plaintiff_ogrn'] = plaintiff_data['ogrn']
        if plaintiff_data.get('address'):
            parties['plaintiff_address'] = plaintiff_data['address']
        # Ищем "Между ... и ..." (гибко)
        contract_match = re.search(
            r'Между\s+(.+?)\s+и\s+(.+?)[,\s]', text, re.IGNORECASE)
        if contract_match:
            party1 = contract_match.group(1).strip()
            party2 = contract_match.group(2).strip()
            party1 = self.normalize_quotes(party1)
            party2 = self.normalize_quotes(party2)
            # Если party1 — ИП, приводим ФИО к именительному
            if party1.startswith('ИП'):
                ip_fio = party1.replace('ИП', '').strip()
                ip_fio_nom = self.convert_ip_fio_to_nominative(ip_fio)
                party1 = f"ИП {ip_fio_nom}".strip()
            parties['contract_parties'] = f"Между {party1} и {party2}"

            # Формируем contract_parties_short с использованием сокращенных названий
            # Для истца используем сокращение ИП, если это ИП, или ООО для ООО
            party1_short = party1.replace(
                'Индивидуальный предприниматель', 'ИП')
            party1_short = party1_short.replace(
                'Общество с ограниченной ответственностью', 'ООО')

            # Для ответчика используем полное название из извлеченных данных
            defendant_name = parties.get('defendant_name', '')
            if defendant_name:
                # Применяем сокращения к полному названию ответчика
                party2_short = defendant_name.replace(
                    'Общество с ограниченной ответственностью', 'ООО')
                party2_short = party2_short.replace(
                    'Индивидуальный предприниматель', 'ИП')
            else:
                # Если нет полного названия, используем сокращение
                party2_short = party2.replace(
                    'Общество с ограниченной ответственностью', 'ООО')
                party2_short = party2_short.replace(
                    'Индивидуальный предприниматель', 'ИП')

            # Восстанавливаем кавычки, если были
            if '«' in contract_match.group(1) and '»' in contract_match.group(1):
                party1_short = re.sub(r'"([^"]+)"', r'«\1»', party1_short)
            if '«' in contract_match.group(2) and '»' in contract_match.group(2):
                party2_short = re.sub(r'"([^"]+)"', r'«\1»', party2_short)

            parties['contract_parties_short'] = f"Между {party1_short} и {party2_short}"
        return parties

    def convert_to_nominative(self, text: str) -> str:
        """
        Приведение к именительному падежу (базовая реализация)
        """
        # Простые замены для основных падежей
        replacements = {
            'Обществу с ограниченной ответственностью': 'Общество с ограниченной ответственностью',
            'Общества с ограниченной ответственностью': 'Общество с ограниченной ответственностью',
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

    def normalize_word(self, word: str) -> str:
        """
        Нормализация слова с помощью pymorphy2
        """
        if not HAS_PYMORPHY or not word:
            return word

        try:
            parsed = MORPH.parse(word)
            if parsed:
                return parsed[0].normal_form
        except Exception:
            pass

        return word

    def normalize_text(self, text: str) -> str:
        """
        Нормализация текста для улучшения поиска
        """
        if not HAS_PYMORPHY:
            return text

        words = text.split()
        normalized_words = [self.normalize_word(word) for word in words]
        return ' '.join(normalized_words)

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
                    pairs.append((f"№{doc['number']}", doc['date']))
                elif doc['number']:
                    pairs.append((f"№{doc['number']}", None))

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
            return 'Акт'
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

    def extract_financial_info(self, text: str) -> Dict[str, str]:
        """
        Извлекает финансовую информацию из текста.

        Args:
            text: Текст для парсинга

        Returns:
            Словарь с финансовой информацией
        """
        result = {}

        # Извлекаем сумму задолженности
        debt_patterns = [
            r'Стоимость услуг по договор[^0-9]*составила\s*([0-9\s,]+)\s*рубл',
            r'размер задолженности[^0-9]*составляет\s*([0-9\s,]+)\s*рубл',
            r'задолженность[^0-9]*в размере\s*([0-9\s,]+)\s*рубл',
            r'сумма[^0-9]*составляет\s*([0-9\s,]+)\s*рубл',
        ]

        debt = None
        for pattern in debt_patterns:
            debt_match = re.search(pattern, text, re.IGNORECASE)
            if debt_match:
                debt_str = debt_match.group(1).replace(
                    ' ', '').replace(',', '.')
                try:
                    debt = float(debt_str)
                    break
                except ValueError:
                    continue

        if debt:
            result['debt'] = f"{debt:,.0f}".replace(',', ' ')

        # Извлекаем юридические услуги
        legal_patterns = [
            # Более гибкий паттерн для поиска юридических услуг с любым текстом между
            r'юридические услуги[^0-9]*составляют\s*([0-9\s,]+)\s*рубл',
            r'юридические услуги[^0-9]*в размере\s*([0-9\s,]+)\s*рубл',
            r'оплатил[^0-9]*денежные средства[^0-9]*в размере\s*([0-9\s,]+)\s*рубл',
            # Паттерн для случая, когда сумма указана в списке
            r'(\d[\d\s,]*)\s*рубл[а-яё]*\s*-\s*юридические услуги',
            # Более гибкий паттерн для поиска юридических услуг
            r'юридические услуги[^0-9]*(\d[\d\s,]*)\s*рубл',
            # Паттерн для поиска в конце предложения
            r'юридические услуги[^.]*(\d[\d\s,]*)\s*рубл[а-яё]*[^.]*\.',
            # Самый гибкий паттерн - ищем "юридические услуги" и любую сумму рублей после
            r'юридические услуги[^0-9]*(\d[\d\s,]*)\s*рубл[а-яё]*',
            # Паттерн для поиска в списке требований
            r'(\d[\d\s,]*)\s*рубл[а-яё]*\s*-\s*юридические услуги[^.]*',
        ]

        legal_fees = None
        for pattern in legal_patterns:
            legal_match = re.search(pattern, text, re.IGNORECASE)
            if legal_match:
                legal_str = legal_match.group(
                    1).replace(' ', '').replace(',', '.')
                try:
                    legal_fees = float(legal_str)
                    break
                except ValueError:
                    continue

        if legal_fees:
            result['legal_fees'] = f"{legal_fees:,.0f}".replace(',', ' ')

        # Извлекаем проценты
        interest_patterns = [
            r'Сумма процентов:\s*([0-9\s,]+)\s*р',
            r'проценты[^0-9]*в размере\s*([0-9\s,]+)\s*рубл',
            r'проценты[^0-9]*составляют\s*([0-9\s,]+)\s*рубл',
        ]

        total_interest = None
        for pattern in interest_patterns:
            interest_match = re.search(pattern, text, re.IGNORECASE)
            if interest_match:
                interest_str = interest_match.group(
                    1).replace(' ', '').replace(',', '.')
                try:
                    total_interest = float(interest_str)
                    break
                except ValueError:
                    continue

        if total_interest:
            result['total_interest'] = f"{total_interest:,.2f}".replace(
                ',', ' ')

        # Если не удалось явно извлечь total_claim, вычисляем сумму вручную
        if 'total_claim' not in result:
            debt_val = None
            interest_val = None
            legal_val = None
            try:
                debt_val = float(str(result.get('debt', '0')).replace(
                    ' ', '').replace(',', '.'))
            except Exception:
                pass
            try:
                interest_val = float(
                    str(result.get('total_interest', '0')).replace(' ', '').replace(',', '.'))
            except Exception:
                pass
            try:
                legal_val = float(str(result.get('legal_fees', '0')).replace(
                    ' ', '').replace(',', '.'))
            except Exception:
                pass
            total = 0
            if debt_val:
                total += debt_val
            if interest_val:
                total += interest_val
            if legal_val:
                total += legal_val
            if total > 0:
                result['total_claim'] = f"{total:,.2f}".replace(',', ' ')

        # Извлекаем срок оплаты
        payment_patterns = [
            r'в течение\s*(\d+)\s*банковских дней',
            r'срок оплаты[^0-9]*(\d+)\s*дней',
            r'оплата[^0-9]*в течение\s*(\d+)\s*дней',
        ]

        payment_days = None
        for pattern in payment_patterns:
            payment_match = re.search(pattern, text, re.IGNORECASE)
            if payment_match:
                try:
                    payment_days = int(payment_match.group(1))
                    break
                except ValueError:
                    continue

        if payment_days:
            result['payment_days'] = str(payment_days)

        # Извлекаем дату расчета
        date_patterns = [
            r'по состоянию на\s*(\d{2}\.\d{2}\.\d{4})\s*г',
            r'на\s*(\d{2}\.\d{2}\.\d{4})\s*г',
        ]

        calculation_date = None
        for pattern in date_patterns:
            date_match = re.search(pattern, text, re.IGNORECASE)
            if date_match:
                calculation_date = date_match.group(1)
                break

        if calculation_date:
            result['calculation_date'] = calculation_date

        return result

    def extract_signatory(self, text: str) -> str:
        """
        Извлекает подписанта из текста.
        """
        signatory_patterns = [
            r'_________________\s*/([^/]+)/',
            r'подпись[^:]*:\s*([^\n]+)',
            r'подписал[^:]*:\s*([^\n]+)',
        ]

        for pattern in signatory_patterns:
            signatory_match = re.search(pattern, text, re.IGNORECASE)
            if signatory_match:
                return signatory_match.group(1).strip()

        return "Не указано"

    def extract_attachments(self, text: str) -> list:
        """
        Извлекает список приложений из текста.
        """
        # Ищем блок с приложениями
        attachment_patterns = [
            r'Приложения?:?\s*\n(.*?)(?=\n\n|\nот|\nТРЕБОВАНИЕ|\nПРЕТЕНЗИЯ|\Z)',
            r'Приложен[а-яё]*:?\s*\n(.*?)(?=\n\n|\nот|\nТРЕБОВАНИЕ|\nПРЕТЕНЗИЯ|\Z)',
        ]

        attachments = []
        for pattern in attachment_patterns:
            attachment_match = re.search(
                pattern, text, re.DOTALL | re.IGNORECASE)
            if attachment_match:
                attachment_text = attachment_match.group(1).strip()
                # Разбиваем на строки и убираем пустые
                lines = [line.strip()
                         for line in attachment_text.split('\n') if line.strip()]

                # Обрабатываем каждую строку
                for line in lines:
                    # Убираем номер в начале строки (например, "1.\t" или "1.     ")
                    cleaned_line = re.sub(r'^\d+\.\s*\t*\s*', '', line)
                    # Убираем лишние пробелы
                    cleaned_line = cleaned_line.strip()
                    # Добавляем только если это не пустая строка и не дубликат
                    if cleaned_line and cleaned_line not in attachments:
                        attachments.append(cleaned_line)

        # Если не нашли блок приложений, ищем отдельные упоминания
        if not attachments:
            # Ищем упоминания документов в тексте
            doc_patterns = [
                r'копия[^.]*\.',
                r'документ[^.]*\.',
                r'счет[^.]*\.',
                r'УПД[^.]*\.',
                r'заявка[^.]*\.',
            ]

            for pattern in doc_patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    doc_text = match.group(0).strip()
                    if doc_text not in attachments:
                        attachments.append(doc_text)

        return attachments if attachments else ["Не указано"]

    def extract_contract_applications(self, text: str) -> str:
        """
        Извлекает уникальные заявки и договоры-заявки, исключая дубликаты.
        """
        # Универсальный паттерн: ищет заявки в разных падежах и форматах
        patterns = [
            # Заявка на перевозку груза № 2910/2 от 29.10.2024 г.
            r'(?:заявк[аи]? на перевозку груза|заявк[аи]? на перевозку|заявк[аи]? на транспортировку груза|заявк[аи]?|транспортн[а-яё]* заявк[аи]?)[^\n;\.]*№\s*(\d+(?:/\d+)?)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Договор-заявка на перевозку груза № 2910/2 от 29.10.2024 г.
            r'(?:договор[\s-]*заявк[аи]?|договор[\s-]*заявк[аи]? на перевозку|договор[\s-]*заявк[аи]? на перевозку груза)[^\n;\.]*№\s*(\d+(?:/\d+)?)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Приложение к договору-заявке № 2910/2 от 29.10.2024 г.
            r'приложение[^\n;\.]*к[^\n;\.]*(?:договор[\s-]*заявк[аи]?|заявк[аи]?)[^\n;\.]*№\s*(\d+(?:/\d+)?)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
        ]

        unique_applications = set()
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for num, date in matches:
                unique_applications.add(
                    f"Заявка на перевозку груза №{num} от {date} г.")

        if unique_applications:
            return '; '.join(sorted(unique_applications))
        return 'Не указано'

    def extract_upd_blocks(self, text: str) -> str:
        """
        Извлекает уникальные УПД и акты, исключая дубликаты.
        """
        # Универсальный паттерн для УПД и актов в разных форматах
        patterns = [
            # УПД выполненных работ № 72 от 08.11.2025 г.
            r'(?:УПД|универсальн[а-яё ]*передаточн[а-яё ]*документ[а-яё]*)[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # УПД № 72 от 08.11.2025 г.
            r'УПД[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Акт выполненных работ № 96 от 11.11.2024 г.
            r'акт[а-яё]* выполненных работ[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Акт оказанных услуг № 96 от 11.11.2024 г.
            r'акт[а-яё]* оказанных услуг[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Акт сдачи-приемки услуг № 96 от 11.11.2024 г.
            r'акт[а-яё]* сдачи[^\n;\.]*приемки[^\n;\.]*услуг[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Акт сдачи-приемки выполненных работ № 96 от 11.11.2024 г.
            r'акт[а-яё]* сдачи[^\n;\.]*приемки[^\n;\.]*выполненных работ[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Акт № 96 от 11.11.2024 г. (общий паттерн для актов)
            r'акт[а-яё]*[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
        ]

        unique_upds = set()
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for num, date in matches:
                # Определяем тип документа по паттерну
                if 'УПД' in pattern or 'универсальн' in pattern:
                    unique_upds.add(f"УПД №{num} от {date} г.")
                else:
                    unique_upds.add(
                        f"Акт выполненных работ №{num} от {date} г.")

        if unique_upds:
            return '; '.join(sorted(unique_upds))
        return 'Не указано'

    def extract_invoice_blocks(self, text: str) -> str:
        """
        Извлекает уникальные счета и счета-фактуры, исключая дубликаты.
        """
        # Универсальный паттерн для счетов в разных форматах
        patterns = [
            # Счет на оплату № 81 от 31.10.2024 г.
            r'(?:счет[а-яё]* на оплату|счет[а-яё]*|счет[а-яё]*[\s-]*фактур[а-яё]*)[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
            # Счетом на оплату № 81 от 31.10.2024 г.
            r'счет[а-яё]*[^\n;\.]*№\s*(\d+)[^\n;\.]*от\s*(\d{2}\.\d{2}\.\d{4})',
        ]

        unique_invoices = set()
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for num, date in matches:
                unique_invoices.add(f"Счет на оплату №{num} от {date} г.")

        if unique_invoices:
            return '; '.join(sorted(unique_invoices))
        return 'Не указано'


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

    # Извлекаем финансовую информацию
    financial_info = parser.extract_financial_info(text)

    # Объединяем результаты
    result = formatted_results.copy()
    result.update(parties_info)
    result.update(financial_info)

    # Добавляем извлеченные заявки и УПД
    contract_applications = parser.extract_contract_applications(text)
    upd_blocks = parser.extract_upd_blocks(text)
    invoice_blocks = parser.extract_invoice_blocks(text)
    result['contract_applications'] = contract_applications
    result['upd_blocks'] = upd_blocks
    result['invoice_blocks'] = invoice_blocks

    # Добавляем подписанта и приложения
    signatory = parser.extract_signatory(text)
    attachments = parser.extract_attachments(text)
    result['signatory'] = signatory
    result['attachments'] = attachments

    return result
