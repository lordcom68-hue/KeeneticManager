<p align="center">
  <img src="assets/keenetic_manager.svg" width="112" alt="Keenetic Manager">
</p>

<h1 align="center">Keenetic Manager</h1>

<p align="center">
  Настольный центр мониторинга, диагностики и управления роутерами Keenetic для Windows.
</p>

<p align="center">
  <a href="https://github.com/lordcom68-hue/KeeneticManager/actions/workflows/quality.yml"><img src="https://github.com/lordcom68-hue/KeeneticManager/actions/workflows/quality.yml/badge.svg" alt="Quality"></a>
  <a href="https://github.com/lordcom68-hue/KeeneticManager/releases"><img src="https://img.shields.io/github/v/release/lordcom68-hue/KeeneticManager?include_prereleases&sort=semver&label=release" alt="GitHub release"></a>
  <img src="https://img.shields.io/badge/platform-Windows-2563EB" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
</p>

> [!NOTE]
> Текущая версия — **1.0.14-rc1**. Набор функций релиз-кандидата заморожен;
> до стабильного выпуска принимаются только исправления.

## Возможности

| Мониторинг | Управление | Диагностика |
|---|---|---|
| Состояние системы, Интернета, Wi-Fi и портов | Wi-Fi-сети, подключения и режимы работы | Безопасная read-only самопроверка роутера |
| Подключённые клиенты и скорость трафика | Проводные подключения и Wireless ISP | Системный журнал и обезличенный диагностический архив |
| Несколько роутеров и автоматическое обновление | Режимы «Спасатель», «Спасаемый» и «Смешанный» | Офлайн-анализ и сравнение файлов прошивок |
| Локальный адрес и KeenDNS | Проверка и установка обновлений KeeneticOS | Каталог прошивок с проверкой SHA-256 |

Дополнительно в репозитории есть автономная автоматизация для **Hopper KN-3811**,
которая переключает Wi-Fi и WISP по состоянию проводного Интернета. Подробности:
[`docs/WAN_WISP_AUTOMATION_HOPPER_KN3811.md`](docs/WAN_WISP_AUTOMATION_HOPPER_KN3811.md).

## Быстрый старт

### Готовая сборка

Откройте раздел [Releases](https://github.com/lordcom68-hue/KeeneticManager/releases),
скачайте установщик или portable-архив последней версии и запустите приложение.

> [!IMPORTANT]
> Сборка без цифровой подписи может вызвать предупреждение Windows SmartScreen.
> Проверяйте источник файла и опубликованную контрольную сумму перед запуском.

### Запуск из исходников

Требуются Windows и Python 3.10 или новее.

```powershell
git clone https://github.com/lordcom68-hue/KeeneticManager.git
cd KeeneticManager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -c constraints-release.txt
python main.py
```

При последующих запусках достаточно активировать окружение и выполнить
`python main.py`.

## Подключение роутера

1. Запустите Keenetic Manager и добавьте роутер.
2. Укажите локальный адрес или KeenDNS, имя пользователя и пароль.
3. Выберите роутер в списке и подключитесь.

Для локального подключения приложение использует HTTP внутри вашей сети, для
KeenDNS — HTTPS. Перед изменяющими действиями интерфейс запрашивает
подтверждение, а диагностические функции отделены от управляющих команд.

## Данные и безопасность

Пользовательские данные не хранятся в каталоге исходного кода. На Windows база,
журналы и история диагностики находятся в:

```text
%LOCALAPPDATA%\KeeneticManager
```

Пароли роутеров сохраняются через системное хранилище учётных данных Windows
(`keyring`). Диагностические архивы обезличиваются и не должны содержать пароли,
токены или адреса роутеров.

Для portable- или тестовой установки каталог данных можно переопределить:

```powershell
$env:KEENETIC_MANAGER_DATA_DIR = "D:\KeeneticManagerData"
python main.py
```

## Офлайн-анализ прошивок

Включите режим разработчика в разделе «Настройки», затем выберите
«Анализировать файл прошивки». Анализатор работает локально и определяет формат
контейнера, модель, архитектуру, версию, SquashFS, SHA-256 и найденные
RCI-признаки. Доступны сравнение двух файлов, проверка файла для выбранного
роутера и локальный каталог результатов.

Файл прошивки не изменяется, не передаётся роутеру и не устанавливается.
Результат анализа не является гарантией совместимости с конкретным устройством.

## Самопроверка роутера

Раздел «Настройки → Совместимость роутера» запускает read-only проверку:

- активной сессии и доступных каналов подключения;
- чтения безопасных системных данных;
- мониторинга Интернета, клиентов, Wi-Fi и физических портов;
- профиля поддерживаемых функций и системного журнала.

Приложение хранит последние десять результатов, показывает новые и устранённые
проблемы и экспортирует обезличенный JSON.

## Разработка

```powershell
python -m pip install -r requirements-dev.txt -c constraints-release.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\quality_check.ps1
```

Проверка компилирует Python-файлы, запускает Ruff, полный набор тестов и
контролирует минимальное покрытие. Тот же сценарий выполняется в GitHub Actions.

### Сборка Windows-приложения

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt -c constraints-release.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_release.ps1
```

Сценарий создаёт приложение PyInstaller, portable-архив и, при наличии
Inno Setup 6, установщик. Дополнительные параметры и цифровая подпись описаны в
[`BUILD.md`](BUILD.md).

## Документация

- [История изменений](docs/CHANGELOG.md)
- [Примечания к 1.0.14-rc1](docs/release_notes/RELEASE_NOTES_1.0.14-rc1.md)
- [RCI-команды и совместимость](docs/rci_commands.md)
- [Сборка и выпуск](BUILD.md)

## Важное замечание

Keenetic Manager — независимый проект и не является официальным приложением
Keenetic. Изменение сетевой конфигурации может временно прервать соединение с
роутером; перед критическими действиями сохраняйте резервную копию его настроек.
