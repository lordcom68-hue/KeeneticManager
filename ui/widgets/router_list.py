from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)


ROUTER_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class RouterItemDelegate(QStyledItemDelegate):
    """Рисует ручку перестановки и плоский индикатор доступности."""

    HANDLE_WIDTH = 34
    STATUS_WIDTH = 24
    HANDLE_CAPSULE_WIDTH = 20.0
    HANDLE_CAPSULE_HEIGHT = 28.0
    HANDLE_LINE_WIDTH = 10.0
    HANDLE_LINE_GAP = 5.0
    STATUS_DIAMETER = 9.0
    STATUS_RING_DIAMETER = 14.0
    STATUS_COLORS = {
        "online": QColor("#34D399"),
        "offline": QColor("#FB7185"),
        "checking": QColor("#FBBF24"),
        "unknown": QColor("#64748B"),
    }

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        widget = option.widget
        fade_drag_source = bool(
            getattr(widget, "_manual_drag_active", False)
            and getattr(widget, "_drag_source_row", -1)
            == index.row()
            and not getattr(
                widget,
                "_painting_drag_preview",
                False,
            )
        )

        if fade_drag_source:
            painter.save()
            painter.setOpacity(0.28)

        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        text = prepared.text
        prepared.text = ""

        widget = prepared.widget
        style = (
            widget.style()
            if widget is not None
            else QApplication.style()
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            prepared,
            painter,
            widget,
        )

        handle_rect = QRect(
            option.rect.left(),
            option.rect.top(),
            self.HANDLE_WIDTH,
            option.rect.height(),
        )
        self._paint_handle(
            painter,
            option,
            handle_rect,
            enabled=bool(
                getattr(widget, "reordering_enabled", True)
            ),
            hovered=(
                getattr(widget, "_hover_handle_row", -1)
                == index.row()
            ),
            pressed=(
                getattr(widget, "_handle_pressed", False)
                and getattr(widget, "_drag_source_row", -1)
                == index.row()
            ),
            dragging=(
                getattr(widget, "_manual_drag_active", False)
                and getattr(widget, "_drag_source_row", -1)
                == index.row()
            ),
        )
        status_rect = QRect(
            option.rect.left() + self.HANDLE_WIDTH,
            option.rect.top(),
            self.STATUS_WIDTH,
            option.rect.height(),
        )
        self._paint_status(
            painter,
            status_rect,
            index.data(ROUTER_STATUS_ROLE),
        )

        text_rect = option.rect.adjusted(
            self.HANDLE_WIDTH + self.STATUS_WIDTH,
            0,
            -6,
            0,
        )
        selected = bool(
            option.state
            & QStyle.StateFlag.State_Selected
        )
        color_role = (
            QPalette.ColorRole.HighlightedText
            if selected
            else QPalette.ColorRole.Text
        )

        painter.save()
        painter.setPen(option.palette.color(color_role))
        painter.setFont(option.font)
        painter.drawText(
            text_rect,
            int(
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignLeft
            ),
            text,
        )
        painter.restore()

        if fade_drag_source:
            painter.restore()

    def _paint_handle(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        rect: QRect,
        *,
        enabled: bool,
        hovered: bool,
        pressed: bool,
        dragging: bool,
    ) -> None:
        color = self.handle_color(
            option,
            enabled=enabled,
            hovered=hovered,
            dragging=dragging,
        )

        center = rect.center()
        capsule = QRectF(
            center.x() - self.HANDLE_CAPSULE_WIDTH / 2.0,
            center.y() - self.HANDLE_CAPSULE_HEIGHT / 2.0,
            self.HANDLE_CAPSULE_WIDTH,
            self.HANDLE_CAPSULE_HEIGHT,
        )

        painter.save()
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        if enabled and (hovered or pressed or dragging):
            if dragging:
                background = QColor("#2563EB")
                background.setAlpha(46)
                border = QColor("#60A5FA")
                border.setAlpha(135)
            else:
                background = QColor("#334155")
                background.setAlpha(150 if pressed else 105)
                border = QColor("#64748B")
                border.setAlpha(100)

            painter.setBrush(background)
            painter.setPen(QPen(border, 1.0))
            painter.drawRoundedRect(
                capsule,
                7.0,
                7.0,
            )

        line_pen = QPen(color, 1.7)
        line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(line_pen)
        half_width = self.HANDLE_LINE_WIDTH / 2.0

        for row in (-1, 0, 1):
            y = center.y() + row * self.HANDLE_LINE_GAP
            painter.drawLine(
                QPointF(center.x() - half_width, y),
                QPointF(center.x() + half_width, y),
            )

        painter.restore()

    def _paint_status(
        self,
        painter: QPainter,
        rect: QRect,
        state: object,
    ) -> None:
        normalized = self.normalize_status(state)
        color = self.status_color(normalized)
        center = rect.center()

        painter.save()
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        if normalized == "online":
            ring_color = QColor(color)
            ring_color.setAlpha(85)
            ring_radius = self.STATUS_RING_DIAMETER / 2.0
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring_color, 1.25))
            painter.drawEllipse(
                QRectF(
                    center.x() - ring_radius,
                    center.y() - ring_radius,
                    self.STATUS_RING_DIAMETER,
                    self.STATUS_RING_DIAMETER,
                )
            )

        radius = self.STATUS_DIAMETER / 2.0
        dot_rect = QRectF(
            center.x() - radius,
            center.y() - radius,
            self.STATUS_DIAMETER,
            self.STATUS_DIAMETER,
        )

        if normalized == "unknown":
            fill = QColor(color)
            fill.setAlpha(38)
            painter.setBrush(fill)
            painter.setPen(QPen(color, 1.25))
        else:
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)

        painter.drawEllipse(dot_rect)
        painter.restore()

    @classmethod
    def status_color(
        cls,
        state: object,
    ) -> QColor:
        return QColor(
            cls.STATUS_COLORS[
                cls.normalize_status(state)
            ]
        )

    @staticmethod
    def normalize_status(
        state: object,
    ) -> str:
        normalized = str(state or "").strip().lower()

        if normalized in {
            "online",
            "offline",
            "checking",
            "unknown",
        }:
            return normalized

        return "unknown"

    def handle_color(
        self,
        option: QStyleOptionViewItem,
        *,
        enabled: bool,
        hovered: bool = False,
        dragging: bool = False,
    ) -> QColor:
        del option

        if not enabled:
            color = QColor("#64748B")
            color.setAlpha(70)
            return color

        if dragging:
            return QColor("#60A5FA")

        if hovered:
            return QColor("#A8B3C2")

        color = QColor("#7C899B")
        return color

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index,
    ) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(
            size.width()
            + self.HANDLE_WIDTH
            + self.STATUS_WIDTH,
            max(size.height(), 34),
        )


