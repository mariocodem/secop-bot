"""Bot de licitaciones SECOP II – punto de entrada."""

import sys

from PySide6.QtWidgets import QApplication

from secop_bot.gui.main_window import VentanaPrincipal


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bot SECOP II")
    app.setQuitOnLastWindowClosed(False)  # permite seguir en la bandeja
    ventana = VentanaPrincipal()
    ventana.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
