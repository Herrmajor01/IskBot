#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
from typing import Dict, Tuple

# --- Словари синонимов и ключей ---
DOC_SYNONYMS = {
    'contract_applications': [
        'заявка', 'заявки', 'заявку', 'заявкой', 'договор-заявка', 'договор-заявки',
        'транспортная заявка', 'транспортные заявки', 'приложение к заявке', 'приложение к договору-заявке'
    ],
    'invoice_blocks': [
        'счет', 'счета', 'счет на оплату', 'счета на оплату'
    ],
    'cargo_docs': [
        'товарно-транспортная накладная', 'товарная накладная', 'транспортная накладная',
        'накладная', 'комплект сопроводительных документов', 'коносамент', 'экспедиторская расписка'
    ],
    'upd_blocks': [
        'упд', 'акт выполненных работ', 'акт оказанных услуг', 'акт сдачи-приемки', 'универсальный передаточный документ',
        'счет-фактура', 'счет-фактуры'
    ],
    'postal_block': [
        'почтовое уведомление', 'почтовым уведомлением', 'курьерское уведомление', 'почтовая квитанция',
        'отправка оригиналов документов', 'получение оригиналов документов', 'идентификатор отправления'
    ]
}

# Для нормализации
NORM_MAP = {}
for group, variants in DOC_SYNONYMS.items():
    for v in variants:
        NORM_MAP[v] = variants[0]

# --- Морфологические формы для нормализации ---
MORPHOLOGY = {
    'contract_applications': {
        'singular': 'договор-заявка',
        'plural': 'договоры-заявки',
        'forms': [
            'договор-заявка', 'договор-заявки', 'договор-заявку', 'договор-заявкой', 'договоры-заявки',
            'договоров-заявок', 'договорам-заявкам', 'договорами-заявками', 'договорах-заявках',
            'заявка', 'заявки', 'заявку', 'заявкой', 'заявок', 'заявкам', 'заявками', 'заявках'
        ]
    },
    'invoice_blocks': {
        'singular': 'счет на оплату',
        'plural': 'счета на оплату',
        'forms': [
            'счет на оплату', 'счета на оплату', 'счету на оплату', 'счетом на оплату', 'счете на оплату',
            'счетов на оплату', 'счетам на оплату', 'счетами на оплату', 'счетах на оплату'
        ]
    },
    'upd_blocks': {
        'singular': 'УПД',
        'plural': 'УПД',
        'forms': [
            'упд', 'универсальный передаточный документ', 'универсального передаточного документа',
            'универсальному передаточному документу', 'универсальным передаточным документом',
            'универсальном передаточном документе',
            'акт выполненных работ', 'акта выполненных работ', 'акту выполненных работ',
            'актом выполненных работ', 'акте выполненных работ', 'акты выполненных работ',
            'актов выполненных работ', 'актам выполненных работ', 'актами выполненных работ',
            'актах выполненных работ',
            'акт оказанных услуг', 'акта оказанных услуг', 'акту оказанных услуг',
            'актом оказанных услуг', 'акте оказанных услуг', 'акты оказанных услуг',
            'актов оказанных услуг', 'актам оказанных услуг', 'актами оказанных услуг',
            'актах оказанных услуг',
            'акт сдачи-приемки выполненных работ', 'акта сдачи-приемки выполненных работ',
            'акту сдачи-приемки выполненных работ', 'актом сдачи-приемки выполненных работ',
            'акте сдачи-приемки выполненных работ', 'акты сдачи-приемки выполненных работ',
            'актов сдачи-приемки выполненных работ', 'актам сдачи-приемки выполненных работ',
            'актами сдачи-приемки выполненных работ', 'актах сдачи-приемки выполненных работ',
            'счет-фактура', 'счета-фактуры', 'счет-фактуру', 'счет-фактуре', 'счет-фактурой',
            'счетах-фактурах', 'счетов-фактур', 'счетам-фактурам', 'счетами-фактурами'
        ]
    },
    'cargo_docs': {
        'singular': 'накладная',
        'plural': 'накладные',
        'forms': [
            'накладная', 'накладные', 'накладной', 'накладную', 'накладными', 'накладных',
            'товарная накладная', 'товарные накладные', 'товарной накладной', 'товарную накладную',
            'товарными накладными', 'товарных накладных',
            'транспортная накладная', 'транспортные накладные', 'транспортной накладной',
            'транспортную накладную', 'транспортными накладными', 'транспортных накладных',
            'товарно-транспортная накладная', 'товарно-транспортные накладные',
            'товарно-транспортной накладной', 'товарно-транспортную накладную',
            'товарно-транспортными накладными', 'товарно-транспортных накладных',
            'комплект сопроводительных документов', 'комплекты сопроводительных документов',
            'комплекта сопроводительных документов', 'комплектов сопроводительных документов',
            'комплекту сопроводительных документов', 'комплектам сопроводительных документов',
            'комплектом сопроводительных документов', 'комплектами сопроводительных документов',
            'комплекте сопроводительных документов', 'комплектах сопроводительных документов',
            'коносамент', 'коносаменты', 'коносамента', 'коносаментов', 'коносаменту', 'коносаментам',
            'коносаментом', 'коносаментами', 'коносаменте', 'коносаментах',
            'экспедиторская расписка', 'экспедиторские расписки', 'экспедиторской расписке',
            'экспедиторских расписках', 'экспедиторской распиской', 'экспедиторскими расписками'
        ]
    },
    'postal_block': {
        'singular': 'почтовое уведомление',
        'plural': 'почтовые уведомления',
        'forms': [
            'почтовое уведомление', 'почтовые уведомления', 'почтового уведомления', 'почтовых уведомлений',
            'почтовому уведомлению', 'почтовым уведомлениям', 'почтовым уведомлением', 'почтовыми уведомлениями',
            'почтовом уведомлении', 'почтовых уведомлениях',
            'курьерское уведомление', 'курьерские уведомления', 'курьерского уведомления', 'курьерских уведомлений',
            'курьерскому уведомлению', 'курьерским уведомлениям', 'курьерским уведомлением', 'курьерскими уведомлениями',
            'курьерском уведомлении', 'курьерских уведомлениях',
            'почтовая квитанция', 'почтовые квитанции', 'почтовой квитанции', 'почтовых квитанций',
            'почтовой квитанцией', 'почтовыми квитанциями', 'почтовой квитанции', 'почтовых квитанциях'
        ]
    }
}

