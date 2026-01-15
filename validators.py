"""
Модуль валидации данных для юридических лиц и ИП.
Проверяет ИНН, КПП, ОГРН по алгоритмам ФНС России.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class EntityType(Enum):
    """Типы организаций"""
    LEGAL_ENTITY = "legal_entity"  # Юридическое лицо (ООО, АО и т.д.)
    INDIVIDUAL = "individual"       # ИП
    UNKNOWN = "unknown"


class ValidationStatus(Enum):
    """Статус валидации"""
    VALID = "valid"
    INVALID = "invalid"
    NOT_PROVIDED = "not_provided"
    INVALID_FORMAT = "invalid_format"
    INVALID_CHECKSUM = "invalid_checksum"


@dataclass
class ValidationResult:
    """Результат валидации поля"""
    field_name: str
    status: ValidationStatus
    value: Optional[str]
    error_message: Optional[str] = None
    entity_type: Optional[EntityType] = None

    def is_valid(self) -> bool:
        """Проверяет, валидно ли поле"""
        return self.status == ValidationStatus.VALID

    def __str__(self) -> str:
        if self.is_valid():
            return f"✅ {self.field_name}: {self.value}"
        else:
            return f"❌ {self.field_name}: {self.error_message}"


@dataclass
class EntityValidationReport:
    """Полный отчет о валидации данных организации"""
    inn: ValidationResult
    kpp: ValidationResult
    ogrn: ValidationResult
    entity_type: EntityType
    warnings: List[str]
    is_valid: bool

    def get_summary(self) -> str:
        """Возвращает краткую сводку о валидации"""
        lines = [
            f"Тип: {self._entity_type_name()}",
            "",
            str(self.inn),
            str(self.kpp),
            str(self.ogrn),
        ]

        if self.warnings:
            lines.append("")
            lines.append("Предупреждения:")
            for warning in self.warnings:
                lines.append(f"  ⚠️ {warning}")

        return "\n".join(lines)

    def _entity_type_name(self) -> str:
        """Возвращает название типа организации"""
        if self.entity_type == EntityType.LEGAL_ENTITY:
            return "Юридическое лицо (ООО, АО и т.д.)"
        elif self.entity_type == EntityType.INDIVIDUAL:
            return "Индивидуальный предприниматель"
        else:
            return "Неизвестный тип"


class DataValidator:
    """Класс для валидации данных юридических лиц и ИП"""

    # Регулярные выражения для форматов
    INN_PATTERN_10 = re.compile(r'^\d{10}$')
    INN_PATTERN_12 = re.compile(r'^\d{12}$')
    KPP_PATTERN = re.compile(r'^\d{9}$')
    OGRN_PATTERN = re.compile(r'^\d{13}$')
    OGRNIP_PATTERN = re.compile(r'^\d{15}$')

    def validate_inn(self, inn: Optional[str]) -> ValidationResult:
        """
        Валидирует ИНН по алгоритму ФНС РФ.

        ИНН может быть:
        - 10 цифр (юридическое лицо)
        - 12 цифр (ИП или физ.лицо)

        Args:
            inn: ИНН для проверки

        Returns:
            ValidationResult
        """
        if not inn:
            return ValidationResult(
                field_name="ИНН",
                status=ValidationStatus.NOT_PROVIDED,
                value=None,
                error_message="ИНН не указан"
            )

        # Очистка от пробелов и других символов
        inn_clean = re.sub(r'[^\d]', '', inn)

        # Проверка формата
        if len(inn_clean) == 10:
            # ИНН юридического лица
            entity_type = EntityType.LEGAL_ENTITY
        elif len(inn_clean) == 12:
            # ИНН ИП
            entity_type = EntityType.INDIVIDUAL
        else:
            return ValidationResult(
                field_name="ИНН",
                status=ValidationStatus.INVALID_FORMAT,
                value=inn,
                error_message=f"ИНН должен содержать 10 или 12 цифр (найдено: {len(inn_clean)})"
            )

        # Проверка контрольной суммы
        if not self._validate_inn_checksum(inn_clean):
            return ValidationResult(
                field_name="ИНН",
                status=ValidationStatus.INVALID_CHECKSUM,
                value=inn_clean,
                error_message="ИНН не прошел проверку контрольной суммы",
                entity_type=entity_type
            )

        return ValidationResult(
            field_name="ИНН",
            status=ValidationStatus.VALID,
            value=inn_clean,
            entity_type=entity_type
        )

    def _validate_inn_checksum(self, inn: str) -> bool:
        """
        Проверяет контрольную сумму ИНН.

        Алгоритм ФНС:
        - 10 цифр: одна контрольная цифра
        - 12 цифр: две контрольные цифры

        Args:
            inn: ИНН (только цифры)

        Returns:
            True если контрольная сумма верна
        """
        if len(inn) == 10:
            # Коэффициенты для 10-значного ИНН
            coeffs = [2, 4, 10, 3, 5, 9, 4, 6, 8]
            checksum = sum(int(inn[i]) * coeffs[i] for i in range(9)) % 11 % 10
            return checksum == int(inn[9])

        elif len(inn) == 12:
            # Коэффициенты для 12-значного ИНН (два контрольных числа)
            coeffs1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
            coeffs2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]

            check1 = sum(int(inn[i]) * coeffs1[i] for i in range(10)) % 11 % 10
            check2 = sum(int(inn[i]) * coeffs2[i] for i in range(11)) % 11 % 10

            return check1 == int(inn[10]) and check2 == int(inn[11])

        return False

    def validate_kpp(
        self,
        kpp: Optional[str],
        entity_type: Optional[EntityType] = None
    ) -> ValidationResult:
        """
        Валидирует КПП.

        КПП должен быть 9 цифр.
        У ИП КПП отсутствует.

        Args:
            kpp: КПП для проверки
            entity_type: Тип организации (если известен)

        Returns:
            ValidationResult
        """
        # Если это ИП, КПП не требуется
        if entity_type == EntityType.INDIVIDUAL:
            if kpp and kpp.strip():
                return ValidationResult(
                    field_name="КПП",
                    status=ValidationStatus.INVALID,
                    value=kpp,
                    error_message="У ИП не должно быть КПП"
                )
            else:
                return ValidationResult(
                    field_name="КПП",
                    status=ValidationStatus.VALID,
                    value=None,
                    error_message="КПП не требуется для ИП"
                )

        # Для юридических лиц КПП обязателен
        if not kpp:
            if entity_type == EntityType.LEGAL_ENTITY:
                return ValidationResult(
                    field_name="КПП",
                    status=ValidationStatus.NOT_PROVIDED,
                    value=None,
                    error_message="КПП обязателен для юридических лиц"
                )
            else:
                return ValidationResult(
                    field_name="КПП",
                    status=ValidationStatus.NOT_PROVIDED,
                    value=None,
                    error_message="КПП не указан"
                )

        # Очистка от пробелов
        kpp_clean = re.sub(r'[^\d]', '', kpp)

        # Проверка формата
        if not self.KPP_PATTERN.match(kpp_clean):
            return ValidationResult(
                field_name="КПП",
                status=ValidationStatus.INVALID_FORMAT,
                value=kpp,
                error_message=f"КПП должен содержать 9 цифр (найдено: {len(kpp_clean)})"
            )

        return ValidationResult(
            field_name="КПП",
            status=ValidationStatus.VALID,
            value=kpp_clean
        )

    def validate_ogrn(
        self,
        ogrn: Optional[str],
        entity_type: Optional[EntityType] = None
    ) -> ValidationResult:
        """
        Валидирует ОГРН/ОГРНИП по алгоритму ФНС РФ.

        ОГРН может быть:
        - 13 цифр (юридическое лицо)
        - 15 цифр (ИП - ОГРНИП)

        Args:
            ogrn: ОГРН для проверки
            entity_type: Тип организации (если известен)

        Returns:
            ValidationResult
        """
        if not ogrn:
            return ValidationResult(
                field_name="ОГРН",
                status=ValidationStatus.NOT_PROVIDED,
                value=None,
                error_message="ОГРН не указан"
            )

        # Очистка от пробелов
        ogrn_clean = re.sub(r'[^\d]', '', ogrn)

        # Определение типа по длине
        if len(ogrn_clean) == 13:
            detected_type = EntityType.LEGAL_ENTITY
            field_name = "ОГРН"
        elif len(ogrn_clean) == 15:
            detected_type = EntityType.INDIVIDUAL
            field_name = "ОГРНИП"
        else:
            return ValidationResult(
                field_name="ОГРН",
                status=ValidationStatus.INVALID_FORMAT,
                value=ogrn,
                error_message=f"ОГРН должен содержать 13 цифр, ОГРНИП - 15 цифр (найдено: {len(ogrn_clean)})"
            )

        # Проверка соответствия типу организации
        if entity_type and entity_type != detected_type:
            type_name = "юридического лица" if entity_type == EntityType.LEGAL_ENTITY else "ИП"
            return ValidationResult(
                field_name=field_name,
                status=ValidationStatus.INVALID,
                value=ogrn_clean,
                error_message=f"ОГРН не соответствует типу организации ({type_name})",
                entity_type=detected_type
            )

        # Проверка контрольной цифры
        if not self._validate_ogrn_checksum(ogrn_clean):
            return ValidationResult(
                field_name=field_name,
                status=ValidationStatus.INVALID_CHECKSUM,
                value=ogrn_clean,
                error_message=f"{field_name} не прошел проверку контрольной цифры",
                entity_type=detected_type
            )

        return ValidationResult(
            field_name=field_name,
            status=ValidationStatus.VALID,
            value=ogrn_clean,
            entity_type=detected_type
        )

    def _validate_ogrn_checksum(self, ogrn: str) -> bool:
        """
        Проверяет контрольную цифру ОГРН/ОГРНИП.

        Алгоритм ФНС:
        - ОГРН (13 цифр): (первые 12 цифр mod 11) mod 10 = последняя цифра
        - ОГРНИП (15 цифр): (первые 14 цифр mod 13) mod 10 = последняя цифра

        Args:
            ogrn: ОГРН/ОГРНИП (только цифры)

        Returns:
            True если контрольная цифра верна
        """
        if len(ogrn) == 13:
            # ОГРН юридического лица
            number = int(ogrn[:12])
            check_digit = int(ogrn[12])
            calculated = (number % 11) % 10
            return calculated == check_digit

        elif len(ogrn) == 15:
            # ОГРНИП
            number = int(ogrn[:14])
            check_digit = int(ogrn[14])
            calculated = (number % 13) % 10
            return calculated == check_digit

        return False

    def validate_entity(
        self,
        inn: Optional[str],
        kpp: Optional[str],
        ogrn: Optional[str]
    ) -> EntityValidationReport:
        """
        Комплексная валидация данных организации.

        Проверяет все поля и их согласованность между собой.

        Args:
            inn: ИНН
            kpp: КПП
            ogrn: ОГРН/ОГРНИП

        Returns:
            EntityValidationReport с полным отчетом
        """
        warnings = []

        # Шаг 1: Валидация ИНН
        inn_result = self.validate_inn(inn)

        # Шаг 2: Определение типа организации
        entity_type = EntityType.UNKNOWN

        if inn_result.is_valid() and inn_result.entity_type:
            entity_type = inn_result.entity_type

        # Шаг 3: Валидация ОГРН
        ogrn_result = self.validate_ogrn(ogrn, entity_type)

        # Проверка согласованности ИНН и ОГРН
        if inn_result.entity_type and ogrn_result.entity_type:
            if inn_result.entity_type != ogrn_result.entity_type:
                warnings.append(
                    f"ИНН указывает на {inn_result.entity_type.value}, "
                    f"а ОГРН на {ogrn_result.entity_type.value}"
                )
                # Используем тип из ИНН как приоритетный
                entity_type = inn_result.entity_type
            elif inn_result.is_valid() and ogrn_result.is_valid():
                # Если оба поля согласованы, используем их тип
                entity_type = inn_result.entity_type

        # Шаг 4: Валидация КПП с учетом типа
        kpp_result = self.validate_kpp(kpp, entity_type)

        # Общая валидность
        is_valid = (
            inn_result.is_valid() and
            ogrn_result.is_valid() and
            (kpp_result.is_valid() or entity_type == EntityType.INDIVIDUAL)
        )

        # Дополнительные предупреждения
        if entity_type == EntityType.UNKNOWN:
            warnings.append("Не удалось определить тип организации")

        if entity_type == EntityType.LEGAL_ENTITY and not kpp_result.is_valid():
            warnings.append("КПП обязателен для юридических лиц")

        return EntityValidationReport(
            inn=inn_result,
            kpp=kpp_result,
            ogrn=ogrn_result,
            entity_type=entity_type,
            warnings=warnings,
            is_valid=is_valid
        )


# Удобные функции для быстрой проверки
def is_valid_inn(inn: str) -> bool:
    """Быстрая проверка ИНН"""
    validator = DataValidator()
    return validator.validate_inn(inn).is_valid()


def is_valid_ogrn(ogrn: str) -> bool:
    """Быстрая проверка ОГРН"""
    validator = DataValidator()
    return validator.validate_ogrn(ogrn).is_valid()


def get_entity_type(inn: Optional[str] = None, ogrn: Optional[str] = None) -> EntityType:
    """
    Определяет тип организации по ИНН или ОГРН.

    Args:
        inn: ИНН
        ogrn: ОГРН/ОГРНИП

    Returns:
        EntityType
    """
    if inn:
        clean_inn = re.sub(r'[^\d]', '', inn)
        if len(clean_inn) == 10:
            return EntityType.LEGAL_ENTITY
        elif len(clean_inn) == 12:
            return EntityType.INDIVIDUAL

    if ogrn:
        clean_ogrn = re.sub(r'[^\d]', '', ogrn)
        if len(clean_ogrn) == 13:
            return EntityType.LEGAL_ENTITY
        elif len(clean_ogrn) == 15:
            return EntityType.INDIVIDUAL

    return EntityType.UNKNOWN
