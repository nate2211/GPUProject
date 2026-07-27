APP_STYLESHEET = """
QWidget {
    background: #10151d;
    color: #e7edf5;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog {
    background: #10151d;
}
QFrame#Sidebar {
    background: #151c27;
    border-right: 1px solid #283346;
}
QLabel#Title {
    font-size: 18pt;
    font-weight: 600;
}
QLabel#SectionTitle {
    font-size: 14pt;
    font-weight: 600;
}
QLabel#MetricValue {
    font-size: 20pt;
    font-weight: 700;
}
QLabel#Muted {
    color: #98a7ba;
}
QListWidget {
    background: transparent;
    border: none;
    padding: 8px;
}
QListWidget::item {
    padding: 12px;
    border-radius: 7px;
    margin: 2px 0;
}
QListWidget::item:selected {
    background: #275d8f;
    color: white;
}
QPushButton {
    background: #243348;
    border: 1px solid #344a67;
    border-radius: 7px;
    padding: 8px 14px;
}
QPushButton:hover {
    background: #2d4160;
}
QPushButton#Primary {
    background: #2472b8;
    border-color: #3386cf;
    font-weight: 600;
}
QPushButton#Danger {
    background: #71353b;
    border-color: #9a4a52;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #0d1219;
    border: 1px solid #2a394d;
    border-radius: 6px;
    padding: 7px;
    selection-background-color: #2472b8;
}
QTableView {
    background: #0d1219;
    alternate-background-color: #131b25;
    border: 1px solid #283649;
    border-radius: 7px;
    gridline-color: #243143;
}
QHeaderView::section {
    background: #1b2635;
    color: #cbd6e5;
    border: none;
    border-right: 1px solid #2c3b4e;
    padding: 8px;
}
QGroupBox {
    border: 1px solid #2a394d;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}
QTabWidget::pane {
    border: 1px solid #2a394d;
}
QTabBar::tab {
    background: #182231;
    padding: 9px 14px;
}
QTabBar::tab:selected {
    background: #275d8f;
}
QProgressBar {
    border: 1px solid #2a394d;
    border-radius: 5px;
    text-align: center;
    background: #0d1219;
}
QProgressBar::chunk {
    background: #2472b8;
    border-radius: 4px;
}
"""