# --- Служебные обороты для сегментации ---
# Сегментация, которая не разрывает списки документов
SPLIT_PATTERNS = [
    r';',  # Только точка с запятой
    r'что подтверждается',
    r'в том числе',
    r'в частности',
    r'подтверждается',
    r'в подтверждение'
]
SPLIT_REGEX = re.compile('|'.join(SPLIT_PATTERNS), re.IGNORECASE)


def normalize_document_name(doc_name: str) -> tuple:
    """Нормализует название документа и определяет группу"""
    doc_name_lower = doc_name.lower()

    for group, info in MORPHOLOGY.items():
        for form in info['forms']:
            if form in doc_name_lower:
                return form, group

    return None, None


def extract_document_from_left(text: str, start_pos: int, max_lookback: int = 200) -> Tuple[str, str, int]:
    """
    Извлекает документ, идя влево от позиции start_pos
    Возвращает: (тип_документа, полное_название, позиция_начала_документа)
    """
    # Определяем границы поиска
    search_start = max(0, start_pos - max_lookback)
    left_text = text[search_start:start_pos]

    # Идем влево и собираем части названия документа
    document_parts = []
    current_pos = len(left_text) - 1

    # Слова, которые могут быть частью названия документа
    document_keywords = {
        'накладная', 'накладной', 'накладные', 'накладную', 'накладной',
        'транспортная', 'транспортной', 'транспортные', 'транспортную',
        'товарно', 'товарная', 'товарной', 'товарные', 'товарную',
        'заявка', 'заявки', 'заявку', 'заявкой',
        'счет', 'счета', 'счетом', 'счет-фактура', 'счет-фактуры',
        'упд', 'акт', 'акты', 'актом',
        'договор', 'договора', 'договоры', 'договором',
        'уведомление', 'уведомления', 'уведомлением',
        'почтовым', 'почтовые', 'почтовой'
    }

    # Слова, которые НЕ являются частью названия документа
    stop_words = {
        'от', '№', 'года', 'г.', 'года', 'год', 'месяца', 'дня',
        'в', 'на', 'по', 'с', 'из', 'для', 'от', 'до', 'через',
        'и', 'или', 'но', 'а', 'однако', 'поэтому', 'затем',
        'что', 'который', 'которая', 'которые', 'которых',
        'это', 'этот', 'эта', 'эти', 'этих',
        'такой', 'такая', 'такие', 'таких',
        'наш', 'наша', 'наше', 'наши',
        'ваш', 'ваша', 'ваше', 'ваши',
        'следующие', 'документы', 'документ', 'документом',
        'были', 'оформлены', 'оформлен', 'оформлена',
        'согласно', 'в соответствии', 'согласно'
    }

    # Идем влево и собираем слова
    while current_pos >= 0:
        # Ищем слово (последовательность букв)
        word_match = re.search(
            r'\b[а-яёa-z]+\b', left_text[:current_pos+1][::-1])
        if not word_match:
            current_pos -= 1
            continue

        word = word_match.group(0)[::-1].lower()  # Переворачиваем обратно

        # Если встретили стоп-слово, останавливаемся
        if word in stop_words:
            break

        # Если встретили ключевое слово документа, добавляем его
        if word in document_keywords:
            document_parts.insert(0, word)
            current_pos -= len(word_match.group(0))
        else:
            # Проверяем, может ли это быть частью составного слова
            # (например, "товарно-транспортная")
            if '-' in word or len(word) > 3:
                document_parts.insert(0, word)
            current_pos -= len(word_match.group(0))

    if not document_parts:
        return None, "", start_pos

    # Собираем полное название
    full_name = ' '.join(document_parts)

    # Определяем тип документа
    doc_type = determine_document_type(full_name)

    # Находим позицию начала документа
    doc_start_pos = search_start + current_pos + 1

    return doc_type, full_name, doc_start_pos


