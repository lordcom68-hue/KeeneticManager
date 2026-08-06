from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.database import Database
from tests.test_router_credentials import (
    MemoryCredentialStore,
    closing_sqlite_connection,
)


class RouterOrderingDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name)
            / "routers.db"
        )
        self.store = MemoryCredentialStore()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_database(self) -> Database:
        return Database(
            database_path=self.database_path,
            credential_store=self.store,
        )

    def add_router(
        self,
        database: Database,
        name: str,
    ) -> int:
        slug = name.lower().replace(" ", "-")
        return database.add_router(
            name=name,
            local_ip="",
            keendns=f"{slug}.example.invalid",
            connection_mode="auto",
            username="admin",
            password=None,
            group_name="",
        )

    def test_migration_from_0270_preserves_alphabetical_order(self) -> None:
        with closing_sqlite_connection(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE routers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    local_ip TEXT,
                    keendns TEXT,
                    connection_mode TEXT NOT NULL DEFAULT 'auto',
                    username TEXT NOT NULL DEFAULT 'admin',
                    credential_ref TEXT,
                    group_name TEXT DEFAULT '',
                    status INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO routers (
                    name,
                    keendns
                )
                VALUES (?, ?)
                """,
                (
                    ("Zulu", "zulu.example.invalid"),
                    ("Alpha", "alpha.example.invalid"),
                    ("Mike", "mike.example.invalid"),
                ),
            )
            connection.execute("PRAGMA user_version = 270")

        database = self.create_database()
        routers = database.get_routers()

        self.assertEqual(
            [router["name"] for router in routers],
            ["Alpha", "Mike", "Zulu"],
        )
        self.assertEqual(
            [router["sort_order"] for router in routers],
            [0, 1, 2],
        )
        self.assertEqual(
            database.get_schema_snapshot()["user_version"],
            280,
        )

    def test_drag_order_is_persisted_after_reopen(self) -> None:
        database = self.create_database()
        first = self.add_router(database, "First")
        second = self.add_router(database, "Second")
        third = self.add_router(database, "Third")

        database.set_router_order([third, first, second])

        reopened = self.create_database()
        self.assertEqual(
            [router["id"] for router in reopened.get_routers()],
            [third, first, second],
        )
        self.assertEqual(
            [
                router["sort_order"]
                for router in reopened.get_routers()
            ],
            [0, 1, 2],
        )

    def test_new_router_is_appended_to_saved_order(self) -> None:
        database = self.create_database()
        first = self.add_router(database, "First")
        second = self.add_router(database, "Second")
        database.set_router_order([second, first])

        third = self.add_router(database, "Third")

        self.assertEqual(
            [router["id"] for router in database.get_routers()],
            [second, first, third],
        )

    def test_delete_compacts_sort_order(self) -> None:
        database = self.create_database()
        first = self.add_router(database, "First")
        second = self.add_router(database, "Second")
        third = self.add_router(database, "Third")
        database.set_router_order([third, first, second])

        database.delete_router(first)
        routers = database.get_routers()

        self.assertEqual(
            [router["id"] for router in routers],
            [third, second],
        )
        self.assertEqual(
            [router["sort_order"] for router in routers],
            [0, 1],
        )

    def test_incomplete_or_duplicate_order_is_rejected(self) -> None:
        database = self.create_database()
        first = self.add_router(database, "First")
        second = self.add_router(database, "Second")

        with self.assertRaises(ValueError):
            database.set_router_order([first])

        with self.assertRaises(ValueError):
            database.set_router_order([first, first])

        self.assertEqual(
            [router["id"] for router in database.get_routers()],
            [first, second],
        )

    def test_sort_order_column_is_not_unique_or_secret(self) -> None:
        database = self.create_database()
        self.add_router(database, "First")
        snapshot = database.get_schema_snapshot()
        columns = {
            str(column["name"]): column
            for column in snapshot["routers_columns"]
        }

        self.assertIn("sort_order", columns)
        self.assertEqual(int(columns["sort_order"]["notnull"]), 1)
        self.assertEqual(str(columns["sort_order"]["dflt_value"]), "0")

        with closing_sqlite_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT sort_order FROM routers LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], 0)


try:
    from PySide6.QtCore import (
        QAbstractAnimation,
        QEvent,
        QPoint,
        QPointF,
        Qt,
    )
    from PySide6.QtGui import QColor, QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QListWidgetItem,
    )

    from ui.widgets.router_list import (
        ROUTER_STATUS_ROLE,
        RouterItemDelegate,
        RouterListWidget,
    )

    PYSIDE_AVAILABLE = True
except ModuleNotFoundError:
    PYSIDE_AVAILABLE = False


@unittest.skipUnless(
    PYSIDE_AVAILABLE,
    "PySide6 отсутствует в тестовой среде",
)
class RouterListWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QApplication.instance()
            or QApplication([])
        )

    def build_widget(self) -> RouterListWidget:
        widget = RouterListWidget()
        widget.resize(360, 240)

        for router_id, name, status in (
            (1, "First", "online"),
            (2, "Second", "offline"),
            (3, "Third", "checking"),
            (4, "Fourth", "unknown"),
        ):
            item = QListWidgetItem(name)
            item.setData(
                Qt.ItemDataRole.UserRole,
                router_id,
            )
            item.setData(
                ROUTER_STATUS_ROLE,
                status,
            )
            flags = item.flags()
            flags &= ~Qt.ItemFlag.ItemIsDragEnabled
            flags &= ~Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            widget.addItem(item)

        widget.show()
        self.app.processEvents()
        return widget

    def test_flat_status_palette_matches_dark_theme(self) -> None:
        expected = {
            "online": "#34d399",
            "offline": "#fb7185",
            "checking": "#fbbf24",
            "unknown": "#64748b",
        }

        for state, hex_color in expected.items():
            with self.subTest(state=state):
                color = RouterItemDelegate.status_color(state)
                self.assertIsInstance(color, QColor)
                self.assertEqual(color.name(), hex_color)

        self.assertEqual(
            RouterItemDelegate.normalize_status(None),
            "unknown",
        )

    def test_status_is_separate_from_router_name(self) -> None:
        widget = self.build_widget()
        try:
            item = widget.item(0)
            self.assertEqual(item.text(), "First")
            self.assertEqual(
                item.data(ROUTER_STATUS_ROLE),
                "online",
            )
        finally:
            widget.close()

    def send_pressed_move(
        self,
        widget: RouterListWidget,
        position: QPoint,
    ) -> None:
        viewport = widget.viewport()
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(position),
            QPointF(position),
            QPointF(
                viewport.mapToGlobal(position)
            ),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, event)
        self.app.processEvents()

    def drag_row(
        self,
        widget: RouterListWidget,
        source_row: int,
        target_row: int,
        *,
        bottom_half: bool,
    ) -> None:
        source_rect = widget.visualItemRect(
            widget.item(source_row)
        )
        start = QPoint(
            source_rect.left() + 8,
            source_rect.center().y(),
        )

        target_rect = widget.visualItemRect(
            widget.item(target_row)
        )
        target = QPoint(
            target_rect.center().x(),
            (
                target_rect.bottom() - 2
                if bottom_half
                else target_rect.top() + 2
            ),
        )

        QTest.mousePress(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        self.send_pressed_move(widget, target)
        self.assertTrue(widget.manual_drag_active)
        QTest.mouseRelease(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        self.app.processEvents()

    def test_handle_hit_area_is_separate_from_row_body(self) -> None:
        widget = self.build_widget()
        item = widget.item(0)
        rect = widget.visualItemRect(item)

        self.assertTrue(
            widget.is_drag_handle_position(
                QPoint(
                    rect.left() + 8,
                    rect.center().y(),
                )
            )
        )
        self.assertFalse(
            widget.is_drag_handle_position(
                QPoint(
                    rect.left()
                    + widget.HANDLE_WIDTH
                    + 30,
                    rect.center().y(),
                )
            )
        )
        widget.close()

    def test_handle_uses_open_and_closed_hand_cursors(self) -> None:
        widget = self.build_widget()
        try:
            rect = widget.visualItemRect(widget.item(0))
            handle_position = QPoint(
                rect.left() + 12,
                rect.center().y(),
            )

            widget._update_hover_handle(
                handle_position
            )
            self.app.processEvents()
            self.assertEqual(widget._hover_handle_row, 0)
            self.assertEqual(
                widget.viewport().cursor().shape(),
                Qt.CursorShape.OpenHandCursor,
            )

            QTest.mousePress(
                widget.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                handle_position,
            )
            self.app.processEvents()
            self.assertEqual(
                widget.viewport().cursor().shape(),
                Qt.CursorShape.ClosedHandCursor,
            )

            QTest.mouseRelease(
                widget.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                handle_position,
            )
            self.app.processEvents()
            self.assertEqual(
                widget.viewport().cursor().shape(),
                Qt.CursorShape.OpenHandCursor,
            )
        finally:
            widget.close()

    def test_native_qt_drag_and_drop_is_always_disabled(self) -> None:
        widget = self.build_widget()

        self.assertEqual(
            widget.dragDropMode(),
            QAbstractItemView.DragDropMode.NoDragDrop,
        )
        self.assertFalse(widget.dragEnabled())
        self.assertFalse(widget.acceptDrops())

        for row in range(widget.count()):
            flags = widget.item(row).flags()
            self.assertFalse(
                bool(flags & Qt.ItemFlag.ItemIsDragEnabled)
            )
            self.assertFalse(
                bool(flags & Qt.ItemFlag.ItemIsDropEnabled)
            )

        widget.close()

    def test_reordering_can_be_disabled_during_search(self) -> None:
        widget = self.build_widget()
        widget.set_reordering_enabled(False)

        self.assertFalse(widget.reordering_enabled)
        self.assertEqual(
            widget.dragDropMode(),
            QAbstractItemView.DragDropMode.NoDragDrop,
        )
        widget.close()

    def test_drop_on_row_uses_top_or_bottom_half(self) -> None:
        widget = self.build_widget()
        target = widget.item(1)
        rect = widget.visualItemRect(target)

        self.assertEqual(
            widget.drop_insertion_row(
                QPoint(
                    rect.center().x(),
                    rect.top() + 2,
                )
            ),
            1,
        )
        self.assertEqual(
            widget.drop_insertion_row(
                QPoint(
                    rect.center().x(),
                    rect.bottom() - 2,
                )
            ),
            2,
        )
        widget.close()

    def test_manual_move_never_loses_router_item(self) -> None:
        widget = self.build_widget()
        before_ids = widget.current_router_order()

        self.assertTrue(widget.move_router_row(0, 3))
        self.assertEqual(widget.count(), 4)
        self.assertCountEqual(
            widget.current_router_order(),
            before_ids,
        )
        self.assertEqual(
            widget.current_router_order(),
            [2, 3, 1, 4],
        )
        widget.close()

    def test_real_mouse_gesture_reorders_without_disappearing(self) -> None:
        widget = self.build_widget()
        emitted: list[list[int]] = []
        widget.router_order_changed.connect(
            lambda order: emitted.append(
                [int(value) for value in order]
            )
        )

        self.drag_row(
            widget,
            0,
            2,
            bottom_half=True,
        )

        self.assertEqual(widget.count(), 4)
        self.assertEqual(
            widget.current_router_order(),
            [2, 3, 1, 4],
        )
        self.assertEqual(
            emitted[-1],
            [2, 3, 1, 4],
        )
        self.assertFalse(widget.manual_drag_active)
        widget.close()

    def test_repeated_real_mouse_gestures_preserve_all_rows(self) -> None:
        widget = self.build_widget()
        original = widget.current_router_order()

        for source_row, target_row, bottom_half in (
            (0, 2, True),
            (3, 1, False),
            (0, 2, True),
            (2, 0, False),
            (1, 3, True),
            (0, 1, True),
            (3, 2, False),
            (1, 0, False),
        ):
            self.drag_row(
                widget,
                source_row,
                target_row,
                bottom_half=bottom_half,
            )
            current = widget.current_router_order()
            self.assertEqual(widget.count(), 4)
            self.assertEqual(len(current), 4)
            self.assertCountEqual(current, original)

        widget.close()

    def test_drag_preview_follows_cursor_without_early_reorder(self) -> None:
        widget = self.build_widget()
        before = widget.current_router_order()
        source_rect = widget.visualItemRect(
            widget.item(3)
        )
        start = QPoint(
            source_rect.left() + 10,
            source_rect.center().y(),
        )
        target_rect = widget.visualItemRect(
            widget.item(1)
        )
        target = QPoint(
            target_rect.center().x(),
            target_rect.center().y(),
        )

        QTest.mousePress(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        self.send_pressed_move(widget, target)

        preview = widget.drag_preview_rect()
        grab_offset = start.y() - source_rect.top()
        self.assertTrue(widget.manual_drag_active)
        self.assertFalse(preview.isEmpty())
        self.assertEqual(
            preview.top(),
            target.y() - grab_offset,
        )
        self.assertEqual(
            widget.current_router_order(),
            before,
        )
        self.assertEqual(
            widget.verticalScrollMode(),
            QAbstractItemView.ScrollMode.ScrollPerPixel,
        )

        QTest.mouseRelease(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        self.app.processEvents()

        self.assertEqual(
            widget.current_router_order(),
            [1, 2, 4, 3],
        )
        widget.close()

    def test_insertion_indicator_uses_short_eased_animation(self) -> None:
        widget = self.build_widget()
        source_rect = widget.visualItemRect(
            widget.item(3)
        )
        start = QPoint(
            source_rect.left() + 10,
            source_rect.center().y(),
        )
        first_rect = widget.visualItemRect(
            widget.item(2)
        )
        first_target = QPoint(
            first_rect.center().x(),
            first_rect.bottom() - 2,
        )
        final_rect = widget.visualItemRect(
            widget.item(0)
        )
        final_target = QPoint(
            final_rect.center().x(),
            final_rect.top() + 2,
        )

        QTest.mousePress(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        self.send_pressed_move(widget, first_target)
        self.send_pressed_move(widget, final_target)

        expected_y = widget.insertion_indicator_y(0)
        self.assertEqual(
            widget._indicator_animation.duration(),
            95,
        )
        self.assertEqual(
            widget._indicator_animation.state(),
            QAbstractAnimation.State.Running,
        )
        self.assertEqual(
            float(widget._indicator_animation.endValue()),
            float(expected_y),
        )

        QTest.qWait(120)
        self.app.processEvents()
        self.assertAlmostEqual(
            float(widget._indicator_y),
            float(expected_y),
            delta=0.5,
        )

        widget.cancel_manual_drag()
        self.assertFalse(widget.manual_drag_active)
        self.assertIsNone(widget._indicator_y)
        widget.close()

    def test_edge_auto_scroll_is_continuous_and_speed_limited(self) -> None:
        widget = self.build_widget()

        for index in range(20):
            item = QListWidgetItem(f"Extra {index}")
            item.setData(
                Qt.ItemDataRole.UserRole,
                100 + index,
            )
            widget.addItem(item)

        self.app.processEvents()
        widget._manual_drag_active = True
        widget._drag_cursor_position = QPoint(
            20,
            widget.viewport().height() + 200,
        )
        widget._update_auto_scroll(
            widget._drag_cursor_position
        )

        self.assertEqual(widget._auto_scroll_speed, 12)
        self.assertTrue(widget._auto_scroll_timer.isActive())
        previous = widget.verticalScrollBar().value()
        QTest.qWait(55)
        self.app.processEvents()
        self.assertGreater(
            widget.verticalScrollBar().value(),
            previous,
        )

        widget.cancel_manual_drag()
        self.assertFalse(widget._auto_scroll_timer.isActive())
        widget.close()

    def test_dragging_from_row_body_does_not_reorder(self) -> None:
        widget = self.build_widget()
        before = widget.current_router_order()
        source_rect = widget.visualItemRect(
            widget.item(0)
        )
        start = QPoint(
            source_rect.left()
            + widget.HANDLE_WIDTH
            + 40,
            source_rect.center().y(),
        )
        target_rect = widget.visualItemRect(
            widget.item(2)
        )
        target = QPoint(
            target_rect.center().x(),
            target_rect.bottom() - 2,
        )

        QTest.mousePress(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        self.send_pressed_move(widget, target)
        QTest.mouseRelease(
            widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        self.app.processEvents()

        self.assertFalse(widget.manual_drag_active)
        self.assertEqual(
            widget.current_router_order(),
            before,
        )
        self.assertEqual(widget.count(), 4)
        widget.close()


if __name__ == "__main__":
    unittest.main()
