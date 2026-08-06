# Сборка Keenetic Manager 1.0.1-beta

Эта копия содержит только код приложения и файлы выпуска. Пользовательские
базы, пароли, журналы, диагностика, тесты и кэши в неё не включены.

## Подготовка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
```

## Сборка

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_release.ps1
```

Готовое приложение появится в `dist\KeeneticManager`, переносимая версия — в
`dist\portable`, а установщик — в `dist\installer`. Ключ `-SkipInstaller`
пропускает только установщик, но сохраняет создание portable-архива.

Рабочие данные бета-выпуска создаются в отдельном каталоге
`%LOCALAPPDATA%\KeeneticManager-Beta-1.0.1-beta`, а пароли — в отдельном
пространстве системного хранилища учётных данных Windows. Данные установленного
рабочего экземпляра Keenetic Manager не импортируются.

Portable-версия хранит базу, журналы и диагностику в собственной папке
`data` после распаковки. Начальный архив не содержит пользовательских данных.
