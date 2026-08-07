# Keenetic Manager

Настольное приложение для мониторинга и управления роутерами Keenetic.
Текущая версия — `1.0.5-beta`. Это стабильная контрольная точка в ходе
разработки, предназначенная для технической проверки перед следующим выпуском.

## Установка и запуск

Откройте PowerShell и выполните:

```powershell
cd D:\Proekt\KeeneticManager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

При последующих запусках достаточно:

```powershell
cd D:\Proekt\KeeneticManager
.\.venv\Scripts\Activate.ps1
python main.py
```

## Рабочие данные

Исходный код не используется как хранилище пользовательских данных. На
Windows база роутеров, журналы и диагностическая история находятся в:

```text
%LOCALAPPDATA%\KeeneticManager
```

При первом запуске существующая `database\routers.db` безопасно копируется в
новое расположение и не перезаписывает уже перенесённую базу. Пароли роутеров
хранятся через системное хранилище учётных данных (`keyring`).

Для переносимой или тестовой установки каталог можно задать до запуска:

```powershell
$env:KEENETIC_MANAGER_DATA_DIR = "D:\KeeneticManagerData"
python main.py
```

## Проверка проекта

```powershell
python -m pip install -r requirements-dev.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\quality_check.ps1
```

Проверка компилирует Python-файлы, запускает Ruff, полный комплект тестов и
контролирует минимальное покрытие. Сборщики патча и выпуска запускают её
автоматически; отключать проверку следует только после уже успешного прогона.

## Сборка патча

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_patch.ps1
```

Сборщик включает полный согласованный набор исходников, тестов и документации,
а установщик проверяет SHA-256 каждого файла, создаёт резервную копию и запускает
полный комплект тестов перед подтверждением установки.

## Сборка Windows-приложения

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_release.ps1
```

Сценарий создаёт самостоятельное Windows-приложение через PyInstaller,
переносимый ZIP и, при наличии Inno Setup 6, полноценный установщик.

## Чистый бекап исходников

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_clean_backup.ps1
```

Команда создаёт рядом с проектом папку и ZIP с версией и временем в имени.
Пользовательская база, пароли, журналы, диагностика, кэши и виртуальные
окружения в такой бекап не включаются.
