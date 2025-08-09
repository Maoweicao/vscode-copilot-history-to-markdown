#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_md_gui.py

PyQt5 图形界面: 调用 chat_json_to_md.py 与 aggregate_markdown.py 两个脚本。

功能:
 1. 输入根路径 (默认: C:/Users/<当前用户>/AppData/Roaming/Code/User/workspaceStorage)
 2. 生成: 会话 JSON(version=3) 批量转 Markdown
 3. 聚合: 目录下全部 Markdown 合并为汇总
 4. 组合: 先生成再聚合
 5. 显示执行日志

依赖: PyQt5 (pip install PyQt5)
"""
from __future__ import annotations
import os
import sys
import io
import traceback
from types import SimpleNamespace
from datetime import datetime, timedelta

try:
    from PyQt5.QtWidgets import (
            QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
            QTextEdit, QCheckBox, QSpinBox, QComboBox, QFileDialog, QMessageBox, QGroupBox, QFormLayout,
            QProgressBar, QDateEdit
        )
    from PyQt5.QtCore import QThread, pyqtSignal, QDate
except ImportError:
    print("[ERROR] 未安装 PyQt5, 请先执行: pip install PyQt5")
    sys.exit(1)

# 将当前脚本目录加入 sys.path 以便导入同目录脚本
CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    import chat_json_to_md
    import aggregate_markdown
except Exception as e:  # noqa
    print("[ERROR] 导入依赖脚本失败:", e)
    sys.exit(1)

DEFAULT_ROOT = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Code', 'User', 'workspaceStorage')

class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    done_signal = pyqtSignal(bool, str)

    def __init__(self, task_fn, *args, **kwargs):
        super().__init__()
        self.task_fn = task_fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.task_fn(*self.args, **self.kwargs)
            self.done_signal.emit(True, str(result) if result else '')
        except Exception:
            buf = io.StringIO()
            traceback.print_exc(file=buf)
            self.log_signal.emit(buf.getvalue())
            self.done_signal.emit(False, '执行出错')

class MainWindow(QWidget):
    # 进度信号: done, total, current_file
    progress_signal = pyqtSignal(int, int, str)
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Chat JSON → Markdown / 聚合')
        self.resize(960, 720)
        self._threads: list[WorkerThread] = []
        self._cancel_flag = None
        self._build_ui()
        self.progress_signal.connect(self._on_progress)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # 根路径
        path_box = QHBoxLayout()
        path_box.addWidget(QLabel('根路径:'))
        self.path_edit = QLineEdit(DEFAULT_ROOT)
        browse_btn = QPushButton('浏览…')
        browse_btn.clicked.connect(self._choose_root)
        path_box.addWidget(self.path_edit, 1)
        path_box.addWidget(browse_btn)
        layout.addLayout(path_box)

        # 输出目录 (可选)
        out_box = QHBoxLayout()
        out_box.addWidget(QLabel('输出目录(可选):'))
        self.output_dir_edit = QLineEdit('')
        out_browse_btn = QPushButton('选择…')
        out_browse_btn.clicked.connect(self._choose_output_dir)
        out_box.addWidget(self.output_dir_edit, 1)
        out_box.addWidget(out_browse_btn)
        layout.addLayout(out_box)

        # 生成参数
        gen_group = QGroupBox('JSON → Markdown 参数')
        gen_form = QFormLayout()
        self.embed_chk = QCheckBox('嵌入文件')
        self.embed_verbose_chk = QCheckBox('调试日志')
        self.parallel_chk = QCheckBox('并行')
        self.file_root_edit = QLineEdit('')
        self.image_max_spin = QSpinBox(); self.image_max_spin.setRange(10_000, 50_000_000); self.image_max_spin.setValue(2_000_000)
        self.text_max_spin = QSpinBox(); self.text_max_spin.setRange(1_000, 10_000_000); self.text_max_spin.setValue(200_000)
        self.assets_dir_edit = QLineEdit('assets')
        gen_form.addRow(self.embed_chk, self.embed_verbose_chk)
        gen_form.addRow(QLabel('模式:'), self.parallel_chk)
        gen_form.addRow(QLabel('搜索根目录:'), self.file_root_edit)
        gen_form.addRow(QLabel('图片最大字节:'), self.image_max_spin)
        gen_form.addRow(QLabel('文本最大字节:'), self.text_max_spin)
        gen_form.addRow(QLabel('资源目录名:'), self.assets_dir_edit)
        gen_group.setLayout(gen_form)
        layout.addWidget(gen_group)

        # 聚合参数
        agg_group = QGroupBox('聚合参数')
        agg_form = QFormLayout()
        self.output_md_edit = QLineEdit('AGGREGATED.md')
        self.sort_mode_combo = QComboBox(); self.sort_mode_combo.addItems(['name', 'mtime'])
        self.include_edit = QLineEdit('')
        self.exclude_edit = QLineEdit('')
        self.preset_combo = QComboBox(); self.preset_combo.addItems(['(无)', '仅会话:.*', '排除聚合:^AGGREGATED\\.md$', '排除assets:(^|/)assets/'])
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        agg_form.addRow(QLabel('输出文件名:'), self.output_md_edit)
        agg_form.addRow(QLabel('排序:'), self.sort_mode_combo)
        agg_form.addRow(QLabel('纳入文件:'), self.include_edit)
        agg_form.addRow(QLabel('排除文件:'), self.exclude_edit)
        agg_form.addRow(QLabel('预设:'), self.preset_combo)
        agg_group.setLayout(agg_form)
        layout.addWidget(agg_group)

        # 时间选择器
        date_group = QGroupBox('文件创建日期范围')
        date_form = QFormLayout()
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-1))  # 默认一个月前
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())  # 默认今天
        date_form.addRow(QLabel('开始日期:'), self.start_date_edit)
        date_form.addRow(QLabel('结束日期:'), self.end_date_edit)
        date_group.setLayout(date_form)
        layout.addWidget(date_group)

        # 按钮
        btn_box = QHBoxLayout()
        self.btn_generate = QPushButton('生成Markdown')
        self.btn_aggregate = QPushButton('聚合Markdown')
        self.btn_both = QPushButton('生成并聚合')
        self.btn_generate.clicked.connect(self._run_generate)
        self.btn_aggregate.clicked.connect(self._run_aggregate)
        self.btn_both.clicked.connect(self._run_both)
        btn_box.addWidget(self.btn_generate)
        btn_box.addWidget(self.btn_aggregate)
        btn_box.addWidget(self.btn_both)
        self.btn_cancel = QPushButton('取消')
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_tasks)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)

        # 进度
        prog_box = QHBoxLayout()
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100)
        self.progress_label = QLabel('0/0')
        prog_box.addWidget(QLabel('进度:'))
        prog_box.addWidget(self.progress_bar, 1)
        prog_box.addWidget(self.progress_label)
        layout.addLayout(prog_box)

        # 日志
        self.log_edit = QTextEdit(); self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, 1)
        self.setLayout(layout)

    # ---------- Helpers ----------
    def _append_log(self, text: str):
        if not text.endswith('\n'):
            text += '\n'
        self.log_edit.moveCursor(self.log_edit.textCursor().End)
        self.log_edit.insertPlainText(text)
        self.log_edit.moveCursor(self.log_edit.textCursor().End)

    def _choose_root(self):
        from PyQt5.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, '选择根目录', self.path_edit.text() or DEFAULT_ROOT)
        if d:
            self.path_edit.setText(d)
            if not self.file_root_edit.text().strip():
                self.file_root_edit.setText(d)

    def _choose_output_dir(self):
        from PyQt5.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, '选择输出目录', self.output_dir_edit.text() or self.path_edit.text() or DEFAULT_ROOT)
        if d:
            self.output_dir_edit.setText(d)

    def _make_embed_ctx(self):
        if not self.embed_chk.isChecked():
            return None
        args = SimpleNamespace(
            file_root=self.file_root_edit.text().strip() or self.path_edit.text().strip(),
            image_max_bytes=self.image_max_spin.value(),
            text_max_bytes=self.text_max_spin.value(),
            assets_dir_name=self.assets_dir_edit.text().strip() or 'assets',
            embed_verbose=self.embed_verbose_chk.isChecked(),
        )
        return chat_json_to_md.build_embed_context(args)

    def _apply_preset(self):
        text = self.preset_combo.currentText()
        if text.startswith('仅会话'):
            self.include_edit.setText(r'.*')
        elif text.startswith('排除聚合'):
            self.exclude_edit.setText(r'^AGGREGATED\.md$')
        elif text.startswith('排除assets'):
            self.exclude_edit.setText(r'(^|/)assets/')
        else:
            # 清空
            pass

    def _capture_stdout(self, func, *a, **kw):
        buf = io.StringIO()
        old = sys.stdout
        try:
            sys.stdout = buf
            return func(*a, **kw), buf.getvalue()
        finally:
            sys.stdout = old

    def _toggle_buttons(self, enable: bool):
        for b in (self.btn_generate, self.btn_aggregate, self.btn_both):
            b.setEnabled(enable)

    # ---------- Tasks ----------
    def _task_generate(self, root_path: str, embed_ctx):
        cancel_flag = self._cancel_flag
        def progress_cb(done, total, current):
            # 发射信号到主线程
            self.progress_signal.emit(done, total, current)
        output_dir = self.output_dir_edit.text().strip() or None
        start_date = self.start_date_edit.date().toPyDate()
        end_date = self.end_date_edit.date().toPyDate()
        
        def date_filter(file_path):
            file_creation_date = datetime.fromtimestamp(os.path.getctime(file_path)).date()
            return start_date <= file_creation_date <= end_date

        return chat_json_to_md.convert_path(
            root_path,
            embed_ctx=embed_ctx,
            progress_cb=progress_cb,
            cancel_flag=cancel_flag,
            parallel=self.parallel_chk.isChecked(),
            output_dir=output_dir,
            file_filter=date_filter  # 添加文件过滤器
        )

    def _task_aggregate(self, root_path: str, output_file: str, sort_mode: str, inc: str|None, exc: str|None):
        return aggregate_markdown.aggregate(root_path, output_file, sort_mode, inc or None, exc or None)

    def _run_generate(self):
        root_path = self.path_edit.text().strip()
        if not os.path.exists(root_path):
            QMessageBox.warning(self, '错误', f'根路径不存在: {root_path}')
            return
        self._reset_progress()
        embed_ctx = self._make_embed_ctx()
        self._append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 生成 Markdown 开始")
        self._start_thread(self._task_generate, root_path, embed_ctx)

    def _run_aggregate(self):
        root_path = self.path_edit.text().strip()
        if not os.path.exists(root_path):
            QMessageBox.warning(self, '错误', f'根路径不存在: {root_path}')
            return
        # 若设置输出目录则聚合该目录下的 md; 否则聚合根路径
        search_dir = self.output_dir_edit.text().strip() or root_path
        if not os.path.exists(search_dir):
            QMessageBox.warning(self, '错误', f'目录不存在: {search_dir}')
            return
        os.makedirs(search_dir, exist_ok=True)
        output_file = os.path.join(search_dir, self.output_md_edit.text().strip() or 'AGGREGATED.md')
        sort_mode = self.sort_mode_combo.currentText()
        inc = self.include_edit.text().strip() or None
        exc = self.exclude_edit.text().strip() or None
        self._append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 聚合 Markdown ({search_dir}) -> {output_file}")
        self._start_thread(self._task_aggregate, search_dir, output_file, sort_mode, inc, exc)

    def _run_both(self):
        root_path = self.path_edit.text().strip()
        if not os.path.exists(root_path):
            QMessageBox.warning(self, '错误', f'根路径不存在: {root_path}')
            return
        self._reset_progress()
        embed_ctx = self._make_embed_ctx()
        base_dir = self.output_dir_edit.text().strip() or root_path
        os.makedirs(base_dir, exist_ok=True)
        output_file = os.path.join(base_dir, self.output_md_edit.text().strip() or 'AGGREGATED.md')
        sort_mode = self.sort_mode_combo.currentText()
        inc = self.include_edit.text().strip() or None
        exc = self.exclude_edit.text().strip() or None

        def seq_task():
            cancel_flag = self._cancel_flag
            def progress_cb(done, total, current):
                self.progress_signal.emit(done, total, current)
            chat_json_to_md.convert_path(
                root_path,
                embed_ctx=embed_ctx,
                progress_cb=progress_cb,
                cancel_flag=cancel_flag,
                parallel=self.parallel_chk.isChecked(),
                output_dir=base_dir
            )
            if cancel_flag and cancel_flag.is_set():
                return '已取消'
            aggregate_markdown.aggregate(base_dir, output_file, sort_mode, inc, exc)
            return output_file

        self._append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 生成并聚合 开始")
        self._start_thread(seq_task)

    def _start_thread(self, fn, *a):
        import threading
        self._toggle_buttons(False)
        self.btn_cancel.setEnabled(True)
        self._cancel_flag = threading.Event()
        th = WorkerThread(fn, *a)
        th.log_signal.connect(self._append_log)
        th.done_signal.connect(self._on_done)
        th.start()
        self._threads.append(th)

    def _cancel_tasks(self):
        if self._cancel_flag:
            self._cancel_flag.set()
            self._append_log('请求取消...')
            self.btn_cancel.setEnabled(False)

    def _reset_progress(self):
        self.progress_bar.setValue(0)
        self.progress_label.setText('0/0')

    def _on_done(self, ok: bool, result: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"[{ts}] {'完成' if ok else '失败'} {result}")
        self._toggle_buttons(True)
        self.btn_cancel.setEnabled(False)
        self._cancel_flag = None

    def _on_progress(self, done: int, total: int, current: str):
        pct = int(done / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_label.setText(f"{done}/{total}")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
