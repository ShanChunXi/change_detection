# -*- coding: utf-8 -*-
"""
城市变化检测与地图更新工具 - PyQt5 版（旧版）
⚠ 此文件已被 change_detection_ui.py 取代，保留仅作参考。
新版 change_detection_ui.py 为 PyQt5 界面，包含全部四种检测模式 +
地物分类 + 专题图 + 批量管线功能。

启动方式: python change_detection.py ui
"""

import sys
import os
import json
import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QFrame, QCheckBox,
    QScrollArea, QSizePolicy, QStackedWidget, QSplitter, QDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QEvent
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon, QResizeEvent

# 导入现有模块
from change_detection import (
    run_self_check, run_single_inference, run_enhanced_inference,
    run_batch_inference, run_folder_batch, AVAILABLE_MODELS,
    load_config, save_config, _remember_last_params
)
from classify_vectorize import run_classify, run_vectorize
from mapper import generate_thematic_map

# 批量处理模块 — 支持 PyInstaller 打包和开发环境两种场景
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后资源在 sys._MEIPASS 下
    _BATCH_DIR = os.path.join(sys._MEIPASS, "batch", "01_源代码")
else:
    # 开发环境: 变化检测/src/ → 变化检测/ → 项目根目录/ → batch/01_源代码/
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _BATCH_DIR = os.path.join(_PROJECT_ROOT, "batch", "01_源代码")
if _BATCH_DIR not in sys.path:
    sys.path.insert(0, _BATCH_DIR)
from li_batch_api import run_full_pipeline


# ============================================================
# 后台工作线程
# ============================================================

class WorkerThread(QThread):
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, target, args=(), kwargs=None):
        super().__init__()
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.result = None

    def run(self):
        try:
            self.result = self.target(*self.args, **self.kwargs)
            self.finished_signal.emit(True if self.result else False)
        except Exception as e:
            self.log_signal.emit(f"[错误] {e}", "error")
            self.finished_signal.emit(False)


class BatchPipelineThread(QThread):
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, task_csv, config_path, output_dir, report_title):
        super().__init__()
        self.task_csv = task_csv
        self.config_path = config_path
        self.output_dir = output_dir
        self.report_title = report_title

    def run(self):
        try:
            result = run_full_pipeline(
                task_list_path=self.task_csv,
                config_path=self.config_path,
                output_dir=self.output_dir,
                report_title=self.report_title,
                real_mode=True,
            )
            ok = result.get("success", False)
            self.log_signal.emit(f"批量处理完成", "info")
            self.finished_signal.emit(ok)
        except Exception as e:
            self.log_signal.emit(f"[错误] {e}", "error")
            self.finished_signal.emit(False)


# ============================================================
# 环境配置对话框（PyQt5 版）
# ============================================================

