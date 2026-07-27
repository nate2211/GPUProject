from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.isolation import apply_process_isolation, parse_cpu_list
from app.main_window import MainWindow
from app.settings import WorkstationSettings
from app.theme import APP_STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GPU Virtual Workstation")
    app.setOrganizationName("GPUVirtualWorkstation")
    app.setStyleSheet(APP_STYLESHEET)

    try:
        settings = WorkstationSettings.load(Path("config/workstation.json"))
        startup_isolation = ""
        try:
            startup_cores = parse_cpu_list(settings.default_affinity)
            apply_process_isolation(
                os.getpid(),
                startup_cores if settings.default_pin_workstation else [],
                settings.default_priority,
                very_low_io=False,
                eco_qos=settings.default_eco_qos,
            )
        except Exception as exc:
            startup_isolation = f"[startup isolation] {exc}"

        window = MainWindow(settings)
        if startup_isolation:
            window.console.appendPlainText(startup_isolation)
        window.resize(1320, 820)
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Startup error",
            f"GPU Virtual Workstation could not start:\n\n{exc}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
