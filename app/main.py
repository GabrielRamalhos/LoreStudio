import sys
from app.services.product_info import app_version
from app.ui.window import Window
from PySide6.QtWidgets import QApplication
from app.ui.theme import STYLESHEET

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLESHEET)
    window = Window()
    window.setWindowTitle(f'Lore Oculta Studio {app_version()}')
    window.show()
    return app.exec()

if __name__ == '__main__':
    raise SystemExit(main())
