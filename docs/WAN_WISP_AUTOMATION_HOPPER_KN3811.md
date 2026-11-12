# Автоматизация проводного WAN, Wi-Fi и WISP на Keenetic Hopper KN-3811

## Назначение

Автоматизация работает внутри роутера и не зависит от Keenetic Manager или
включённого компьютера.

- Проводной Интернет доступен: точки доступа Wi-Fi включены, WISP выключен.
- Проводной Интернет недоступен после пяти проверок: точки доступа Wi-Fi
  выключаются, WISP включается.
- Проводной Интернет восстановился после двух проверок: точки доступа Wi-Fi
  включаются, WISP выключается.
- Неизвестное состояние или ошибка чтения Ping Check: состояние интерфейсов не
  изменяется.
- Профиль Ping Check привязан не к указанному проводному интерфейсу: состояние
  интерфейсов не изменяется, в журнал записывается предупреждение.

Проверки выполняет штатный Ping Check KeeneticOS каждые 10 секунд. Служба
Entware только читает готовый результат каждые 2 секунды и применяет режим.
Поэтому переход на WISP занимает примерно 50–60 секунд, а возврат на проводной
Интернет — примерно 20–30 секунд.

## Важные ограничения

1. Инструкция подготовлена для Hopper KN-3811 с KeeneticOS 4.2 или новее и
   Entware во внутренней памяти `storage:`.
2. Первую настройку и испытание выполнять только с компьютера, подключённого
   LAN-кабелем. В резервном режиме Wi-Fi будет намеренно выключен.
3. WISP должен быть заранее настроен и один раз успешно подключён вручную.
4. Не отключать родительские интерфейсы `WifiMaster0` и `WifiMaster1`. Служба
   управляет только дочерними `AccessPoint` и `WifiStation`.
5. Идентификаторы интерфейсов обязательно проверить на конкретном роутере.
6. Перед началом экспортировать конфигурацию роутера через веб-интерфейс.

## 1. Подготовка KeeneticOS и Entware

В веб-интерфейсе установите компонент **Поддержка открытых пакетов**. Для
Hopper KN-3811 используется архитектура AArch64. В CLI KeeneticOS выполните:

```text
opkg disk storage:/ https://bin.entware.net/aarch64-k3.10/installer/aarch64-installer.tar.gz
system configuration save
```

Дождитесь завершения установки. Подключитесь к Entware по SSH как `root`,
смените стандартный пароль и проверьте локальный мост к CLI KeeneticOS:

```sh
command -v ndmq
/opt/bin/ndmq -p "show version" -P model
```

Если `/opt/bin/ndmq` отсутствует или команда возвращает ошибку, установку
автоматизации не продолжать до восстановления Entware/ndmq.

## 2. Подготовка подключений

В веб-интерфейсе:

1. Настройте проводное подключение как основное.
2. Создайте и проверьте Wireless ISP, сохраните SSID и пароль, затем выключите
   WISP.
3. В приоритетах подключений оставьте проводной Интернет выше WISP.
4. Убедитесь, что обычные Wi-Fi-сети работают на нужных диапазонах.

## 3. Определение интерфейсов

В Entware SSH выполните:

```sh
/opt/bin/ndmq -p "show interface" -P interface/id
```

Для устройства, на котором проектировалась автоматизация, использовались:

```text
проводной Интернет: GigabitEthernet1
WISP:                WifiMaster0/WifiStation0
Wi-Fi 2,4 ГГц:       WifiMaster0/AccessPoint0
Wi-Fi 5 ГГц:         WifiMaster1/AccessPoint0
```

На другом устройстве имена могут отличаться. Проводной интерфейс должен быть
именно логическим интерфейсом Интернет-подключения, а не только физическим
портом коммутатора. Сверить его можно командами:

```sh
/opt/bin/ndmq -p "show interface GigabitEthernet1" -x
/opt/bin/ndmq -p "show interface ISP" -x
```

Если активное проводное подключение называется `ISP`, во всех дальнейших
командах и в конфигурации используйте `ISP` вместо `GigabitEthernet1`.

## 4. Настройка Ping Check

Ниже `GigabitEthernet1` — проверенный идентификатор проводного Интернет-
интерфейса. При необходимости замените его на найденное на шаге 3 значение.

