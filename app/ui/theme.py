"""Identidade visual compartilhada da aplicação desktop."""

STYLESHEET = """
QWidget { background: #10121a; color: #e9eaf2; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QTabWidget::pane { background: #10121a; border: 0; }
QTabBar::tab { background: #191c28; color: #a6acc1; padding: 14px 22px; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 3px solid #a894ff; background: #242439; }
QTabBar::tab:hover { background: #242439; }
QLabel#eyebrow { color: #a894ff; font-size: 12px; font-weight: 700; }
QLabel#heading { color: #ffffff; font-size: 30px; font-weight: 700; }
QLabel#subtitle { color: #a6acc1; font-size: 14px; }
QLabel#card { background: #1b1e2d; border: 1px solid #30364b; border-radius: 12px; padding: 20px; font-size: 16px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QListWidget {
 background: #191d2a; border: 1px solid #343b51; border-radius: 7px; padding: 9px; selection-background-color: #55458c;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #a894ff; }
QListWidget::item { padding: 12px; border-bottom: 1px solid #272d40; }
QListWidget::item:selected { background: #34304f; color: #ffffff; border-radius: 6px; }
QPushButton { background: #262d40; border: 1px solid #424a64; border-radius: 7px; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #363e58; border-color: #a894ff; }
QPushButton#primary { background: #a894ff; color: #181329; border: 0; }
QPushButton#primary:hover { background: #c0b2ff; }
QPushButton:disabled { background: #1c202b; color: #737b91; border-color: #292f3e; }
QStatusBar { background: #191c28; border-top: 1px solid #30364b; padding: 5px; }
QToolTip { background: #282e40; color: #ffffff; border: 1px solid #a894ff; }
"""
