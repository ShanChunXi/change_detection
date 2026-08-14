# -*- coding: utf-8 -*-
"""
城市变化检测与地图更新工具 — PyQt5 GIS 风格界面
"""

import sys, os, io, glob
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QFrame, QCheckBox,
    QStackedWidget, QSplitter, QDialog, QMenuBar, QMenu, QAction,
    QToolBar, QStatusBar, QGroupBox, QGridLayout, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QSpacerItem,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QTextCursor, QPixmap, QImage

from change_detection import (
    run_self_check, run_single_inference, run_enhanced_inference,
    run_batch_inference, run_folder_batch, AVAILABLE_MODELS,
    load_config, save_config, _remember_last_params,
    export_vector_multiformat,
)
from classify_vectorize import run_classify, run_vectorize
from mapper import generate_thematic_map

if getattr(sys, 'frozen', False):
    _BATCH_DIR = os.path.join(sys._MEIPASS, "batch", "01_源代码")
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    _BATCH_DIR = os.path.join(_PROJECT_ROOT, "batch", "01_源代码")
if _BATCH_DIR not in sys.path:
    sys.path.insert(0, _BATCH_DIR)
from li_batch_api import run_full_pipeline, generate_report


# ═══════════════════════════════════════════════════════════
# 工作线程
# ═══════════════════════════════════════════════════════════

class Worker(QThread):
    log = pyqtSignal(str, str)
    done = pyqtSignal(bool)
    result = pyqtSignal(object)

    def __init__(self, fn, args=(), kw=None):
        super().__init__()
        self.fn = fn; self.args = args; self.kw = kw or {}

    def run(self):
        old = sys.stdout; buf = io.StringIO(); sys.stdout = buf
        ok = False
        try:
            r = self.fn(*self.args, **self.kw)
            ok = r[0] if isinstance(r, tuple) else bool(r)
            self.result.emit(r)
        except Exception as e:
            print(f"[错误] {e}")
        finally:
            sys.stdout = old
        for line in buf.getvalue().split("\n"):
            t = line.strip()
            if not t: continue
            if any(k in t for k in ("[OK]", "完成", "✅")): self.log.emit(t, "ok")
            elif any(k in t for k in ("[错误]", "Error", "❌", "[FAIL]")): self.log.emit(t, "error")
            elif any(k in t for k in ("[WARN]", "警告", "⚠")): self.log.emit(t, "warning")
            elif t.startswith(("=", "─", "═")): self.log.emit(t, "header")
            else: self.log.emit(t, "dim")
        self.done.emit(ok)


class BatchWorker(QThread):
    log = pyqtSignal(str, str)
    done = pyqtSignal(bool)
    result = pyqtSignal(object)

    def __init__(self, task_csv, config_path, output_dir, report_title):
        super().__init__()
        self.task_csv = task_csv; self.config_path = config_path
        self.output_dir = output_dir; self.report_title = report_title

    def run(self):
        old = sys.stdout; buf = io.StringIO(); sys.stdout = buf
        ok = False
        try:
            r = run_full_pipeline(task_list_path=self.task_csv,
                config_path=self.config_path, output_dir=self.output_dir,
                report_title=self.report_title, real_mode=True)
            ok = r.get("success", False)
            self.result.emit(r)
        except Exception as e:
            print(f"[错误] {e}")
        finally:
            sys.stdout = old
        for line in buf.getvalue().split("\n"):
            t = line.strip()
            if not t: continue
            if any(k in t for k in ("[OK]", "完成", "✅")): self.log.emit(t, "ok")
            elif any(k in t for k in ("[错误]", "Error", "❌")): self.log.emit(t, "error")
            elif t.startswith(("=", "─")): self.log.emit(t, "header")
            else: self.log.emit(t, "dim")
        self.done.emit(ok)


# ═══════════════════════════════════════════════════════════
# 环境配置对话框
# ═══════════════════════════════════════════════════════════

class SetupDialog(QDialog):
    FIELDS = [
        ("python_path", "SuperMap Python 解释器"),
        ("java_home", "iObjects Java JRE 目录"),
        ("iobjects_bin", "iObjects Java Bin 目录"),
        ("resources_ml", "ML 资源包目录"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("环境配置")
        self.setMinimumWidth(800); self.setMinimumHeight(500)
        self.entries = {}
        self._build()
        self._load()

    def _build(self):
        ly = QVBoxLayout(self); ly.setSpacing(14)
        ly.addWidget(QLabel("SuperMap 环境路径配置"))
        ly.addWidget(self._sep())
        for key, label in self.FIELDS:
            row = QHBoxLayout(); row.setSpacing(10)
            row.addWidget(QLabel(label))
            e = QLineEdit(); e.setPlaceholderText("点击浏览或自动检测...")
            self.entries[key] = e; row.addWidget(e, 1)
            btn = QPushButton("浏览")
            if key == "python_path":
                btn.clicked.connect(lambda _, k=key: self._file(k))
            else:
                btn.clicked.connect(lambda _, k=key: self._dir(k))
            row.addWidget(btn); ly.addLayout(row)
        ar = QHBoxLayout()
        ar.addWidget(QLabel("💡 标准路径可自动检测"))
        ar.addStretch()
        ab = QPushButton("自动检测"); ab.clicked.connect(self._auto)
        ar.addWidget(ab); ly.addLayout(ar)
        fr = QHBoxLayout(); fr.addStretch()
        cb = QPushButton("取消"); cb.clicked.connect(self.reject); fr.addWidget(cb)
        sb = QPushButton("保存"); sb.clicked.connect(self._save); fr.addWidget(sb)
        ly.addLayout(fr)

    def _sep(self):
        f = QFrame(); f.setFrameShape(QFrame.HLine)
        f.setStyleSheet("background:#dce3ec;max-height:1px;"); return f

    def _load(self):
        try:
            cfg = load_config()
            for k, e in self.entries.items():
                if cfg.get(k): e.setText(cfg[k])
        except Exception: pass

    def _file(self, k):
        p, _ = QFileDialog.getOpenFileName(self, "选择", "", "可执行文件 (*.exe);;*.*")
        if p: self.entries[k].setText(p.replace("\\", "/"))

    def _dir(self, k):
        p = QFileDialog.getExistingDirectory(self, "选择目录")
        if p: self.entries[k].setText(p.replace("\\", "/"))

    def _locate(self, bases, prefixes, sub_rel):
        """在候选目录列表下按目录名前缀搜索，返回第一个存在 sub_rel 子路径的完整路径。"""
        for base in bases:
            if not base:
                continue
            for prefix in prefixes:
                for hit in sorted(glob.glob(os.path.join(base, prefix)), reverse=True):
                    full = os.path.join(hit, sub_rel)
                    if os.path.exists(full):
                        return full.replace("\\", "/")
        return None

    def _auto(self):
        # 1) 从已填写的路径里推断盘符与公共根目录（supermap-* 目录的父目录），作为优先搜索位置
        hint_drives = []
        roots = []
        for e in self.entries.values():
            t = e.text().strip().replace("\\", "/")
            if not t:
                continue
            if len(t) >= 2 and t[1] == ":":
                hint_drives.append(t[:2] + "/")
            parts = t.split("/")
            for i, seg in enumerate(parts):
                if seg.lower().startswith("supermap"):
                    roots.append("/".join(parts[:i]))
                    break

        drives = []
        for d in hint_drives + ["F:/", "D:/", "E:/", "C:/"]:
            if d not in drives:
                drives.append(d)

        # 2) 每个字段的搜索目标：目录名前缀（不再假设父目录叫 supermap）+ 需存在的子路径
        targets = {
            "python_path":  (["supermap-iobjectspy-env*"], "conda/python.exe"),
            "java_home":    (["supermap-iobjectsjava*"], "jre1.8_x64"),
            "iobjects_bin": (["supermap-iobjectsjava*"], "Bin"),
            "resources_ml": (["supermap-iobjectspy-resources_ml*"], "resources_ml"),
        }

        # 3) 候选目录：公共根目录提示 + 各盘符根 + 盘符下一级子目录
        bases = [r for r in roots if r]
        for d in drives:
            bases.append(d)
            bases += [x for x in glob.glob(os.path.join(d, "*")) if os.path.isdir(x)]

        # 4) 只补全空字段
        cnt = 0
        for key, (prefixes, sub_rel) in targets.items():
            e = self.entries[key]
            if e.text().strip():
                continue
            p = self._locate(bases, prefixes, sub_rel)
            if p:
                e.setText(p)
                cnt += 1
        QMessageBox.information(self, "自动检测",
            f"已自动补全 {cnt} 个路径，请核对保存。" if cnt else
            "未找到可匹配的路径，请手动填写，或先填一个有效路径后重试。")

    def _save(self):
        cfg = {k: e.text().strip() for k, e in self.entries.items()}
        missing = [k for k, v in cfg.items() if not v]
        if missing: return QMessageBox.warning(self, "不完整", f"未填: {', '.join(missing)}")
        try:
            existing = load_config(); existing.update(cfg)
            save_config(existing)
            QMessageBox.information(self, "完成", "配置已保存到 config.json")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))


