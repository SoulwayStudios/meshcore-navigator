"""Modern Slate Dark Theme QSS Stylesheet and Color Tokens matching the Map palette."""

DARK_THEME_QSS = """
/* Global Window & Fonts */
QWidget {
    background-color: #1C1C1C;
    color: #E5E7EB;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    selection-background-color: #464C5A;
    selection-color: #FFFFFF;
}

QLabel {
    background: transparent;
    background-color: transparent;
}


/* Main Window & Dialogs */
QMainWindow, QDialog {
    background-color: #1C1C1C;
}

/* Scrollbars */
QScrollBar:vertical {
    background: #1C1C1C;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #2E3036;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #464C5A;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    height: 0px;
    width: 0px;
    background: transparent;
}

/* Sidebar & Navigation */
QListWidget {
    background-color: #222327;
    border: 1px solid #414143;
    border-radius: 8px;
    padding: 4px;
}
QListWidget QLabel {
    background: transparent;
    background-color: transparent;
}
QListWidget::item {
    height: 36px;
    padding: 6px 10px;
    border-radius: 6px;
    margin-bottom: 2px;
}
QListWidget::item:disabled {
    background: transparent;
}
QListWidget::item:hover {
    background-color: #2B2F38;
}
QListWidget::item:selected {
    background-color: #464C5A;
    color: #FFFFFF;
    font-weight: 600;
}

/* Chat & Viewports */
QScrollArea {
    background-color: #1C1C1C;
    border: 1px solid #414143;
    border-radius: 8px;
}
MessageBubble QLabel,
QFrame#messageBubble QLabel {
    background: transparent;
    background-color: transparent;
    border: none;
}

/* Buttons */
QPushButton {
    background-color: #2B2F38;
    color: #E5E7EB;
    border: 1px solid #414143;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #353A45;
    border-color: #60687A;
}
QPushButton:pressed {
    background-color: #222327;
}
DockButton, LayerButton, CycleFilterButton,
QWidget#navDock QPushButton {
    padding: 0px;
    text-align: center;
    font-size: 18px;
}
QPushButton#primaryButton {
    background-color: #238636;
    border-color: #2EA043;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background-color: #2EA043;
}
QPushButton#accentButton {
    background-color: #464C5A;
    border-color: #60687A;
    color: #FFFFFF;
}
QPushButton#accentButton:hover {
    background-color: #555C6D;
}
QPushButton#secondaryButton {
    background-color: #2B2F38;
    border-color: #414143;
    color: #E5E7EB;
}
QPushButton#secondaryButton:hover {
    background-color: #353A45;
    border-color: #60687A;
}

/* Text Inputs & Composer */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #222327;
    border: 1px solid #414143;
    border-radius: 6px;
    padding: 8px 12px;
    color: #E5E7EB;
}
QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #60687A;
    background-color: #2A2C32;
}

/* Composer Input Box */
QLineEdit#composerInput {
    background-color: #222327;
    border: 1px solid #414143;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    color: #FFFFFF;
}
QLineEdit#composerInput:focus {
    border-color: #60687A;
    background-color: #2A2C32;
}

/* Autocomplete Popup List */
QListView#autocompleteList {
    background-color: #222327;
    border: 1px solid #414143;
    border-radius: 8px;
    padding: 4px;
    color: #E5E7EB;
}
QListView#autocompleteList::item {
    padding: 8px 12px;
    border-radius: 4px;
}
QListView#autocompleteList::item:selected {
    background-color: #464C5A;
    color: #FFFFFF;
}

/* Tab Widget */
QTabWidget::pane {
    border: 1px solid #414143;
    border-radius: 8px;
    background-color: #222327;
    top: -1px;
}
QTabBar::tab {
    background-color: #1C1C1C;
    color: #9CA3AF;
    border: 1px solid #414143;
    border-bottom: none;
    padding: 8px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #222327;
    color: #E5E7EB;
    font-weight: 600;
    border-color: #4B5363;
}
QTabBar::tab:hover:!selected {
    background-color: #2A2C32;
    color: #D1D5DB;
}

/* Group Boxes */
QGroupBox {
    border: 1px solid #414143;
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 14px;
    font-weight: 600;
    color: #D1D5DB;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
}

/* Dropdowns (QComboBox) */
QComboBox {
    background-color: #2B2F38;
    border: 1px solid #414143;
    border-radius: 6px;
    padding: 6px 12px;
    color: #E5E7EB;
}
QComboBox:hover {
    border-color: #60687A;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #222327;
    border: 1px solid #414143;
    selection-background-color: #464C5A;
    selection-color: #FFFFFF;
    padding: 4px;
}

/* Checkboxes */
QCheckBox {
    spacing: 8px;
    color: #E5E7EB;
    background: transparent;
    background-color: transparent;
}
QRadioButton {
    spacing: 8px;
    color: #E5E7EB;
    background: transparent;
    background-color: transparent;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #4B5363;
    background-color: #222327;
}
QCheckBox::indicator:checked {
    background-color: #238636;
    border-color: #2EA043;
}

/* Status Badges */
QLabel#statusPill {
    background-color: #2B2F38;
    color: #9CA3AF;
    border: 1px solid #414143;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}

/* Splitters & Draggable Handles */
QSplitter::handle {
    background-color: #1C1C1C;
}
QSplitter::handle:horizontal {
    width: 6px;
    margin: 0px 1px;
    background-color: #414143;
    border-radius: 2px;
}
QSplitter::handle:horizontal:hover {
    background-color: #60687A;
}
QSplitter::handle:vertical {
    height: 6px;
    margin: 1px 0px;
    background-color: #414143;
    border-radius: 2px;
}
QSplitter::handle:vertical:hover {
    background-color: #60687A;
}
"""
