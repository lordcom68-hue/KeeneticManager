# Карта RCI Keenetic Manager

## Подтверждённые команды чтения

| Endpoint | Назначение | Статус |
|---|---|---|
| `show/version` | Версия, модель, features/components | verified |
| `show/system` | Системное состояние | verified |
| `show/internet/status` | Состояние доступа в интернет | verified |
| `show/interface` | Текущее состояние интерфейсов | verified |
| `show/sc/interface` | Сохранённая конфигурация интерфейсов | verified |
| `show/associations` | Wi-Fi-клиенты | verified |
| `show/ip/hotspot` | Сегменты и клиенты | verified |
| `show/interface/stat` | Статистика интерфейса | observed |
| `show/usb` | USB на части моделей | optional |
| `show/storage` | Storage на части моделей | optional |

## Запрещённые без проверки endpoints

Эти endpoints нельзя вызывать наугад, поскольку KeeneticOS может
записывать `not found` в системный журнал:

- `show/hardware`;
- `show/usb`;
- `show/storage`.

## Интернет-приоритеты

### Чтение

```text
show/sc/interface
    <interface-id>
        ip
            global
                order
```

### Запись

```json
{
  "interface": {
    "ip": {
      "global": {
        "enabled": true,
        "order": 0
      }
    },
    "name": "GigabitEthernet1"
  }
}
```

`order` является нулевым индексом:

- `0` — основной;
- `1` — резервный 1;
- `2` — резервный 2.

После пакетного изменения конфигурация сохраняется командой:

```json
{
  "system": {
    "configuration": {
      "save": {}
    }
  }
}
```

## Доступ отдельного Wi-Fi клиента

Клиент сопоставляется с локальным сегментом по полю `interface.id` из
`show/ip/hotspot` или по включению его AccessPoint в
`show/sc/interface.<Bridge>.include`. ACL применяется к Bridge-сегменту,
а не к физическому AccessPoint: это охватывает обе частоты и не разрывает
`follow` на KeeneticOS 4.

Чтение состояния:

```text
show/sc/interface
    BridgeN
        mac
            access-list
                type
                address
```

Запрет в стандартном режиме `none`:

```text
interface BridgeN mac access-list address <mac>
interface BridgeN mac access-list type deny
system configuration save
```

Повторное разрешение в режиме `deny`:

```text
interface BridgeN no mac access-list address <mac>
system configuration save
```

В режиме белого списка `permit` логика обратная: адрес удаляется для
запрета и добавляется для разрешения. Реальная команда записи во время
разработки не запускалась; формы конфигурации подтверждены чтением на
KeeneticOS 4.03, 5.00 и 5.01, а запись покрыта изолированными тестами.

## Поля, которые нельзя смешивать

```text
show/interface.priority
```

не равно:

```text
show/sc/interface.*.ip.global.order
```

Первое — текущая маршрутная метрика/технический приоритет.
Второе — пользовательский порядок интернет-каналов.
