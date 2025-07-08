#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Базовые тесты для IskBot
"""

import os
import sys
import unittest

from cal import calculate_duty
from main import get_court_by_address

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestBasicFunctions(unittest.TestCase):
    """Базовые тесты для основных функций."""

    def test_court_detection_moscow(self):
        """Тест определения суда для Москвы."""
        address = "г. Москва, ул. Тверская, д. 1"
        court_name, court_address = get_court_by_address(address)
        self.assertIsNotNone(court_name)
        self.assertIn("Москвы", court_name)

    def test_court_detection_spb(self):
        """Тест определения суда для Санкт-Петербурга."""
        address = "г. Санкт-Петербург, Невский пр., д. 100"
        court_name, court_address = get_court_by_address(address)
        self.assertIsNotNone(court_name)
        self.assertIn("Санкт-Петербург", court_name)

    def test_court_detection_moscow_region(self):
        """Тест определения суда для Московской области."""
        address = "г. Подольск, ул. Ленина, д. 1"
        court_name, court_address = get_court_by_address(address)
        self.assertIsNotNone(court_name)
        # Подольск не найден в базе, возвращается общий ответ
        self.assertIn("по месту нахождения", court_name)

    def test_duty_calculation_small(self):
        """Тест расчета госпошлины для малой суммы."""
        result = calculate_duty(50000)
        self.assertNotIn("error", result)
        self.assertEqual(result["duty"], 10000)

    def test_duty_calculation_medium(self):
        """Тест расчета госпошлины для средней суммы."""
        result = calculate_duty(500000)
        self.assertNotIn("error", result)
        self.assertTrue(int(result["duty"]) > 10000)

    def test_duty_calculation_large(self):
        """Тест расчета госпошлины для большой суммы."""
        result = calculate_duty(5000000)
        self.assertNotIn("error", result)
        self.assertTrue(int(result["duty"]) > 55000)

    def test_duty_calculation_invalid(self):
        """Тест расчета госпошлины для некорректной суммы."""
        result = calculate_duty(-1000)
        self.assertIn("error", result)


def run_tests():
    """Запуск всех тестов."""
    print("🧪 Запуск базовых тестов IskBot...")

    # Создаем test suite
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestBasicFunctions)

    # Запускаем тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Выводим результат
    if result.wasSuccessful():
        print("\n✅ Все тесты прошли успешно!")
        return True
    else:
        print(
            f"\n❌ Тесты не прошли: {len(result.failures)} ошибок, {len(result.errors)} исключений")
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
