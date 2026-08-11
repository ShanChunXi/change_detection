# -*- coding: utf-8 -*-
"""张硕岐单次变化检测入口适配器（含核心控制台输出捕获）。
支持智能路由：自动判断是地物分类还是变化检测。
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

_MODULE_CACHE: Dict[str, ModuleType] = {}


def _load_change_detection(module_dir: Path) -> ModuleType:
    module_dir = module_dir.resolve()
    source = module_dir / "change_detection.py"
    if not source.is_file():
        raise FileNotFoundError(f"张硕岐核心程序不存在：{source}")
    cache_key = str(source)
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("zhang_change_detection_v2", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建模块导入规范：{source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "run_single_inference", None)):
        raise AttributeError("change_detection.py 未提供可调用的 run_single_inference")
    _MODULE_CACHE[cache_key] = module
    return module


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _run_classification(before: Path, output: Path, task: Dict) -> Dict:
    """地物分类模式：分类 + 转矢量"""
    # 动态导入地物分类模块
    src_dir = Path(__file__).resolve().parent.parent.parent / "变化检测" / "src"
    sys.path.insert(0, str(src_dir))

    try:
        from classify_vectorize import run_classify, run_vectorize
    except ImportError:
        # 尝试从当前目录导入
        try:
            from classify_vectorize import run_classify, run_vectorize
        except ImportError:
            raise ImportError("无法导入 classify_vectorize.py，请确保它在 Python 路径中")

    tif_path = output.with_suffix(".tif")

    print(f"[地物分类] 输入影像: {before}")
    print(f"[地物分类] 输出栅格: {tif_path}")

    ok = run_classify(
        input_image=str(before),
        output_raster=str(tif_path),
        model_key="landcover",
        gpu=int(task.get("gpu_id", "0")),
        batch_size=1,
        offset=128,
    )
    if not ok:
        raise RuntimeError("地物分类失败")

    print(f"[栅格转矢量] 输入栅格: {tif_path}")
    print(f"[栅格转矢量] 输出矢量: {output}")

    ok = run_vectorize(
        input_raster=str(tif_path),
        output_vector=str(output),
        min_area=0,
        simplify_tolerance=0,
    )
    if not ok:
        raise RuntimeError("栅格转矢量失败")

    # 清理临时文件
    try:
        if tif_path.exists():
            os.remove(tif_path)
    except:
        pass

    return {
        "return_value": True,
        "output_exists": output.is_file(),
        "output_size_bytes": output.stat().st_size if output.is_file() else 0,
        "output_path": str(output),
        "core_output": "",
    }


def _run_change_detection(before: Path, after: Path, output: Path, task: Dict, config: Dict) -> Dict:
    """变化检测模式：调用张硕岐的 run_single_inference"""
    if not before.is_file():
        raise FileNotFoundError(f"前时相影像不存在：{before}")
    if not after.is_file():
        raise FileNotFoundError(f"后时相影像不存在：{after}")
    if before.stat().st_size <= 0:
        raise ValueError(f"前时相影像为空：{before}")
    if after.stat().st_size <= 0:
        raise ValueError(f"后时相影像为空：{after}")

    output_format = task.get("result_type", "").strip().lower()
    if output_format not in {"tif", "tiff", "udbx"}:
        raise ValueError(f"不支持的输出格式：{output_format}")
    if output_format == "tiff":
        output_format = "tif"
    expected_suffixes = {".tif", ".tiff"} if output_format == "tif" else {".udbx"}
    if output.suffix.lower() not in expected_suffixes:
        raise ValueError(
            f"输出扩展名与格式不符：result_type={output_format}, output={output}"
        )

    module = _load_change_detection(Path(str(config["zhang_module_work_copy"])))
    task_id = task.get("task_id", "task")
    dataset_name = "predict_change_" + "".join(
        char if char.isalnum() or char == "_" else "_" for char in task_id
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    captured = io.StringIO()
    with redirect_stdout(captured), redirect_stderr(captured):
        ok = module.run_single_inference(
            before_path=str(before),
            after_path=str(after),
            out_path=str(output),
            model_key=task.get("model_name", "building").strip(),
            gpu=int(task.get("gpu_id", "0")),
            batch_size=int(config.get("batch_size", 1)),
            offset=config.get("offset"),
            result_type=str(config.get("core_result_type", "grid")),
            out_dataset_name=dataset_name,
            out_format=output_format,
        )
    core_output = captured.getvalue().strip()
    if ok is not True:
        raise RuntimeError(
            "张硕岐 run_single_inference 返回 False"
            + (f"\n--- 核心输出 ---\n{core_output}" if core_output else "")
        )
    if not _nonempty_file(output):
        raise RuntimeError(f"推理返回成功，但输出不存在或为空：{output}")
    return {
        "return_value": True,
        "output_exists": True,
        "output_size_bytes": output.stat().st_size,
        "output_path": str(output),
        "core_output": core_output,
    }


def run_single_task(task: Dict[str, str], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    智能路由：自动判断是地物分类还是变化检测
    - 如果 after_image 存在且有效 → 变化检测
    - 如果 after_image 不存在或为空 → 地物分类
    """
    before = Path(task["before_image"])
    after = Path(task["after_image"])
    output = Path(task["output_path"])
    model_name = task.get("model_name", "building").strip().lower()

    output.parent.mkdir(parents=True, exist_ok=True)

    # 判断 after_image 是否有效
    after_str = str(after) if after else ""
    has_after = after_str.strip() and after.exists() and after.stat().st_size > 0

    # 如果 model_name 明确指定为 landcover，强制走地物分类
    if model_name == "landcover":
        has_after = False

    if has_after:
        # 模式1：变化检测（两张影像都存在）
        print(f"[自动路由] 检测到两张影像 → 变化检测模式")
        print(f"  前期: {before}")
        print(f"  后期: {after}")
        return _run_change_detection(before, after, output, task, config)
    else:
        # 模式2：地物分类（只有一张影像）
        print(f"[自动路由] 只有一张影像 → 地物分类模式")
        print(f"  输入: {before}")
        return _run_classification(before, output, task)


def inspect_interface(module_dir: str) -> Dict[str, Any]:
    module = _load_change_detection(Path(module_dir))
    return {
        "module_file": os.path.abspath(module.__file__),
        "entry_name": "run_single_inference",
        "entry_callable": callable(getattr(module, "run_single_inference", None)),
    }