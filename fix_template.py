#!/usr/bin/env python3
"""
Скрипт для исправления шаблона - убирает слова "Грузосопроводительными документами"
"""

from docx import Document


def fix_template():
    """Убирает слова 'Грузосопроводительными документами' из шаблона"""
    doc = Document('template.docx')

    # Находим параграфы с "Грузосопроводительными документами"
    for paragraph in doc.paragraphs:
        if 'Грузосопроводительными документами' in paragraph.text:
            # Заменяем текст, убирая "Грузосопроводительными документами"
            new_text = paragraph.text.replace(
                'Грузосопроводительными документами ', '')
            paragraph.text = new_text
            print(f"Исправлен параграф: {new_text}")

    # Сохраняем изменения
    doc.save('template.docx')
    print("Шаблон исправлен")


if __name__ == "__main__":
    fix_template()
