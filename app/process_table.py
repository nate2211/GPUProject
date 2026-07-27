from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from app.models import InstanceSnapshot


class ProcessTableModel(QAbstractTableModel):
    HEADERS = [
        "vPID",
        "Host PID",
        "Instance",
        "State",
        "Backend",
        "Backend health",
        "Pseudo lanes",
        "GPU device",
        "Affinity",
        "Priority",
        "CPU",
        "RAM",
        "10s Hashrate",
        "Accepted",
        "Rejected",
        "CPU guard",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[InstanceSnapshot] = []

    def set_rows(self, rows: list[InstanceSnapshot]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None

        row = self._rows[index.row()]
        values = [
            row.virtual_pid,
            row.host_pid or "—",
            row.name,
            row.state,
            row.backend,
            row.backend_health,
            row.pseudo_lanes or "—",
            row.gpu_devices or "—",
            row.affinity,
            row.priority,
            f"{row.cpu_percent:.1f}%",
            f"{row.memory_mib:,.1f} MiB",
            f"{row.hashrate_10s:,.1f} H/s",
            row.accepted,
            row.rejected,
            row.guard_status,
        ]

        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {0, 1, 6, 10, 11, 12, 13, 14}:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.instance_dir
        return None

    def snapshot_at(self, row: int) -> InstanceSnapshot | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