```sh
/opt/bin/ndmq -p "ping-check profile WAN_GUARD"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD host 1.1.1.1"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD update-interval 10"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD timeout 3"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD mode icmp"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD max-fails 5"
/opt/bin/ndmq -p "ping-check profile WAN_GUARD min-success 2"
/opt/bin/ndmq -p "interface GigabitEthernet1 ping-check profile WAN_GUARD"
/opt/bin/ndmq -p "system configuration save"
```

Проверьте сохранённую конфигурацию и текущее состояние:

```sh
/opt/bin/ndmq -p "show rc ping-check" -x
/opt/bin/ndmq -p "show ping-check WAN_GUARD" -x
/opt/bin/ndmq -p "show ping-check WAN_GUARD" -P 'pingcheck/interface/status'
```

Последняя команда после инициализации должна возвращать `pass` или `fail`.
Значения `not ready` и пустой ответ считаются неизвестным состоянием и не
вызывают переключение.

## 5. Установка файлов службы

Готовые файлы находятся в каталоге проекта `tools/wan_wisp_guard`:

```text
wan-wisp-guard.conf
wan-wisp-guard-worker.sh
wan-wisp-guard-supervisor.sh
S80wan-wisp-guard
```

Через WinSCP/SFTP скопируйте их с сохранением окончаний строк LF:

```text
wan-wisp-guard.conf          -> /opt/etc/wan-wisp-guard.conf
wan-wisp-guard-worker.sh     -> /opt/sbin/wan-wisp-guard-worker.sh
wan-wisp-guard-supervisor.sh -> /opt/sbin/wan-wisp-guard-supervisor.sh
S80wan-wisp-guard            -> /opt/etc/init.d/S80wan-wisp-guard
```

Выдайте права:

```sh
chmod 600 /opt/etc/wan-wisp-guard.conf
chmod 755 /opt/sbin/wan-wisp-guard-worker.sh
chmod 755 /opt/sbin/wan-wisp-guard-supervisor.sh
chmod 755 /opt/etc/init.d/S80wan-wisp-guard
```

Откройте `/opt/etc/wan-wisp-guard.conf` и проверьте `WIRED_IF`, `WISP_IF` и
`AP_LIST`. Если вместе с основной сетью должны выключаться гостевые или
дополнительные сети, добавьте их интерфейсы `AccessPoint` в `AP_LIST` через
пробел.

На первом запуске обязательно оставьте:

```sh
DRY_RUN=1
```

## 6. Безопасная проверка без переключения

Запустите службу:

```sh
/opt/etc/init.d/S80wan-wisp-guard start
/opt/etc/init.d/S80wan-wisp-guard check
```

После 60-секундной паузы проверьте журнал:

```sh
tail -n 50 /opt/var/log/wan-wisp-guard.log
```

В тестовом режиме должны появиться строки `DRY-RUN`, но реальные интерфейсы
меняться не будут. При доступном проводном Интернете ожидается
`target=wired`; при недоступном — `target=wisp`.

Если указан неверный профиль или интерфейс, остановите службу и исправьте
конфигурацию:

```sh
/opt/etc/init.d/S80wan-wisp-guard stop
```

## 7. Включение реального режима

После успешного DRY-RUN измените в `/opt/etc/wan-wisp-guard.conf`:

```sh
DRY_RUN=0
```

Затем перезапустите службу:

```sh
/opt/etc/init.d/S80wan-wisp-guard restart
/opt/etc/init.d/S80wan-wisp-guard check
tail -n 50 /opt/var/log/wan-wisp-guard.log
```

## 8. Реальное испытание

Испытание выполняйте по LAN-кабелю.

1. Не вынимайте кабель из WAN-порта роутера.
2. Отключите Интернет на стороне провайдера/ONT/модема так, чтобы физический
   Ethernet-link остался поднят.
3. Через 5 последовательных неудачных проверок, примерно через 50–60 секунд,
   Ping Check перейдёт в `fail`.
4. Служба выключит перечисленные точки доступа и включит WISP. Выключение Wi-Fi
   не зависит от того, удалось ли WISP подключиться.
5. Восстановите проводной Интернет.
6. После 2 успешных проверок, примерно через 20–30 секунд, служба включит точки
   доступа и выключит WISP.

Контроль состояния:

```sh
/opt/bin/ndmq -p "show ping-check WAN_GUARD" -x
/opt/etc/init.d/S80wan-wisp-guard check
tail -n 100 /opt/var/log/wan-wisp-guard.log
```

## 9. Проверка автозапуска