class RouterListWidget(QListWidget):
    """
    Ручная перестановка роутеров без нативного Qt drag-and-drop.

    Qt InternalMove удалял исходный QListWidgetItem повторно после того,
    как пользовательский dropEvent уже переставил его. В результате
    строки последовательно исчезали только из GUI. Теперь до отпускания
    кнопки мыши модель вообще не изменяется, а затем выполняется ровно
    один контролируемый takeItem()/insertItem(). Во время движения
    плавающая копия строки следует за курсором, а место вставки и
    прокрутка анимируются отдельно от модели.
    """

    router_order_changed = Signal(list)
    HANDLE_WIDTH = RouterItemDelegate.HANDLE_WIDTH

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.reordering_enabled = True
        self._handle_pressed = False
        self._drag_source_row = -1
        self._press_position = QPoint()
        self._manual_drag_active = False
        self._pending_insertion_row = -1
        self._hover_handle_row = -1
        self._drag_cursor_position = QPoint()
        self._drag_preview_offset_y = 0
        self._painting_drag_preview = False
        self._indicator_y: float | None = None
        self._auto_scroll_speed = 0

        self._indicator_animation = QVariantAnimation(
            self
        )
        self._indicator_animation.setDuration(95)
        self._indicator_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self._indicator_animation.valueChanged.connect(
            self._indicator_animation_changed
        )

        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(16)
        self._auto_scroll_timer.timeout.connect(
            self._perform_auto_scroll
        )

        self.setItemDelegate(RouterItemDelegate(self))
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        # Важно: полностью отключаем стандартный drag-and-drop Qt.
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDropIndicatorShown(False)

        self.setAutoScroll(False)
        self.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.verticalScrollBar().setSingleStep(7)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setToolTip(
            "Чтобы изменить порядок, зажмите трёхлинейную "
            "ручку слева, переместите строку и отпустите кнопку."
        )

    @property
    def manual_drag_active(self) -> bool:
        return self._manual_drag_active

    def set_reordering_enabled(
        self,
        enabled: bool,
    ) -> None:
        self.reordering_enabled = bool(enabled)
        self.cancel_manual_drag()

        # Нативный Qt DnD не включается даже в разрешённом режиме.
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.viewport().update()

    def is_drag_handle_position(
        self,
        position: QPoint,
    ) -> bool:
        item = self.itemAt(position)

        if item is None:
            return False

        item_rect = self.visualItemRect(item)
        handle_rect = QRect(
            item_rect.left(),
            item_rect.top(),
            self.HANDLE_WIDTH,
            item_rect.height(),
        )
        return handle_rect.contains(position)

    def mousePressEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        position = event.position().toPoint()
        pressed_item = self.itemAt(position)

        self._handle_pressed = bool(
            self.reordering_enabled
            and event.button()
            == Qt.MouseButton.LeftButton
            and pressed_item is not None
            and self.is_drag_handle_position(position)
        )
        self._drag_source_row = (
            self.row(pressed_item)
            if self._handle_pressed
            else -1
        )
        self._press_position = QPoint(position)
        self._manual_drag_active = False
        self._pending_insertion_row = -1
        self._drag_cursor_position = QPoint(position)
        self._indicator_animation.stop()
        self._indicator_y = None

        # Сначала сохраняем обычное поведение выбора строки.
        super().mousePressEvent(event)

        if self._handle_pressed:
            self._set_hover_handle_row(
                self._drag_source_row
            )
            self.viewport().setCursor(
                Qt.CursorShape.ClosedHandCursor
            )

    def mouseMoveEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        position = event.position().toPoint()

        if not (
            self.reordering_enabled
            and self._handle_pressed
            and 0 <= self._drag_source_row < self.count()
            and bool(
                event.buttons()
                & Qt.MouseButton.LeftButton
            )
        ):
            self._update_hover_handle(position)
            super().mouseMoveEvent(event)
            return

        if not self._manual_drag_active:
            distance = (
                position - self._press_position
            ).manhattanLength()
            drag_distance = (
                QApplication.styleHints()
                .startDragDistance()
            )

            if distance < drag_distance:
                event.accept()
                return

            self._manual_drag_active = True
            source_item = self.item(
                self._drag_source_row
            )
            source_rect = self.visualItemRect(
                source_item
            )
            self._drag_preview_offset_y = (
                self._press_position.y()
                - source_rect.top()
            )
            self.viewport().setCursor(
                Qt.CursorShape.ClosedHandCursor
            )

        self._drag_cursor_position = QPoint(position)
        self._update_auto_scroll(position)
        self._set_pending_insertion_row(
            self.drop_insertion_row(position)
        )
        self.viewport().update()
        event.accept()

    def mouseReleaseEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._manual_drag_active
        ):
            source_row = self._drag_source_row
            insertion_row = self.drop_insertion_row(
                event.position().toPoint()
            )
            changed = self.move_router_row(
                source_row,
                insertion_row,
            )
            self.cancel_manual_drag()
            event.accept()

            if changed:
                self._emit_router_order()
            self._update_hover_handle(
                event.position().toPoint()
            )
            return

        super().mouseReleaseEvent(event)
        self.cancel_manual_drag()
        self._update_hover_handle(
            event.position().toPoint()
        )

    def leaveEvent(self, event) -> None:
        self._set_hover_handle_row(-1)

        if not self._manual_drag_active:
            self.viewport().unsetCursor()

        super().leaveEvent(event)

    def keyPressEvent(
        self,
        event: QKeyEvent,
    ) -> None:
        if (
            event.key() == Qt.Key.Key_Escape
            and (
                self._handle_pressed
                or self._manual_drag_active
            )
        ):
            self.cancel_manual_drag()
            event.accept()
            return

        super().keyPressEvent(event)

    def cancel_manual_drag(self) -> None:
        self._handle_pressed = False
        self._drag_source_row = -1
        self._press_position = QPoint()
        self._manual_drag_active = False
        self._pending_insertion_row = -1
        self._hover_handle_row = -1
        self._drag_cursor_position = QPoint()
        self._drag_preview_offset_y = 0
        self._indicator_animation.stop()
        self._indicator_y = None
        self._auto_scroll_speed = 0
        self._auto_scroll_timer.stop()
        self.viewport().unsetCursor()
        self.viewport().update()

    def _update_hover_handle(
        self,
        position: QPoint,
    ) -> None:
        row = -1

        if self.reordering_enabled:
            item = self.itemAt(position)

            if (
                item is not None
                and self.is_drag_handle_position(position)
            ):
                row = self.row(item)

        self._set_hover_handle_row(row)

        if row >= 0:
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor
            )
        else:
            self.viewport().unsetCursor()

    def _set_hover_handle_row(
        self,
        row: int,
    ) -> None:
        normalized = int(row)

        if self._hover_handle_row == normalized:
            return

        self._hover_handle_row = normalized
        self.viewport().update()

    def _update_auto_scroll(
        self,
        position: QPoint,
    ) -> None:
        margin = min(
            48,
            max(28, self.viewport().height() // 5),
        )
        scroll_bar = self.verticalScrollBar()
        speed = 0

        if position.y() < margin:
            penetration = min(
                margin,
                margin - position.y(),
            )
            speed = -max(
                2,
                round(12 * penetration / margin),
            )
        elif (
            position.y()
            > self.viewport().height() - margin
        ):
            penetration = min(
                margin,
                position.y()
                - (self.viewport().height() - margin),
            )
            speed = max(
                2,
                round(12 * penetration / margin),
            )

        if scroll_bar.maximum() <= scroll_bar.minimum():
            speed = 0

        self._auto_scroll_speed = speed

        if speed:
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        else:
            self._auto_scroll_timer.stop()

    def _perform_auto_scroll(self) -> None:
        if not (
            self._manual_drag_active
            and self._auto_scroll_speed
        ):
            self._auto_scroll_timer.stop()
            return

        scroll_bar = self.verticalScrollBar()
        previous = scroll_bar.value()
        scroll_bar.setValue(
            previous + self._auto_scroll_speed
        )

        if scroll_bar.value() == previous:
            self._auto_scroll_speed = 0
            self._auto_scroll_timer.stop()
            return

        self._set_pending_insertion_row(
            self.drop_insertion_row(
                self._drag_cursor_position
            ),
            force_animation=True,
        )
        self.viewport().update()

    def _set_pending_insertion_row(
        self,
        row: int,
        *,
        force_animation: bool = False,
    ) -> None:
        normalized = max(0, min(int(row), self.count()))
        target_y = float(
            self.insertion_indicator_y(normalized)
        )

        if (
            normalized == self._pending_insertion_row
            and not force_animation
        ):
            return

        self._pending_insertion_row = normalized

        if self._indicator_y is None:
            self._indicator_y = target_y
            return

        if abs(self._indicator_y - target_y) < 0.5:
            self._indicator_y = target_y
            return

        self._indicator_animation.stop()
        self._indicator_animation.setStartValue(
            float(self._indicator_y)
        )
        self._indicator_animation.setEndValue(target_y)
        self._indicator_animation.start()

    def _indicator_animation_changed(
        self,
        value: object,
    ) -> None:
        try:
            self._indicator_y = float(value)
        except (TypeError, ValueError):
            return

        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        if not self._manual_drag_active:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        self._paint_insertion_indicator(painter)
        self._paint_drag_preview(painter)

    def _paint_insertion_indicator(
        self,
        painter: QPainter,
    ) -> None:
        if not (
            0 <= self._pending_insertion_row
            <= self.count()
            and self.count() > 0
        ):
            return

        y = round(
            self._indicator_y
            if self._indicator_y is not None
            else self.insertion_indicator_y(
                self._pending_insertion_row
            )
        )

        glow = QColor("#60A5FA")
        glow.setAlpha(48)
        glow_pen = QPen(glow)
        glow_pen.setWidthF(6.0)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawLine(
            6,
            y,
            max(6, self.viewport().width() - 7),
            y,
        )

        pen = QPen(
            QColor("#60A5FA")
        )
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            6,
            y,
            max(6, self.viewport().width() - 7),
            y,
        )

    def drag_preview_rect(self) -> QRect:
        if not (
            self._manual_drag_active
            and 0 <= self._drag_source_row < self.count()
        ):
            return QRect()

        source_rect = self.visualItemRect(
            self.item(self._drag_source_row)
        )
        top = (
            self._drag_cursor_position.y()
            - self._drag_preview_offset_y
        )
        return QRect(
            6,
            top,
            max(1, self.viewport().width() - 12),
            source_rect.height(),
        )

    def _paint_drag_preview(
        self,
        painter: QPainter,
    ) -> None:
        preview_rect = self.drag_preview_rect()

        if preview_rect.isEmpty():
            return

        item = self.item(self._drag_source_row)
        index = self.indexFromItem(item)
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = preview_rect
        option.widget = self
        option.state |= (
            QStyle.StateFlag.State_Enabled
            | QStyle.StateFlag.State_Active
            | QStyle.StateFlag.State_Selected
        )

        shadow = QColor("#000000")
        shadow.setAlpha(72)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(
            QRectF(preview_rect.translated(0, 3)),
            7,
            7,
        )

        clip_path = QPainterPath()
        clip_path.addRoundedRect(
            QRectF(preview_rect),
            7,
            7,
        )

        painter.save()
        painter.setClipPath(clip_path)
        painter.setOpacity(0.96)
        self._painting_drag_preview = True

        try:
            self.itemDelegate().paint(
                painter,
                option,
                index,
            )
        finally:
            self._painting_drag_preview = False
            painter.restore()

        border = QColor("#60A5FA")
        border.setAlpha(150)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(
            QRectF(preview_rect).adjusted(
                0.5,
                0.5,
                -0.5,
                -0.5,
            ),
            7,
            7,
        )

    def insertion_indicator_y(
        self,
        insertion_row: int,
    ) -> int:
        if self.count() <= 0:
            return 0

        if insertion_row <= 0:
            return self.visualItemRect(
                self.item(0)
            ).top()

        if insertion_row >= self.count():
            return self.visualItemRect(
                self.item(self.count() - 1)
            ).bottom() + 1

        return self.visualItemRect(
            self.item(insertion_row)
        ).top()

    def drop_insertion_row(
        self,
        position: QPoint,
    ) -> int:
        if self.count() <= 0:
            return 0

        target_item = self.itemAt(position)

        if target_item is None:
            first_rect = self.visualItemRect(
                self.item(0)
            )

            if position.y() < first_rect.top():
                return 0

            return self.count()

        target_row = self.row(target_item)
        target_rect = self.visualItemRect(
            target_item
        )

        if position.y() >= target_rect.center().y():
            return target_row + 1

        return target_row

    def move_router_row(
        self,
        source_row: int,
        insertion_row: int,
    ) -> bool:
        count_before = self.count()
        ids_before = self.current_router_order()

        if not (
            0 <= source_row < count_before
        ):
            return False

        insertion_row = max(
            0,
            min(int(insertion_row), count_before),
        )
        destination_row = (
            insertion_row - 1
            if insertion_row > source_row
            else insertion_row
        )

        if destination_row == source_row:
            return False

        item = self.takeItem(source_row)

        if item is None:
            return False

        destination_row = max(
            0,
            min(destination_row, self.count()),
        )
        self.insertItem(destination_row, item)
        self.setCurrentItem(item)
        self.scrollToItem(
            item,
            QAbstractItemView.ScrollHint.EnsureVisible,
        )
        self.viewport().update()

        ids_after = self.current_router_order()

        if self.count() != count_before:
            raise RuntimeError(
                "Количество роутеров изменилось во время переноса"
            )

        if (
            len(ids_after) != len(ids_before)
            or set(ids_after) != set(ids_before)
        ):
            raise RuntimeError(
                "Набор роутеров изменился во время переноса"
            )

        return True

    def _emit_router_order(self, *args) -> None:
        del args

        if self.reordering_enabled:
            self.router_order_changed.emit(
                self.current_router_order()
            )

    def current_router_order(
        self,
    ) -> list[int]:
        result: list[int] = []

        for index in range(self.count()):
            item = self.item(index)
            value = item.data(
                Qt.ItemDataRole.UserRole
            )

            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue

        return result