def determine_document_type(doc_name: str) -> str:
    """
    Определяет тип документа по названию
    """
    doc_name_lower = doc_name.lower()

    # Проверяем в порядке специфичности (от более специфичных к общим)
    if 'товарно-транспортн' in doc_name_lower:
        return 'cargo_waybills'
    elif 'товарн' in doc_name_lower and 'накладн' in doc_name_lower:
        return 'cargo_waybills'
    elif 'транспортн' in doc_name_lower and 'накладн' in doc_name_lower:
        return 'transport_waybills'
    elif 'накладн' in doc_name_lower:
        return 'general_waybills'
    elif 'договор' in doc_name_lower and 'заявк' in doc_name_lower:
        return 'contracts'
    elif 'заявк' in doc_name_lower:
        return 'contract_applications'
    elif 'счет' in doc_name_lower:
        return 'invoices'
    elif 'упд' in doc_name_lower or 'акт' in doc_name_lower:
        return 'upds'
    elif 'уведомлени' in doc_name_lower or 'почтов' in doc_name_lower:
        return 'postal'
    else:
        return 'other_cargo_docs'


def find_documents_with_numbers_grouped(text: str, debug=False) -> Dict[str, str]:
    """
    Новая логика: идем от даты влево и собираем документы
    """
    # Паттерн для поиска дат
    date_pattern = r'\d{2}\.\d{2}\.\d{4}(?:\s*г?\.?|)'

    # Находим все даты в тексте
    date_matches = list(re.finditer(date_pattern, text))

    if debug:
        print(f"[DEBUG] Найдено дат: {len(date_matches)}")

    # Словарь для группировки документов
    # Ключ: тип_документа, Значение: {нормализованное_название: {original_name, numbers}}
    grouped_documents = {k: {} for k in MORPHOLOGY.keys()}

    for i, date_match in enumerate(date_matches):
        date_text = date_match.group(0)
        date_pos = date_match.start()

        if debug:
            print(
                f"\n[DEBUG] Обрабатываем дату {i+1}: '{date_text}' на позиции {date_pos}")

        # 2.1. Остальные документы — ищем все паттерны '№ <номер> от <дата>' по всему сегменту
        doc_pat = re.compile(
            r'№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})(\s*г?\.?|,)?')
        for m in doc_pat.finditer(text):
            num = m.group(1)
            date = m.group(2)
            date_suffix = m.group(3) or ''
            date_full = date + date_suffix.strip()
            num_pos = m.start(1)

            if debug:
                print(f"[DEBUG] Найден паттерн: № {num} от {date_full}")

            # Собираем название документа слева от номера
            doc_left = text[:num_pos].strip()
            doc_name = None
            for group, info in MORPHOLOGY.items():
                for form in info['forms']:
                    if form.lower() in doc_left.lower():
                        doc_name = form
                        break
                if doc_name:
                    break

            if not doc_name:
                words = doc_left.split()
                if len(words) >= 2:
                    doc_name = ' '.join(words[-2:])
                elif len(words) == 1:
                    doc_name = words[0]
                else:
                    doc_name = doc_left

            if not doc_name:
                if debug:
                    print(
                        f"[DEBUG] Не удалось извлечь название документа из: {doc_left}")
                continue

            norm_name, doc_group = normalize_document_name(doc_name)
            if not norm_name or not doc_group:
                if debug:
                    print(
                        f"[DEBUG] Не удалось нормализовать документ: {doc_name}")
                continue

            if debug:
                print(
                    f"[DEBUG] Найден документ: {doc_name} -> {norm_name} ({doc_group})")

            key = doc_group
            if norm_name not in grouped_documents[key]:
                grouped_documents[key][norm_name] = set()
            grouped_documents[key][norm_name].add(f"№ {num} от {date_full}")

    # 3. Формируем итоговые блоки
    result = {}
    for key, docs in grouped_documents.items():
        if not docs:
            continue
        blocks = []
        for name, numbers in docs.items():
            # Определяем форму для вывода
            form = MORPHOLOGY[key]['singular'] if len(
                numbers) == 1 else MORPHOLOGY[key]['plural']
            # Всегда сортируем и выводим полные номера и даты
            blocks.append(
                f"{name} {', '.join(sorted(numbers, key=lambda x: (x.split('№')[1] if '№' in x else x)))}")
        # Особое форматирование для накладных (cargo_docs)
        if key == 'cargo_docs':
            result[key] = ', '.join(blocks)
        elif key == 'postal_block':
            # Для почтовых уведомлений: один блок с префиксом и все номера/даты через точку с запятой
            all_postal = []
            for name, numbers in docs.items():
                all_postal.extend(sorted(numbers, key=lambda x: (
                    x.split('№')[1] if '№' in x else x)))
            result[key] = 'Почтовые уведомления: ' + '; '.join(all_postal)
        else:
            result[key] = '; '.join(blocks)
    return result