Перезагрузите роутер и подождите до 90 секунд:

```sh
/opt/bin/ndmq -p "system reboot"
```

После повторного входа:

```sh
/opt/etc/init.d/S80wan-wisp-guard check
ps | grep '[w]an-wisp-guard'
tail -n 50 /opt/var/log/wan-wisp-guard.log
```

Служба `S80wan-wisp-guard` запускается системой Entware автоматически.
Supervisor повторно запускает worker через 5 секунд, если тот неожиданно
завершился. После каждого старта worker ждёт 60 секунд, чтобы KeeneticOS,
проводное подключение и Ping Check успели инициализироваться.

## 10. Аварийное восстановление

Подключитесь к роутеру LAN-кабелем и выполните:

```sh
/opt/etc/init.d/S80wan-wisp-guard stop
/opt/bin/ndmq -p "interface WifiMaster0/AccessPoint0 up"
/opt/bin/ndmq -p "interface WifiMaster1/AccessPoint0 up"
/opt/bin/ndmq -p "interface WifiMaster0/WifiStation0 no up"
```

Если SSH недоступен, перезагрузите роутер. Команды динамического переключения
служба не сохраняет во flash, поэтому сначала загрузится последняя сохранённая
конфигурация KeeneticOS. В течение 60-секундной защитной паузы подключитесь по
LAN и остановите службу. Если это невозможно, временно отключите запуск OPKG в
веб-интерфейсе, перезагрузите роутер и только затем исправляйте конфигурацию.

Не выполняйте `system configuration save`, пока роутер находится в резервном
режиме: иначе текущее выключенное состояние точек доступа может попасть в
сохранённую конфигурацию.

## 11. Полное удаление автоматизации

Сначала восстановите обычный режим командами из шага 10, затем выполните:

```sh
/opt/etc/init.d/S80wan-wisp-guard stop
/opt/bin/ndmq -p "interface GigabitEthernet1 no ping-check profile"
/opt/bin/ndmq -p "no ping-check profile WAN_GUARD"
/opt/bin/ndmq -p "system configuration save"
rm -f /opt/etc/init.d/S80wan-wisp-guard
rm -f /opt/etc/wan-wisp-guard.conf
rm -f /opt/sbin/wan-wisp-guard-worker.sh
rm -f /opt/sbin/wan-wisp-guard-supervisor.sh
rm -f /opt/var/log/wan-wisp-guard.log
rm -f /opt/var/log/wan-wisp-guard.log.1
rm -f /tmp/wan-wisp-guard.pid
rm -f /tmp/wan-wisp-guard.state
```

Если проводной интерфейс называется не `GigabitEthernet1`, замените его имя и
в команде удаления профиля.

## Как система ведёт себя при сбоях

- Один–четыре неудачных теста: ничего не переключается.
- Пятый неудачный тест: Ping Check становится `fail`, Wi-Fi выключается, WISP
  включается.
- WISP не подключился: Wi-Fi всё равно остаётся выключенным, как требуется;
  служба повторяет незавершённые команды.
- Ответ Ping Check прочитать не удалось: последнее состояние сохраняется,
  никаких команд не выполняется.
- Профиль оказался привязан не к `WIRED_IF`: служба ничего не переключает до
  исправления привязки.
- Worker завершился: supervisor запускает его снова через 5 секунд.
- Роутер перезагрузился: Entware запускает службу автоматически; после
  60-секундной паузы состояние приводится в соответствие с проводным Интернетом.
- Entware не запустился: автоматизация не работает, остаётся сохранённая базовая
  конфигурация KeeneticOS.

Журнал ограничен 64 КиБ и содержит только старты, переходы и ошибки, поэтому не
создаёт постоянной нагрузки на внутреннюю flash-память.

## Справочные материалы

- [Установка Entware во внутреннюю память Hopper KN-3811](https://support.keenetic.com/hopper/kn-3811/en/18482-installing-opkg-entware-in-the-router-s-internal-memory.html)
- [Изменения KeeneticOS 4.2 с онлайн-установкой OPKG](https://support.keenetic.com/hopper/kn-3811/en/36742-keeneticos-4-2.html)
- [Пример настройки Ping Check с интервалом и порогом](https://support.keenetic.com/buddy-4/kn-3211/en/33184-setting-up-ping-check-to-ping-a-lan-host.html)
- [Документация утилиты ndmq](https://github.com/ndmsystems/ndmq)
