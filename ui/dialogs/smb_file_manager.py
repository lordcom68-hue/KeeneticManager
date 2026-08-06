from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QSettings,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.app_profile import (
    APP_STORAGE_NAMESPACE,
    LEGACY_SMB_CREDENTIAL_SERVICES,
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
    SMB_CREDENTIAL_SERVICE,
)

from ui.message_boxes import ask_yes_no

try:
    import smbclient
    from smbclient import (
        ClientConfig,
    )
except ImportError:
    smbclient = None
    ClientConfig = None

try:
    import keyring
except ImportError:
    keyring = None


class _SmbShutdownFilter(logging.Filter):
    """Скрывает служебное сообщение о закрытии сокета роутером."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        return not (
            "wsaeshutdown" in message
            and "treating as eof" in message
        )


logging.getLogger("smbprotocol").addFilter(
    _SmbShutdownFilter()
)


@dataclass(slots=True)
class SmbEntry:
    name: str
    is_dir: bool
    size: int
    modified: float | None


class SmbFileManagerDialog(QDialog):
    """
    Встроенный локальный SMB-файловый менеджер.

    Работает напрямую через smbprotocol/smbclient и не создаёт
    Windows-сессии командой `net use`.
    """

    def __init__(
        self,
        *,
        router_id: int,
        host: str,
        device: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.router_id = router_id
        self.host = self._normalize_host(host)
        self.device = dict(device or {})
        self.settings = QSettings(
            SETTINGS_ORGANIZATION,
            f"{SETTINGS_APPLICATION}.UsbSmbFileManager",
        )

        self.share_name = ""
        self.current_parts: list[str] = []
        self.connected = False
        self._password = ""
        self._username = ""
        self._entries: list[SmbEntry] = []

        self.profile_key = f"router/{self.router_id}/smb"
        self.credential_service = SMB_CREDENTIAL_SERVICE
        self.credential_account = f"router-{self.router_id}"

        self.setWindowTitle(
            "USB-накопитель — локальный SMB"
        )
        self.resize(980, 650)
        self.setMinimumSize(760, 500)

        self._create_interface()
        self._load_settings()
        self._set_connected(False)

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------

    def _create_interface(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        form_widget = QWidget(self)
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)

        self.host_edit = QLineEdit(self.host)
        self.host_edit.setReadOnly(True)

        self.share_edit = QLineEdit()
        self.share_edit.setPlaceholderText(
            "Например: USB или имя накопителя"
        )

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText(
            "Пусто — гостевой доступ"
        )

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.password_edit.setPlaceholderText(
            "Пароль"
        )

        self.remember_password_checkbox = QCheckBox(
            "Запомнить пароль для этого роутера"
        )
        self.remember_password_checkbox.setEnabled(
            keyring is not None
        )
        self.remember_password_checkbox.setToolTip(
            (
                "Пароль будет сохранён в Windows "
                "Credential Manager."
            )
            if keyring is not None
            else (
                "Установите пакет keyring, чтобы "
                "безопасно сохранять пароль."
            )
        )

        form.addRow(
            "Локальный адрес:",
            self.host_edit,
        )
        form.addRow(
            "Общий ресурс:",
            self.share_edit,
        )
        form.addRow(
            "Пользователь:",
            self.username_edit,
        )
        form.addRow(
            "Пароль:",
            self.password_edit,
        )
        form.addRow(
            "",
            self.remember_password_checkbox,
        )

        toolbar = QHBoxLayout()

        self.connect_button = QPushButton(
            "Подключить"
        )
        self.up_button = QPushButton("Вверх")
        self.refresh_button = QPushButton(
            "Обновить"
        )
        self.new_folder_button = QPushButton(
            "Новая папка"
        )
        self.upload_button = QPushButton(
            "Загрузить"
        )
        self.download_button = QPushButton(
            "Скачать"
        )
        self.rename_button = QPushButton(
            "Переименовать"
        )
        self.delete_button = QPushButton(
            "Удалить"
        )
        self.connect_button.setObjectName("primaryButton")
        self.delete_button.setObjectName("dangerButton")

        for button in (
            self.connect_button,
            self.up_button,
            self.refresh_button,
            self.new_folder_button,
            self.upload_button,
            self.download_button,
            self.rename_button,
            self.delete_button,
        ):
            toolbar.addWidget(button)

        toolbar.addStretch()

        self.path_label = QLabel(
            "Не подключено"
        )
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            [
                "Имя",
                "Тип",
                "Размер",
                "Изменён",
            ]
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 470)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 180)

        root.addWidget(form_widget)
        root.addLayout(toolbar)
        root.addWidget(self.path_label)
        root.addWidget(self.table, 1)

        self.connect_button.clicked.connect(
            self.connect_share
        )
        self.up_button.clicked.connect(
            self.go_up
        )
        self.refresh_button.clicked.connect(
            self.refresh
        )
        self.new_folder_button.clicked.connect(
            self.create_folder
        )
        self.upload_button.clicked.connect(
            self.upload_files
        )
        self.download_button.clicked.connect(
            self.download_selected
        )
        self.rename_button.clicked.connect(
            self.rename_selected
        )
        self.delete_button.clicked.connect(
            self.delete_selected
        )
        self.table.cellDoubleClicked.connect(
            self.open_row
        )
        self.table.itemSelectionChanged.connect(
            self._update_action_buttons
        )

    def _load_settings(self) -> None:
        saved_share = self.settings.value(
            f"{self.profile_key}/share",
            "",
            str,
        )
        saved_username = self.settings.value(
            f"{self.profile_key}/username",
            "",
            str,
        )
        saved_host = self.settings.value(
            f"{self.profile_key}/host",
            "",
            str,
        )
        saved_path = self.settings.value(
            f"{self.profile_key}/last_path",
            "",
            str,
        )

        detected_share = str(
            self.device.get("share_name")
            or self.device.get("share")
            or ""
        ).strip()

        self.share_edit.setText(
            detected_share or saved_share
        )
        self.username_edit.setText(
            saved_username
        )

        if (
            saved_host
            and self._is_local_host(saved_host)
        ):
            self.host = saved_host
            self.host_edit.setText(saved_host)

        self.current_parts = [
            part
            for part in str(saved_path).split("\\")
            if part
        ]

        self._load_saved_password()

    def _load_saved_password(self) -> None:
        if keyring is None:
            return

        try:
            password = keyring.get_password(
                self.credential_service,
                self.credential_account,
            )
            if not password:
                for service in LEGACY_SMB_CREDENTIAL_SERVICES:
                    password = keyring.get_password(
                        service,
                        self.credential_account,
                    )
                    if password:
                        keyring.set_password(
                            self.credential_service,
                            self.credential_account,
                            password,
                        )
                        break
        except Exception:
            password = None

        if password:
            self.password_edit.setText(password)
            self.remember_password_checkbox.setChecked(
                True
            )

    def _save_profile(
        self,
        *,
        share: str,
        username: str,
        password: str,
    ) -> None:
        self.settings.setValue(
            f"{self.profile_key}/share",
            share,
        )
        self.settings.setValue(
            f"{self.profile_key}/username",
            username,
        )
        self.settings.setValue(
            f"{self.profile_key}/host",
            self.host,
        )
        self._save_last_path()

        if keyring is None:
            return

        try:
            if (
                self.remember_password_checkbox.isChecked()
                and password
            ):
                keyring.set_password(
                    self.credential_service,
                    self.credential_account,
                    password,
                )
            else:
                try:
                    for service in (
                        self.credential_service,
                        *LEGACY_SMB_CREDENTIAL_SERVICES,
                    ):
                        try:
                            keyring.delete_password(
                                service,
                                self.credential_account,
                            )
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception as error:
            QMessageBox.warning(
                self,
                "Не удалось сохранить пароль",
                str(error),
            )

    def _save_last_path(self) -> None:
        self.settings.setValue(
            f"{self.profile_key}/last_path",
            "\\".join(self.current_parts),
        )

    # ---------------------------------------------------------
    # Connection
    # ---------------------------------------------------------

    def connect_share(self) -> None:
        if smbclient is None:
            QMessageBox.critical(
                self,
                "Не установлена SMB-библиотека",
                (
                    "Установите пакет smbprotocol:\n\n"
                    "python -m pip install smbprotocol"
                ),
            )
            return

        if not self._is_local_host(self.host):
            QMessageBox.warning(
                self,
                "Только локальный доступ",
                (
                    "Файловый менеджер подключается только "
                    "к локальному IP-адресу Keenetic."
                ),
            )
            return

        share = self._clean_share(
            self.share_edit.text()
        )

        if not share:
            QMessageBox.warning(
                self,
                "Не указан общий ресурс",
                (
                    "Введите имя SMB-ресурса, опубликованного "
                    "на Keenetic."
                ),
            )
            return

        username = self.username_edit.text().strip()
        password = self.password_edit.text()

        try:
            # Удаляем предыдущую сессию этого процесса, чтобы
            # можно было переподключиться с другими данными.
            try:
                smbclient.delete_session(
                    self.host,
                )
            except Exception:
                pass

            ClientConfig(
                username=username or None,
                password=password or None,
            )
            smbclient.register_session(
                self.host,
                username=username or None,
                password=password or None,
                connection_timeout=8,
            )

            # Реальный запрос подтверждает существование ресурса.
            root_path = self._share_root(
                share
            )
            self._run_smb(
                lambda: smbclient.listdir(root_path),
                retry=False,
            )

            if self.current_parts:
                saved_path = (
                    root_path
                    + "\\"
                    + "\\".join(
                        self.current_parts
                    )
                )
                try:
                    self._run_smb(
                        lambda: smbclient.listdir(saved_path),
                        retry=False,
                    )
                except Exception:
                    self.current_parts = []
        except Exception as error:
            self.connected = False
            self._set_connected(False)
            self._show_smb_error(
                "Не удалось подключиться",
                error,
            )
            return

        self.share_name = share
        self.connected = True
        self._username = username
        self._password = password

        self._save_profile(
            share=share,
            username=username,
            password=password,
        )

        self._set_connected(True)
        self.refresh()

    def closeEvent(self, event) -> None:
        if smbclient is not None:
            try:
                smbclient.delete_session(
                    self.host,
                )
            except Exception:
                pass

        super().closeEvent(event)

    # ---------------------------------------------------------
    # Navigation and listing
    # ---------------------------------------------------------

    def refresh(self) -> None:
        if not self.connected:
            return

        path = self._current_unc()

        def read_entries() -> list[SmbEntry]:
            entries: list[SmbEntry] = []

            with smbclient.scandir(path) as iterator:
                for item in iterator:
                    try:
                        stat = item.stat()
                        modified = float(
                            stat.st_mtime
                        )
                        size = (
                            0
                            if item.is_dir()
                            else int(stat.st_size)
                        )
                    except Exception:
                        modified = None
                        size = 0

                    entries.append(
                        SmbEntry(
                            name=item.name,
                            is_dir=item.is_dir(),
                            size=size,
                            modified=modified,
                        )
                    )

            entries.sort(
                key=lambda entry: (
                    not entry.is_dir,
                    entry.name.casefold(),
                )
            )
            return entries

        try:
            entries = self._run_smb(
                read_entries
            )
        except Exception as error:
            self._show_smb_error(
                "Не удалось прочитать папку",
                error,
            )
            return

        self._entries = entries
        self._fill_table()
        self._update_path_label()
        self._update_action_buttons()

    def _fill_table(self) -> None:
        self.table.setRowCount(
            len(self._entries)
        )

        for row, entry in enumerate(
            self._entries
        ):
            name_item = QTableWidgetItem(
                entry.name
            )
            name_item.setData(
                Qt.ItemDataRole.UserRole,
                row,
            )

            type_item = QTableWidgetItem(
                "Папка"
                if entry.is_dir
                else "Файл"
            )
            size_item = QTableWidgetItem(
                ""
                if entry.is_dir
                else self._format_size(
                    entry.size
                )
            )
            modified_item = QTableWidgetItem(
                self._format_time(
                    entry.modified
                )
            )

            self.table.setItem(
                row,
                0,
                name_item,
            )
            self.table.setItem(
                row,
                1,
                type_item,
            )
            self.table.setItem(
                row,
                2,
                size_item,
            )
            self.table.setItem(
                row,
                3,
                modified_item,
            )

        self.table.clearSelection()

    def open_row(
        self,
        row: int,
        _column: int,
    ) -> None:
        entry = self._entry_at(row)

        if entry is None:
            return

        if entry.is_dir:
            self.current_parts.append(
                entry.name
            )
            self._save_last_path()
            self.refresh()
            return

        self._open_remote_file(entry)

    def go_up(self) -> None:
        if not self.connected:
            return

        if self.current_parts:
            self.current_parts.pop()
            self._save_last_path()
            self.refresh()

    # ---------------------------------------------------------
    # File operations
    # ---------------------------------------------------------

    def create_folder(self) -> None:
        if not self.connected:
            return

        name, accepted = QInputDialog.getText(
            self,
            "Новая папка",
            "Название папки:",
        )
        name = name.strip()

        if not accepted or not name:
            return

        if not self._valid_name(name):
            QMessageBox.warning(
                self,
                "Недопустимое имя",
                "Название содержит недопустимые символы.",
            )
            return

        try:
            self._run_smb(
                lambda: smbclient.mkdir(
                    self._child_unc(name)
                )
            )
        except Exception as error:
            self._show_smb_error(
                "Не удалось создать папку",
                error,
            )
            return

        self.refresh()

    def upload_files(self) -> None:
        if not self.connected:
            return

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите файлы для загрузки",
        )

        if not files:
            return

        for local_path in files:
            remote_path = self._child_unc(
                os.path.basename(local_path)
            )

            if (
                self._remote_exists(remote_path)
                and not self._confirm_replace(
                    os.path.basename(local_path)
                )
            ):
                continue

            try:
                with open(
                    local_path,
                    "rb",
                ) as source:
                    with smbclient.open_file(
                        remote_path,
                        mode="wb",
                    ) as destination:
                        shutil.copyfileobj(
                            source,
                            destination,
                            length=1024 * 1024,
                        )
            except Exception as error:
                self._show_smb_error(
                    "Не удалось загрузить файл",
                    error,
                )
                return

        self.refresh()

    def download_selected(self) -> None:
        entry = self._selected_entry()

        if entry is None:
            return

        destination = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для сохранения",
        )

        if not destination:
            return

        local_target = os.path.join(
            destination,
            entry.name,
        )

        if (
            os.path.exists(local_target)
            and not self._confirm_replace(
                entry.name
            )
        ):
            return

        try:
            if entry.is_dir:
                self._download_directory(
                    self._child_unc(entry.name),
                    Path(local_target),
                )
            else:
                self._download_file(
                    self._child_unc(entry.name),
                    Path(local_target),
                )
        except Exception as error:
            self._show_smb_error(
                "Не удалось скачать объект",
                error,
            )

    def rename_selected(self) -> None:
        entry = self._selected_entry()

        if entry is None:
            return

        new_name, accepted = QInputDialog.getText(
            self,
            "Переименовать",
            "Новое имя:",
            text=entry.name,
        )
        new_name = new_name.strip()

        if (
            not accepted
            or not new_name
            or new_name == entry.name
        ):
            return

        if not self._valid_name(new_name):
            QMessageBox.warning(
                self,
                "Недопустимое имя",
                "Название содержит недопустимые символы.",
            )
            return

        target = self._child_unc(new_name)

        if self._remote_exists(target):
            QMessageBox.warning(
                self,
                "Объект уже существует",
                (
                    "Файл или папка с таким именем "
                    "уже существует."
                ),
            )
            return

        try:
            self._run_smb(
                lambda: smbclient.rename(
                    self._child_unc(entry.name),
                    target,
                )
            )
        except Exception as error:
            self._show_smb_error(
                "Не удалось переименовать",
                error,
            )
            return

        self.refresh()

    def delete_selected(self) -> None:
        entry = self._selected_entry()

        if entry is None:
            return

        answer = ask_yes_no(
            self,
            "Удалить объект",
            (
                "Удалить без возможности восстановления?\n\n"
                f"{entry.name}"
            ),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            path = self._child_unc(
                entry.name
            )

            if entry.is_dir:
                self._remove_directory(path)
            else:
                smbclient.remove(path)
        except Exception as error:
            self._show_smb_error(
                "Не удалось удалить объект",
                error,
            )
            return

        self.refresh()

    def _open_remote_file(
        self,
        entry: SmbEntry,
    ) -> None:
        temp_dir = Path(
            tempfile.gettempdir()
        ) / APP_STORAGE_NAMESPACE / "smb"
        temp_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        local_path = temp_dir / entry.name

        try:
            self._download_file(
                self._child_unc(entry.name),
                local_path,
            )
        except Exception as error:
            self._show_smb_error(
                "Не удалось открыть файл",
                error,
            )
            return

        QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                str(local_path)
            )
        )

    def _download_file(
        self,
        remote_path: str,
        local_path: Path,
    ) -> None:
        local_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with smbclient.open_file(
            remote_path,
            mode="rb",
        ) as source:
            with local_path.open("wb") as destination:
                shutil.copyfileobj(
                    source,
                    destination,
                    length=1024 * 1024,
                )

    def _download_directory(
        self,
        remote_path: str,
        local_path: Path,
    ) -> None:
        local_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        with smbclient.scandir(
            remote_path
        ) as iterator:
            for item in iterator:
                remote_child = (
                    remote_path
                    + "\\"
                    + item.name
                )
                local_child = (
                    local_path
                    / item.name
                )

                if item.is_dir():
                    self._download_directory(
                        remote_child,
                        local_child,
                    )
                else:
                    self._download_file(
                        remote_child,
                        local_child,
                    )

    def _remove_directory(
        self,
        remote_path: str,
    ) -> None:
        with smbclient.scandir(
            remote_path
        ) as iterator:
            entries = list(iterator)

        for item in entries:
            child = (
                remote_path
                + "\\"
                + item.name
            )

            if item.is_dir():
                self._remove_directory(
                    child
                )
            else:
                smbclient.remove(child)

        smbclient.rmdir(remote_path)

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _set_connected(
        self,
        connected: bool,
    ) -> None:
        self.connected = connected
        self.connect_button.setText(
            "Переподключить"
            if connected
            else "Подключить"
        )

        for widget in (
            self.up_button,
            self.refresh_button,
            self.new_folder_button,
            self.upload_button,
        ):
            widget.setEnabled(connected)

        self._update_action_buttons()

        if not connected:
            self.path_label.setText(
                "Не подключено"
            )
            self.table.setRowCount(0)

    def _update_action_buttons(self) -> None:
        selected = (
            self.connected
            and self._selected_entry()
            is not None
        )

        for widget in (
            self.download_button,
            self.rename_button,
            self.delete_button,
        ):
            widget.setEnabled(selected)

        self.up_button.setEnabled(
            self.connected
            and bool(self.current_parts)
        )

    def _update_path_label(self) -> None:
        self.path_label.setText(
            self._current_unc()
        )

    def _selected_entry(
        self,
    ) -> SmbEntry | None:
        rows = self.table.selectionModel().selectedRows()

        if not rows:
            return None

        return self._entry_at(
            rows[0].row()
        )

    def _entry_at(
        self,
        row: int,
    ) -> SmbEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]

        return None

    def _share_root(
        self,
        share: str | None = None,
    ) -> str:
        selected_share = (
            share
            if share is not None
            else self.share_name
        )

        return (
            rf"\\{self.host}"
            rf"\{selected_share}"
        )

    def _current_unc(self) -> str:
        root = self._share_root()

        if not self.current_parts:
            return root

        return (
            root
            + "\\"
            + "\\".join(self.current_parts)
        )

    def _child_unc(
        self,
        name: str,
    ) -> str:
        return (
            self._current_unc()
            + "\\"
            + name
        )

    def _register_current_session(self) -> None:
        if smbclient is None:
            raise RuntimeError(
                "Библиотека smbprotocol не установлена."
            )

        smbclient.register_session(
            self.host,
            username=self._username or None,
            password=self._password or None,
            connection_timeout=8,
        )

    def _reset_and_reconnect(self) -> None:
        if smbclient is None:
            return

        try:
            smbclient.delete_session(
                self.host
            )
        except Exception:
            pass

        try:
            smbclient.reset_connection_cache(
                fail_on_error=False
            )
        except TypeError:
            smbclient.reset_connection_cache()
        except Exception:
            pass

        self._register_current_session()

    def _run_smb(
        self,
        operation,
        *,
        retry: bool = True,
    ):
        try:
            return operation()
        except Exception as error:
            if (
                not retry
                or not self._is_connection_closed_error(
                    error
                )
            ):
                raise

            self._reset_and_reconnect()
            return operation()

    @staticmethod
    def _is_connection_closed_error(
        error: Exception,
    ) -> bool:
        message = str(error).lower()

        markers = (
            "wsaeshutdown",
            "socket aborted by peer",
            "treating as eof",
            "connection reset",
            "connection aborted",
            "broken pipe",
            "unexpected eof",
            "end of file",
            "network name deleted",
            "status_network_name_deleted",
            "status_connection_disconnected",
        )

        return any(
            marker in message
            for marker in markers
        )

    @staticmethod
    def _normalize_host(
        host: str,
    ) -> str:
        text = str(host or "").strip()

        for prefix in (
            "https://",
            "http://",
        ):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]

        text = text.split("/", 1)[0]

        # Корректно удаляем порт только у IPv4/имени.
        if text.count(":") == 1:
            text = text.split(":", 1)[0]

        return text

    @staticmethod
    def _is_local_host(
        host: str,
    ) -> bool:
        try:
            address = ipaddress.ip_address(
                host
            )
        except ValueError:
            return False

        return bool(
            address.is_private
            or address.is_link_local
            or address.is_loopback
        )

    @staticmethod
    def _clean_share(
        value: str,
    ) -> str:
        return (
            str(value or "")
            .strip()
            .strip("\\/")
        )

    @staticmethod
    def _valid_name(
        name: str,
    ) -> bool:
        return not any(
            character in name
            for character in '<>:"/\\|?*'
        )

    @staticmethod
    def _format_size(
        value: int,
    ) -> str:
        size = float(value)

        for unit in (
            "Б",
            "КБ",
            "МБ",
            "ГБ",
            "ТБ",
        ):
            if size < 1024 or unit == "ТБ":
                if unit == "Б":
                    return f"{size:.0f} {unit}"

                return f"{size:.1f} {unit}"

            size /= 1024

        return f"{value} Б"

    @staticmethod
    def _format_time(
        value: float | None,
    ) -> str:
        if value is None:
            return ""

        try:
            return datetime.fromtimestamp(
                value
            ).strftime(
                "%d.%m.%Y %H:%M"
            )
        except (
            OSError,
            OverflowError,
            ValueError,
        ):
            return ""

    @staticmethod
    def _confirm_replace(
        name: str,
    ) -> bool:
        answer = ask_yes_no(
            None,
            "Заменить объект",
            (
                "Объект с таким именем уже существует. "
                "Заменить?\n\n"
                f"{name}"
            ),
        )

        return (
            answer
            == QMessageBox.StandardButton.Yes
        )

    @staticmethod
    def _show_smb_error(
        title: str,
        error: Exception,
    ) -> None:
        message = str(error).strip()

        if not message:
            message = error.__class__.__name__

        QMessageBox.critical(
            None,
            title,
            message,
        )

    def _remote_exists(
        self,
        path: str,
    ) -> bool:
        try:
            self._run_smb(
                lambda: smbclient.stat(path)
            )
            return True
        except Exception:
            return False
