"""
Тесты для модуля валидации данных.
"""

import unittest
from validators import (
    DataValidator,
    EntityType,
    ValidationStatus,
    is_valid_inn,
    is_valid_ogrn,
    get_entity_type
)


class TestINNValidation(unittest.TestCase):
    """Тесты валидации ИНН"""

    def setUp(self):
        self.validator = DataValidator()

    def test_valid_inn_10_digits(self):
        """Тест валидного 10-значного ИНН (ООО)"""
        # ИНН ООО "Яндекс"
        result = self.validator.validate_inn("7736207543")
        self.assertTrue(result.is_valid())
        self.assertEqual(result.entity_type, EntityType.LEGAL_ENTITY)
        self.assertEqual(result.value, "7736207543")

    def test_valid_inn_12_digits(self):
        """Тест валидного 12-значного ИНН (ИП)"""
        # Реальный ИНН ИП (пример)
        result = self.validator.validate_inn("526317984689")
        self.assertTrue(result.is_valid())
        self.assertEqual(result.entity_type, EntityType.INDIVIDUAL)

    def test_invalid_inn_wrong_checksum(self):
        """Тест ИНН с неверной контрольной суммой"""
        result = self.validator.validate_inn("7736207544")  # последняя цифра изменена
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.INVALID_CHECKSUM)

    def test_invalid_inn_wrong_length(self):
        """Тест ИНН с неверной длиной"""
        result = self.validator.validate_inn("123456789")  # только 9 цифр
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.INVALID_FORMAT)

    def test_inn_not_provided(self):
        """Тест отсутствующего ИНН"""
        result = self.validator.validate_inn(None)
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.NOT_PROVIDED)

    def test_inn_with_spaces(self):
        """Тест ИНН с пробелами"""
        result = self.validator.validate_inn("7736 2075 43")
        self.assertTrue(result.is_valid())
        self.assertEqual(result.value, "7736207543")


class TestKPPValidation(unittest.TestCase):
    """Тесты валидации КПП"""

    def setUp(self):
        self.validator = DataValidator()

    def test_valid_kpp(self):
        """Тест валидного КПП"""
        result = self.validator.validate_kpp("773601001", EntityType.LEGAL_ENTITY)
        self.assertTrue(result.is_valid())
        self.assertEqual(result.value, "773601001")

    def test_kpp_for_individual(self):
        """Тест КПП для ИП (должен быть пустым)"""
        result = self.validator.validate_kpp(None, EntityType.INDIVIDUAL)
        self.assertTrue(result.is_valid())
        self.assertIsNone(result.value)

    def test_kpp_provided_for_individual(self):
        """Тест КПП у ИП (ошибка)"""
        result = self.validator.validate_kpp("123456789", EntityType.INDIVIDUAL)
        self.assertFalse(result.is_valid())

    def test_invalid_kpp_length(self):
        """Тест КПП с неверной длиной"""
        result = self.validator.validate_kpp("12345678", EntityType.LEGAL_ENTITY)
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.INVALID_FORMAT)


class TestOGRNValidation(unittest.TestCase):
    """Тесты валидации ОГРН/ОГРНИП"""

    def setUp(self):
        self.validator = DataValidator()

    def test_valid_ogrn_13_digits(self):
        """Тест валидного 13-значного ОГРН"""
        # ОГРН ООО "Яндекс"
        result = self.validator.validate_ogrn("1027700229193")
        self.assertTrue(result.is_valid())
        self.assertEqual(result.entity_type, EntityType.LEGAL_ENTITY)
        self.assertEqual(result.value, "1027700229193")

    def test_valid_ogrnip_15_digits(self):
        """Тест валидного 15-значного ОГРНИП"""
        # Пример ОГРНИП
        result = self.validator.validate_ogrn("304500116000157")
        self.assertTrue(result.is_valid())
        self.assertEqual(result.entity_type, EntityType.INDIVIDUAL)

    def test_invalid_ogrn_wrong_checksum(self):
        """Тест ОГРН с неверной контрольной цифрой"""
        result = self.validator.validate_ogrn("1027700229194")  # последняя цифра изменена
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.INVALID_CHECKSUM)

    def test_invalid_ogrn_wrong_length(self):
        """Тест ОГРН с неверной длиной"""
        result = self.validator.validate_ogrn("102770022919")  # только 12 цифр
        self.assertFalse(result.is_valid())
        self.assertEqual(result.status, ValidationStatus.INVALID_FORMAT)

    def test_ogrn_type_mismatch(self):
        """Тест несоответствия типа ОГРН ожидаемому типу"""
        # ОГРН юр.лица, но ожидаем ИП
        result = self.validator.validate_ogrn("1027700229193", EntityType.INDIVIDUAL)
        self.assertFalse(result.is_valid())


class TestEntityValidation(unittest.TestCase):
    """Тесты комплексной валидации организации"""

    def setUp(self):
        self.validator = DataValidator()

    def test_valid_legal_entity(self):
        """Тест валидного юридического лица"""
        report = self.validator.validate_entity(
            inn="7736207543",
            kpp="773601001",
            ogrn="1027700229193"
        )
        self.assertTrue(report.is_valid)
        self.assertEqual(report.entity_type, EntityType.LEGAL_ENTITY)
        self.assertEqual(len(report.warnings), 0)

    def test_valid_individual(self):
        """Тест валидного ИП"""
        report = self.validator.validate_entity(
            inn="526317984689",
            kpp=None,
            ogrn="304500116000157"
        )
        self.assertTrue(report.is_valid)
        self.assertEqual(report.entity_type, EntityType.INDIVIDUAL)

    def test_inconsistent_entity_types(self):
        """Тест несогласованных типов (ИНН юр.лица, ОГРНИП)"""
        report = self.validator.validate_entity(
            inn="7736207543",  # ИНН юр.лица
            kpp="773601001",
            ogrn="304500116000157"  # ОГРНИП
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(len(report.warnings) > 0)

    def test_missing_kpp_for_legal_entity(self):
        """Тест отсутствия КПП у юр.лица"""
        report = self.validator.validate_entity(
            inn="7736207543",
            kpp=None,
            ogrn="1027700229193"
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("КПП" in w for w in report.warnings))


class TestHelperFunctions(unittest.TestCase):
    """Тесты вспомогательных функций"""

    def test_is_valid_inn(self):
        """Тест быстрой проверки ИНН"""
        self.assertTrue(is_valid_inn("7736207543"))
        self.assertFalse(is_valid_inn("7736207544"))

    def test_is_valid_ogrn(self):
        """Тест быстрой проверки ОГРН"""
        self.assertTrue(is_valid_ogrn("1027700229193"))
        self.assertFalse(is_valid_ogrn("1027700229194"))

    def test_get_entity_type_from_inn(self):
        """Тест определения типа по ИНН"""
        result1 = get_entity_type(inn="7736207543")
        self.assertEqual(result1, EntityType.LEGAL_ENTITY)
        result2 = get_entity_type(inn="526317984689")
        self.assertEqual(result2, EntityType.INDIVIDUAL)

    def test_get_entity_type_from_ogrn(self):
        """Тест определения типа по ОГРН"""
        self.assertEqual(get_entity_type(ogrn="1027700229193"), EntityType.LEGAL_ENTITY)
        self.assertEqual(get_entity_type(ogrn="304500116000157"), EntityType.INDIVIDUAL)

    def test_get_entity_type_unknown(self):
        """Тест неизвестного типа"""
        self.assertEqual(get_entity_type(), EntityType.UNKNOWN)


if __name__ == '__main__':
    # Запуск тестов
    unittest.main(verbosity=2)