class SetupDialog(QDialog):
    """环境配置对话框 - PyQt5 版，支持自动检测"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("环境配置")
        self.setModal(True)
        self.setMinimumWidth(850)
        self.setMinimumHeight(600)

        self.entries = {}
        self.status_labels = {}

        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(18)

        # 标题
        title = QLabel("⚙ 环境配置")
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #0d1117;")
        layout.addWidget(title)

        desc = QLabel("配置 SuperMap iObjects Python 运行环境路径，配置后会自动保存到 config.json。")
        desc.setStyleSheet("color: #3d4a5a; font-size: 16px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #d0d7de; max-height: 1px;")
        layout.addWidget(line)

        # 表单
        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setSpacing(16)

        fields = [
            ("python_path", "SuperMap Python 解释器路径",
             "D:/supermap安装包/python/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe", "文件"),
            ("java_home", "iObjects Java JRE 路径",
             "D:/supermap安装包/java/supermap-iobjectsjava-2026-win-all/jre1.8_x64", "目录"),
            ("iobjects_bin", "iObjects Java Bin 路径", "D:/supermap安装包/java/supermap-iobjectsjava-2026-win-all/Bin",
             "目录"),
            ("resources_ml", "ML 资源包路径",
             "D:/supermap安装包/resources/supermap-iobjectspy-resources_ml-2025u1/resources_ml", "目录"),
        ]

        self.field_configs = fields

        for key, label, placeholder, path_type in fields:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)

            # 标签 - 大号加粗
            lbl = QLabel(label)
            lbl.setMinimumWidth(220)
            lbl.setStyleSheet("color: #0d1117; font-size: 17px; font-weight: bold;")
            row_layout.addWidget(lbl)

            # 输入框 - 大号
            entry = QLineEdit()
            entry.setPlaceholderText(placeholder)
            entry.setObjectName("SetupEntry")
            entry.setStyleSheet("""
                QLineEdit#SetupEntry {
                    background-color: white;
                    border: 2px solid #d0d7de;
                    border-radius: 8px;
                    padding: 12px 16px;
                    color: #0d1117;
                    font-size: 16px;
                }
                QLineEdit#SetupEntry:focus {
                    border-color: #4a7cf7;
                }
            """)
            row_layout.addWidget(entry, 1)
            self.entries[key] = entry

            # 浏览按钮 - 大号
            btn = QPushButton("浏览")
            btn.setFixedWidth(90)
            btn.setObjectName("SetupBrowseBtn")
            btn.setStyleSheet("""
                QPushButton#SetupBrowseBtn {
                    background-color: #e8ecf2;
                    color: #0d1117;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 18px;
                    font-size: 15px;
                    font-weight: bold;
                }
                QPushButton#SetupBrowseBtn:hover {
                    background-color: #d5dce6;
                }
            """)
            if path_type == "文件":
                btn.clicked.connect(lambda checked, k=key: self._browse_file(k))
            else:
                btn.clicked.connect(lambda checked, k=key: self._browse_dir(k))
            row_layout.addWidget(btn)

            form_layout.addWidget(row)

            # 状态标签 - 大号
            status_lbl = QLabel("")
            status_lbl.setStyleSheet("color: #5a6a7a; font-size: 14px; padding-left: 230px;")
            form_layout.addWidget(status_lbl)
            self.status_labels[key] = status_lbl

            # 实时校验
            entry.textChanged.connect(lambda text, k=key: self._validate_path(k))

        layout.addWidget(form)

        # 自动检测按钮行 - 大号
        auto_row = QHBoxLayout()
        auto_hint = QLabel("💡 点击「自动检测」可扫描常见 SuperMap 安装路径")
        auto_hint.setStyleSheet("color: #3d4a5a; font-size: 15px;")
        auto_row.addWidget(auto_hint)
        auto_row.addStretch()
        auto_btn = QPushButton("🔍 自动检测")
        auto_btn.setObjectName("SetupAutoBtn")
        auto_btn.setStyleSheet("""
            QPushButton#SetupAutoBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 32px;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton#SetupAutoBtn:hover {
                background-color: #5a8cf7;
            }
        """)
        auto_btn.clicked.connect(self._auto_detect)
        auto_row.addWidget(auto_btn)
        layout.addLayout(auto_row)

        # 底部按钮 - 大号
        footer = QHBoxLayout()
        footer.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SetupCancelBtn")
        cancel_btn.setStyleSheet("""
            QPushButton#SetupCancelBtn {
                background-color: #e8ecf2;
                color: #0d1117;
                border: none;
                border-radius: 8px;
                padding: 14px 36px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton#SetupCancelBtn:hover {
                background-color: #d5dce6;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        save_btn = QPushButton("💾 保存配置")
        save_btn.setObjectName("SetupSaveBtn")
        save_btn.setStyleSheet("""
            QPushButton#SetupSaveBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 36px;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton#SetupSaveBtn:hover {
                background-color: #28b86a;
            }
        """)
        save_btn.clicked.connect(self._save)
        footer.addWidget(save_btn)

        layout.addLayout(footer)

    def _load_config(self):
        """加载已有配置到输入框"""
        try:
            cfg = load_config()
            for key, entry in self.entries.items():
                if key in cfg and cfg[key]:
                    entry.setText(cfg[key])
        except Exception:
            pass

    def _browse_file(self, key):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            "可执行文件 (*.exe);;所有文件 (*.*)"
        )
        if path:
            self.entries[key].setText(path.replace("\\", "/"))

    def _browse_dir(self, key):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            self.entries[key].setText(path.replace("\\", "/"))

    def _validate_path(self, key):
        """实时校验路径是否存在"""
        path = self.entries[key].text().strip()
        lbl = self.status_labels[key]
        if not path:
            lbl.setText("⚠ 尚未填写")
            lbl.setStyleSheet("color: #e8a838; font-size: 14px; padding-left: 230px;")
        elif os.path.exists(path):
            lbl.setText("✅ 路径存在")
            lbl.setStyleSheet("color: #22a65e; font-size: 14px; padding-left: 230px;")
        else:
            lbl.setText("❌ 路径不存在")
            lbl.setStyleSheet("color: #e8544a; font-size: 14px; padding-left: 230px;")

    def _auto_detect(self):
        """自动扫描常见 SuperMap 安装路径"""
        drives = ["D:/", "F:/", "E:/", "C:/"]
        patterns = {
            "python_path": [
                "/supermap安装包/python/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe",
                "/supermap安装包/python/supermap-iobjectspy-env-2026-win64/conda/python.exe",
                "/SuperMap/iObjectspy/env/conda/python.exe",
                "/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe",
            ],
            "java_home": [
                "/supermap安装包/java/supermap-iobjectsjava-2026-win-all/jre1.8_x64",
                "/supermap安装包/java/supermap-iobjectsjava-2025-win-all/jre1.8_x64",
                "/SuperMap/iObjectsJava/jre1.8_x64",
            ],
            "iobjects_bin": [
                "/supermap安装包/java/supermap-iobjectsjava-2026-win-all/Bin",
                "/supermap安装包/java/supermap-iobjectsjava-2025-win-all/Bin",
                "/SuperMap/iObjectsJava/Bin",
            ],
            "resources_ml": [
                "/supermap安装包/resources/supermap-iobjectspy-resources_ml-2025u1/resources_ml",
                "/supermap安装包/resources/supermap-iobjectspy-resources_ml-2026/resources_ml",
                "/supermap-iobjectspy-resources_ml-2025u1/resources_ml",
                "/SuperMap/resources_ml",
            ],
        }

        found_count = 0
        for key, subpaths in patterns.items():
            found = False
            for drive in drives:
                for sub in subpaths:
                    full = os.path.join(drive, sub.replace("/", os.sep))
                    if os.path.exists(full):
                        self.entries[key].setText(full.replace("\\", "/"))
                        found_count += 1
                        found = True
                        break
                if found:
                    break

        if found_count > 0:
            QMessageBox.information(self, "自动检测", f"✅ 已找到 {found_count} 个路径，请核对后保存。")
        else:
            QMessageBox.warning(self, "自动检测", "❌ 未找到 SuperMap 标准安装路径，请手动填写。")

    def _save(self):
        """保存配置"""
        cfg = {}
        for key, entry in self.entries.items():
            cfg[key] = entry.text().strip()

        # 验证
        missing = [k for k, v in cfg.items() if not v]
        if missing:
            QMessageBox.warning(self, "配置不完整", f"以下路径尚未填写：\n{', '.join(missing)}")
            return

        invalid = [k for k, v in cfg.items() if v and not os.path.exists(v)]
        if invalid:
            reply = QMessageBox.question(
                self, "路径无效",
                f"以下路径不存在：\n{', '.join(invalid)}\n\n是否仍要保存？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        try:
            existing = load_config()
            existing.update(cfg)
            if save_config(existing):
                QMessageBox.information(self, "保存成功", "✅ 配置已保存到 config.json")
                if self.parent():
                    self.parent()._log("环境配置已更新", "ok")
                self.accept()
            else:
                QMessageBox.critical(self, "保存失败", "❌ 无法写入 config.json，请检查文件权限。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"❌ {e}")


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("城市变化检测与地图更新工具")
        self.setGeometry(100, 100, 1300, 800)
        self.setMinimumSize(1000, 700)

        self.worker = None
        self.batch_worker = None
        self.current_tab = 0
        self._resize_timer = None
        self._last_font_base = 0
        self.log_visible = True
        self._log_min_height = 50
        self._log_expanded_height = 200

        self._init_ui()
        self._load_saved_params()
        self._switch_tab(0)
        QTimer.singleShot(50, self._update_fonts)

    def _init_ui(self):
        central = QWidget()
        central.setStyleSheet("""
            QWidget {
                background-color: #f5f7fa;
            }
            QLineEdit {
                background-color: white;
                border: 2px solid #d0d7de;
                border-radius: 6px;
                padding: 8px 12px;
                color: #1a2332;
                min-height: 30px;
            }
            QLineEdit:focus {
                border-color: #4a7cf7;
            }
            QLineEdit:hover {
                border-color: #8ab4f8;
            }
            QComboBox {
                background-color: white;
                border: 2px solid #d0d7de;
                border-radius: 6px;
                padding: 6px 12px;
                color: #1a2332;
                min-height: 30px;
            }
            QComboBox:focus {
                border-color: #4a7cf7;
            }
            QComboBox:hover {
                border-color: #8ab4f8;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                border: 2px solid #d0d7de;
                selection-background-color: #4a7cf7;
                color: #1a2332;
            }
            QCheckBox {
                color: #2a3a4a;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                background-color: white;
                border: 2px solid #d0d7de;
            }
            QCheckBox::indicator:checked {
                background-color: #4a7cf7;
                border-color: #4a7cf7;
            }
            QCheckBox::indicator:hover {
                border-color: #8ab4f8;
            }
            QPushButton#PrimaryBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #5a8cf7;
            }
            QPushButton#PrimaryBtn:disabled {
                background-color: #b0c4e8;
            }
            QPushButton#SecondaryBtn {
                background-color: #e8ecf2;
                color: #2a3a4a;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
            }
            QPushButton#SecondaryBtn:hover {
                background-color: #d5dce6;
            }
            QPushButton#BrowseBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: bold;
                min-width: 70px;
            }
            QPushButton#BrowseBtn:hover {
                background-color: #5a8cf7;
            }
            QPushButton#SuccessBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton#SuccessBtn:hover {
                background-color: #28b86a;
            }
            QPushButton#SuccessBtn:disabled {
                background-color: #90c8a8;
            }
            QPushButton#CardBtn {
                background-color: white;
                border: 2px solid #dce3ec;
                border-radius: 10px;
                text-align: left;
            }
            QPushButton#CardBtn:hover {
                border-color: #4a7cf7;
                background-color: #f8faff;
            }
            QPushButton#CardBtn QLabel#CardTitle {
                color: #1a2332 !important;
            }
            QPushButton#CardBtn QLabel#CardDesc {
                color: #7a8a9a !important;
            }
            QPushButton#CardBtn QLabel#CardIcon {
                color: #4a7cf7 !important;
            }
            QPushButton#CardBtn[active="true"] {
                background-color: #e8f0fe;
                border: 2px solid #4a7cf7;
            }
            QPushButton#CardBtn[active="true"] QLabel#CardTitle {
                color: #1a2332 !important;
            }
            QPushButton#CardBtn[active="true"] QLabel#CardDesc {
                color: #4a6a8a !important;
            }
            QPushButton#CardBtn[active="true"] QLabel#CardIcon {
                color: #4a7cf7 !important;
            }
            QStackedWidget#ContentStack {
                background-color: white;
                border-radius: 10px;
            }
            QWidget#LogContainer {
                background-color: white;
                border-radius: 10px;
            }
            QTextEdit#LogText {
                background-color: #f8f9fb;
                border: 1px solid #e8ecf2;
                border-radius: 6px;
                color: #2a3a4a;
                font-family: "Consolas", monospace;
                padding: 12px;
                min-height: 60px;
            }
            QProgressBar#ProgressBar {
                background-color: #e8ecf2;
                border: none;
                border-radius: 4px;
                height: 6px;
            }
            QProgressBar#ProgressBar::chunk {
                background-color: #4a7cf7;
                border-radius: 4px;
            }
            QFrame#Separator {
                background-color: #dce3ec;
                max-height: 1px;
                min-height: 1px;
            }
            QLabel#TitleLabel {
                font-weight: bold;
                color: #1a2332;
            }
            QLabel#StatusLabel {
                color: #5a6a7a;
            }
            QLabel#PageTitle {
                font-weight: bold;
                color: #1a2332;
            }
            QLabel#PageDesc {
                color: #7a8a9a;
                margin-bottom: 8px;
            }
            QLabel#FieldLabel {
                color: #2a3a4a;
                min-width: 80px;
            }
            QLabel#CardTitle {
                font-weight: bold;
                color: #1a2332;
            }
            QLabel#CardDesc {
                color: #7a8a9a;
            }
            QLabel#LogLabel {
                font-weight: bold;
                color: #1a2332;
            }
            QLabel#ModelDesc {
                color: #7a8a9a;
            }
            QSplitter::handle {
                background-color: #dce3ec;
                width: 4px;
                height: 4px;
            }
            QSplitter::handle:hover {
                background-color: #4a7cf7;
            }
        """)
        self.setCentralWidget(central)
        self.main_layout = QVBoxLayout(central)
        self.main_layout.setContentsMargins(20, 16, 20, 20)
        self.main_layout.setSpacing(14)

        # ----- 标题栏 -----
        self.title_bar = QWidget()
        self.title_bar.setStyleSheet("background-color: white; border-radius: 10px;")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(20, 8, 20, 8)

        self.title_label = QLabel("🛰  城市变化检测与地图更新工具")
        self.title_label.setObjectName("TitleLabel")
        title_layout.addWidget(self.title_label)

        title_layout.addStretch()

        self.status_label = QLabel("● 就绪")
        self.status_label.setObjectName("StatusLabel")
        title_layout.addWidget(self.status_label)

        self.check_btn = QPushButton("🔍 自检")
        self.check_btn.setObjectName("SecondaryBtn")
        self.check_btn.setStyleSheet("""
            QPushButton#SecondaryBtn {
                background-color: #e8ecf2;
                color: #2a3a4a;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton#SecondaryBtn:hover {
                background-color: #d5dce6;
            }
        """)
        self.check_btn.clicked.connect(self._check_env)
        title_layout.addWidget(self.check_btn)

        # 环境配置按钮
        self.setup_btn = QPushButton("⚙ 环境配置")
        self.setup_btn.setObjectName("PrimaryBtn")
        self.setup_btn.setMinimumWidth(150)
        self.setup_btn.setStyleSheet("""
            QPushButton#PrimaryBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-weight: bold;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #5a8cf7;
            }
            QPushButton#PrimaryBtn:disabled {
                background-color: #b0c4e8;
            }
        """)
        self.setup_btn.clicked.connect(self._open_setup)
        title_layout.addWidget(self.setup_btn)

        self.main_layout.addWidget(self.title_bar)

        # ----- 功能卡片行（高度统一120）-----
        self.card_layout = QHBoxLayout()
        self.card_layout.setSpacing(12)

        self.card_btns = []
        cards = [
            ("📸", "地物分类", "单张影像\n分类 + 矢量"),
            ("🔍", "变化检测", "两期影像\n变化矢量"),
            ("📦", "批量处理", "多组任务\n统计报告"),
            ("📊", "专题图", "结果数据\n专题图图片"),
        ]
        for i, (icon, title, desc) in enumerate(cards):
            btn = QPushButton()
            btn.setProperty("card_index", i)
            btn.setObjectName("CardBtn")
            btn.setFixedHeight(120)
            btn.setMinimumWidth(150)
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))

            layout = QHBoxLayout(btn)
            layout.setContentsMargins(16, 10, 16, 10)
            layout.setSpacing(10)

            icon_lbl = QLabel(icon)
            icon_lbl.setObjectName("CardIcon")
            layout.addWidget(icon_lbl)

            text_widget = QWidget()
            text_layout = QVBoxLayout(text_widget)
            text_layout.setContentsMargins(4, 0, 0, 0)
            text_layout.setSpacing(4)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("CardTitle")
            text_layout.addWidget(title_lbl)
            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("CardDesc")
            desc_lbl.setWordWrap(True)
            text_layout.addWidget(desc_lbl)
            layout.addWidget(text_widget)
            layout.addStretch()

            self.card_btns.append(btn)
            self.card_layout.addWidget(btn)

        self.main_layout.addLayout(self.card_layout)

        self.line = QFrame()
        self.line.setObjectName("Separator")
        self.main_layout.addWidget(self.line)

        # ----- 主体区域（使用 QSplitter 实现可伸缩）-----
        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setHandleWidth(6)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentStack")
        self.content_stack.addWidget(self._create_classify_page())
        self.content_stack.addWidget(self._create_detection_page())
        self.content_stack.addWidget(self._create_batch_page())
        self.content_stack.addWidget(self._create_thematic_page())
        self.main_splitter.addWidget(self.content_stack)

        # 下方：日志区域（可折叠为细条）
        self.log_container = QWidget()
        self.log_container.setObjectName("LogContainer")
        log_layout = QVBoxLayout(self.log_container)
        log_layout.setContentsMargins(16, 8, 16, 8)
        log_layout.setSpacing(4)

        log_header = QHBoxLayout()
        self.log_label = QLabel("📟 运行日志")
        self.log_label.setObjectName("LogLabel")
        log_header.addWidget(self.log_label)

        self.toggle_log_btn = QPushButton("▲ 收起")
        self.toggle_log_btn.setObjectName("SecondaryBtn")
        self.toggle_log_btn.clicked.connect(self._toggle_log)
        log_header.addWidget(self.toggle_log_btn)

        log_header.addStretch()
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("SecondaryBtn")
        self.clear_btn.clicked.connect(self._clear_log)
        log_header.addWidget(self.clear_btn)
        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("LogText")
        log_layout.addWidget(self.log_text)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setObjectName("ProgressBar")
        log_layout.addWidget(self.progress)

        self.main_splitter.addWidget(self.log_container)
        self.main_splitter.setSizes([500, 200])

        self.main_layout.addWidget(self.main_splitter)

        self._log("欢迎使用城市变化检测与地图更新工具", "header")
        self._log("点击上方功能卡片选择任务类型，然后填写参数运行。", "dim")
        self._log("点击「▲ 收起」可折叠日志区域，点击「▼ 展开」恢复。", "dim")

    def _toggle_log(self):
        sizes = self.main_splitter.sizes()
        total = sizes[0] + sizes[1]

        if self.log_visible:
            self.log_visible = False
            self.toggle_log_btn.setText("▼ 展开")
            self.main_splitter.setSizes([total - self._log_min_height, self._log_min_height])
            self.log_text.setVisible(False)
            self.progress.setVisible(False)
        else:
            self.log_visible = True
            self.toggle_log_btn.setText("▲ 收起")
            self.main_splitter.setSizes([total - self._log_expanded_height, self._log_expanded_height])
            self.log_text.setVisible(True)
            if self.progress.isVisible():
                self.progress.setVisible(True)

    # ============================================================
    # 页面创建方法
    # ============================================================

    def _create_classify_page(self):
        page = QWidget()
        page.setStyleSheet("background-color: white; border-radius: 10px;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("📸 地物分类")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        desc = QLabel("对单张遥感影像进行地物分类，提取建筑、道路、水体、植被等地物类型。")
        desc.setObjectName("PageDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.class_image = QLineEdit()
        self.class_image.setObjectName("InputField")
        layout.addLayout(self._create_input_row("输入影像", self.class_image,
                                                lambda: self._browse_file(self.class_image,
                                                                          "影像文件 (*.tif *.tiff *.img)")))

        self.class_out = QLineEdit("classify.tif")
        self.class_out.setObjectName("InputField")
        layout.addLayout(self._create_input_row("分类输出", self.class_out,
                                                lambda: self._browse_save(self.class_out, "GeoTIFF (*.tif)")))

        self.vec_out = QLineEdit("classify.udbx")
        self.vec_out.setObjectName("InputField")
        layout.addLayout(self._create_input_row("矢量输出", self.vec_out,
                                                lambda: self._browse_save(self.vec_out, "UDBX (*.udbx)")))

        # ===== 【修改】GPU 和两个按钮放在同一行 =====
        action_row = QHBoxLayout()
        action_row.setSpacing(15)

        # GPU 选择（左对齐）
        gpu_label = QLabel("GPU")
        gpu_label.setObjectName("FieldLabel")
        action_row.addWidget(gpu_label)
        self.class_gpu = QComboBox()
        self.class_gpu.addItems(["0", "1", "-1 (CPU)"])
        self.class_gpu.setObjectName("ComboBox")
        self.class_gpu.setMinimumWidth(100)
        action_row.addWidget(self.class_gpu)

        action_row.addStretch()  # 弹性空间，把按钮推到右边

        # 两个运行按钮（右下角）
        self.classify_btn = QPushButton("▶ 运行地物分类")
        self.classify_btn.setObjectName("ActionBtn")
        self.classify_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 28px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 160px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #28b86a;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #90c8a8;
            }
        """)
        self.classify_btn.clicked.connect(self._run_classify)
        action_row.addWidget(self.classify_btn)

        self.vectorize_btn = QPushButton("↗ 转矢量")
        self.vectorize_btn.setObjectName("ActionBtn")
        self.vectorize_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 28px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 130px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #5a8cf7;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #b0c4e8;
            }
        """)
        self.vectorize_btn.clicked.connect(self._run_vectorize)
        action_row.addWidget(self.vectorize_btn)

        layout.addLayout(action_row)
        layout.addStretch()
        return page

    def _create_detection_page(self):
        page = QWidget()
        page.setStyleSheet("background-color: white; border-radius: 10px;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(10)

        title = QLabel("🔍 变化检测")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        desc = QLabel("对两期遥感影像进行变化检测，输出变化矢量结果，支持建筑物变化检测等多种模型。")
        desc.setObjectName("PageDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.before_image = QLineEdit()
        self.before_image.setObjectName("InputField")
        layout.addLayout(self._create_input_row("前期影像", self.before_image,
                                                lambda: self._browse_file(self.before_image,
                                                                          "影像文件 (*.tif *.tiff *.img)")))

        self.after_image = QLineEdit()
        self.after_image.setObjectName("InputField")
        layout.addLayout(self._create_input_row("后期影像", self.after_image,
                                                lambda: self._browse_file(self.after_image,
                                                                          "影像文件 (*.tif *.tiff *.img)")))

        self.detection_out = QLineEdit("result.udbx")
        self.detection_out.setObjectName("InputField")
        layout.addLayout(self._create_input_row("输出路径", self.detection_out,
                                                lambda: self._browse_save(self.detection_out, "UDBX (*.udbx)")))

        row = QHBoxLayout()
        row.setSpacing(10)
        model_label = QLabel("模型")
        model_label.setObjectName("FieldLabel")
        row.addWidget(model_label)
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(AVAILABLE_MODELS.keys()))
        self.model_combo.setObjectName("ComboBox")
        self.model_combo.currentIndexChanged.connect(self._on_model_change)
        row.addWidget(self.model_combo)
        row.addSpacing(20)
        gpu_label2 = QLabel("GPU")
        gpu_label2.setObjectName("FieldLabel")
        row.addWidget(gpu_label2)
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(["0", "1", "-1 (CPU)"])
        self.gpu_combo.setObjectName("ComboBox")
        row.addWidget(self.gpu_combo)
        row.addStretch()
        layout.addLayout(row)

        self.model_desc = QLabel("")
        self.model_desc.setObjectName("ModelDesc")
        self.model_desc.setWordWrap(True)
        layout.addWidget(self.model_desc)

        # ============================================================
        # 变化检测：复选框 + 两个按钮（右下角）
        # ============================================================
        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.setContentsMargins(0, 4, 0, 4)

        self.classify_check = QCheckBox("启用变化类型分类")
        self.classify_check.setChecked(True)
        self.classify_check.setObjectName("CheckBox")
        action_row.addWidget(self.classify_check)

        hint_label = QLabel("(仅增强检测生效)")
        hint_label.setObjectName("FieldLabel")
        action_row.addWidget(hint_label)

        action_row.addStretch()  # 弹性空间

        self.detection_btn = QPushButton("运行变化检测")
        self.detection_btn.setObjectName("ActionBtn")
        self.detection_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 28px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 150px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #28b86a;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #90c8a8;
            }
        """)
        self.detection_btn.clicked.connect(self._run_detection)
        action_row.addWidget(self.detection_btn)

        self.enhanced_btn = QPushButton("增强检测")
        self.enhanced_btn.setObjectName("ActionBtn")
        self.enhanced_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 28px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 130px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #5a8cf7;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #b0c4e8;
            }
        """)
        self.enhanced_btn.clicked.connect(self._run_enhanced)
        action_row.addWidget(self.enhanced_btn)

        layout.addLayout(action_row)

        layout.addStretch()
        return page

    def _create_batch_page(self):
        page = QWidget()
        page.setStyleSheet("background-color: white; border-radius: 10px;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("📦 批量处理与统计报告")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        desc = QLabel("批量处理多组影像任务，自动生成统计Excel、图表、Word/PDF报告。支持断点续跑、异常隔离。")
        desc.setObjectName("PageDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.batch_task = QLineEdit(r"D:\项目根目录\batch\03_输入输出样例\示例任务清单.csv")
        self.batch_task.setObjectName("InputField")
        layout.addLayout(self._create_input_row("任务清单", self.batch_task,
                                                lambda: self._browse_file(self.batch_task, "CSV文件 (*.csv)")))

        self.batch_config = QLineEdit(r"D:\项目根目录\batch\03_输入输出样例\示例配置.json")
        self.batch_config.setObjectName("InputField")
        layout.addLayout(self._create_input_row("配置文件", self.batch_config,
                                                lambda: self._browse_file(self.batch_config, "JSON文件 (*.json)")))

        self.batch_output = QLineEdit(r"D:\项目根目录\batch_output")
        self.batch_output.setObjectName("InputField")
        layout.addLayout(self._create_input_row("输出目录", self.batch_output,
                                                lambda: self._browse_folder(self.batch_output)))

        self.batch_title = QLineEdit("城市变化检测统计报告")
        self.batch_title.setObjectName("InputField")
        layout.addLayout(self._create_input_row("报告标题", self.batch_title, lambda: None))

        # ===== 批量处理按钮（右下角） =====
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.batch_btn = QPushButton("▶ 运行批量处理")
        self.batch_btn.setObjectName("ActionBtn")
        self.batch_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 32px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 180px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #28b86a;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #90c8a8;
            }
        """)
        self.batch_btn.clicked.connect(self._run_batch_pipeline)
        btn_row.addWidget(self.batch_btn)

        layout.addLayout(btn_row)
        layout.addStretch()
        return page

    def _create_thematic_page(self):
        page = QWidget()
        page.setStyleSheet("background-color: white; border-radius: 10px;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("📊 专题图生成")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        desc = QLabel("自动生成带图例、比例尺、指北针和边框的专题图图片（PNG格式）。")
        desc.setObjectName("PageDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.thematic_udbx = QLineEdit()
        self.thematic_udbx.setObjectName("InputField")
        layout.addLayout(self._create_input_row("数据源 (.udbx)", self.thematic_udbx,
                                                lambda: self._browse_file(self.thematic_udbx, "UDBX文件 (*.udbx)")))

        self.thematic_out = QLineEdit("专题图.png")
        self.thematic_out.setObjectName("InputField")
        layout.addLayout(self._create_input_row("输出图片", self.thematic_out,
                                                lambda: self._browse_save(self.thematic_out, "PNG图片 (*.png)")))

        # ===== 专题图按钮（右下角） =====
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.thematic_btn = QPushButton("▶ 生成专题图")
        self.thematic_btn.setObjectName("ActionBtn")
        self.thematic_btn.setStyleSheet("""
            QPushButton#ActionBtn {
                background-color: #22a65e;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 32px;
                font-weight: bold;
                font-size: 20px;
                min-height: 48px;
                min-width: 170px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #28b86a;
            }
            QPushButton#ActionBtn:disabled {
                background-color: #90c8a8;
            }
        """)
        self.thematic_btn.clicked.connect(self._run_thematic)
        btn_row.addWidget(self.thematic_btn)

        layout.addLayout(btn_row)
        layout.addStretch()
        return page

    def _create_input_row(self, label_text, line_edit, browse_callback):
        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel(label_text)
        lbl.setObjectName("FieldLabel")
        lbl.setMinimumWidth(90)
        row.addWidget(lbl)
        row.addWidget(line_edit)
        btn = QPushButton("📂 浏览")
        btn.setObjectName("BrowseBtn")
        btn.setStyleSheet("""
            QPushButton#BrowseBtn {
                background-color: #4a7cf7;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 22px;
                font-weight: bold;
                font-size: 15px;
                min-width: 80px;
                min-height: 36px;
            }
            QPushButton#BrowseBtn:hover {
                background-color: #5a8cf7;
            }
        """)
        btn.clicked.connect(browse_callback)
        row.addWidget(btn)
        return row

    # ============================================================
    # 其他方法
    # ============================================================

    def _switch_tab(self, index):
        self.current_tab = index
        self.content_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.card_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _browse_file(self, line_edit, filter_str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filter_str)
        if path:
            line_edit.setText(path)

    def _browse_save(self, line_edit, filter_str):
        path, _ = QFileDialog.getSaveFileName(self, "保存文件", "", filter_str)
        if path:
            line_edit.setText(path)

    def _browse_folder(self, line_edit):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(path)

    def _on_model_change(self):
        key = self.model_combo.currentText()
        info = AVAILABLE_MODELS.get(key, {})
        self.model_desc.setText(info.get("description", ""))

    def _load_saved_params(self):
        try:
            cfg = load_config()
            lp = cfg.get("last_params", {})
            if lp.get("before"):
                self.before_image.setText(lp["before"])
            if lp.get("after"):
                self.after_image.setText(lp["after"])
            if lp.get("out"):
                self.detection_out.setText(lp["out"])
            if lp.get("model"):
                idx = self.model_combo.findText(lp["model"])
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
            if lp.get("gpu") is not None:
                g = str(lp["gpu"])
                idx = self.gpu_combo.findText(g)
                if idx >= 0:
                    self.gpu_combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _open_setup(self):
        """打开环境配置对话框（PyQt5 版）"""
        dialog = SetupDialog(self)
        dialog.exec_()

    def _log(self, msg, tag="dim"):
        colors = {
            "header": "#1a2332",
            "dim": "#7a8a9a",
            "ok": "#22a65e",
            "error": "#e8544a",
            "warning": "#e8a838",
            "info": "#4a7cf7",
        }
        color = colors.get(tag, "#7a8a9a")
        self.log_text.append(f'<span style="color:{color};">{msg}</span>')
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _clear_log(self):
        self.log_text.clear()

    def _set_buttons_enabled(self, enabled):
        self.classify_btn.setEnabled(enabled)
        self.vectorize_btn.setEnabled(enabled)
        self.detection_btn.setEnabled(enabled)
        self.enhanced_btn.setEnabled(enabled)
        self.batch_btn.setEnabled(enabled)
        self.thematic_btn.setEnabled(enabled)

    # ============================================================
    # 字体自动缩放
    # ============================================================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._update_fonts)
        self._resize_timer.start(100)

    def _update_fonts(self):
        width = self.width()
        base = max(10, min(24, int(width / 80)))
        if self._last_font_base == base:
            return
        self._last_font_base = base

        for widget in self.findChildren(QWidget):
            obj_name = widget.objectName()
            if obj_name == "TitleLabel":
                self._set_font_size(widget, base + 5, bold=True)
            elif obj_name == "StatusLabel":
                self._set_font_size(widget, base - 2)
            elif obj_name == "CardTitle":
                self._set_font_size(widget, base + 1, bold=True)
            elif obj_name == "CardDesc":
                self._set_font_size(widget, base - 4)
            elif obj_name == "CardIcon":
                self._set_font_size(widget, base + 6)
            elif obj_name == "PageTitle":
                self._set_font_size(widget, base + 3, bold=True)
            elif obj_name == "PageDesc":
                self._set_font_size(widget, base - 2)
            elif obj_name == "FieldLabel":
                self._set_font_size(widget, base - 2)
            elif obj_name in ("InputField", "ComboBox", "CheckBox"):
                self._set_font_size(widget, base - 2)
            elif obj_name == "LogLabel":
                self._set_font_size(widget, base - 1, bold=True)
            elif obj_name == "LogText":
                self._set_font_size(widget, base - 2)
            elif obj_name in ("PrimaryBtn", "SecondaryBtn", "SuccessBtn", "BrowseBtn"):
                self._set_font_size(widget, base - 2, bold=True)
            elif obj_name == "ActionBtn":
                self._set_font_size(widget, base + 2, bold=True)  # 运行按钮字体增大
            elif obj_name == "ModelDesc":
                self._set_font_size(widget, base - 3)
            elif obj_name == "CardBtn":
                widget.setFixedHeight(int(base * 7))
            elif obj_name == "ProgressBar":
                pass

    def _set_font_size(self, widget, size, bold=False):
        font = widget.font()
        if font.pointSize() != size:
            font.setPointSize(size)
            font.setBold(bold)
            widget.setFont(font)

    # ============================================================
    # 环境自检
    # ============================================================
    def _check_env(self):
        self._log("═══════════════════════════════════════", "header")
        self._log("  运行环境自检...", "info")
        self.status_label.setText("● 检测中...")
        self.status_label.setStyleSheet("color: #e8a838;")

        import io
        old_stdout = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            ok = run_self_check()
        except Exception as e:
            self._log(f"[错误] {e}", "error")
            ok = False
        finally:
            sys.stdout = old_stdout

        for line in buf.getvalue().split("\n"):
            t = line.strip()
            if not t:
                continue
            if "[OK]" in t:
                self._log(t, "ok")
            elif "[FAIL]" in t:
                self._log(t, "error")
            elif "[WARN]" in t:
                self._log(t, "warning")
            elif t.startswith("="):
                self._log(t, "header")
            else:
                self._log(t, "dim")

        if ok:
            self.status_label.setText("● 环境就绪")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self.status_label.setText("● 配置有误")
            self.status_label.setStyleSheet("color: #e8544a;")

    # ============================================================
    # 功能执行
    # ============================================================
    def _run_classify(self):
        input_image = self.class_image.text().strip()
        output_raster = self.class_out.text().strip()
        if not input_image:
            QMessageBox.warning(self, "参数不完整", "请选择输入影像")
            return
        if not output_raster:
            output_raster = "classify.tif"
            self.class_out.setText(output_raster)

        gpu = 0
        try:
            gpu = int(self.class_gpu.currentText().split()[0])
        except:
            pass

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 分类中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  地物分类", "header")
        self._log(f"  输入影像: {input_image}", "dim")
        self._log(f"  输出栅格: {output_raster}", "dim")

        def target():
            return run_classify(input_image, output_raster, model_key="landcover", gpu=gpu, batch_size=1, offset=128)

        self.worker = WorkerThread(target)
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_classify_done)
        self.worker.start()

    def _on_classify_done(self, ok):
        self.progress.setVisible(False)
        self._set_buttons_enabled(True)
        if ok:
            self._log("✅ 地物分类完成！", "ok")
            self.status_label.setText("● 分类完成")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self._log("❌ 地物分类失败", "error")
            self.status_label.setText("● 分类失败")
            self.status_label.setStyleSheet("color: #e8544a;")

    def _run_vectorize(self):
        input_raster = self.class_out.text().strip()
        output_vector = self.vec_out.text().strip()
        if not input_raster:
            QMessageBox.warning(self, "参数不完整", "请先运行地物分类")
            return
        if not os.path.exists(input_raster):
            QMessageBox.warning(self, "文件不存在", f"找不到分类栅格：{input_raster}")
            return

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 转矢量中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  栅格转矢量", "header")
        self._log(f"  输入栅格: {input_raster}", "dim")
        self._log(f"  输出矢量: {output_vector}", "dim")

        def target():
            return run_vectorize(input_raster, output_vector, min_area=0, simplify_tolerance=0)

        self.worker = WorkerThread(target)
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_vectorize_done)
        self.worker.start()

    def _on_vectorize_done(self, ok):
        self.progress.setVisible(False)
        self._set_buttons_enabled(True)
        if ok:
            self._log("✅ 栅格转矢量完成！", "ok")
            self.status_label.setText("● 转矢量完成")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self._log("❌ 栅格转矢量失败", "error")
            self.status_label.setText("● 转矢量失败")
            self.status_label.setStyleSheet("color: #e8544a;")

    def _run_detection(self):
        before = self.before_image.text().strip()
        after = self.after_image.text().strip()
        out = self.detection_out.text().strip()
        if not before:
            QMessageBox.warning(self, "参数不完整", "请选择前期影像")
            return
        if not after:
            QMessageBox.warning(self, "参数不完整", "请选择后期影像")
            return

        model = self.model_combo.currentText()
        gpu = 0
        try:
            gpu = int(self.gpu_combo.currentText().split()[0])
        except:
            pass

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 检测中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  变化检测", "header")
        self._log(f"  前期影像: {before}", "dim")
        self._log(f"  后期影像: {after}", "dim")
        self._log(f"  输出路径: {out}", "dim")

        def target():
            return run_single_inference(before, after, out, model_key=model, gpu=gpu, out_format="udbx")

        self.worker = WorkerThread(target)
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_detection_done)
        self.worker.start()

    def _on_detection_done(self, ok):
        self.progress.setVisible(False)
        self._set_buttons_enabled(True)
        if ok:
            self._log("✅ 变化检测完成！", "ok")
            self.status_label.setText("● 检测完成")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self._log("❌ 变化检测失败", "error")
            self.status_label.setText("● 检测失败")
            self.status_label.setStyleSheet("color: #e8544a;")

    def _run_enhanced(self):
        before = self.before_image.text().strip()
        after = self.after_image.text().strip()
        out = self.detection_out.text().strip()
        if not before or not after:
            QMessageBox.warning(self, "参数不完整", "请选择前期和后期影像")
            return

        model = self.model_combo.currentText()
        gpu = 0
        try:
            gpu = int(self.gpu_combo.currentText().split()[0])
        except:
            pass
        classify = self.classify_check.isChecked()

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 增强检测中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  增强检测 (矢量+分类)", "header")
        self._log(f"  前期影像: {before}", "dim")
        self._log(f"  后期影像: {after}", "dim")
        self._log(f"  输出路径: {out}", "dim")

        def target():
            ok, result = run_enhanced_inference(
                before, after, out, model_key=model, gpu=gpu,
                classify=classify, min_change_area=0, smooth=True,
                out_format="udbx"
            )
            return ok

        self.worker = WorkerThread(target)
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_detection_done)
        self.worker.start()

    def _run_batch_pipeline(self):
        task_csv = self.batch_task.text().strip()
        config_path = self.batch_config.text().strip()
        output_dir = self.batch_output.text().strip()
        report_title = self.batch_title.text().strip()

        if not task_csv or not os.path.exists(task_csv):
            QMessageBox.warning(self, "参数不完整", "请选择有效的任务清单CSV")
            return
        if not config_path or not os.path.exists(config_path):
            QMessageBox.warning(self, "参数不完整", "请选择有效的配置JSON")
            return
        if not output_dir:
            output_dir = r"D:\项目根目录\batch_output"
            self.batch_output.setText(output_dir)
        if not report_title:
            report_title = "城市变化检测统计报告"

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 批量处理中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  批量处理与统计报告", "header")
        self._log(f"  任务清单: {task_csv}", "dim")
        self._log(f"  配置: {config_path}", "dim")
        self._log(f"  输出目录: {output_dir}", "dim")

        self.batch_worker = BatchPipelineThread(task_csv, config_path, output_dir, report_title)
        self.batch_worker.log_signal.connect(self._log)
        self.batch_worker.finished_signal.connect(self._on_batch_done)
        self.batch_worker.start()

    def _on_batch_done(self, ok):
        self.progress.setVisible(False)
        self._set_buttons_enabled(True)
        if ok:
            self._log("✅ 批量处理完成！", "ok")
            self.status_label.setText("● 批量处理完成")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self._log("❌ 批量处理失败", "error")
            self.status_label.setText("● 批量处理失败")
            self.status_label.setStyleSheet("color: #e8544a;")

    def _run_thematic(self):
        udbx_path = self.thematic_udbx.text().strip()
        output_image = self.thematic_out.text().strip()

        if not udbx_path or not os.path.exists(udbx_path):
            QMessageBox.warning(self, "参数不完整", "请选择有效的UDBX数据源文件")
            return
        if not output_image:
            output_image = "专题图.png"
            self.thematic_out.setText(output_image)

        self._set_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("● 生成专题图中...")
        self.status_label.setStyleSheet("color: #4a7cf7;")

        self._log("═══════════════════════════════════════", "header")
        self._log("  专题图生成", "header")
        self._log(f"  数据源: {udbx_path}", "dim")
        self._log(f"  输出: {output_image}", "dim")

        datasets_to_try = ["vector_result", "change_polygons", "binclassify_result", "objdetect_result"]

        def target():
            for ds_name in datasets_to_try:
                self._log(f"  尝试数据集: {ds_name}...", "dim")
                ok = generate_thematic_map(udbx_path, ds_name, output_image)
                if ok:
                    return True
            self._log("所有数据集都失败", "error")
            return False

        self.worker = WorkerThread(target)
        self.worker.log_signal.connect(self._log)
        self.worker.finished_signal.connect(self._on_thematic_done)
        self.worker.start()

    def _on_thematic_done(self, ok):
        self.progress.setVisible(False)
        self._set_buttons_enabled(True)
        if ok:
            self._log("✅ 专题图生成完成！", "ok")
            self.status_label.setText("● 专题图完成")
            self.status_label.setStyleSheet("color: #22a65e;")
        else:
            self._log("❌ 专题图生成失败", "error")
            self.status_label.setText("● 专题图失败")
            self.status_label.setStyleSheet("color: #e8544a;")


# ============================================================
# 入口
# ============================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()