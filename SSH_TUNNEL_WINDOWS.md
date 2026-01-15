# SSH туннель для Ollama на Windows

## Шаг 1: Включение OpenSSH Server на Windows (192.168.0.106)

### Через PowerShell (от администратора):

```powershell
# Проверьте доступность OpenSSH Server
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'

# Установите OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Запустите службу SSH
Start-Service sshd

# Настройте автозапуск
Set-Service -Name sshd -StartupType 'Automatic'

# Проверьте что служба запущена
Get-Service sshd

# Разрешите SSH в брандмауэре (обычно делается автоматически)
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

### Через графический интерфейс:

1. **Параметры** → **Приложения** → **Дополнительные компоненты**
2. Найдите **"Сервер OpenSSH"** в списке доступных компонентов
3. Нажмите **"Добавить"**
4. После установки:
   - **Win + R** → `services.msc`
   - Найдите **"OpenSSH SSH Server"**
   - ПКМ → **Свойства** → Тип запуска: **"Автоматически"**
   - Нажмите **"Запустить"**

## Шаг 2: Проверка SSH на Windows

```powershell
# Проверьте что SSH слушает порт 22
netstat -an | findstr :22
```

Должно быть:
```
TCP    0.0.0.0:22             0.0.0.0:0              LISTENING
```

## Шаг 3: Настройка SSH ключей с вашего Mac

### На Mac (192.168.0.XXX):

```bash
# Узнайте имя пользователя Windows
# Обычно это то же имя, которое вы видите в C:\Users\ИМЯ

# Сгенерируйте SSH ключ
ssh-keygen -t ed25519 -C "iskbot-ollama-windows"

# Скопируйте ключ на Windows
# Замените USERNAME на ваше имя пользователя Windows
ssh-copy-id USERNAME@192.168.0.106

# Если ssh-copy-id не работает, используйте альтернативный способ:
cat ~/.ssh/id_ed25519.pub | ssh USERNAME@192.168.0.106 "powershell -Command \"New-Item -Force -ItemType Directory -Path \$env:USERPROFILE\.ssh; Add-Content -Path \$env:USERPROFILE\.ssh\authorized_keys -Value \$input\""
```

### Ручное копирование (если автоматика не работает):

1. **Скопируйте содержимое публичного ключа:**
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```

2. **На Windows создайте файл:**
   - Откройте PowerShell
   - Выполните:
     ```powershell
     # Создайте директорию .ssh если нет
     New-Item -Force -ItemType Directory -Path "$env:USERPROFILE\.ssh"

     # Откройте Блокнот для редактирования authorized_keys
     notepad "$env:USERPROFILE\.ssh\authorized_keys"
     ```

3. **Вставьте содержимое публичного ключа** в opened notepad и сохраните

4. **Настройте права доступа:**
   ```powershell
   # Установите правильные права на файл
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "$env:USERNAME:F"
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "SYSTEM:F"
   ```

## Шаг 4: Проверка SSH соединения

### С Mac:

```bash
# Попробуйте подключиться (замените USERNAME на ваше имя)
ssh USERNAME@192.168.0.106

# Если просит пароль в первый раз - это нормально, введите пароль Windows
# После настройки ключей пароль больше не потребуется

# Если подключение успешно, вы увидите PowerShell на Windows
# Выйдите:
exit
```

## Шаг 5: Создание SSH туннеля

### На Mac создайте туннель:

```bash
# Запустите туннель
ssh -L 11434:localhost:11434 USERNAME@192.168.0.106 -N -f

# Проверьте что туннель работает:
curl http://localhost:11434/api/tags

# Должны увидеть список моделей Ollama
```

## Шаг 6: Автоматический запуск туннеля (macOS)

### Создайте launchd сервис:

```bash
# Создайте файл конфигурации
cat > ~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist << 'EOF'
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
        <string>-o</string>
        <string>ExitOnForwardFailure=yes</string>
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
EOF

# ВАЖНО: Замените USERNAME на ваше имя пользователя Windows!
sed -i '' 's/USERNAME/ваше_имя_пользователя/' ~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist

# Загрузите сервис
launchctl load ~/Library/LaunchAgents/com.iskbot.ollama-tunnel.plist

# Проверьте статус
launchctl list | grep ollama-tunnel
```

### Или используйте простой скрипт:

```bash
# Создайте скрипт запуска
cat > ~/start-ollama-tunnel.sh << 'EOF'
#!/bin/bash
USERNAME="ваше_имя_windows"  # Замените!
REMOTE_HOST="192.168.0.106"

# Проверяем, запущен ли туннель
if pgrep -f "ssh.*11434:localhost:11434.*${REMOTE_HOST}" > /dev/null; then
    echo "✅ Туннель уже запущен"
    exit 0
fi

# Запускаем туннель
echo "Запускаем SSH туннель к Ollama на Windows..."
ssh -L 11434:localhost:11434 ${USERNAME}@${REMOTE_HOST} \
    -N -f \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes

# Ждем и проверяем
sleep 2
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "✅ Туннель успешно запущен"
else
    echo "❌ Ошибка: туннель не отвечает"
    exit 1
fi
EOF

chmod +x ~/start-ollama-tunnel.sh

# Запустите
~/start-ollama-tunnel.sh
```

## Шаг 7: Настройка .env

После успешного запуска туннеля обновите `.env`:

```env
# Теперь используем localhost через SSH туннель
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b-instruct
OLLAMA_TIMEOUT=90
OLLAMA_MAX_CHARS=15000
```

## Устранение проблем

### SSH не запускается на Windows:

```powershell
# Проверьте логи
Get-EventLog -LogName Application -Source OpenSSH -Newest 10

# Или в Event Viewer:
# Win + R → eventvwr.msc
# Applications and Services Logs → OpenSSH → Operational
```

### Ошибка "Connection refused":

```powershell
# На Windows проверьте брандмауэр
Get-NetFirewallRule -Name *ssh*

# Если правило отключено, включите:
Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True
```

### Не работает авторизация по ключу:

```powershell
# На Windows проверьте права на файлы SSH
icacls "$env:USERPROFILE\.ssh\authorized_keys"

# Должен быть доступ только для вашего пользователя и SYSTEM
# Если нет, исправьте:
icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r
icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "$env:USERNAME:F"
```

### Туннель разрывается:

Добавьте в `~/.ssh/config` на Mac:

```bash
cat >> ~/.ssh/config << 'EOF'

Host ollama-windows
    HostName 192.168.0.106
    User ваше_имя_windows
    LocalForward 11434 localhost:11434
    ServerAliveInterval 60
    ServerAliveCountMax 3
    TCPKeepAlive yes
    ExitOnForwardFailure yes
EOF

# Теперь можно запускать просто:
ssh -N -f ollama-windows
```

## Итоговая проверка

```bash
# 1. Проверьте туннель
curl http://localhost:11434/api/tags

# 2. Проверьте модель
curl http://localhost:11434/api/generate -d '{
  "model": "qwen2.5:14b-instruct",
  "prompt": "Привет! Отвечай кратко.",
  "stream": false
}'

# 3. Запустите бота
cd ~/Dev/IskBot
python3 main.py
```

---

**Готово!** Теперь все запросы к Ollama на Windows безопасно шифруются через SSH туннель.
