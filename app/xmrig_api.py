from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal

from app.xmrig_api_data import parse_summary


class ApiPoller(QThread):
    summary_received = pyqtSignal(object)
    api_status = pyqtSignal(bool, str)

    def __init__(self, port: int, interval_seconds: float = 2.0) -> None:
        super().__init__()
        self._port = int(port)
        self._interval = max(0.5, float(interval_seconds))
        self._stop_event = threading.Event()
        self._last_online: bool | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        url = f"http://127.0.0.1:{self._port}/2/summary"
        while not self._stop_event.is_set():
            online = False
            message = ""
            try:
                request = urllib.request.Request(
                    url,
                    headers={"Accept": "application/json", "User-Agent": "GPUVirtualWorkstation/1.0"},
                )
                with urllib.request.urlopen(request, timeout=0.8) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict):
                    self.summary_received.emit(parse_summary(data))
                    online = True
                    message = "API online"
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                message = str(exc)

            if self._last_online is None or online != self._last_online:
                self.api_status.emit(online, message)
                self._last_online = online

            self._stop_event.wait(self._interval)
