"""Modern Dark Theme QSS Stylesheet and Color Tokens for MeshCore Pixoo Tray."""

DARK_THEME_QSS = """
/* Global Window & Fonts */
QWidget {
    background-color: #12151B;
    color: #E2E8F0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    selection-background-color: #00E5FF;
    selection-color: #090D16;
}

/* Main Window & Dialogs */
QMainWindow, QDialog {
    background-color: #0D1117;
}

/* Scrollbars */
QScrollBar:vertical {
    background: #12151B;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #2D3748;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #4A5568;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* Sidebar & Navigation */
QListWidget {
    background-color: #161B22;
    border: 1px solid #21262D;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item {
    height: 36px;
    padding: 6px 10px;
    border-radius: 6px;
    margin-bottom: 2px;
}
QListWidget::item:hover {
    background-color: #21262D;
}
QListWidget::item:selected {
    background-color: #1F6FEB;
    color: #FFFFFF;
    font-weight: 600;
}

/* Chat & Viewports */
QScrollArea {
    background-color: #0D1117;
    border: 1px solid #21262D;
    border-radius: 8px;
}

/* Buttons */
QPushButton {
    background-color: #21262D;
    color: #F0F6FC;
    border: 1px solid #30363D;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #30363D;
    border-color: #8B949E;
}
QPushButton:pressed {
    background-color: #161B22;
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
    background-color: #1F6FEB;
    border-color: #388BFD;
    color: #FFFFFF;
}
QPushButton#accentButton:hover {
    background-color: #388BFD;
}

/* Text Inputs & Composer */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #0D1117;
    border: 1px solid #30363D;
    border-radius: 6px;
    padding: 8px 12px;
    color: #F0F6FC;
}
QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #58A6FF;
}

/* Composer Input Box */
QLineEdit#composerInput {
    background-color: #161B22;
    border: 2px solid #30363D;
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    color: #FFFFFF;
}
QLineEdit#composerInput:focus {
    border-color: #00E5FF;
    background-color: #1C2128;
}

/* Autocomplete Popup List */
QListView#autocompleteList {
    background-color: #1C2128;
    border: 1px solid #484F58;
    border-radius: 8px;
    padding: 4px;
    color: #E6EDF3;
}
QListView#autocompleteList::item {
    padding: 8px 12px;
    border-radius: 4px;
}
QListView#autocompleteList::item:selected {
    background-color: #1F6FEB;
    color: #FFFFFF;
}

/* Tab Widget */
QTabWidget::pane {
    border: 1px solid #21262D;
    border-radius: 8px;
    background-color: #161B22;
    top: -1px;
}
QTabBar::tab {
    background-color: #0D1117;
    color: #8B949E;
    border: 1px solid #21262D;
    border-bottom: none;
    padding: 8px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #161B22;
    color: #58A6FF;
    font-weight: 600;
    border-color: #30363D;
}
QTabBar::tab:hover:!selected {
    background-color: #1C2128;
    color: #C9D1D9;
}

/* Group Boxes */
QGroupBox {
    border: 1px solid #30363D;
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 14px;
    font-weight: 600;
    color: #58A6FF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
}

/* Dropdowns (QComboBox) */
QComboBox {
    background-color: #21262D;
    border: 1px solid #30363D;
    border-radius: 6px;
    padding: 6px 12px;
    color: #F0F6FC;
}
QComboBox:hover {
    border-color: #58A6FF;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #161B22;
    border: 1px solid #30363D;
    selection-background-color: #1F6FEB;
    selection-color: #FFFFFF;
    padding: 4px;
}

/* Checkboxes */
QCheckBox {
    spacing: 8px;
    color: #E6EDF3;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #484F58;
    background-color: #161B22;
}
QCheckBox::indicator:checked {
    background-color: #238636;
    border-color: #2EA043;
}

/* Status Badges */
QLabel#statusPill {
    background-color: #21262D;
    color: #58A6FF;
    border: 1px solid #30363D;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
"""
