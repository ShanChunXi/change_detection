# -*- coding: utf-8 -*-
"""
独立环境配置工具 — 仅依赖 PyQt5 + json，不碰 SuperMap
同学用任何装过 PyQt5 的 Python 即可打开
"""

import sys, os, json

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

FIELDS = [
    ("python_path",  "SuperMap Python 解释器",     "python.exe 的完整路径",  "file",
     "D:/supermap/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe"),
    ("java_home",    "iObjects Java JRE 目录",      "jre1.8_x64 所在的目录", "dir",
     "D:/supermap/supermap-iobjectsjava-2026-win-all/jre1.8_x64"),
    ("iobjects_bin", "iObjects Java Bin 目录",      "Bin 目录（含 34 个 JAR）", "dir",
     "D:/supermap/supermap-iobjectsjava-2026-win-all/Bin"),
    ("resources_ml", "ML 资源包目录",               "resources_ml 目录",       "dir",
     "D:/supermap/supermap-iobjectspy-resources_ml-2025u1/resources_ml"),
]

AUTO_PATTERNS = {
    "python_path":  ["/supermap/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe"],
    "java_home":    ["/supermap/supermap-iobjectsjava-2026-win-all/jre1.8_x64"],
    "iobjects_bin": ["/supermap/supermap-iobjectsjava-2026-win-all/Bin"],
    "resources_ml": ["/supermap/supermap-iobjectspy-resources_ml-2025u1/resources_ml"],
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return True


# ═══════════════════════════════════════════════════════════
# PyQt5 GUI
# ═══════════════════════════════════════════════════════════

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QMessageBox, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


class ConfigWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SuperMap 环境配置")
        self.setMinimumWidth(820)
        self.setMinimumHeight(520)
        self.entries = {}
        self.status_lbls = {}
        self._build()
        self._load()

    def _build(self):
        ly = QVBoxLayout(self); ly.setSpacing(12)
        ly.setContentsMargins(24, 20, 24, 20)

        # 标题
        t = QLabel("⚙ SuperMap 环境配置")
        t.setStyleSheet("font-size:22px;font-weight:bold;color:#1a2332;"); ly.addWidget(t)
        d = QLabel("配置后保存到 config.json，主程序启动时自动读取。")
        d.setStyleSheet("color:#5a6a7a;font-size:13px;"); d.setWordWrap(True); ly.addWidget(d)

        ly.addWidget(self._sep())

        # 表单
        for key, label, hint, ptype, placeholder in FIELDS:
            row = QHBoxLayout(); row.setSpacing(8)
            lbl = QLabel(label); lbl.setMinimumWidth(160)
            lbl.setStyleSheet("font-weight:bold;color:#2a3a4a;")
            row.addWidget(lbl)

            e = QLineEdit(); e.setPlaceholderText(placeholder)
            e.setStyleSheet("""
                QLineEdit{background:white;border:2px solid #d0d7de;border-radius:4px;
                padding:8px 12px;color:#1a2332;font-size:13px;}
                QLineEdit:focus{border-color:#4a7cf7;}""")
            self.entries[key] = e
            e.textChanged.connect(lambda _, k=key: self._check(k))
            row.addWidget(e, 1)

            b = QPushButton("浏览")
            b.setStyleSheet("""
                QPushButton{background:#4a7cf7;color:white;border:none;border-radius:4px;
                padding:8px 16px;font-weight:bold;}
                QPushButton:hover{background:#5a8cf7;}""")
            if ptype == "file":
                b.clicked.connect(lambda _, k=key: self._file(k))
            else:
                b.clicked.connect(lambda _, k=key: self._dir(k))
            row.addWidget(b)
            ly.addLayout(row)

            # 状态提示
            sl = QLabel("")
            sl.setStyleSheet("color:#5a6a7a;font-size:12px;padding-left:170px;")
            self.status_lbls[key] = sl; ly.addWidget(sl)

        ly.addSpacing(6)

        # 自动检测行
        ar = QHBoxLayout()
        ar.addWidget(QLabel("💡 标准路径可自动检测"))
        ar.addStretch()
        auto_btn = QPushButton("🔍 自动检测")
        auto_btn.setStyleSheet("""
            QPushButton{background:#4a7cf7;color:white;border:none;border-radius:4px;
            padding:10px 24px;font-weight:bold;font-size:14px;}
            QPushButton:hover{background:#5a8cf7;}""")
        auto_btn.clicked.connect(self._auto)
        ar.addWidget(auto_btn); ly.addLayout(ar)

        ly.addWidget(self._sep())

        # 底部按钮
        fr = QHBoxLayout(); fr.addStretch()
        fr.addWidget(QLabel("💡 启动后可直接双击 "), alignment=Qt.AlignRight)
        fr.addWidget(QLabel("change_detection.py ui", alignment=Qt.AlignLeft)
                     .setStyleSheet("font-weight:bold;color:#4a7cf7;"))
        fr.addStretch()
        cb = QPushButton("取消"); cb.setStyleSheet("""
            QPushButton{background:#e8ecf2;color:#2a3a4a;border:none;border-radius:4px;
            padding:10px 28px;font-weight:bold;}QPushButton:hover{background:#d5dce6;}""")
        cb.clicked.connect(self.close); fr.addWidget(cb)
        sb = QPushButton("💾 保存配置"); sb.setStyleSheet("""
            QPushButton{background:#22a65e;color:white;border:none;border-radius:4px;
            padding:10px 28px;font-weight:bold;font-size:14px;}
            QPushButton:hover{background:#28b86a;}""")
        sb.clicked.connect(self._save); fr.addWidget(sb)
        ly.addLayout(fr)

    def _sep(self):
        f = QFrame(); f.setFrameShape(QFrame.HLine)
        f.setStyleSheet("background:#dce3ec;max-height:1px;"); return f

    def _load(self):
        cfg = load_config()
        for k, e in self.entries.items():
            if cfg.get(k):
                e.setText(cfg[k])

    def _file(self, k):
        p, _ = QFileDialog.getOpenFileName(self, "选择文件", "",
            "可执行文件 (*.exe);;所有文件 (*.*)")
        if p: self.entries[k].setText(p.replace("\\", "/"))

    def _dir(self, k):
        p = QFileDialog.getExistingDirectory(self, "选择目录")
        if p: self.entries[k].setText(p.replace("\\", "/"))

    def _check(self, k):
        p = self.entries[k].text().strip()
        sl = self.status_lbls[k]
        if not p:
            sl.setText("尚未填写"); sl.setStyleSheet("color:#5a6a7a;font-size:12px;padding-left:170px;")
        elif os.path.exists(p):
            sl.setText("✅ 路径存在"); sl.setStyleSheet("color:#22a65e;font-size:12px;padding-left:170px;")
        else:
            sl.setText("❌ 路径不存在"); sl.setStyleSheet("color:#e8544a;font-size:12px;padding-left:170px;")

    def _auto(self):
        drives = ["F:/", "D:/", "E:/", "C:/"]
        cnt = 0
        for key, subs in AUTO_PATTERNS.items():
            found = False
            for d in drives:
                for s in subs:
                    fp = os.path.join(d, s.replace("/", os.sep))
                    if os.path.exists(fp):
                        self.entries[key].setText(fp.replace("\\", "/"))
                        cnt += 1; found = True; break
                if found: break
        QMessageBox.information(self, "自动检测",
            f"找到 {cnt} 个路径，请核对后保存。" if cnt else "未找到标准路径，请手动填写。")

    def _save(self):
        cfg = {}
        for k, e in self.entries.items():
            cfg[k] = e.text().strip()

        # 去重：只保存最后一个路径组件
        # 如果 python_path 是 F:/supermap/.../conda/python.exe，
        # 则自动推断 java_home / iobjects_bin / resources_ml（如果用户没填）
        pp = cfg.get("python_path", "")
        if pp:
            base = os.path.dirname(os.path.dirname(pp))  # .../conda → .../env根
            supermap_root = os.path.dirname(base)         # .../env根 → .../supermap安装根
            if not cfg.get("java_home"):
                guess = os.path.join(supermap_root, "java", "supermap-iobjectsjava-2026-win-all", "jre1.8_x64")
                if os.path.exists(guess): cfg["java_home"] = guess.replace("\\", "/")
            if not cfg.get("iobjects_bin"):
                guess = os.path.join(supermap_root, "java", "supermap-iobjectsjava-2026-win-all", "Bin")
                if os.path.exists(guess): cfg["iobjects_bin"] = guess.replace("\\", "/")
            if not cfg.get("resources_ml"):
                guess = os.path.join(supermap_root, "resources", "supermap-iobjectspy-resources_ml-2025u1", "resources_ml")
                if os.path.exists(guess): cfg["resources_ml"] = guess.replace("\\", "/")

        missing = [k for k, v in cfg.items() if not v]
        if missing:
            QMessageBox.warning(self, "不完整", f"以下路径未填写:\n{', '.join(missing)}")
            return

        try:
            existing = load_config()
            existing.update(cfg)
            save_config(existing)
            QMessageBox.information(self, "保存成功",
                f"✅ 配置已保存到:\n{CONFIG_PATH}\n\n可以启动主程序了。")
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    w = ConfigWindow(); w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
