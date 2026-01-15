"""
Модуль для определения подсудности споров.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from config import COURTS_DATABASE

logger = logging.getLogger(__name__)


class JurisdictionType(Enum):
    """Типы подсудности"""
    DEFENDANT_ADDRESS = "defendant_address"  # По месту ответчика (ст. 35 АПК РФ)
    CONTRACTUAL = "contractual"              # Договорная (ст. 37 АПК РФ)
    PLAINTIFF_ADDRESS = "plaintiff_address"  # По месту истца (редко)
    CUSTOM = "custom"                        # Другой суд


@dataclass
class JurisdictionInfo:
    """Информация о подсудности"""
    type: JurisdictionType
    court_name: str
    court_address: str
    confidence: float  # Уверенность определения (0.0 - 1.0)
    extracted_text: Optional[str] = None  # Текст из договора о подсудности
    region: Optional[str] = None


class JurisdictionDetector:
    """Класс для определения подсудности из текста документа"""

    # Паттерны для поиска условий о подсудности
    JURISDICTION_PATTERNS = [
        # "Подсудность споров - Арбитражный суд..."
        r'[Пп]одсудност[ьи]\s+спор[а-я]*\s*[—\-:]\s*(.+?)(?:\.|;|\n)',

        # "Споры разрешаются в Арбитражном суде..."
        r'[Сс]пор[ыа]*\s+(?:разрешаются|рассматриваются)\s+в\s+(.+?)(?:\.|;|\n)',

        # "Споры подлежат рассмотрению в..."
        r'[Сс]пор[ыа]*\s+подлежат\s+рассмотрению\s+в\s+(.+?)(?:\.|;|\n)',

        # "... подсудны Арбитражному суду..."
        r'подсуд[нь][ыа]*\s+(.+?)(?:\.|;|\n)',

        # "По месту нахождения истца/ответчика"
        r'[Пп]о\s+месту\s+нахождения\s+(истца|ответчика|исполнителя|заказчика)',
    ]

    # Паттерн для извлечения названия суда
    COURT_NAME_PATTERN = re.compile(
        r'[Аа]рбитражн[а-я]+\s+суд[а-я]*\s+([А-Яа-я\s\-]+(?:област[иья]|кра[йя]|республик[аи]|город[а]?\s+[А-Яа-я\-]+))',
        re.IGNORECASE
    )

    def __init__(self):
        """Инициализация детектора"""
        pass

    def detect_jurisdiction(
        self,
        text: str,
        plaintiff_address: str = "",
        defendant_address: str = ""
    ) -> JurisdictionInfo:
        """
        Определяет подсудность из текста документа.

        Args:
            text: Текст документа (претензия/договор)
            plaintiff_address: Адрес истца
            defendant_address: Адрес ответчика

        Returns:
            JurisdictionInfo: Информация о подсудности
        """
        # 1. Ищем упоминание договорной подсудности
        contractual_info = self._find_contractual_jurisdiction(text)

        if contractual_info:
            logger.info(
                f"Найдена договорная подсудность: {contractual_info.court_name}"
            )
            return contractual_info

        # 2. Если договорной нет, определяем по месту ответчика (по умолчанию)
        logger.info("Договорная подсудность не найдена, применяется ст. 35 АПК РФ")
        return self._get_default_jurisdiction(defendant_address)

    def _find_contractual_jurisdiction(self, text: str) -> Optional[JurisdictionInfo]:
        """
        Ищет условие о договорной подсудности в тексте.

        Args:
            text: Текст документа

        Returns:
            JurisdictionInfo или None, если не найдено
        """
        for pattern in self.JURISDICTION_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)

            for match in matches:
                clause_text = match.group(0)
                logger.debug(f"Найден фрагмент о подсудности: {clause_text}")

                # Пытаемся извлечь название суда
                court_match = self.COURT_NAME_PATTERN.search(clause_text)

                if court_match:
                    region = court_match.group(1).strip()
                    court_info = self._find_court_by_region(region)

                    if court_info:
                        return JurisdictionInfo(
                            type=JurisdictionType.CONTRACTUAL,
                            court_name=court_info['name'],
                            court_address=court_info['address'],
                            confidence=0.9,
                            extracted_text=clause_text,
                            region=region
                        )

                # Проверяем фразы типа "по месту нахождения ответчика"
                if re.search(r'по\s+месту\s+нахождения\s+ответчика', clause_text, re.IGNORECASE):
                    # Это по сути стандартная подсудность, но указанная в договоре
                    return None  # Вернем None, чтобы использовать стандартную логику

        return None

    def _find_court_by_region(self, region: str) -> Optional[Dict[str, str]]:
        """
        Находит суд по названию региона.

        Args:
            region: Название региона

        Returns:
            Словарь с данными суда или None
        """
        region_lower = region.lower().strip()

        # Точное совпадение
        if region in COURTS_DATABASE:
            return COURTS_DATABASE[region]

        # Нечеткий поиск
        for court_region, court_info in COURTS_DATABASE.items():
            if region_lower in court_region.lower() or court_region.lower() in region_lower:
                logger.debug(f"Найдено совпадение: {region} -> {court_region}")
                return court_info

        logger.warning(f"Суд для региона '{region}' не найден в базе")
        return None

    def _get_default_jurisdiction(self, defendant_address: str) -> JurisdictionInfo:
        """
        Определяет подсудность по умолчанию (по месту ответчика).

        Args:
            defendant_address: Адрес ответчика

        Returns:
            JurisdictionInfo
        """
        from courts_code import ARBITRATION_COURTS, CITY_TO_REGION

        try:
            defendant_address_lower = defendant_address.lower()

            # Сначала ищем по названию региона
            court_name = None
            court_address = None

            for region, court_info in ARBITRATION_COURTS.items():
                if region.lower() in defendant_address_lower:
                    court_name = court_info["name"]
                    court_address = court_info["address"]
                    break

            # Если регион не найден, ищем по городам
            if not court_name:
                for city, region in CITY_TO_REGION.items():
                    if city in defendant_address_lower:
                        if region in ARBITRATION_COURTS:
                            court_info = ARBITRATION_COURTS[region]
                            court_name = court_info["name"]
                            court_address = court_info["address"]
                            break

            # Если ничего не найдено
            if not court_name:
                court_name = "Арбитражный суд по месту нахождения ответчика"
                court_address = "Адрес суда не определен"

            return JurisdictionInfo(
                type=JurisdictionType.DEFENDANT_ADDRESS,
                court_name=court_name,
                court_address=court_address,
                confidence=0.95 if "не определен" not in court_address.lower() else 0.5
            )
        except Exception as e:
            logger.error(f"Ошибка определения суда: {e}")
            return JurisdictionInfo(
                type=JurisdictionType.DEFENDANT_ADDRESS,
                court_name="Арбитражный суд по месту нахождения ответчика",
                court_address="Адрес суда не определен",
                confidence=0.3
            )

    def get_all_courts(self) -> List[Tuple[str, str, str]]:
        """
        Возвращает список всех судов из базы.

        Returns:
            List[(region, court_name, court_address)]
        """
        courts = []
        for region, info in COURTS_DATABASE.items():
            courts.append((region, info['name'], info['address']))
        return sorted(courts, key=lambda x: x[0])


def format_jurisdiction_for_user(info: JurisdictionInfo) -> str:
    """
    Форматирует информацию о подсудности для отображения пользователю.

    Args:
        info: Информация о подсудности

    Returns:
        Отформатированная строка
    """
    type_names = {
        JurisdictionType.DEFENDANT_ADDRESS: "По месту нахождения ответчика (ст. 35 АПК РФ)",
        JurisdictionType.CONTRACTUAL: "Договорная подсудность (ст. 37 АПК РФ)",
        JurisdictionType.PLAINTIFF_ADDRESS: "По месту нахождения истца",
        JurisdictionType.CUSTOM: "Указано в договоре"
    }

    result = f"📍 Тип: {type_names.get(info.type, 'Не определен')}\n"
    result += f"🏛 Суд: {info.court_name}\n"
    result += f"📮 Адрес: {info.court_address}\n"

    if info.extracted_text:
        result += f"\n💬 Найдено в документе:\n\"{info.extracted_text[:200]}...\""

    return result


# Вспомогательная функция для обратной совместимости
def get_court_by_address(defendant_address: str) -> Tuple[str, str]:
    """
    Wrapper для существующей функции.

    Args:
        defendant_address: Адрес ответчика

    Returns:
        (court_name, court_address)
    """
    detector = JurisdictionDetector()
    info = detector._get_default_jurisdiction(defendant_address)
    return info.court_name, info.court_address
