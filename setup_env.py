#!/usr/bin/env python3
"""
Скрипт для настройки переменных окружения
"""

import os


def create_env_file():
    """Создает файл .env с токеном бота"""
    env_content = """# Telegram Bot Token
# Получите токен у @BotFather в Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Замените 'your_bot_token_here' на ваш реальный токен
"""

    if os.path.exists('.env'):
        print("Файл .env уже существует!")
        return

    with open('.env', 'w', encoding='utf-8') as f:
        f.write(env_content)

    print("Файл .env создан!")
    print("Не забудьте заменить 'your_bot_token_here' на ваш реальный токен бота")


if __name__ == "__main__":
    create_env_file()
