# Настройка SSH туннеля для безопасного подключения к Ollama

## Шаг 1: Настройка SSH на компьютере с Ollama (192.168.0.106)

### Linux:
```bash
# Установите OpenSSH Server (если не установлен)
sudo apt update
sudo apt install openssh-server

# Запустите SSH сервис
sudo systemctl start ssh
sudo systemctl enable ssh

# Проверьте статус
sudo systemctl status ssh
```

### macOS:
```bash
# Включите "Удаленный вход" (Remote Login)
sudo systemsetup -setremotelogin on

# Или через GUI:
# Системные настройки → Общий доступ → Удаленный вход (включить)
```

### Windows:
```powershell
# Установите OpenSSH Server через PowerShell (от администратора)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Запустите сервис
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'
```

## Шаг 2: Настройка SSH ключей (рекомендуется)

### На компьютере с ботом (вашем Mac):

```bash
# Сгенерируйте SSH ключ (если еще нет)
ssh-keygen -t ed25519 -C "iskbot-ollama-tunnel"

# Нажмите Enter для всех вопросов (или установите passphrase для безопасности)

# Скопируйте публичный ключ на сервер с Ollama
ssh-copy-id USERNAME@192.168.0.106

# Где USERNAME - ваш логин на компьютере с Ollama
```

### Если ssh-copy-id не работает:

```bash
# Ручное копирование ключа
cat ~/.ssh/id_ed25519.pub | ssh USERNAME@192.168.0.106 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

## Шаг 3: Проверка SSH соединения

```bash
# Попробуйте подключиться
ssh USERNAME@192.168.0.106

# Если работает, отключитесь:
exit
```

## Шаг 4: Настройка SSH туннеля

### Вариант А: Ручной запуск (для тестирования)

```bash
# Создайте туннель
ssh -L 11434:localhost:11434 USERNAME@192.168.0.106 -N -f

# Флаги:
# -L 11434:localhost:11434 - проброс порта
# -N - не выполнять команды
# -f - запуск в фоне

# Проверьте что туннель работает:
curl http://localhost:11434/api/tags

# Остановить туннель:
pkill -f "ssh -L 11434"
```

### Вариант Б: Автоматический запуск через launchd (macOS)

Создайте файл `~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.iskbot.ollama-tunnel</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/ssh</string>
        <string>-N</string>
        <string>-L</string>
        <string>11434:localhost:11434</string>
        <string>USERNAME@192.168.0.106</string>
        <string>-o</string>
        <string>ServerAliveInterval=60</string>
        <string>-o</string>
        <string>ServerAliveCountMax=3</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>NetworkState</key>
        <true/>
    </dict>

    <key>StandardOutPath</key>
    <string>/tmp/ollama-tunnel.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/ollama-tunnel.error.log</string>
</dict>
</plist>
```

Замените `USERNAME` на ваш логин на 192.168.0.106.

Запустите сервис:
```bash
# Загрузите конфигурацию
launchctl load ~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist

# Проверьте статус
launchctl list | grep ollama-tunnel

# Остановить:
launchctl unload ~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist
```

### Вариант В: Скрипт запуска

Создайте файл `start-ollama-tunnel.sh`:

```bash
#!/bin/bash
# Скрипт автоматического запуска SSH туннеля для Ollama

USERNAME="your_username"  # Замените на ваш логин
REMOTE_HOST="192.168.0.106"
LOCAL_PORT=11434
REMOTE_PORT=11434

# Проверяем, запущен ли уже туннель
if pgrep -f "ssh.*${LOCAL_PORT}:localhost:${REMOTE_PORT}" > /dev/null; then
    echo "Туннель уже запущен"
    exit 0
fi

# Запускаем туннель
echo "Запускаем SSH туннель к Ollama..."
ssh -L ${LOCAL_PORT}:localhost:${REMOTE_PORT} ${USERNAME}@${REMOTE_HOST} \
    -N -f \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes

# Проверяем что туннель работает
sleep 2
if curl -s http://localhost:${LOCAL_PORT}/api/tags > /dev/null; then
    echo "✅ Туннель успешно запущен и работает"
else
    echo "❌ Ошибка: туннель не отвечает"
    exit 1
fi
```

Сделайте исполняемым и запустите:
```bash
chmod +x start-ollama-tunnel.sh
./start-ollama-tunnel.sh
```

## Шаг 5: Настройка .env для использования туннеля

После настройки туннеля измените `.env`:

```env
# Теперь используем localhost через SSH туннель
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b-instruct
OLLAMA_TIMEOUT=90
OLLAMA_MAX_CHARS=15000
```

## Проверка работы

```bash
# 1. Проверьте что туннель работает
curl http://localhost:11434/api/tags

# 2. Запустите бота
python3 main.py

# 3. Отправьте документ в бота и проверьте логи
```

## Устранение проблем

### Туннель не запускается:
```bash
# Проверьте SSH соединение
ssh USERNAME@192.168.0.106 echo "OK"

# Проверьте что порт 11434 не занят
lsof -i :11434

# Если занят, освободите:
pkill -f "ssh.*11434"
```

### Туннель разрывается:
```bash
# Добавьте keep-alive параметры в ~/.ssh/config:
cat >> ~/.ssh/config << 'EOF'

Host ollama-server
    HostName 192.168.0.106
    User USERNAME
    LocalForward 11434 localhost:11434
    ServerAliveInterval 60
    ServerAliveCountMax 3
    TCPKeepAlive yes
EOF

# Теперь можно запускать просто:
ssh -N -f ollama-server
```

## Безопасность

✅ **Что мы получили:**
- Весь трафик между компьютерами шифруется
- Данные претензий защищены от перехвата
- Ollama не нужно открывать в интернет
- Автоматическое переподключение при разрыве связи

✅ **Дополнительные меры:**
- Используйте SSH ключи вместо паролей
- Отключите парольную аутентификацию в SSH:
  ```bash
  # На 192.168.0.106 в /etc/ssh/sshd_config:
  PasswordAuthentication no
  ```
- Настройте firewall на 192.168.0.106 (разрешить SSH только с вашего IP)

---

**Готово!** После настройки туннеля все запросы к Ollama будут безопасно шифроваться через SSH.