def extract_document_blocks_simple(text: str) -> Dict[str, str]:
    """
    Парсер для юридических документов: находит документы слева от номеров и дат
    """
    text = re.sub(r'\s+', ' ', text).strip()

    # Находим все документы с номерами (с группировкой)
    return parse_legal_documents(text)


def extract_document_blocks_debug(text: str, debug=False) -> Dict[str, str]:
    """
    Основная функция - использует новый парсер с поддержкой debug
    """
    return parse_legal_documents(text, debug=debug)


# --- Основная функция ---
def parse_legal_documents(text: str, debug=False) -> Dict[str, str]:
    # 1. Сегментация
    segments = [s.strip() for s in SPLIT_REGEX.split(text) if s.strip()]
    if debug:
        print(f"[DEBUG] Сегментов: {len(segments)}")
        print("[DEBUG] Первые 5 сегментов:")
        for i, seg in enumerate(segments[:5]):
            print(f"[DEBUG] Сегмент {i+1}: {seg[:100]}...")

    # 2. Поиск документов
    found_docs = {k: {} for k in MORPHOLOGY}
    postal_blocks = []

    for seg_idx, seg in enumerate(segments):
        if debug and seg_idx < 10:  # Показываем первые 10 сегментов
            print(
                f"\n[DEBUG] Обрабатываем сегмент {seg_idx+1}: {seg[:150]}...")

        # 2.2. Почтовые документы — ищем ключ, собираем вправо до стоп-слова
        for form in MORPHOLOGY['postal_block']['forms']:
            m = re.search(form, seg, re.IGNORECASE)
            if m:
                if debug:
                    print(f"[DEBUG] Найдено почтовое уведомление: {form}")
                # Собираем весь блок вправо до стоп-слова
                start_pos = m.start()
                end_pos = len(seg)

                # Стоп-слова для почтовых документов
                stop_words = ['оригиналов', 'документов',
                              'заказчиком', 'перевозке', 'груз']

                # Ищем ближайшее стоп-слово справа
                for stop_word in stop_words:
                    stop_pos = seg.find(stop_word, start_pos)
                    if stop_pos != -1 and stop_pos < end_pos:
                        end_pos = stop_pos + len(stop_word)

                postal_block = seg[start_pos:end_pos].strip()
                if postal_block:
                    postal_blocks.append(postal_block)
                break

        # 2.1. Остальные документы — ищем все паттерны '№ <номер> от <дата>' по всему сегменту
        doc_pat = re.compile(
            r'№\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})(\s*г?\.?|,)?')
        for m in doc_pat.finditer(seg):
            num = m.group(1)
            date = m.group(2)
            date_suffix = m.group(3) or ''
            date_full = date + date_suffix.strip()
            num_pos = m.start(1)

            if debug:
                print(f"[DEBUG] Найден паттерн: № {num} от {date_full}")

            # Собираем название документа слева от номера
            doc_left = seg[:num_pos].strip()
            doc_name = None
            for group, info in MORPHOLOGY.items():
                for form in info['forms']:
                    if form.lower() in doc_left.lower():
                        doc_name = form
                        break
                if doc_name:
                    break

            if not doc_name:
                words = doc_left.split()
                if len(words) >= 2:
                    doc_name = ' '.join(words[-2:])
                elif len(words) == 1:
                    doc_name = words[0]
                else:
                    doc_name = doc_left

            if not doc_name:
                if debug:
                    print(
                        f"[DEBUG] Не удалось извлечь название документа из: {doc_left}")
                continue

            norm_name, doc_group = normalize_document_name(doc_name)
            if not norm_name or not doc_group:
                if debug:
                    print(
                        f"[DEBUG] Не удалось нормализовать документ: {doc_name}")
                continue

            if debug:
                print(
                    f"[DEBUG] Найден документ: {doc_name} -> {norm_name} ({doc_group})")

            key = doc_group
            if norm_name not in found_docs[key]:
                found_docs[key][norm_name] = set()
            found_docs[key][norm_name].add(f"№ {num} от {date_full}")

    # 3. Формируем итоговые блоки
    result = {}
    for key, docs in found_docs.items():
        if not docs:
            continue
        blocks = []
        for name, numbers in docs.items():
            # Определяем форму для вывода
            form = MORPHOLOGY[key]['singular'] if len(
                numbers) == 1 else MORPHOLOGY[key]['plural']
            # Всегда сортируем и выводим полные номера и даты
            blocks.append(
                f"{name} {', '.join(sorted(numbers, key=lambda x: (x.split('№')[1] if '№' in x else x)))}")
        # Особое форматирование для накладных (cargo_docs)
        if key == 'cargo_docs':
            result[key] = ', '.join(blocks)
        elif key == 'postal_block':
            # Для почтовых уведомлений: один блок с префиксом и все номера/даты через точку с запятой
            all_postal = []
            for name, numbers in docs.items():
                all_postal.extend(sorted(numbers, key=lambda x: (
                    x.split('№')[1] if '№' in x else x)))
            result[key] = 'Почтовые уведомления: ' + '; '.join(all_postal)
        else:
            result[key] = '; '.join(blocks)
    if postal_blocks:
        # Почтовые уведомления: объединяем все найденные блоки, добавляем префикс (если вдруг что-то осталось)
        if 'postal_block' in result:
            result['postal_block'] += '; ' + '; '.join(postal_blocks)
        else:
            result['postal_block'] = 'Почтовые уведомления: ' + \
                '; '.join(postal_blocks)
    return result