# ═══════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════

M_DETECT, M_BATCH, M_RESULT = 0, 1, 2

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("城市变化检测与地图更新工具")
        self.setGeometry(100, 100, 1200, 820)
        self.setMinimumSize(960, 640)

        self._worker: Worker | None = None
        self._bw: BatchWorker | None = None
        self._mode = M_DETECT
        self._sub_detect = 0   # 0=单次 1=增强 2=分类 3=矢量
        self._sub_batch = 0    # 0=CSV 1=文件夹 2=仪表盘 3=报告
        self._sub_result = 0   # 0=对比 1=属性 2=专题 3=导出

        self._build_menubar()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self.setStyleSheet("""
            QMainWindow{background:#f0f2f5;}
            QWidget{font-family:"Microsoft YaHei UI";}
            QLineEdit{background:white;border:1px solid #c8ced6;border-radius:3px;
                       padding:5px 8px;color:#1a2332;min-height:26px;}
            QLineEdit:focus{border-color:#4a7cf7;}
            QComboBox{background:white;border:1px solid #c8ced6;border-radius:3px;
                      padding:4px 8px;color:#1a2332;min-height:26px;}
            QComboBox:focus{border-color:#4a7cf7;}
            QComboBox::drop-down{border:none;width:20px;}
            QComboBox QAbstractItemView{background:white;border:1px solid #c8ced6;
                       selection-background-color:#4a7cf7;color:#1a2332;}
            QCheckBox{color:#2a3a4a;spacing:6px;}
            QCheckBox::indicator{width:16px;height:16px;border-radius:2px;
                       background:white;border:2px solid #c0c8d2;}
            QCheckBox::indicator:checked{background:#4a7cf7;border-color:#4a7cf7;}
            QGroupBox{font-weight:bold;border:1px solid #dce3ec;border-radius:4px;
                       margin-top:10px;padding-top:16px;color:#2a3a4a;}
            QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;}
            QTableWidget{background:white;border:1px solid #dce3ec;gridline-color:#e8ecf2;}
            QTableWidget::item{padding:4px 8px;}
            QHeaderView::section{background:#f0f2f5;border:none;padding:6px 10px;
                                 font-weight:bold;color:#2a3a4a;}
            QStatusBar{background:#f0f2f5;border-top:1px solid #dce3ec;color:#5a6a7a;}
            QMenuBar{background:#ffffff;border-bottom:1px solid #dce3ec;padding:2px 0;}
            QMenuBar::item{padding:6px 16px;color:#2a3a4a;}
            QMenuBar::item:selected{background:#e8f0fe;color:#4a7cf7;border-radius:4px;}
            QMenu{background:white;border:1px solid #dce3ec;padding:4px;}
            QMenu::item{padding:8px 32px 8px 16px;color:#2a3a4a;}
            QMenu::item:selected{background:#e8f0fe;color:#4a7cf7;}
            QMenu::separator{height:1px;background:#dce3ec;margin:4px 12px;}
            QToolBar{background:#ffffff;border-bottom:1px solid #dce3ec;
                      padding:4px 8px;spacing:6px;}
            QToolBar QPushButton{background:transparent;border:1px solid transparent;
                       border-radius:3px;padding:4px 12px;color:#2a3a4a;font-size:13px;}
            QToolBar QPushButton:hover{background:#e8ecf2;border-color:#c8ced6;}
            QToolBar QPushButton:checked{background:#e8f0fe;border-color:#4a7cf7;color:#4a7cf7;}
            QSplitter::handle{background:#dce3ec;width:1px;}
            QScrollBar:vertical{background:#f0f2f5;width:8px;}
            QScrollBar::handle:vertical{background:#c0c8d2;border-radius:4px;min-height:30px;}
            QProgressBar{background:#e8ecf2;border:none;border-radius:3px;height:4px;}
            QProgressBar::chunk{background:#4a7cf7;border-radius:3px;}
        """)

        self._load_params()
        self._switch_panel(M_DETECT, 0)

    # ── 菜单栏 ──
    def _build_menubar(self):
        mb = self.menuBar()

        # 文件
        file_menu = mb.addMenu("文件")
        a = QAction("⚙ 环境配置", self); a.triggered.connect(self._setup); file_menu.addAction(a)
        a = QAction("🔍 环境自检", self); a.triggered.connect(self._check); file_menu.addAction(a)
        file_menu.addSeparator()
        a = QAction("退出", self); a.triggered.connect(self.close); file_menu.addAction(a)

        # 变化检测
        self.detect_menu = mb.addMenu("变化检测")
        for name, idx in [("单次检测", 0), ("增强检测", 1), ("地物分类", 2), ("转矢量", 3)]:
            a = QAction(name, self)
            a.triggered.connect(lambda _, i=idx: self._switch_panel(M_DETECT, i))
            self.detect_menu.addAction(a)

        # 批量处理
        self.batch_menu = mb.addMenu("批量处理")
        for name, idx in [("CSV批量", 0), ("文件夹批量", 1), ("任务仪表盘", 2), ("统计报告", 3), ("完整流水线", 4)]:
            a = QAction(name, self)
            a.triggered.connect(lambda _, i=idx: self._switch_panel(M_BATCH, i))
            self.batch_menu.addAction(a)

        # 结果工具
        self.result_menu = mb.addMenu("结果工具")
        for name, idx in [("影像对比", 0), ("属性浏览", 1), ("专题图", 2), ("导出", 3)]:
            a = QAction(name, self)
            a.triggered.connect(lambda _, i=idx: self._switch_panel(M_RESULT, i))
            self.result_menu.addAction(a)

    # ── 工具栏 ──
    def _build_toolbar(self):
        self.toolbar = self.addToolBar("主工具栏")
        self.toolbar.setMovable(False)

        self._tb_model = QComboBox()
        self._tb_model.addItems(list(AVAILABLE_MODELS.keys()))
        self._tb_model.setFixedWidth(130)
        self._tb_model.currentIndexChanged.connect(self._on_model_chg)
        self.toolbar.addWidget(QLabel("模型 "))
        self.toolbar.addWidget(self._tb_model)

        self.toolbar.addWidget(QLabel("  GPU "))
        self._tb_gpu = QComboBox()
        self._tb_gpu.addItems(["0", "1", "-1(CPU)"])
        self._tb_gpu.setFixedWidth(90)
        self.toolbar.addWidget(self._tb_gpu)

        self.toolbar.addWidget(QLabel("  格式 "))
        self._tb_fmt = QComboBox()
        self._tb_fmt.addItems(["udbx", "tif"])
        self._tb_fmt.setFixedWidth(80)
        self.toolbar.addWidget(self._tb_fmt)

        self.toolbar.addSeparator()

        self._tb_run = QPushButton("▶ 运行")
        self._tb_run.setStyleSheet("""
            QPushButton{background:#22a65e;color:white;border:none;border-radius:3px;
            padding:6px 24px;font-weight:bold;font-size:14px;}
            QPushButton:hover{background:#28b86a;}
            QPushButton:disabled{background:#b0c4e8;}""")
        self._tb_run.clicked.connect(self._run)
        self.toolbar.addWidget(self._tb_run)

    # ── 中央区域 ──
    def _build_central(self):
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(2)

        # 上方: 参数面板栈
        self._panel_stack = QStackedWidget()
        self._panel_stack.setStyleSheet("background:white;")

        # 变化检测面板
        self._detect_panels = QStackedWidget()
        self._detect_panels.addWidget(self._panel_single())
        self._detect_panels.addWidget(self._panel_enhanced())
        self._detect_panels.addWidget(self._panel_classify())
        self._detect_panels.addWidget(self._panel_vectorize())
        self._panel_stack.addWidget(self._detect_panels)

        # 批量处理面板
        self._batch_panels = QStackedWidget()
        self._batch_panels.addWidget(self._panel_csv())
        self._batch_panels.addWidget(self._panel_folder())
        self._batch_panels.addWidget(self._panel_dashboard())
        self._batch_panels.addWidget(self._panel_report())
        self._batch_panels.addWidget(self._panel_pipeline())
        self._panel_stack.addWidget(self._batch_panels)

        # 结果工具面板
        self._result_panels = QStackedWidget()
        self._result_panels.addWidget(self._panel_preview())
        self._result_panels.addWidget(self._panel_table())
        self._result_panels.addWidget(self._panel_thematic())
        self._result_panels.addWidget(self._panel_export())
        self._panel_stack.addWidget(self._result_panels)

        splitter.addWidget(self._panel_stack)

        # 下方: 日志
        log_w = QWidget()
        log_w.setStyleSheet("background:white;")
        ll = QVBoxLayout(log_w); ll.setContentsMargins(12, 6, 12, 6); ll.setSpacing(4)
        hh = QHBoxLayout()
        hh.addWidget(QLabel("日志"))
        hh.addStretch()
        b = QPushButton("清空"); b.clicked.connect(self._clr_log)
        b.setStyleSheet("QPushButton{background:transparent;border:1px solid #c8ced6;"
            "border-radius:3px;padding:3px 12px;color:#5a6a7a;}"
            "QPushButton:hover{background:#e8ecf2;}")
        hh.addWidget(b); ll.addLayout(hh)
        self._log_edit = QTextEdit(); self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet("QTextEdit{background:#f8f9fb;border:1px solid #e8ecf2;"
            "border-radius:3px;font-family:Consolas,monospace;font-size:12px;color:#2a3a4a;}")
        ll.addWidget(self._log_edit)
        self._progress = QProgressBar(); self._progress.setVisible(False)
        self._progress.setMaximumHeight(4); ll.addWidget(self._progress)
        splitter.addWidget(log_w)
        splitter.setSizes([520, 180])
        self.setCentralWidget(splitter)

    # ── 状态栏 ──
    def _build_statusbar(self):
        self._status = self.statusBar()
        self._status_lbl = QLabel("就绪")
        self._status_lbl.setStyleSheet("color:#5a6a7a;padding:2px 8px;")
        self._status.addWidget(self._status_lbl)

    # ═══════════════════════════════════════════════════
    # 变化检测 — 单次检测
    # ═══════════════════════════════════════════════════
    def _panel_single(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("输入参数"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._det_before = QLineEdit(); self._det_after = QLineEdit()
        self._det_out = QLineEdit("result.udbx")
        gl.addLayout(self._row("前期影像", self._det_before,
            lambda: self._open_file(self._det_before, "影像 (*.tif *.img)")))
        gl.addLayout(self._row("后期影像", self._det_after,
            lambda: self._open_file(self._det_after, "影像 (*.tif *.img)")))
        gl.addLayout(self._row("输出路径", self._det_out,
            lambda: self._open_save(self._det_out, "UDBX (*.udbx);;TIFF (*.tif)")))
        ly.addWidget(g)
        # 前后期影像缩略图预览
        pv = QHBoxLayout(); pv.setSpacing(10); pv.addStretch()
        self._det_pv_b = self._thumb_label("前期影像预览"); pv.addWidget(self._det_pv_b)
        self._det_pv_a = self._thumb_label("后期影像预览"); pv.addWidget(self._det_pv_a)
        pv.addStretch(); ly.addLayout(pv)
        ly.addStretch()
        self._det_before.textChanged.connect(self._update_det_preview)
        self._det_after.textChanged.connect(self._update_det_preview)
        return w

    # ── 增强检测 ──
    def _panel_enhanced(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("输入参数"); gl = QVBoxLayout(g); gl.setSpacing(8)
        # 增强面板使用独立的字段实例，避免与单次面板共用 widget 导致被重新父化（旧 bug）
        self._enh_before = QLineEdit(); self._enh_after = QLineEdit()
        self._enh_out = QLineEdit("result.udbx")
        gl.addLayout(self._row("前期影像", self._enh_before,
            lambda: self._open_file(self._enh_before, "影像 (*.tif *.img)")))
        gl.addLayout(self._row("后期影像", self._enh_after,
            lambda: self._open_file(self._enh_after, "影像 (*.tif *.img)")))
        gl.addLayout(self._row("输出路径", self._enh_out,
            lambda: self._open_save(self._enh_out, "UDBX (*.udbx);;TIFF (*.tif)")))
        opts = QHBoxLayout()
        self._enh_cls = QCheckBox("变化类型分类"); self._enh_cls.setChecked(True); opts.addWidget(self._enh_cls)
        opts.addWidget(QLabel("最小面积(m²)"))
        self._enh_ma = QLineEdit("0"); self._enh_ma.setFixedWidth(80); opts.addWidget(self._enh_ma)
        opts.addStretch(); gl.addLayout(opts)
        ly.addWidget(g)
        # 前后期影像缩略图预览
        pv = QHBoxLayout(); pv.setSpacing(10); pv.addStretch()
        self._enh_pv_b = self._thumb_label("前期影像预览"); pv.addWidget(self._enh_pv_b)
        self._enh_pv_a = self._thumb_label("后期影像预览"); pv.addWidget(self._enh_pv_a)
        pv.addStretch(); ly.addLayout(pv)
        ly.addStretch()
        self._enh_before.textChanged.connect(self._update_enh_preview)
        self._enh_after.textChanged.connect(self._update_enh_preview)
        # 与单次面板字段双向同步（setText 同值不触发 textChanged，不会死循环）
        self._link_fields(self._det_before, self._enh_before)
        self._link_fields(self._det_after, self._enh_after)
        self._link_fields(self._det_out, self._enh_out)
        return w

    # ── 地物分类 ──
    def _panel_classify(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("输入参数"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._cls_img = QLineEdit(); self._cls_out = QLineEdit("classify.tif")
        gl.addLayout(self._row("输入影像", self._cls_img,
            lambda: self._open_file(self._cls_img, "影像 (*.tif *.img)")))
        gl.addLayout(self._row("分类输出", self._cls_out,
            lambda: self._open_save(self._cls_out, "GeoTIFF (*.tif)")))
        ly.addWidget(g); ly.addStretch(); return w

    # ── 转矢量 ──
    def _panel_vectorize(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("输入参数"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._vec_in = QLineEdit(); self._vec_out = QLineEdit("vectorize.udbx")
        gl.addLayout(self._row("输入栅格", self._vec_in,
            lambda: self._open_file(self._vec_in, "GeoTIFF (*.tif)")))
        gl.addLayout(self._row("输出矢量", self._vec_out,
            lambda: self._open_save(self._vec_out, "UDBX (*.udbx)")))
        ly.addWidget(g); ly.addStretch(); return w

    # ═══════════════════════════════════════════════════
    # 批量处理面板
    # ═══════════════════════════════════════════════════
    def _panel_csv(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("CSV 批量处理"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._csv_p = QLineEdit()
        gl.addLayout(self._row("任务清单", self._csv_p,
            lambda: self._open_file(self._csv_p, "CSV (*.csv)")))
        gl.addWidget(QLabel("格式: 前期影像, 后期影像, 输出路径"))
        ly.addWidget(g); ly.addStretch(); return w

    def _panel_folder(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("文件夹批量处理"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._fld_p = QLineEdit(); self._fld_mode = QComboBox()
        self._fld_mode.addItems(["subdirs", "pattern", "pairs_file"])
        gl.addLayout(self._row("数据目录", self._fld_p, lambda: self._dir(self._fld_p)))
        hr = QHBoxLayout(); hr.addWidget(QLabel("配对模式")); hr.addWidget(self._fld_mode, 1)
        gl.addLayout(hr)
        gl.addWidget(QLabel("subdirs: 目录/before/*.tif + 目录/after/*.tif"))
        self._fld_cls = QCheckBox("变化分类"); self._fld_cls.setChecked(True); gl.addWidget(self._fld_cls)
        ly.addWidget(g); ly.addStretch(); return w

    def _panel_dashboard(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("任务仪表盘"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._dash_tbl = QTableWidget(0, 6)
        self._dash_tbl.setHorizontalHeaderLabels(["任务ID", "前期", "后期", "状态", "耗时", "变化统计"])
        self._dash_tbl.horizontalHeader().setStretchLastSection(True)
        self._dash_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._dash_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        gl.addWidget(self._dash_tbl)
        sr = QHBoxLayout()
        self._dash_total = QLabel("总计: --"); sr.addWidget(self._dash_total)
        self._dash_ok = QLabel("成功: --"); self._dash_ok.setStyleSheet("color:#22a65e;"); sr.addWidget(self._dash_ok)
        self._dash_fail = QLabel("失败: --"); self._dash_fail.setStyleSheet("color:#e8544a;"); sr.addWidget(self._dash_fail)
        sr.addStretch(); gl.addLayout(sr)
        ly.addWidget(g); ly.addStretch()
        self._log("[接口] 任务仪表盘 — 等待接入", "dim"); return w

    def _panel_report(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("统计报告"); gl = QVBoxLayout(g); gl.setSpacing(10)
        self._rpt_xl = QLineEdit(); self._rpt_title = QLineEdit("城市变化检测统计报告")
        gl.addLayout(self._row("统计 Excel", self._rpt_xl, lambda: self._open_file(self._rpt_xl, "Excel (*.xlsx)")))
        gl.addLayout(self._row("报告标题", self._rpt_title, lambda: None))
        br = QHBoxLayout(); br.setSpacing(8)
        for t in ["生成Excel", "生成Word", "导出PDF", "生成图表"]:
            b = QPushButton(t); b.setStyleSheet("QPushButton{background:#e8ecf2;border:1px solid #c8ced6;"
                "border-radius:3px;padding:6px 16px;}QPushButton:hover{background:#d5dce6;}")
            b.clicked.connect(lambda _, tt=t: self._r_report(tt))
            br.addWidget(b)
        br.addStretch(); gl.addLayout(br)
        ly.addWidget(g); ly.addStretch()
        self._log("统计报告 — 基于批量处理生成的统计 Excel", "dim"); return w

    def _panel_pipeline(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("完整流水线（批量检测 → 统计 → 报告）"); gl = QVBoxLayout(g); gl.setSpacing(8)
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace("\\", "/")
        self._pipe_csv = QLineEdit(_root + "/output/pipeline/batch_tasks.csv")
        self._pipe_cfg = QLineEdit(_root + "/output/pipeline/batch_config.json")
        self._pipe_dir = QLineEdit(_root + "/output/pipeline/run")
        self._pipe_title = QLineEdit("城市变化检测统计报告")
        gl.addLayout(self._row("任务清单", self._pipe_csv, lambda: self._open_file(self._pipe_csv, "CSV (*.csv)")))
        gl.addLayout(self._row("配置文件", self._pipe_cfg, lambda: self._open_file(self._pipe_cfg, "JSON (*.json)")))
        gl.addLayout(self._row("输出目录", self._pipe_dir, lambda: self._dir(self._pipe_dir)))
        gl.addLayout(self._row("报告标题", self._pipe_title, lambda: None))
        gl.addWidget(QLabel("任务清单列：task_id, before_image, after_image, model_name, result_type(tif/udbx), gpu_id, output_path, max_retry, enabled"))
        ly.addWidget(g); ly.addStretch()
        self._log("完整流水线 — 一键跑完 批量检测→统计Excel→Word/PDF/图表", "dim"); return w

    # ═══════════════════════════════════════════════════
    # 结果工具面板
    # ═══════════════════════════════════════════════════
    def _panel_preview(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("影像对比"); gl = QVBoxLayout(g); gl.setSpacing(8)
        grid = QGridLayout(); grid.setSpacing(6)
        for c, t in enumerate(["前期(T1)", "后期(T2)", "变化叠加"]):
            f = QFrame(); f.setStyleSheet("background:#f0f2f5;border:1px dashed #c0c8d2;border-radius:4px;min-height:200px;")
            fl = QVBoxLayout(f); fl.setAlignment(Qt.AlignCenter); fl.addWidget(QLabel(f"📷 {t}", alignment=Qt.AlignCenter))
            grid.addWidget(f, 0, c)
        gl.addLayout(grid)
        self._pv_b = QLineEdit(); self._pv_a = QLineEdit(); self._pv_r = QLineEdit()
        cr = QHBoxLayout()
        cr.addWidget(QLabel("T1:")); cr.addWidget(self._pv_b, 1)
        cr.addWidget(QLabel("T2:")); cr.addWidget(self._pv_a, 1)
        cr.addWidget(QLabel("结果:")); cr.addWidget(self._pv_r, 1)
        gl.addLayout(cr); ly.addWidget(g); ly.addStretch()
        self._log("影像对比 — 选择 T1/T2 后点击运行", "dim"); return w

    def _panel_table(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("属性浏览"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._tbl_udbx = QLineEdit(); self._tbl_ds = QComboBox()
        self._tbl_ds.addItems(["change_polygons", "vector_result"])
        sr = QHBoxLayout(); sr.addWidget(QLabel("数据源")); sr.addWidget(self._tbl_udbx, 1)
        sr.addWidget(QLabel("数据集")); sr.addWidget(self._tbl_ds); gl.addLayout(sr)
        self._tbl_table = QTableWidget(0, 5)
        self._tbl_table.setHorizontalHeaderLabels(["SmID", "变化类型", "面积(m²)", "周长(m)", "备注"])
        self._tbl_table.horizontalHeader().setStretchLastSection(True)
        self._tbl_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._tbl_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        gl.addWidget(self._tbl_table)
        ly.addWidget(g); ly.addStretch()
        self._log("属性浏览 — 选择数据源后点击运行", "dim"); return w

    def _panel_thematic(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("专题图"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._thm_udbx = QLineEdit(); self._thm_out = QLineEdit(os.path.join(_PROJECT_ROOT, "thematic_map", "专题图.png"))
        gl.addLayout(self._row("数据源", self._thm_udbx,
            lambda: self._open_file(self._thm_udbx, "UDBX (*.udbx)")))
        gl.addLayout(self._row("输出图片", self._thm_out,
            lambda: self._open_save(self._thm_out, "PNG (*.png)")))
        ly.addWidget(g); ly.addStretch(); return w

    def _panel_export(self):
        w = QWidget(); ly = QVBoxLayout(w); ly.setContentsMargins(20, 16, 20, 16); ly.setSpacing(10)
        g = QGroupBox("多格式导出"); gl = QVBoxLayout(g); gl.setSpacing(8)
        self._exp_udbx = QLineEdit(); self._exp_dir = QLineEdit()
        gl.addLayout(self._row("数据源", self._exp_udbx,
            lambda: self._open_file(self._exp_udbx, "UDBX (*.udbx)")))
        gl.addLayout(self._row("输出目录", self._exp_dir, lambda: self._dir(self._exp_dir)))
        g2 = QGroupBox("格式"); gl2 = QGridLayout(g2); gl2.setSpacing(8)
        self._exp_geojson = QCheckBox("GeoJSON"); self._exp_geojson.setChecked(True)
        self._exp_shp = QCheckBox("Shapefile"); self._exp_shp.setChecked(True)
        self._exp_kml = QCheckBox("KML")
        self._exp_csv = QCheckBox("CSV属性表")
        self._exp_tif = QCheckBox("GeoTIFF掩膜")
        self._exp_png = QCheckBox("PNG截图")
        gl2.addWidget(self._exp_geojson, 0, 0); gl2.addWidget(self._exp_shp, 0, 1)
        gl2.addWidget(self._exp_kml, 1, 0); gl2.addWidget(self._exp_csv, 1, 1)
        gl2.addWidget(self._exp_tif, 2, 0); gl2.addWidget(self._exp_png, 2, 1)
        gl.addWidget(g2); ly.addWidget(g); ly.addStretch()
        self._log("[接口] 导出 — 等待接入", "dim"); return w

    # ═══════════════════════════════════════════════════
    # 切换
    # ═══════════════════════════════════════════════════
    def _switch_panel(self, card, sub):
        self._mode = card
        self._panel_stack.setCurrentIndex(card)
        if card == M_DETECT:
            self._sub_detect = sub; self._detect_panels.setCurrentIndex(sub)
            self._tb_run.setText(["▶ 检测", "▶ 增强检测", "▶ 分类", "▶ 转矢量"][sub])
        elif card == M_BATCH:
            self._sub_batch = sub; self._batch_panels.setCurrentIndex(sub)
            self._tb_run.setText(["▶ CSV批量", "▶ 文件夹批量", "", "", "▶ 运行流水线"][sub])
            self._tb_run.setVisible(sub < 2 or sub == 4)
        else:
            self._sub_result = sub; self._result_panels.setCurrentIndex(sub)
            self._tb_run.setText(["▶ 加载预览", "▶ 加载属性", "▶ 专题图", "▶ 导出"][sub])

    # ═══════════════════════════════════════════════════
    # 运行
    # ═══════════════════════════════════════════════════
    def _run(self):
        if self._mode == M_DETECT:
            {0: self._r_single, 1: self._r_enhanced,
             2: self._r_classify, 3: self._r_vectorize}[self._sub_detect]()
        elif self._mode == M_BATCH:
            {0: self._r_csv, 1: self._r_folder, 4: self._r_pipeline}[self._sub_batch]()
        elif self._mode == M_RESULT:
            {0: self._r_preview, 1: self._r_table,
             2: self._r_thematic, 3: self._r_export}.get(self._sub_result, lambda: self._log("接口预留 — 等待接入", "info"))()

    def _start(self, name):
        self._tb_run.setEnabled(False); self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status_lbl.setText(f"● {name}中..."); self._status_lbl.setStyleSheet("color:#4a7cf7;")

    def _done(self, ok, name):
        self._tb_run.setEnabled(True); self._progress.setVisible(False)
        s = f"● {name}{'完成' if ok else '失败'}"; c = "#22a65e" if ok else "#e8544a"
        self._status_lbl.setText(s); self._status_lbl.setStyleSheet(f"color:{c};")

    # ── 单次 ──
    def _r_single(self):
        b = self._det_before.text().strip(); a = self._det_after.text().strip()
        o = self._det_out.text().strip() or "result.udbx"
        if not b or not a: return QMessageBox.warning(self, "参数", "请选择前后期影像")
        m = self._tb_model.currentText(); g = self._gpu(); f = self._tb_fmt.currentText()
        self._start("单次检测"); self._log(f"前期: {b}\n后期: {a}\n输出: {o}", "dim")
        def t():
            ok = run_single_inference(b, a, o, model_key=m, gpu=g, batch_size=1,
                offset=None, result_type="grid", out_dataset_name="predict_change", out_format=f)
            if ok: _remember_last_params(b, a, o, m, g, f)
            return ok
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "单次检测")); self._worker.start()

    # ── 增强 ──
    def _r_enhanced(self):
        b = self._enh_before.text().strip(); a = self._enh_after.text().strip()
        o = self._enh_out.text().strip() or "result.udbx"
        if not b or not a: return QMessageBox.warning(self, "参数", "请选择前后期影像")
        m = self._tb_model.currentText(); g = self._gpu(); f = self._tb_fmt.currentText()
        cls = self._enh_cls.isChecked()
        try: ma = float(self._enh_ma.text())
        except ValueError: ma = 0
        # 增强检测同时输出栅格+矢量，仅支持 UDBX：格式选 tif 或输出后缀非 .udbx 时提示并纠正
        fix_msg = []
        if f != "udbx":
            self._tb_fmt.setCurrentText("udbx"); f = "udbx"
            fix_msg.append("输出格式已自动切换为 udbx")
        if o.lower().endswith((".tif", ".tiff")):
            o = os.path.splitext(o)[0] + ".udbx"; self._enh_out.setText(o)
            fix_msg.append("输出路径后缀已改为 .udbx")
        if fix_msg:
            QMessageBox.information(self, "输出格式提示",
                "增强检测会同时输出变化栅格与矢量多边形，只能保存为 UDBX 格式。\n\n"
                + "\n".join("• " + x for x in fix_msg))
        self._start("增强检测"); self._log(f"增强检测 | 分类:{cls} 最小面积:{ma}m²", "dim")
        def t(): return run_enhanced_inference(b, a, o, model_key=m, gpu=g,
            classify=cls, min_change_area=ma, smooth=True, batch_size=1, offset=None, out_format=f)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "增强检测")); self._worker.start()

    # ── 分类 ──
    def _r_classify(self):
        img = self._cls_img.text().strip(); out = self._cls_out.text().strip()
        if not img: return QMessageBox.warning(self, "参数", "请选择输入影像")
        if not out: out = "classify.tif"; self._cls_out.setText(out)
        g = self._gpu(); self._start("地物分类")
        def t(): return run_classify(img, out, model_key="landcover", gpu=g, batch_size=1, offset=128)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "分类")); self._worker.start()

    # ── 矢量 ──
    def _r_vectorize(self):
        r = self._vec_in.text().strip(); v = self._vec_out.text().strip()
        if not r: return QMessageBox.warning(self, "参数", "请选择输入栅格")
        if not os.path.exists(r): return QMessageBox.warning(self, "文件", f"找不到: {r}")
        self._start("转矢量")
        def t(): return run_vectorize(r, v, min_area=0, simplify_tolerance=0)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "转矢量")); self._worker.start()

    # ── CSV批量 ──
    def _r_csv(self):
        csv = self._csv_p.text().strip()
        if not csv: return QMessageBox.warning(self, "参数", "请选择CSV")
        m = self._tb_model.currentText(); g = self._gpu(); f = self._tb_fmt.currentText()
        self._start("CSV批量")
        def t(): return run_batch_inference(csv, model_key=m, gpu=g,
            batch_size=1, offset=None, result_type="grid", out_format=f)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "CSV批量")); self._worker.start()

    # ── 文件夹批量 ──
    def _r_folder(self):
        folder = self._fld_p.text().strip()
        if not folder: return QMessageBox.warning(self, "参数", "请选择目录")
        mode = self._fld_mode.currentText(); m = self._tb_model.currentText()
        g = self._gpu(); f = self._tb_fmt.currentText(); cls = self._fld_cls.isChecked()
        self._start("文件夹批量")
        def t(): return run_folder_batch(folder=folder, mode=mode, model_key=m,
            gpu=g, classify=cls, min_change_area=0, out_format=f, output_dir=None)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "文件夹批量")); self._worker.start()

    # ── 完整流水线 ──
    def _r_pipeline(self):
        csv = self._pipe_csv.text().strip(); cfg = self._pipe_cfg.text().strip()
        out = self._pipe_dir.text().strip(); title = self._pipe_title.text().strip() or "城市变化检测统计报告"
        if not csv or not os.path.exists(csv): return QMessageBox.warning(self, "参数", "请选择任务清单 CSV")
        if not cfg or not os.path.exists(cfg): return QMessageBox.warning(self, "参数", "请选择配置文件 JSON")
        if not out: return QMessageBox.warning(self, "参数", "请选择输出目录")
        self._start("完整流水线")
        self._log(f"任务清单: {csv}\n配置文件: {cfg}\n输出目录: {out}\n报告标题: {title}", "dim")
        self._bw = BatchWorker(csv, cfg, out, title)
        self._bw.log.connect(self._log)
        self._bw.result.connect(self._on_pipeline_result)
        self._bw.done.connect(lambda ok: self._done(ok, "完整流水线"))
        self._bw.start()

    def _on_pipeline_result(self, r):
        if not isinstance(r, dict):
            return
        stats = r.get("statistics") or {}
        excel = stats.get("excel_path") or ""
        if excel and os.path.exists(excel):
            self._rpt_xl.setText(excel)
            self._log(f"统计 Excel 已生成: {excel}", "ok")
        batch = r.get("batch_detection") or {}
        status_csv = batch.get("status_table_path") or ""
        if status_csv:
            self._fill_dashboard(status_csv)
        rep = r.get("report") or {}
        if isinstance(rep, dict):
            if rep.get("word_path"): self._log(f"Word 报告: {rep.get('word_path')}", "dim")
            if rep.get("pdf_path"): self._log(f"PDF: {rep.get('pdf_path')}", "dim")
            if rep.get("chart_dir"): self._log(f"图表目录: {rep.get('chart_dir')}", "dim")
        if r.get("output_dir"): self._log(f"流水线输出目录: {r.get('output_dir')}", "dim")

    def _fill_dashboard(self, status_csv):
        import csv as _csv
        if not status_csv or not os.path.exists(status_csv):
            return
        try:
            with open(status_csv, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(_csv.DictReader(f))
        except Exception as e:
            self._log(f"读取状态表失败: {e}", "error")
            return
        trans = {"success": "成功", "failed": "失败", "running": "进行中",
                 "pending": "待处理", "skipped": "跳过"}
        tbl = self._dash_tbl
        tbl.clear(); tbl.setRowCount(len(rows)); tbl.setColumnCount(6)
        tbl.setHorizontalHeaderLabels(["任务ID", "前期", "后期", "状态", "耗时", "变化统计"])
        n_ok = n_fail = 0
        for r, row in enumerate(rows):
            status = str(row.get("status", ""))
            if status == "success": n_ok += 1
            elif status == "failed": n_fail += 1
            dur = row.get("duration_seconds", "") or ""
            try:
                dur = f"{float(dur):.1f}s"
            except (TypeError, ValueError):
                pass
            size = row.get("output_size_bytes", "") or ""
            try:
                size = f"{int(size) / 1048576:.1f}MB" if int(size) > 0 else "—"
            except (TypeError, ValueError):
                size = "—"
            vals = [row.get("task_id", ""), row.get("before_image", ""),
                    row.get("after_image", ""), trans.get(status, status), dur, size]
            for c, v in enumerate(vals):
                tbl.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._dash_total.setText(f"总计: {len(rows)}")
        self._dash_ok.setText(f"成功: {n_ok}")
        self._dash_fail.setText(f"失败: {n_fail}")

    # ── 专题图 ──
    def _r_thematic(self):
        u = self._thm_udbx.text().strip(); o = self._thm_out.text().strip()
        if not u or not os.path.exists(u): return QMessageBox.warning(self, "参数", "请选择UDBX")
        if not o: o = "专题图.png"; self._thm_out.setText(o)
        self._start("专题图"); self._log(f"数据源: {u}\n输出: {o}", "dim")
        datasets = ["vector_result", "change_polygons", "binclassify_result", "objdetect_result"]
        def t():
            for ds in datasets:
                self._log(f"尝试数据集: {ds}...", "dim")
                if generate_thematic_map(u, ds, o): return True
            return False
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "专题图")); self._worker.start()

    # ── 多格式导出 ──
    def _r_export(self):
        u = self._exp_udbx.text().strip(); d = self._exp_dir.text().strip()
        if not u or not os.path.exists(u): return QMessageBox.warning(self, "参数", "请选择UDBX数据源")
        if not d: return QMessageBox.warning(self, "参数", "请选择输出目录")
        fmts = []
        for cb, name in [(self._exp_geojson, "geojson"), (self._exp_shp, "shp"),
                         (self._exp_kml, "kml"), (self._exp_csv, "csv"),
                         (self._exp_tif, "tif")]:
            if cb.isChecked():
                fmts.append(name)
        if not fmts:
            return QMessageBox.warning(self, "参数", "请至少勾选一种导出格式")
        self._start("导出"); self._log(f"数据源: {u}\n输出目录: {d}\n格式: {', '.join(fmts)}", "dim")
        def t(): return export_vector_multiformat(u, d, fmts)
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, "导出")); self._worker.start()

    # ── 属性浏览 ──
    def _r_table(self):
        u = self._tbl_udbx.text().strip()
        if not u or not os.path.exists(u): return QMessageBox.warning(self, "参数", "请选择UDBX数据源")
        ds_name = self._tbl_ds.currentText().strip()
        self._start("加载属性")
        self._log(f"数据源: {u}\n数据集: {ds_name}", "dim")
        def t():
            from iobjectspy import DatasourceConnectionInfo, Workspace, DatasetVector
            ws = Workspace(); conn = DatasourceConnectionInfo()
            conn.set_server(u); conn.set_driver("UDBX")
            try:
                ds = ws.open_datasource(conn)
                vec = None
                for d in ds.datasets:
                    if d.name == ds_name and isinstance(d, DatasetVector):
                        vec = d; break
                if vec is None:
                    vec = next((d for d in ds.datasets if isinstance(d, DatasetVector)), None)
                if vec is None:
                    print("[错误] 未找到矢量数据集")
                    return None
                fields = [f.name for f in vec.field_infos]
                rows = []
                rd = vec.get_recordset(); rd.move_first()
                n = 0
                while not rd.is_eof() and n < 500:
                    rows.append([rd.get_value(f) for f in fields])
                    rd.move_next(); n += 1
                print(f"[OK] 读取 {len(rows)} 条记录，字段: {fields}")
                return {"rows": rows, "fields": fields}
            finally:
                ws.close()
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.result.connect(self._on_table_result)
        self._worker.done.connect(lambda ok: self._done(ok, "属性浏览")); self._worker.start()

    def _on_table_result(self, data):
        if not isinstance(data, dict) or "rows" not in data:
            return
        self._fill_table(data["rows"], data["fields"])

    def _fill_table(self, rows, fields):
        tbl = self._tbl_table
        tbl.clear(); tbl.setColumnCount(len(fields)); tbl.setHorizontalHeaderLabels(fields)
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem("" if v is None else str(v))
                tbl.setItem(r, c, it)

    # ── 影像对比 ──
    def _r_preview(self):
        b = self._pv_b.text().strip(); a = self._pv_a.text().strip()
        if not b and not a:
            return QMessageBox.warning(self, "参数", "请至少选择 T1 或 T2 影像路径")
        dlg = QDialog(self); dlg.setWindowTitle("影像对比")
        dlg.setMinimumSize(760, 400)
        lay = QVBoxLayout(dlg); row = QHBoxLayout()
        for title, path in [("前期 T1", b), ("后期 T2", a)]:
            col = QVBoxLayout(); col.addWidget(QLabel(title, alignment=Qt.AlignCenter))
            pix = self._load_raster_thumb(path, max_w=350, max_h=290)
            lb = QLabel(); lb.setAlignment(Qt.AlignCenter)
            lb.setFixedSize(350, 290)
            if pix is None:
                lb.setText("（无法加载影像）"); lb.setStyleSheet("color:#7a8a9a;border:1px dashed #c0c8d2;border-radius:4px;")
            else:
                lb.setPixmap(pix); lb.setStyleSheet("border:1px solid #dce3ec;border-radius:4px;")
            col.addWidget(lb); row.addLayout(col)
        lay.addLayout(row)
        btn = QPushButton("关闭"); btn.clicked.connect(dlg.accept)
        bwrap = QHBoxLayout(); bwrap.addStretch(); bwrap.addWidget(btn)
        lay.addLayout(bwrap)
        dlg.exec_()

    # ── 统计报告 ──（基于 li_batch_api 的统计 Excel 生成 Word/PDF/图表）
    def _r_report(self, kind):
        xl = self._rpt_xl.text().strip()
        title = self._rpt_title.text().strip() or "城市变化检测统计报告"
        if kind == "生成Excel":
            # 统计 Excel 由批量处理流水线（run_full_pipeline）自动生成，
            # 此面板仅做后续的 Word/PDF/图表生成。
            return QMessageBox.information(self, "生成Excel",
                "统计 Excel 在「批量处理」流水线中自动生成。\n"
                "请在「统计 Excel」处选择已生成的 .xlsx 文件，再生成 Word/PDF/图表。")
        if not xl or not os.path.exists(xl):
            return QMessageBox.warning(self, "参数", "请选择已生成的「统计 Excel」（.xlsx）")
        out_dir = os.path.dirname(xl) or "."
        self._start(f"生成{kind}")
        self._log(f"统计 Excel: {xl}\n报告标题: {title}\n输出目录: {out_dir}", "dim")
        def t():
            r = generate_report(xl, out_dir, title)
            ok = bool(r.get("success")) if isinstance(r, dict) else bool(r)
            if ok:
                print("[OK] 报告生成完成")
            else:
                st = r.get("status", "") if isinstance(r, dict) else r
                print(f"[错误] 报告生成未成功: {st}")
            return ok
        self._worker = Worker(t); self._worker.log.connect(self._log)
        self._worker.done.connect(lambda ok: self._done(ok, f"生成{kind}")); self._worker.start()

    # ═══════════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════════
    def _row(self, label, edit, browse_fn):
        r = QHBoxLayout(); r.setSpacing(8)
        r.addWidget(QLabel(label))
        r.addWidget(edit, 1)
        b = QPushButton("浏览")
        b.setStyleSheet("QPushButton{background:#4a7cf7;color:white;border:none;border-radius:3px;"
            "padding:5px 16px;}QPushButton:hover{background:#5a8cf7;}")
        b.clicked.connect(browse_fn); r.addWidget(b)
        return r

    def _open_file(self, e, f): p, _ = QFileDialog.getOpenFileName(self, "", "", f); p and e.setText(p)
    def _open_save(self, e, f): p, _ = QFileDialog.getSaveFileName(self, "", "", f); p and e.setText(p)
    def _dir(self, e): p = QFileDialog.getExistingDirectory(self, ""); p and e.setText(p)

    def _thumb_label(self, title):
        lb = QLabel(title)
        lb.setFixedSize(300, 170)
        lb.setAlignment(Qt.AlignCenter)
        lb.setWordWrap(True)
        lb.setStyleSheet("background:#1a2332;border:1px solid #2a3a4a;border-radius:6px;"
                         "color:#7a8a9a;font-size:12px;")
        return lb

    def _link_fields(self, a, b):
        a.textChanged.connect(b.setText)
        b.textChanged.connect(a.setText)

    def _load_raster_thumb(self, path, max_w=300, max_h=170):
        """读取栅格影像并下采样 + 2%~98% 拉伸为 QPixmap，失败返回 None。"""
        if not path or not os.path.exists(path):
            return None
        try:
            import numpy as np
            import rasterio
            with rasterio.open(path) as src:
                w, h = src.width, src.height
                f = max(1, int(max(w / max_w, h / max_h)))
                arr = src.read(out_shape=(src.count, max(1, h // f), max(1, w // f)))
            rgb = arr[:3].astype(np.float32)
            while rgb.shape[0] < 3:
                rgb = np.concatenate([rgb, rgb[-1:]], axis=0)
            for i in range(3):
                band = rgb[i]
                lo, hi = float(np.percentile(band, 2)), float(np.percentile(band, 98))
                rgb[i] = np.clip((band - lo) * 255.0 / max(hi - lo, 1.0), 0, 255)
            rgb = np.ascontiguousarray(rgb.transpose(1, 2, 0).astype(np.uint8))
            hh, ww, _ = rgb.shape
            img = QImage(rgb.data, ww, hh, 3 * ww, QImage.Format_RGB888)
            return QPixmap.fromImage(img).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            return None

    def _show_thumb(self, label, path, title):
        if not path:
            label.setText(title)
        else:
            pm = self._load_raster_thumb(path)
            if pm is not None:
                label.setPixmap(pm)
            else:
                label.setText("无法预览影像")

    def _update_det_preview(self):
        self._show_thumb(self._det_pv_b, self._det_before.text().strip(), "前期影像预览")
        self._show_thumb(self._det_pv_a, self._det_after.text().strip(), "后期影像预览")

    def _update_enh_preview(self):
        self._show_thumb(self._enh_pv_b, self._enh_before.text().strip(), "前期影像预览")
        self._show_thumb(self._enh_pv_a, self._enh_after.text().strip(), "后期影像预览")
    def _gpu(self):
        try: return int(self._tb_gpu.currentText().split("(")[0])
        except: return 0

    def _log(self, msg, tag="dim"):
        if not hasattr(self, '_log_edit'): return
        c = {"header":"#1a2332","dim":"#7a8a9a","ok":"#22a65e",
             "error":"#e8544a","warning":"#e8a838","info":"#4a7cf7"}.get(tag, "#7a8a9a")
        self._log_edit.append(f'<span style="color:{c};">{msg}</span>')
        cur = self._log_edit.textCursor(); cur.movePosition(QTextCursor.End)
        self._log_edit.setTextCursor(cur)

    def _clr_log(self): self._log_edit.clear()

    def _setup(self):
        dlg = SetupDialog(self)
        if dlg.exec_(): self._log("环境配置已更新", "ok")

    def _check(self):
        self._log("═"*40, "header"); self._log("环境自检中...", "info")
        self._start("环境自检")
        # 自检涉及 import torch / CUDA 初始化，耗时可能很长，必须放到后台线程，否则界面卡死
        self._worker = Worker(run_self_check)
        self._worker.log.connect(self._log)
        self._worker.done.connect(self._check_done)
        self._worker.start()

    def _check_done(self, ok):
        self._tb_run.setEnabled(True); self._progress.setVisible(False)
        self._status_lbl.setText("环境就绪" if ok else "配置有误")
        self._status_lbl.setStyleSheet("color:" + ("#22a65e" if ok else "#e8544a") + ";")

    def _load_params(self):
        try:
            lp = load_config().get("last_params", {})
            lp.get("before") and self._det_before.setText(lp["before"])
            lp.get("after") and self._det_after.setText(lp["after"])
            lp.get("out") and self._det_out.setText(lp["out"])
            if lp.get("model"):
                i = self._tb_model.findText(lp["model"])
                i >= 0 and self._tb_model.setCurrentIndex(i)
            if lp.get("gpu") is not None:
                i = self._tb_gpu.findText(str(lp["gpu"]))
                i >= 0 and self._tb_gpu.setCurrentIndex(i)
            if lp.get("out_format"):
                i = self._tb_fmt.findText(lp["out_format"])
                i >= 0 and self._tb_fmt.setCurrentIndex(i)
        except: pass

    def _on_model_chg(self):
        k = self._tb_model.currentText()
        self._log(AVAILABLE_MODELS.get(k, {}).get("description", ""), "dim")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    w = MainWindow(); w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
