# -*- coding: utf-8 -*-
"""
PyInstaller 运行时钩子 — 在导入 iobjectspy 前初始化 SuperMap Java 环境

PyInstaller 打包后在所有导入之前自动执行此脚本。
"""
import os
import sys

# 打包后 sys._MEIPASS 指向解压临时目录
if getattr(sys, 'frozen', False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

# --- 1. 设置 JAVA_HOME ---
_jre = os.path.join(_BASE, "jre1.8_x64")
if os.path.isdir(_jre):
    os.environ["JAVA_HOME"] = _jre

# --- 2. 设置 PATH (JRE bin + iObjects Bin) ---
_iobjects_bin = os.path.join(_BASE, "Bin")
_path_parts = []
if os.path.isdir(_jre):
    _path_parts.append(os.path.join(_jre, "bin"))
if os.path.isdir(_iobjects_bin):
    _path_parts.append(_iobjects_bin)
if _path_parts:
    os.environ["PATH"] = os.pathsep.join(_path_parts) + os.pathsep + os.environ.get("PATH", "")

# --- 3. 设置 iObjects Java 路径 ---
if os.path.isdir(_iobjects_bin):
    try:
        from iobjectspy import env
        env.set_iobjects_java_path(_iobjects_bin)
    except Exception:
        pass

print("[runtime hook] 环境初始化完成")
