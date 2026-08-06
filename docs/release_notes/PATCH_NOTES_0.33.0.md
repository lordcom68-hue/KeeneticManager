# Keenetic Manager 0.33.0

## PingCheckService

В новый `core/services/ping_check_service.py` вынесены:

- чтение `show/sc/ping-check` и `show/ping-check`;
- единый `PingCheckSnapshot`;
- поиск и нормализация профилей;
- распознавание automatic/ICMP/TLS/connect;
- нормализация runtime-состояния, счётчиков и адресов;
- история успешных и неуспешных проверок в текущем сеансе;
- причины состояния и переключения канала;
- формирование CLI-команд профиля;
- обогащение карточек интернет-подключений.

`KeeneticClient` сохраняет совместимые фасады. `WiredInternetService` обращается к PingCheckService напрямую. Схема SQLite остаётся 280.
