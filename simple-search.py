import os
import sys
import shutil
import zipfile

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QCheckBox, QTableWidget,
    QTableWidgetItem, QFileDialog, QPlainTextEdit, QProgressBar,
    QHeaderView, QAbstractItemView, QMessageBox, QMenu, QSplitter,
    QComboBox
)

import rarfile
import py7zr


STRINGS = {
    "ru": {
        "window_title": "Simple search",
        "path_label": "Папка/диск:",
        "path_placeholder": r"H:\  или  H:\Mods  или  .",
        "browse": "Обзор…",
        "word_label": "Искать слово:",
        "ext_label": "Расширение:",
        "case_cb": "Учитывать регистр",
        "start": "Начать поиск",
        "stop": "Стоп",
        "status_ready": "Готово",
        "status_searching": "Поиск…",
        "status_stopping": "Останавливаю…",
        "status_progress": "Проверено архивов: {scanned} · найдено: {found}",
        "status_done": "Готово. Проверено: {scanned}, найдено: {found}",
        "status_copied": "Скопировано в буфер обмена",
        "col_archive": "Архив",
        "col_file": "Файл",
        "col_line": "Строка",
        "col_path": "Полный путь архива",
        "menu_open_folder": "Открыть папку с архивом",
        "menu_open_archive": "Открыть архив",
        "menu_copy_path": "Копировать путь архива",
        "menu_copy_file": "Копировать имя файла",
        "menu_copy_line": "Копировать строку",
        "menu_copy_all": "Копировать всё (Ctrl+C)",
        "err_title": "Ошибка",
        "err_no_path": "Укажите папку или диск.",
        "err_path_not_found": "Папка не найдена:\n{path}",
        "err_no_word": "Введите слово для поиска.",
        "err_open_folder": "Не удалось открыть папку:\n{path}",
        "err_file_missing": "Файл не найден:\n{path}",
        "unrar_warn": "⚠ UnRAR.exe не найден — .rar архивы будут пропущены. "
                      "Установите WinRAR или добавьте UnRAR.exe в PATH.",
        "lang_label": "Язык:",
    },
    "en": {
        "window_title": "Simple search",
        "path_label": "Folder/drive:",
        "path_placeholder": r"H:\  or  H:\Mods  or  .",
        "browse": "Browse…",
        "word_label": "Search word:",
        "ext_label": "Extension:",
        "case_cb": "Case sensitive",
        "start": "Start search",
        "stop": "Stop",
        "status_ready": "Ready",
        "status_searching": "Searching…",
        "status_stopping": "Stopping…",
        "status_progress": "Archives scanned: {scanned} · found: {found}",
        "status_done": "Done. Scanned: {scanned}, found: {found}",
        "status_copied": "Copied to clipboard",
        "col_archive": "Archive",
        "col_file": "File",
        "col_line": "Line",
        "col_path": "Full archive path",
        "menu_open_folder": "Open containing folder",
        "menu_open_archive": "Open archive",
        "menu_copy_path": "Copy archive path",
        "menu_copy_file": "Copy file name",
        "menu_copy_line": "Copy line",
        "menu_copy_all": "Copy all (Ctrl+C)",
        "err_title": "Error",
        "err_no_path": "Please specify a folder or drive.",
        "err_path_not_found": "Folder not found:\n{path}",
        "err_no_word": "Enter a word to search.",
        "err_open_folder": "Could not open folder:\n{path}",
        "err_file_missing": "File not found:\n{path}",
        "unrar_warn": "⚠ UnRAR.exe not found — .rar archives will be skipped. "
                      "Install WinRAR or add UnRAR.exe to PATH.",
        "lang_label": "Language:",
    },
}


def tr(lang, key, **kw):
    s = STRINGS.get(lang, STRINGS["ru"]).get(key, key)
    return s.format(**kw) if kw else s


def setup_unrar():
    for name in ("unrar", "unrar.exe", "UnRAR.exe"):
        p = shutil.which(name)
        if p:
            rarfile.UNRAR_TOOL = p
            return p
    for p in (r"C:\Program Files\WinRAR\UnRAR.exe",
              r"C:\Program Files (x86)\WinRAR\UnRAR.exe"):
        if os.path.isfile(p):
            rarfile.UNRAR_TOOL = p
            return p
    return None


HAS_UNRAR = bool(setup_unrar())

