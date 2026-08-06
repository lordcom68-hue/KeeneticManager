# Keenetic Manager 0.31.0

## WiredInternetService

В новый `core/services/wired_internet_service.py` физически перенесены:

- создание «Кабель Интернет»;
- создание и настройка аппаратного WAN;
- IPoE;
- PPPoE;
- PPTP;
- L2TP;
- смена протокола;
- удаление аппаратного WAN;
- удаление IPoE/PPPoE/PPTP/L2TP;
- нормализация общих параметров проводного подключения.

`KeeneticClient` сохраняет короткие совместимые фасады.
`ConnectionWorker` обращается к `WiredInternetService` напрямую.

Схема SQLite остаётся 280. GUI и форматы сигналов не меняются.
