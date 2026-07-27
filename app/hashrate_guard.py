from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app.isolation import read_xmrig_hashrate


class HashrateGuard(QThread):
    sample_received = pyqtSignal(float, float, int)
    drop_detected = pyqtSignal(str)
    guard_error = pyqtSignal(str)

    def __init__(
        self,
        api_url: str,
        baseline_hs: float,
        max_drop_percent: float,
        consecutive_samples: int,
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__()
        self.api_url = api_url
        self.baseline_hs = float(baseline_hs)
        self.max_drop_percent = max(0.1, float(max_drop_percent))
        self.consecutive_samples = max(1, int(consecutive_samples))
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._stop_event = threading.Event()

    @property
    def threshold_hs(self) -> float:
        return self.baseline_hs * (1.0 - self.max_drop_percent / 100.0)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        below_count = 0
        while not self._stop_event.is_set():
            try:
                sample = read_xmrig_hashrate(self.api_url, timeout=1.2)
                current = sample.guard_hashrate
                if current <= 0:
                    self.guard_error.emit("Protected XMRig API returned no usable hashrate yet.")
                else:
                    if current < self.threshold_hs:
                        below_count += 1
                    else:
                        below_count = 0
                    self.sample_received.emit(current, self.threshold_hs, below_count)
                    if below_count >= self.consecutive_samples:
                        self.drop_detected.emit(
                            f"Protected CPU XMRig fell to {current:,.1f} H/s; "
                            f"threshold is {self.threshold_hs:,.1f} H/s."
                        )
                        return
            except Exception as exc:
                self.guard_error.emit(str(exc))

            self._stop_event.wait(self.interval_seconds)