SKIP_DIRS = {
    "$recycle.bin", "system volume information",
    "$windows.~bt", "$windows.~ws", "recovery",
}

ARCHIVE_EXT = (".zip", ".rar", ".7z")


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def find_line(text: str, word: str, case_sensitive: bool):
    hay = text if case_sensitive else text.lower()
    needle = word if case_sensitive else word.lower()
    if needle not in hay:
        return None
    for line in text.splitlines():
        cmp_line = line if case_sensitive else line.lower()
        if needle in cmp_line:
            return line.strip()[:300]
    return "(match)"


class SearchWorker(QThread):
    found = Signal(str, str, str)
    progress = Signal(int, int)
    log = Signal(str)
    finished_ok = Signal(int, int)
    error = Signal(str)

    def __init__(self, root, word, case_sensitive, target_ext=".ini"):
        super().__init__()
        self.root = root
        self.word = word
        self.case_sensitive = case_sensitive
        self.target_ext = target_ext
        self._stop = False

    def stop(self):
        self._stop = True

    def _scan_zip(self, path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if self._stop:
                    return None
                if name.endswith("/") or not name.lower().endswith(self.target_ext):
                    continue
                try:
                    data = z.read(name)
                except Exception as e:
                    self.log.emit(f"[!] {path} :: {name}: {e}")
                    continue
                line = find_line(decode_bytes(data), self.word, self.case_sensitive)
                if line:
                    return name, line
        return None

    def _scan_rar(self, path):
        if not HAS_UNRAR:
            return None
        with rarfile.RarFile(path) as r:
            for name in r.namelist():
                if self._stop:
                    return None
                if not name.lower().endswith(self.target_ext):
                    continue
                try:
                    data = r.read(name)
                except Exception as e:
                    self.log.emit(f"[!] {path} :: {name}: {e}")
                    continue
                line = find_line(decode_bytes(data), self.word, self.case_sensitive)
                if line:
                    return name, line
        return None

    def _scan_7z(self, path):
        with py7zr.SevenZipFile(path, mode="r") as z:
            names = [n for n in z.getnames()
                     if not n.endswith("/") and n.lower().endswith(self.target_ext)]
            if not names:
                return None
            data_map = z.read(targets=names) or {}
            for name, bio in data_map.items():
                if self._stop:
                    return None
                try:
                    data = bio.read()
                except Exception as e:
                    self.log.emit(f"[!] {path} :: {name}: {e}")
                    continue
                line = find_line(decode_bytes(data), self.word, self.case_sensitive)
                if line:
                    return name, line
        return None

    def run(self):
        scanned = 0
        found = 0
        try:
            for dirpath, dirnames, filenames in os.walk(self.root):
                if self._stop:
                    break
                dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
                for f in filenames:
                    if self._stop:
                        break
                    if not f.lower().endswith(ARCHIVE_EXT):
                        continue
                    full = os.path.join(dirpath, f)
                    ext = os.path.splitext(f)[1].lower()
                    scanned += 1
                    self.progress.emit(scanned, found)
                    try:
                        if ext == ".zip":
                            hit = self._scan_zip(full)
                        elif ext == ".rar":
                            hit = self._scan_rar(full)
                        elif ext == ".7z":
                            hit = self._scan_7z(full)
                        else:
                            hit = None
                    except (zipfile.BadZipFile, rarfile.BadRarFile,
                            py7zr.exceptions.ArchiveError):
                        continue
                    except Exception as e:
                        self.log.emit(f"[!] {full}: {e}")
                        continue
                    if hit:
                        name, line = hit
                        found += 1
                        self.found.emit(full, name, line)
                        self.progress.emit(scanned, found)
            self.finished_ok.emit(scanned, found)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("AnderSearch", "ArchiveSearchGUI")
        self.lang = self.settings.value("lang", "ru", type=str)
        if self.lang not in ("ru", "en"):
            self.lang = "ru"
        self.worker = None
        self._build_ui()
        self._apply_language()
        self._restore_settings()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        row1 = QHBoxLayout()
        self.lbl_path = QLabel()
        row1.addWidget(self.lbl_path)
        self.path_edit = QLineEdit()
        row1.addWidget(self.path_edit, 1)
        self.btn_browse = QPushButton()
        self.btn_browse.clicked.connect(self.browse)
        row1.addWidget(self.btn_browse)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_word = QLabel()
        row2.addWidget(self.lbl_word)
        self.word_edit = QLineEdit("Ander")
        row2.addWidget(self.word_edit, 1)

        self.lbl_ext = QLabel()
        row2.addWidget(self.lbl_ext)
        self.ext_edit = QLineEdit(".ini")
        self.ext_edit.setFixedWidth(70)
        row2.addWidget(self.ext_edit)

        self.case_cb = QCheckBox()
        row2.addWidget(self.case_cb)

        self.btn_start = QPushButton()
        self.btn_start.clicked.connect(self.start_search)
        row2.addWidget(self.btn_start)

        self.btn_stop = QPushButton()
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_search)
        row2.addWidget(self.btn_stop)

        self.lbl_lang = QLabel()
        row2.addWidget(self.lbl_lang)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Русский", "ru")
        self.lang_combo.addItem("English", "en")
        idx = self.lang_combo.findData(self.lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        row2.addWidget(self.lang_combo)

        v.addLayout(row2)

        row3 = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        row3.addWidget(self.progress, 1)
        self.status = QLabel()
        row3.addWidget(self.status)
        v.addLayout(row3)

        self.unrar_warn = QLabel()
        self.unrar_warn.setStyleSheet("color: #b00;")
        self.unrar_warn.setVisible(not HAS_UNRAR)
        v.addWidget(self.unrar_warn)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.itemDoubleClicked.connect(self.copy_row_all)

        hdr = self.table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        splitter.addWidget(self.table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        splitter.addWidget(self.log_view)
        splitter.setSizes([500, 150])
        v.addWidget(splitter, 1)

        copy_act = QAction(self)
        copy_act.setShortcut(QKeySequence.Copy)
        copy_act.triggered.connect(self.copy_selected)
        self.addAction(copy_act)

    def _apply_language(self):
        L = lambda k, **kw: tr(self.lang, k, **kw)
        ext = self.ext_edit.text().strip() or ".ini"
        self.setWindowTitle(L("window_title", ext=ext))
        self.lbl_path.setText(L("path_label"))
        self.path_edit.setPlaceholderText(L("path_placeholder"))
        self.btn_browse.setText(L("browse"))
        self.lbl_word.setText(L("word_label"))
        self.lbl_ext.setText(L("ext_label"))
        self.case_cb.setText(L("case_cb"))
        self.btn_start.setText(L("start"))
        self.btn_stop.setText(L("stop"))
        self.lbl_lang.setText(L("lang_label"))
        self.unrar_warn.setText(L("unrar_warn"))

        headers = [L("col_archive"), L("col_file"), L("col_line"), L("col_path")]
        hdr = self.table.horizontalHeader()
        order = [hdr.logicalIndex(i) for i in range(hdr.count())]
        self.table.setHorizontalHeaderLabels(headers)
        for visual, logical in enumerate(order):
            hdr.moveSection(hdr.visualIndex(logical), visual)

        if self.worker and self.worker.isRunning():
            self.status.setText(L("status_searching"))
        elif self.table.rowCount() == 0:
            self.status.setText(L("status_ready"))

    def _on_lang_changed(self):
        self.lang = self.lang_combo.currentData()
        self.settings.setValue("lang", self.lang)
        self._apply_language()

    def _restore_settings(self):
        self.path_edit.setText(self.settings.value("path", "", type=str))
        self.word_edit.setText(self.settings.value("word", "Ander", type=str))
        self.ext_edit.setText(self.settings.value("ext", ".ini", type=str))
        self.case_cb.setChecked(self.settings.value("case", False, type=bool))

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        st = self.settings.value("header_state")
        if st:
            self.table.horizontalHeader().restoreState(st)

    def _save_settings(self):
        self.settings.setValue("path", self.path_edit.text())
        self.settings.setValue("word", self.word_edit.text())
        self.settings.setValue("ext", self.ext_edit.text())
        self.settings.setValue("case", self.case_cb.isChecked())
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("header_state",
                               self.table.horizontalHeader().saveState())
        self.settings.setValue("lang", self.lang)

    def closeEvent(self, e):
        self._save_settings()
        super().closeEvent(e)

    def browse(self):
        start = self.path_edit.text().strip() or "H:\\"
        d = QFileDialog.getExistingDirectory(self, self.btn_browse.text(), start)
        if d:
            self.path_edit.setText(os.path.normpath(d))

    def start_search(self):
        L = lambda k, **kw: tr(self.lang, k, **kw)
        root = self.path_edit.text().strip().strip('"').strip("'")
        if not root:
            QMessageBox.warning(self, L("err_title"), L("err_no_path"))
            return
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            QMessageBox.warning(self, L("err_title"),
                                L("err_path_not_found", path=root))
            return
        word = self.word_edit.text().strip()
        if not word:
            QMessageBox.warning(self, L("err_title"), L("err_no_word"))
            return
        ext = self.ext_edit.text().strip().lower()
        if not ext.startswith("."):
            ext = "." + ext

        self.setWindowTitle(L("window_title", ext=ext))
        self.table.setRowCount(0)
        self.log_view.clear()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText(L("status_searching"))
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self.worker = SearchWorker(root, word, self.case_cb.isChecked(), ext)
        self.worker.found.connect(self.add_result)
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.log_view.appendPlainText)
        self.worker.error.connect(
            lambda m: QMessageBox.critical(self, L("err_title"), m))
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.start()

    def stop_search(self):
        if self.worker:
            self.worker.stop()
            self.status.setText(tr(self.lang, "status_stopping"))
            self.btn_stop.setEnabled(False)

    def add_result(self, archive, ini, line):
        r = self.table.rowCount()
        self.table.insertRow(r)
        archive_name = os.path.basename(archive)
        for col, text in enumerate((archive_name, ini, line, archive)):
            it = QTableWidgetItem(text)
            it.setToolTip(text)
            self.table.setItem(r, col, it)
        self.table.scrollToBottom()

    def on_progress(self, scanned, found):
        self.status.setText(tr(self.lang, "status_progress",
                               scanned=scanned, found=found))

    def on_finished(self, scanned, found):
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(tr(self.lang, "status_done",
                               scanned=scanned, found=found))
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _row_text(self, row):
        return [self.table.item(row, c).text() if self.table.item(row, c) else ""
                for c in range(self.table.columnCount())]

    def copy_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        lines = []
        for r in rows:
            archive_name, ini, line, full = self._row_text(r)
            lines.append(f"{full}\t{ini}\t{line}")
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.setText(tr(self.lang, "status_copied"))

    def copy_row_all(self, item):
        r = item.row()
        archive_name, ini, line, full = self._row_text(r)
        QGuiApplication.clipboard().setText(f"{full}\t{ini}\t{line}")
        self.status.setText(tr(self.lang, "status_copied"))

    def _open_containing_folder(self, archive_path: str):
        L = lambda k, **kw: tr(self.lang, k, **kw)
        if not os.path.exists(archive_path):
            QMessageBox.warning(self, L("err_title"),
                                L("err_file_missing", path=archive_path))
            return
        folder = os.path.dirname(archive_path)
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        if not ok:
            QMessageBox.warning(self, L("err_title"),
                                L("err_open_folder", path=folder))

    def _open_file(self, path: str):
        L = lambda k, **kw: tr(self.lang, k, **kw)
        if not os.path.exists(path):
            QMessageBox.warning(self, L("err_title"),
                                L("err_file_missing", path=path))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def table_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        archive_name, ini, line, full = self._row_text(row)
        L = lambda k, **kw: tr(self.lang, k, **kw)

        menu = QMenu(self)
        act_open_folder = menu.addAction(L("menu_open_folder"))
        act_open_archive = menu.addAction(L("menu_open_archive"))
        menu.addSeparator()
        act_archive = menu.addAction(L("menu_copy_path"))
        act_ini = menu.addAction(L("menu_copy_file"))
        act_line = menu.addAction(L("menu_copy_line"))
        menu.addSeparator()
        act_all = menu.addAction(L("menu_copy_all"))

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_open_folder:
            self._open_containing_folder(full)
        elif chosen == act_open_archive:
            self._open_file(full)
        elif chosen == act_archive:
            QGuiApplication.clipboard().setText(full)
            self.status.setText(L("status_copied"))
        elif chosen == act_ini:
            QGuiApplication.clipboard().setText(ini)
            self.status.setText(L("status_copied"))
        elif chosen == act_line:
            QGuiApplication.clipboard().setText(line)
            self.status.setText(L("status_copied"))
        elif chosen == act_all:
            QGuiApplication.clipboard().setText(f"{full}\t{ini}\t{line}")
            self.status.setText(L("status_copied"))


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()