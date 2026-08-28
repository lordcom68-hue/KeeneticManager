# Сборка Keenetic Manager 1.0.13-beta

Эта копия содержит только код приложения и файлы выпуска. Пользовательские
базы, пароли, журналы, диагностика, тесты и кэши в неё не включены.

## Подготовка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt -c constraints-release.txt
```

## Сборка

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_release.ps1
```

Эта чистая копия уже прошла Ruff, тесты и проверку покрытия перед экспортом;
тестовый набор и зависимости разработчика намеренно в неё не включаются.

Готовое приложение появится в `dist\KeeneticManager`, переносимая версия — в
`dist\portable`, а установщик — в `dist\installer`. Ключ `-SkipInstaller`
пропускает только установщик, но сохраняет создание portable-архива.

## Цифровая подпись

При наличии code-signing сертификата в хранилище Windows можно подписать EXE и
установщик во время сборки:

```powershell
$env:KEENETIC_MANAGER_SIGNING_THUMBPRINT = "THUMBPRINT_СЕРТИФИКАТА"
$env:KEENETIC_MANAGER_TIMESTAMP_URL = "URL_СЕРВЕРА_ВРЕМЕНИ"
.\tools\build_release.ps1 -SignArtifacts
```

Скрипт использует SHA-256, подписывает приложение до создания portable и
установщика, затем проверяет обе подписи через `signtool verify`. Без ключа
`-SignArtifacts` сборка остаётся неподписанной и не требует сертификата.

Рабочие данные всех следующих бета-обновлений используют стабильный каталог
`%LOCALAPPDATA%\KeeneticManager-Beta`, настройки `KeeneticManager-Beta` и
стабильные пространства паролей Windows. При первом запуске 1.0.2-beta данные
и настройки прежних версионных бета-профилей переносятся без перезаписи уже
существующей базы; старые записи паролей копируются при первом обращении.

Portable-версия хранит базу, журналы и диагностику в собственной папке
`data` после распаковки. Начальный архив не содержит пользовательских данных.
