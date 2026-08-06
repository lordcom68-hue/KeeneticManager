from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


COLORS = {
    "canvas": "#0b1220",
    "window": "#0f172a",
    "surface": "#151e2d",
    "surface_raised": "#1b2638",
    "surface_hover": "#223149",
    "border": "#334155",
    "border_soft": "#283548",
    "text": "#f1f5f9",
    "text_secondary": "#cbd5e1",
    "text_muted": "#94a3b8",
    "disabled": "#64748b",
    "accent": "#60a5fa",
    "accent_strong": "#3b82f6",
    "success": "#34d399",
    "warning": "#fbbf24",
    "danger": "#fb7185",
    "purple": "#a78bfa",
}


APPLICATION_STYLESHEET = """
QWidget {
    color: #f1f5f9;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QMainWindow,
QDialog,
QMessageBox {
    background: #0f172a;
}

QWidget:disabled,
QLabel:disabled,
QCheckBox:disabled,
QRadioButton:disabled {
    color: #64748b;
}

QFrame#headerCard,
QFrame#dashboardCard,
QFrame#internetSummaryCard,
QFrame#internetManagementCard,
QFrame#internetConnectionCard,
QFrame#detailedConnectionCard,
QFrame#internetManagementItem,
QFrame#wifiClientSpeedRow,
QFrame#wifiNetworkCard,
QFrame#wifiSettingsNetworkCard,
QFrame#wifiClientCard,
QFrame#routerPortCard,
QFrame#clientSummaryChip,
QFrame#journalSummaryChip,
QFrame#clientDetailsPanel,
QFrame#selectedClientPanel,
QFrame#disabledPortsPanel {
    background-color: #151e2d;
    border: 1px solid #283548;
    border-radius: 12px;
}

QLabel#disabledPortsTitle {
    color: #fbbf24;
    font-size: 12px;
    font-weight: 700;
}

QFrame#wifiClientCard[selected="true"] {
    background-color: #1b2638;
    border-color: #60a5fa;
}

QFrame#clientSummaryChip {
    border-radius: 9px;
}

QFrame#clientSummaryChip[interactive="true"]:hover {
    background-color: #1b2638;
    border-color: rgba(96, 165, 250, 0.65);
}

QFrame#clientSummaryChip[active="true"] {
    background-color: rgba(37, 99, 235, 0.18);
    border-color: #60a5fa;
}

QLabel#clientSummaryValue {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}

QLabel#clientSummaryCaption,
QLabel#clientTabSubtitle,
QLabel#journalSubtitle,
QLabel#journalStatus,
QLabel#journalVisibleCount,
QLabel#clientDetailsSubtitle,
QLabel#clientsEmptyLabel {
    color: #94a3b8;
}

QLabel#journalTitle {
    color: #f8fafc;
    font-size: 22px;
    font-weight: 700;
}

QFrame#journalSummaryChip {
    border-radius: 9px;
}

QLabel#journalSummaryValue {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}

QLabel#journalSummaryCaption {
    color: #94a3b8;
}

QLabel#clientVisibleCount {
    color: #94a3b8;
    padding: 0 4px;
}

QLabel#selectedClientLabel {
    color: #dbeafe;
    font-size: 12px;
    font-weight: 650;
}

QPushButton#clientWifiAccessButton[danger="true"] {
    color: #bfdbfe;
    border-color: rgba(96, 165, 250, 0.42);
}

QPushButton#clientWifiAccessButton[danger="true"]:hover {
    color: #fda4af;
    background-color: rgba(251, 113, 133, 0.10);
    border-color: rgba(251, 113, 133, 0.72);
}

QLabel#clientDetailsTitle {
    color: #f8fafc;
    font-size: 17px;
    font-weight: 700;
}

QLabel#clientDetailsConnection {
    color: #bfdbfe;
    background-color: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(96, 165, 250, 0.42);
    border-radius: 7px;
    padding: 6px 8px;
}

QScrollArea#clientsListScroll,
QScrollArea#clientDetailsScroll {
    background: transparent;
    border: none;
}

QFrame#headerCard {
    background-color: #131d2b;
    border-color: #334155;
}

QFrame#internetManagementItem,
QFrame#wifiClientSpeedRow {
    background-color: rgba(11, 18, 32, 0.34);
    border-radius: 9px;
}

QLabel#routerTitle {
    color: #f8fafc;
}

QLabel#cardTitle,
QLabel#tabTitle,
QLabel#wifiGroupTitle,
QLabel#wifiClientsTitle,
QLabel#internetCardTitle,
QLabel#connectionRoleTitle,
QLabel#internetSettingsTitle,
QLabel#internetSettingsSectionTitle {
    color: #f1f5f9;
}

  QLabel#internetSettingsTitle,
  QLabel#tabTitle {
      font-size: 20px;
      font-weight: 700;
  }

  QLabel#wifiCapabilityLabel {
      color: #94a3b8;
      font-size: 12px;
  }

  QLabel#wifiCapabilityLabel[supportMode="supported"] {
      color: #34d399;
  }

  QLabel#wifiCapabilityLabel[supportMode="legacy"] {
      color: #fbbf24;
  }

  QLabel#settingsCompatibilityLabel {
      color: #94a3b8;
  }

  QLabel#settingsCompatibilityLabel[supportMode="supported"] {
      color: #34d399;
  }

  QLabel#settingsCompatibilityLabel[supportMode="legacy"] {
      color: #fbbf24;
  }

QLabel#internetSettingsSectionTitle {
    font-size: 14px;
    font-weight: 700;
}

QLabel#internetSettingsNotice,
QLabel#wifiDialogNote,
QLabel#routerDisconnectedLabel,
QLabel#settingsDisconnectedLabel,
QLabel#emptyInternetState {
    color: #94a3b8;
}

QLabel#internetSettingsWarning,
QLabel#internetSettingsError {
    color: #fbbf24;
}

QPushButton,
QToolButton {
    min-height: 30px;
    padding: 0 12px;
    color: #dbeafe;
    background-color: #1b2638;
    border: 1px solid #334155;
    border-radius: 8px;
    font-weight: 600;
}

QPushButton:hover:enabled,
QToolButton:hover:enabled {
    color: #ffffff;
    background-color: #223149;
    border-color: #60a5fa;
}

QPushButton:pressed:enabled,
QToolButton:pressed:enabled,
QPushButton:checked,
QToolButton:checked {
    color: #ffffff;
    background-color: #1d4ed8;
    border-color: #60a5fa;
}

QPushButton:focus,
QToolButton:focus {
    border-color: #60a5fa;
}

QPushButton:disabled,
QToolButton:disabled {
    color: #64748b;
    background-color: #141d2a;
    border-color: #283548;
}

QPushButton#primaryButton,
QPushButton:default {
    color: #ffffff;
    background-color: #2563eb;
    border-color: #3b82f6;
}

QPushButton#primaryButton:hover:enabled,
QPushButton:default:hover:enabled {
    background-color: #3b82f6;
    border-color: #60a5fa;
}

QPushButton#dangerButton,
QPushButton#routerDeleteButton {
    color: #cbd5e1;
    background-color: transparent;
}

QPushButton#dangerButton:hover:enabled,
QPushButton#routerDeleteButton:hover:enabled {
    color: #fecdd3;
    background-color: rgba(251, 113, 133, 0.10);
    border-color: #fb7185;
}

QLineEdit,
QComboBox,
QSpinBox,
QAbstractSpinBox,
QTextEdit,
QPlainTextEdit {
    min-height: 30px;
    padding: 0 10px;
    color: #f1f5f9;
    background-color: #111a29;
    border: 1px solid #334155;
    border-radius: 8px;
}

QTextEdit,
QPlainTextEdit {
    padding: 8px 10px;
}

QLineEdit:hover:enabled,
QComboBox:hover:enabled,
QSpinBox:hover:enabled,
QAbstractSpinBox:hover:enabled,
QTextEdit:hover:enabled,
QPlainTextEdit:hover:enabled {
    border-color: #475569;
}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QAbstractSpinBox:focus,
QTextEdit:focus,
QPlainTextEdit:focus {
    background-color: #131e2f;
    border-color: #60a5fa;
}

QLineEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QAbstractSpinBox:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled {
    color: #64748b;
    background-color: #101827;
    border-color: #283548;
}

QComboBox::drop-down {
    width: 28px;
    border: none;
}

QComboBox QAbstractItemView {
    padding: 5px;
    color: #f1f5f9;
    background-color: #1b2638;
    border: 1px solid #475569;
    border-radius: 8px;
    outline: none;
    selection-background-color: #263b59;
    selection-color: #ffffff;
}

QCheckBox,
QRadioButton {
    spacing: 8px;
    color: #cbd5e1;
}

QCheckBox:hover:enabled,
QRadioButton:hover:enabled {
    color: #ffffff;
}

QGroupBox {
    margin-top: 12px;
    padding: 14px 12px 10px 12px;
    background-color: rgba(21, 30, 45, 0.68);
    border: 1px solid #334155;
    border-radius: 10px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #cbd5e1;
}

QTabWidget::pane {
    top: -1px;
    background-color: transparent;
    border: 1px solid #283548;
    border-radius: 10px;
}

QTabBar::tab {
    min-height: 30px;
    padding: 5px 12px;
    color: #94a3b8;
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
}

QTabBar::tab:hover {
    color: #dbeafe;
    background-color: rgba(96, 165, 250, 0.08);
}

QTabBar::tab:selected {
    color: #ffffff;
    background-color: rgba(96, 165, 250, 0.12);
    border-bottom-color: #60a5fa;
}

QTabBar::tab:disabled {
    color: #64748b;
}

QPushButton#journalCornerTab {
    min-height: 30px;
    padding: 5px 12px;
    color: #94a3b8;
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
}

QPushButton#journalCornerTab:hover {
    color: #dbeafe;
    background-color: rgba(96, 165, 250, 0.08);
}

QPushButton#journalCornerTab:checked {
    color: #ffffff;
    background-color: rgba(96, 165, 250, 0.12);
    border-bottom-color: #60a5fa;
}

QScrollArea,
QAbstractScrollArea,
QAbstractScrollArea > QWidget > QWidget {
    background-color: transparent;
    border: none;
}

QScrollBar:vertical {
    width: 10px;
    margin: 3px 2px;
    background: transparent;
}

QScrollBar:horizontal {
    height: 10px;
    margin: 2px 3px;
    background: transparent;
}

QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {
    min-height: 28px;
    min-width: 28px;
    background: #475569;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {
    background: #64748b;
}

QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {
    width: 0;
    height: 0;
    background: transparent;
    border: none;
}

QListWidget,
QListView,
QTreeWidget,
QTreeView,
QTableWidget,
QTableView {
    color: #e2e8f0;
    alternate-background-color: #131d2b;
    background-color: #101827;
    border: 1px solid #283548;
    border-radius: 9px;
    outline: none;
    gridline-color: #283548;
}

QListWidget::item,
QListView::item,
QTreeWidget::item,
QTreeView::item,
QTableWidget::item,
QTableView::item {
    padding: 5px 7px;
    border: none;
}

QListWidget::item:hover,
QListView::item:hover,
QTreeWidget::item:hover,
QTreeView::item:hover,
QTableWidget::item:hover,
QTableView::item:hover {
    background-color: #1b2a3f;
}

QListWidget::item:selected,
QListView::item:selected,
QTreeWidget::item:selected,
QTreeView::item:selected,
QTableWidget::item:selected,
QTableView::item:selected {
    color: #ffffff;
    background-color: #25364e;
}

QHeaderView::section {
    padding: 7px 9px;
    color: #cbd5e1;
    background-color: #1b2638;
    border: none;
    border-right: 1px solid #334155;
    border-bottom: 1px solid #334155;
    font-weight: 600;
}

QMenu {
    padding: 5px;
    color: #e2e8f0;
    background-color: #1b2638;
    border: 1px solid #475569;
    border-radius: 9px;
}

QMenu::item {
    min-width: 170px;
    padding: 7px 26px 7px 10px;
    border-radius: 6px;
}

QMenu::item:selected {
    color: #ffffff;
    background-color: #263b59;
}

QMenu::item:disabled {
    color: #64748b;
}

QMenu::separator {
    height: 1px;
    margin: 5px 7px;
    background-color: #334155;
}

QProgressBar {
    min-height: 7px;
    color: #cbd5e1;
    text-align: center;
    background-color: #283548;
    border: none;
    border-radius: 3px;
}

QProgressBar::chunk {
    background-color: #60a5fa;
    border-radius: 3px;
}

QSplitter::handle {
    background-color: #283548;
}

QSplitter::handle:horizontal {
    width: 1px;
    margin: 8px 3px;
}

QSplitter::handle:hover {
    background-color: #60a5fa;
}

QStatusBar {
    color: #94a3b8;
    background-color: #101827;
    border-top: 1px solid #283548;
}

QStatusBar::item {
    border: none;
}

QToolTip {
    padding: 6px 8px;
    color: #f8fafc;
    background-color: #1e293b;
    border: 1px solid #64748b;
    border-radius: 6px;
}

QDialogButtonBox {
    dialogbuttonbox-buttons-have-icons: 0;
}
"""


def build_application_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["canvas"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["surface_raised"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface_raised"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text_secondary"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLORS["danger"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent_strong"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLORS["accent"]))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(COLORS["purple"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(COLORS["border"]))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(COLORS["border_soft"]))
    palette.setColor(QPalette.ColorRole.Dark, QColor(COLORS["canvas"]))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#050914"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["disabled"]))

    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(COLORS["disabled"]))
    palette.setColor(disabled, QPalette.ColorRole.Text, QColor(COLORS["disabled"]))
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(COLORS["disabled"]))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor(COLORS["border"]))
    palette.setColor(disabled, QPalette.ColorRole.HighlightedText, QColor(COLORS["text_muted"]))
    return palette


def apply_application_theme(app: QApplication) -> None:
    """Apply the stable project theme independently of the Windows theme."""
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    app.setFont(QFont("Segoe UI", 10))
    app.setPalette(build_application_palette())
    app.setStyleSheet(APPLICATION_STYLESHEET)
