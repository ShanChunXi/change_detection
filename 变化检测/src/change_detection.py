# -*- coding: utf-8 -*-
"""
================================================================================
 通用变化检测 — 参数化命令行工具
 第24届 SuperMap 杯高校 GIS 大赛 开发组
 负责：张硕岐
================================================================================

功能：
  基于 SuperMap iObjects Python 的遥感影像变化检测推理工具。
  通过 config.json 配置环境路径，可在不同电脑上使用。

首次使用：
  python change_detection.py setup    # 配置向导
  python change_detection.py check    # 环境自检

日常使用：
  python change_detection.py run --before 2020.tif --after 2024.tif --out result.udbx
  python change_detection.py menu     # 交互菜单
  python change_detection.py batch --csv tasks.csv
"""

import os
import sys
import json
import warnings
import argparse
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict

warnings.filterwarnings("ignore")

# 修复 Windows GBK 控制台下 print 无法编码的字符（✅/² 等）导致 UnicodeEncodeError 崩溃的问题
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# ============================================================================
# 0. 配置文件加载
# ============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, object] = {
    "java_home": "",
    "iobjects_bin": "",
    "resources_ml": "",
    "python_path": "",
    # --- 记忆功能：保存上次成功运行的参数，下次自动预填 ---
    "last_params": {
        "before": "",
        "after": "",
        "out": "result.udbx",
        "model": "building",
        "gpu": 0,
        "out_format": "udbx",
        "classify": True,
        "min_change_area": 0,
    },
    # --- 增强功能参数 ---
    "enhanced": {
        "classify_enabled": True,       # 是否启用变化类型分类
        "min_change_area": 0,           # 最小变化面积 (平方米), 0=不过滤
        "smooth_boundary": True,        # 矢量边界平滑
        "out_dataset_name": "change_polygons",  # 矢量输出数据集名
    },
    # --- 批量文件夹处理 ---
    "batch_folder": {
        "last_folder": "",              # 上次使用的文件夹
        "pair_mode": "subdirs",         # 配对模式: subdirs / pattern / pairs_file
        "output_dir": "",               # 默认输出目录
    },
}


def load_config() -> Dict[str, object]:
    """加载 config.json，不存在则返回默认空值。"""
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[警告] 配置文件读取失败: {e}")
        return dict(DEFAULT_CONFIG)

    result = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        if key in cfg and not key.startswith("_"):
            # 嵌套字典（如 last_params）：深度合并，保留默认结构
            if isinstance(DEFAULT_CONFIG[key], dict) and isinstance(cfg[key], dict):
                merged = dict(DEFAULT_CONFIG[key])
                merged.update(cfg[key])
                result[key] = merged
            else:
                result[key] = cfg[key]
    return result


def save_config(cfg: Dict[str, object]) -> bool:
    """保存配置到 config.json。"""
    # 只保留 DEFAULT_CONFIG 中定义的 key
    out = {}
    for key in DEFAULT_CONFIG:
        out[key] = cfg.get(key, DEFAULT_CONFIG[key])
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"[错误] 无法写入配置文件: {e}")
        return False


def _remember_last_params(before: str, after: str, out: str,
                         model: str, gpu: int, out_format: str,
                         classify: bool = True, min_change_area: float = 0):
    """将本次成功运行的参数写入 config.json，下次自动预填。"""
    cfg = load_config()
    cfg["last_params"] = {
        "before": before,
        "after": after,
        "out": out,
        "model": model,
        "gpu": gpu,
        "out_format": out_format,
        "classify": classify,
        "min_change_area": min_change_area,
    }
    save_config(cfg)


# ============================================================================
# 1. 环境设置
# ============================================================================

def setup_environment():
    """根据 config.json 设置 JAVA_HOME、PATH 和 iObjects Java 路径。"""
    cfg = load_config()

    java_home = cfg.get("java_home", "")
    iobjects_bin = cfg.get("iobjects_bin", "")

    if java_home:
        os.environ["JAVA_HOME"] = java_home
    if iobjects_bin:
        os.environ["PATH"] = (
            os.path.join(java_home, "bin") + ";" +
            iobjects_bin + ";" +
            os.environ.get("PATH", "")
        )
        try:
            from iobjectspy import env
            env.set_iobjects_java_path(iobjects_bin)
        except ImportError:
            pass


# 启动时自动加载配置
_config = load_config()

# -- 兼容旧版直接引用（从 config 读取） --
JAVA_HOME    = _config.get("java_home", "")
IOBJECTS_BIN = _config.get("iobjects_bin", "")
RESOURCES_ML = _config.get("resources_ml", "")
MODEL_DIR    = os.path.join(RESOURCES_ML, "model") if RESOURCES_ML else ""

# 初始化环境
if JAVA_HOME and IOBJECTS_BIN:
    setup_environment()


# ============================================================================
# 2. 可用模型注册表
# ============================================================================

AVAILABLE_MODELS = {
    "building": {
        "name": "建筑物变化检测 (SiamSFNet)",
        "filename": "general_cd_siamsfnet_building/general_cd_siamsfnet_building.sdm",
        "description": "通用建筑物变化检测模型，适用于城市扩张、违章建筑监测等场景。",
        "offset": 128,
    },
    "building-seg": {
        "name": "建筑物分割 (SegFormer)",
        "filename": "binary_cls_building_segformer/binary_cls_building_segformer.sdm",
        "description": "基于 SegFormer 的建筑物二分类分割模型，可用于单时相建筑物提取。",
        "offset": 128,
    },
    "landcover": {
        "name": "地物分类 (多类别)",
        "filename": "multi_cls_landcover/multi_cls_landcover.sdm",
        "description": "多类别土地利用/土地覆盖分类模型。",
        "offset": 128,
    },
}


def _resolve_model_path(filename: str) -> str:
    """根据 config 中的 resources_ml 动态解析模型完整路径。"""
    cfg = load_config()
    resources_ml = cfg.get("resources_ml", "")
    if not resources_ml:
        return ""
    model_dir = os.path.join(resources_ml, "model")
    return os.path.join(model_dir, filename.replace("/", os.sep))


def list_models():
    """打印所有可用模型信息。"""
    print()
    print("=" * 72)
    print("  可用模型列表")
    print("=" * 72)
    for key, info in AVAILABLE_MODELS.items():
        full_path = _resolve_model_path(info["filename"])
        exists = "[OK]" if os.path.exists(full_path) else "[MISSING]"
        print()
        print(f"  [{key}]  {info['name']}")
        print(f"         路径: {full_path}")
        print(f"         状态: {exists}")
        print(f"         说明: {info['description']}")
    print()
    print("-" * 72)
    print("  使用方式: python change_detection.py run --model <模型名> ...")
    print("  例如:     python change_detection.py run --model building ...")
    print("-" * 72)
    print()


def get_model_info(model_key: str) -> dict:
    """根据 key 获取模型信息。"""
    if model_key not in AVAILABLE_MODELS:
        print(f"\n[错误] 未知模型: '{model_key}'")
        print(f"可用模型: {', '.join(AVAILABLE_MODELS.keys())}")
        print("运行 'python change_detection.py models' 查看详情。\n")
        sys.exit(1)

    info = AVAILABLE_MODELS[model_key].copy()
    info["path"] = _resolve_model_path(info.pop("filename"))

    if not os.path.exists(info["path"]):
        print(f"\n[错误] 模型文件不存在: {info['path']}")
        print("请检查 config.json 中 resources_ml 路径是否正确。")
        print("或运行 'python change_detection.py setup' 重新配置。\n")
        sys.exit(1)
    return info


# ============================================================================
# 3. 核心推理逻辑
# ============================================================================

def _check_supermap_import() -> bool:
    """检查是否能导入 iobjectspy。"""
    try:
        from iobjectspy import env  # noqa: F401
        return True
    except ImportError:
        print("\n[错误] 无法导入 iobjectspy，请确认：")
        print("  1. 使用的是 SuperMap 自带的 Python 环境")
        print("  2. config.json 中的路径配置正确")
        print("  3. 运行 'python change_detection.py setup' 重新配置\n")
        return False


def run_single_inference(
    before_path: str,
    after_path: str,
    out_path: str,
    model_key: str = "building",
    gpu: int = 0,
    batch_size: int = 1,
    offset: int = None,
    result_type: str = "grid",
    out_dataset_name: str = "predict_change",
    out_format: str = "udbx",
) -> bool:
    """执行单次变化检测推理。"""
    # 校验输入
    if not os.path.exists(before_path):
        print(f"\n[错误] 前期影像不存在: {before_path}")
        return False
    if not os.path.exists(after_path):
        print(f"\n[错误] 后期影像不存在: {after_path}")
        return False

    model_info = get_model_info(model_key)
    if offset is None:
        offset = model_info.get("offset", 128)

    # 确保输出目录存在
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if not _check_supermap_import():
        return False

    try:
        from iobjectspy.ml.vision import ImageryInference

        # 打印信息
        print()
        print("=" * 60)
        print("  变化检测推理")
        print("=" * 60)
        print(f"  模型:       {model_info['name']}")
        print(f"  前期影像:   {before_path}")
        print(f"  后期影像:   {after_path}")
        print(f"  输出路径:   {out_path}")
        print(f"  输出格式:   {out_format}")
        print(f"  设备:       {'GPU ' + str(gpu) if gpu >= 0 else 'CPU'}")
        print(f"  Batch Size: {batch_size}")
        print(f"  Offset:     {offset}")
        print("=" * 60)
        print()

        gpus = [gpu] if gpu >= 0 else []

        print("[1/3] 正在加载模型...")
        model = ImageryInference(
            model_path=model_info["path"],
            gpus=gpus,
            batch_size=batch_size,
        )
        print("      模型加载完成。")

        print("[2/3] 正在执行变化检测推理...")
        start_time = datetime.now()

        if out_format == "tif":
            tmp_udbx = out_path.replace(".tif", ".udbx").replace(".tiff", ".udbx")
            try:
                model.general_changedet_infer(
                    input_data=before_path,
                    input_compare_data=after_path,
                    out_data=tmp_udbx,
                    out_dataset_name=out_dataset_name,
                    offset=offset,
                    result_type=result_type,
                )
            except PermissionError as _pe:
                # SuperMap 在结果写出后清理临时文件夹可能抛 PermissionError（临时 tif 仍被占用），
                # 此时结果其实已生成，按成功处理。
                if not os.path.exists(tmp_udbx):
                    raise
                print(f"  [提示] SuperMap 临时目录清理失败（{_pe}），结果已写出，继续。")
            print("      推理完成，正在转换为 GeoTIFF...")
            from iobjectspy import conversion, DatasourceConnectionInfo, Workspace
            _ws = Workspace()
            try:
                _conn = DatasourceConnectionInfo()
                _conn.set_server(tmp_udbx)
                _ds = _ws.open_datasource(_conn)
                _dt = _ds[out_dataset_name]
                conversion.export_to_tif(_dt, out_path)
            finally:
                _ws.close()
            try:
                os.remove(tmp_udbx)
            except Exception:
                pass
        else:
            try:
                model.general_changedet_infer(
                    input_data=before_path,
                    input_compare_data=after_path,
                    out_data=out_path,
                    out_dataset_name=out_dataset_name,
                    offset=offset,
                    result_type=result_type,
                )
            except PermissionError as _pe:
                # SuperMap 在结果写出后清理临时文件夹可能抛 PermissionError（临时 tif 仍被占用），
                # 此时结果其实已生成，按成功处理。
                if not os.path.exists(out_path):
                    raise
                print(f"  [提示] SuperMap 临时目录清理失败（{_pe}），结果已写出，继续。")

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"      推理完成，耗时: {elapsed:.1f} 秒")

        print(f"[3/3] 结果已保存至: {out_path}")
        print()
        print("=" * 60)
        print("  [OK] 变化检测完成")
        print("=" * 60)
        print()
        return True

    except RuntimeError as e:
        print(f"\n[错误] 推理运行时错误: {e}")
        print("可能原因:")
        print("  - GPU 显存不足，尝试减小 batch_size 或使用 CPU (--gpu -1)")
        print("  - 影像格式不支持或损坏")
        print("  - 影像与模型要求的波段数不匹配")
        return False
    except Exception as e:
        print(f"\n[错误] {type(e).__name__}: {e}")
        return False


# ============================================================================
# 4. 批处理
# ============================================================================

def run_batch_inference(
    csv_path: str,
    model_key: str = "building",
    gpu: int = 0,
    batch_size: int = 1,
    offset: int = None,
    result_type: str = "grid",
    out_format: str = "udbx",
) -> bool:
    """从 CSV 文件读取任务列表，批量执行变化检测。"""
    import csv

    if not os.path.exists(csv_path):
        print(f"\n[错误] CSV 文件不存在: {csv_path}")
        return False

    tasks: List[Tuple[str, ...]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            row = [c.strip() for c in row]
            if len(row) < 3:
                print(f"[警告] 跳过不完整行: {row}")
                continue
            tasks.append(tuple(row))

    if not tasks:
        print("[警告] CSV 文件中没有有效任务。")
        return False

    print()
    print("=" * 60)
    print("  批处理模式")
    print("=" * 60)
    print(f"  任务数量:   {len(tasks)}")
    print(f"  模型:       {model_key}")
    print(f"  设备:       {'GPU ' + str(gpu) if gpu >= 0 else 'CPU'}")
    print("=" * 60)
    print()

    if offset is None:
        model_info = get_model_info(model_key)
        offset = model_info.get("offset", 128)

    success_count = 0
    for i, task in enumerate(tasks, 1):
        before, after, out = task[0], task[1], task[2]
        ds_name = task[3] if len(task) >= 4 else "predict_change"

        print(f"\n--- [{i}/{len(tasks)}] ---")
        ok = run_single_inference(
            before_path=before,
            after_path=after,
            out_path=out,
            model_key=model_key,
            gpu=gpu,
            batch_size=batch_size,
            offset=offset,
            result_type=result_type,
            out_dataset_name=ds_name,
            out_format=out_format,
        )
        if ok:
            success_count += 1

    print()
    print("=" * 60)
    print(f"  批处理完成: {success_count}/{len(tasks)} 成功")
    print("=" * 60)
    print()
    return success_count == len(tasks)


# ============================================================================
# 4.5 栅格转矢量 & 变化类型分类
# ============================================================================

def raster_mask_to_vector(udbx_path: str, dataset_name: str,
                          out_dataset_name: str = "change_polygons",
                          threshold: int = 128, min_area: float = 0,
                          smooth_boundary: bool = True) -> Tuple[bool, object, dict]:
    """将变化检测的栅格掩膜转换为矢量面要素。"""
    stats = {"polygon_count": 0, "total_area_m2": 0.0,
             "min_area_m2": 0.0, "max_area_m2": 0.0}

    try:
        from iobjectspy import (
            DatasourceConnectionInfo, Workspace, DatasetType, analyst,
            FieldInfo, FieldType
        )
    except ImportError:
        print("[警告] 无法导入 iobjectspy，跳过矢量转换。")
        return False, None, stats

    ws = Workspace()
    try:
        conn = DatasourceConnectionInfo()
        conn.set_server(udbx_path)
        conn.set_driver("UDBX")
        ds = ws.open_datasource(conn)
        raster_dt = ds[dataset_name]

        print(f"\n[矢量转换] 栅格数据集 '{dataset_name}' → 矢量面要素...")
        print(f"  二值化阈值: {threshold}")

        # ========== 修复：直接传 udbx_path 字符串 ==========
        try:
            analyst.raster_to_vector(
                input_data=raster_dt,
                value_field="value",
                out_dataset_type=DatasetType.REGION,
                back_or_no_value=0,
                is_thin_raster=True,
                # SmoothMethod 枚举: NONE=-1 / BSPLINE=0 / POLISH=1，传 2 会触发库内部 listener 崩溃
                smooth_method=0 if smooth_boundary else None,
                smooth_degree=0.0,
                out_data=udbx_path,
                out_dataset_name=out_dataset_name,
            )
            print("  [OK] 矢量化完成")
        except Exception as e:
            print(f"  [错误] 矢量化失败: {e}")
            import traceback
            traceback.print_exc()
            return False, raster_dt, stats

        # 重新打开获取矢量数据集
        ws.close()
        ws = Workspace()
        conn2 = DatasourceConnectionInfo()
        conn2.set_server(udbx_path)
        conn2.set_driver("UDBX")
        ds2 = ws.open_datasource(conn2)

        vec_dt = None
        for d in ds2.datasets:
            if d.name == out_dataset_name:
                vec_dt = d
                break

        if vec_dt is None:
            print(f"  [警告] 未找到矢量数据集 '{out_dataset_name}'")
            return False, raster_dt, stats

        vec_count = vec_dt.get_record_count() if hasattr(vec_dt, "get_record_count") else 0
        print(f"  生成 {vec_count} 个变化 polygon")

        # 添加面积字段并填充几何面积。
        # 注意：is_available_field_name 在名字可用（未被占用）时返回 True，需在 True 时创建；
        # 编辑记录需先 rd.edit() 进入编辑态，再 set_value + update。
        try:
            if vec_dt.is_available_field_name("area_m2"):
                vec_dt.create_field(FieldInfo("area_m2", FieldType.DOUBLE, caption="面积"))
            _ard = vec_dt.get_recordset()
            _ard.move_first()
            while not _ard.is_eof():
                _ag = _ard.get_geometry()
                if _ag is not None and hasattr(_ag, "area"):
                    _ard.edit()
                    _ard.set_value("area_m2", _ag.area)
                    _ard.update()
                _ard.move_next()
        except Exception:
            pass

        # 统计面积 & 过滤小斑块（min_area=0 时也统计，避免 polygon_count 始终为 0）
        if vec_dt.get_record_count() > 0:
            try:
                deleted = 0
                rd = vec_dt.get_recordset()
                rd.move_first()
                while not rd.is_eof():
                    geom = rd.get_geometry()
                    if geom and hasattr(geom, "area"):
                        area = geom.area
                        if min_area > 0 and area < min_area:
                            rd.delete()
                            deleted += 1
                        else:
                            stats["total_area_m2"] += area
                            if stats["min_area_m2"] == 0 or area < stats["min_area_m2"]:
                                stats["min_area_m2"] = area
                            if area > stats["max_area_m2"]:
                                stats["max_area_m2"] = area
                    rd.move_next()
                if deleted > 0:
                    print(f"  过滤小斑块 (<{min_area}m^2): 删除 {deleted} 个")
                stats["polygon_count"] = vec_dt.get_record_count()
            except Exception:
                stats["polygon_count"] = vec_dt.get_record_count()

        print(f"  [OK] 矢量转换完成: {stats['polygon_count']} 个变化区域")
        if stats["total_area_m2"] > 0:
            print(f"  总面积: {stats['total_area_m2']:.1f} m^2")

        return True, vec_dt, stats

    except Exception as e:
        print(f"  [错误] 矢量转换异常: {e}")
        import traceback
        traceback.print_exc()
        return False, None, stats
    finally:
        try:
            ws.close()
        except Exception:
            pass


def run_building_seg_inference(
    image_path: str, out_udbx: str, gpu: int = 0,
    out_dataset_name: str = "buildings",
    batch_size: int = 1, offset: int = 128,
) -> Tuple[bool, str]:
    """对单张影像做建筑物分割推理。"""
    if not os.path.exists(image_path):
        print(f"  [错误] 影像不存在: {image_path}")
        return False, out_udbx

    model_info = get_model_info("building-seg")

    print(f"\n  [建筑物分割] {os.path.basename(image_path)}")
    print(f"    模型: {model_info['name']}")
    print(f"    影像: {image_path}")
    print(f"    输出: {out_udbx}")

    try:
        from iobjectspy.ml.vision import ImageryInference

        gpus = [gpu] if gpu >= 0 else []
        model = ImageryInference(
            model_path=model_info["path"],
            gpus=gpus,
            batch_size=batch_size,
        )

        infer_ok = False
        errors = []

        try:
            model.binary_classify_infer(
                input_data=image_path,
                out_data=out_udbx,
                out_dataset_name=out_dataset_name,
                offset=offset,
                result_type="grid",
            )
            infer_ok = True
        except Exception as e1:
            errors.append(f"binary_classify_infer: {e1}")

        if not infer_ok:
            try:
                model.scene_classify_infer(
                    input_data=image_path,
                    out_data=out_udbx,
                    out_dataset_name=out_dataset_name,
                    result_type="grid",
                )
                infer_ok = True
            except Exception as e2:
                errors.append(f"scene_classify_infer: {e2}")

        if not infer_ok:
            # SuperMap 在结果写出后清理临时文件夹可能抛 PermissionError（临时 tif 仍被占用），
            # 此时输出数据源其实已经生成，按成功处理。
            if os.path.exists(out_udbx):
                print(f"  [提示] 结果已写出（SuperMap 临时目录清理失败，可忽略）")
                print(f"  [OK] 建筑物分割完成 → {out_udbx}")
                return True, out_udbx
            for e in errors:
                print(f"  [警告] {e}")
            print(f"  [警告] 建筑物分割失败")
            return False, out_udbx

        print(f"  [OK] 建筑物分割完成 → {out_udbx}")
        return True, out_udbx

    except Exception as e:
        print(f"  [错误] 建筑物分割异常: {e}")
        return False, out_udbx


def classify_changes_on_vector(
    change_udbx: str, change_dataset: str,
    before_seg_udbx: str, after_seg_udbx: str,
    before_seg_dataset: str = "buildings",
    after_seg_dataset: str = "buildings",
    out_dataset_name: str = "change_classified",
) -> Tuple[bool, dict]:
    """对变化检测的矢量结果进行类型分类。"""
    counts = {"新增建筑": 0, "消失地物": 0, "属性变更": 0, "其他变化": 0, "总计": 0}

    try:
        from iobjectspy import DatasourceConnectionInfo, Workspace, FieldInfo, FieldType
    except ImportError:
        print("[警告] 无法导入 iobjectspy，跳过变化分类。")
        return False, counts

    ws = Workspace()
    try:
        conn = DatasourceConnectionInfo()
        conn.set_server(change_udbx)
        ds = ws.open_datasource(conn)

        if change_dataset not in [dt.name for dt in ds.datasets]:
            print(f"  [警告] 变化矢量数据集 '{change_dataset}' 不存在")
            return False, counts

        vec_dt = ds[change_dataset]

        before_buildings = None
        after_buildings = None

        try:
            b_conn = DatasourceConnectionInfo()
            b_conn.set_server(before_seg_udbx)
            b_ds = ws.open_datasource(b_conn)
            if before_seg_dataset in [dt.name for dt in b_ds.datasets]:
                before_buildings = b_ds[before_seg_dataset]
        except Exception:
            pass

        try:
            a_conn = DatasourceConnectionInfo()
            a_conn.set_server(after_seg_udbx)
            a_ds = ws.open_datasource(a_conn)
            if after_seg_dataset in [dt.name for dt in a_ds.datasets]:
                after_buildings = a_ds[after_seg_dataset]
        except Exception:
            pass

        if before_buildings is None or after_buildings is None:
            print("  [警告] 建筑物分割栅格不可用，所有变化标记为 '未分类'。")
            try:
                if vec_dt.is_available_field_name("change_type"):
                    vec_dt.create_field(FieldInfo("change_type", FieldType.WTEXT, 20, caption="变化类型"))
                rd = vec_dt.get_recordset()
                rd.move_first()
                while not rd.is_eof():
                    rd.edit()
                    rd.set_value("change_type", "未分类")
                    rd.update()
                    rd.move_next()
                counts["总计"] = vec_dt.get_record_count()
                counts["未分类"] = counts["总计"]
            except Exception:
                pass
            return True, counts

        print(f"\n  [变化分类] 正在判断 {vec_dt.get_record_count()} 个变化区域的类型...")

        try:
            if vec_dt.is_available_field_name("change_type"):
                vec_dt.create_field(FieldInfo("change_type", FieldType.WTEXT, 20, caption="变化类型"))
        except Exception:
            pass

        rd = vec_dt.get_recordset()
        rd.move_first()
        total = vec_dt.get_record_count()
        processed = 0

        while not rd.is_eof():
            geom = rd.get_geometry()
            if geom is None:
                rd.move_next()
                continue

            try:
                center = geom.get_inner_point()
            except Exception:
                try:
                    center = geom.get_label_point()
                except Exception:
                    rd.move_next()
                    continue

            # 读取多边形内点在前后期建筑物分割栅格上的像元值。
            # 分割结果是 0/1 二值掩膜：先 xy_to_grid 把地图坐标转成行列号，再 get_value(col,row) 取值。
            before_val = 0
            try:
                _col, _row = before_buildings.xy_to_grid(center)
                before_val = before_buildings.get_value(int(_col), int(_row))
            except Exception:
                before_val = 0

            after_val = 0
            try:
                _col, _row = after_buildings.xy_to_grid(center)
                after_val = after_buildings.get_value(int(_col), int(_row))
            except Exception:
                after_val = 0

            b_has = before_val >= 0.5
            a_has = after_val >= 0.5

            if not b_has and a_has:
                change_type = "新增建筑"
            elif b_has and not a_has:
                change_type = "消失地物"
            elif b_has and a_has:
                change_type = "属性变更"
            else:
                change_type = "其他变化"

            try:
                rd.edit()
                rd.set_value("change_type", change_type)
                rd.update()
                counts[change_type] += 1
            except Exception:
                counts[change_type] += 1

            processed += 1
            if processed % 100 == 0:
                print(f"    已处理 {processed}/{total}...")

            rd.move_next()

        counts["总计"] = processed
        print(f"  [OK] 变化分类完成: 总计 {counts['总计']} 个区域")
        print(f"    新增建筑: {counts['新增建筑']} | "
              f"消失地物: {counts['消失地物']} | "
              f"属性变更: {counts['属性变更']} | "
              f"其他变化: {counts['其他变化']}")

        return True, counts

    except Exception as e:
        print(f"  [错误] 变化分类异常: {e}")
        return False, counts
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ============================================================================
# 4.6 增强版推理
# ============================================================================

def run_enhanced_inference(
    before_path: str,
    after_path: str,
    out_path: str,
    model_key: str = "building",
    gpu: int = 0,
    classify: bool = True,
    min_change_area: float = 0,
    smooth: bool = True,
    batch_size: int = 1,
    offset: int = None,
    out_format: str = "udbx",
) -> Tuple[bool, dict]:
    """增强版变化检测：推理 → 栅格转矢量 → 变化分类 → 统计。"""
    result: Dict[str, object] = {
        "success": False,
        "before": before_path,
        "after": after_path,
        "output": out_path,
        "model": model_key,
        "classification_enabled": classify,
        "change_stats": {},
        "vector_stats": {},
        "duration_s": 0.0,
        "error": None,
    }

    start_time = datetime.now()

    print()
    print("=" * 66)
    print("  增强版变化检测 — 矢量输出")
    if classify:
        print("  模式: 推理 + 矢量转换 + 变化分类")
    else:
        print("  模式: 推理 + 矢量转换")
    print("=" * 66)

    if not os.path.exists(before_path):
        result["error"] = f"前期影像不存在: {before_path}"
        print(f"\n[错误] {result['error']}")
        return False, result
    if not os.path.exists(after_path):
        result["error"] = f"后期影像不存在: {after_path}"
        print(f"\n[错误] {result['error']}")
        return False, result

    model_info = get_model_info(model_key)
    if offset is None:
        offset = model_info.get("offset", 128)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if not _check_supermap_import():
        result["error"] = "无法导入 iobjectspy"
        return False, result

    try:
        from iobjectspy.ml.vision import ImageryInference

        gpus = [gpu] if gpu >= 0 else []

        print(f"\n  模型:       {model_info['name']}")
        print(f"  前期影像:   {before_path}")
        print(f"  后期影像:   {after_path}")
        print(f"  输出路径:   {out_path}")
        print(f"  设备:       {'GPU ' + str(gpu) if gpu >= 0 else 'CPU'}")
        print(f"  分类:       {'启用' if classify else '禁用'}")
        print(f"  最小面积:   {min_change_area}m^2")

        print(f"\n  [1/{'4' if classify else '3'}] 变化检测推理...")
        model = ImageryInference(
            model_path=model_info["path"],
            gpus=gpus,
            batch_size=batch_size,
        )

        base_name = os.path.splitext(os.path.basename(out_path))[0]
        tmp_udbx_dir = os.path.dirname(os.path.abspath(out_path))
        tmp_udbx = os.path.join(tmp_udbx_dir, f"_{base_name}_raster.udbx")
        raster_ds_name = "predict_change"

        try:
            model.general_changedet_infer(
                input_data=before_path,
                input_compare_data=after_path,
                out_data=tmp_udbx,
                out_dataset_name=raster_ds_name,
                offset=offset,
                result_type="grid",
            )
        except PermissionError as _pe:
            # SuperMap 在结果写出后清理临时文件夹可能抛 PermissionError（临时 tif 仍被占用），
            # 此时结果其实已生成，按成功处理。
            if not os.path.exists(tmp_udbx):
                raise
            print(f"    推理完成（SuperMap 临时目录清理失败，可忽略）: {_pe}")
        print(f"    推理完成 → {tmp_udbx}")
        result["change_stats"]["raster_udbx"] = tmp_udbx

        print(f"\n  [2/{'4' if classify else '3'}] 栅格 → 矢量转换...")
        vec_ds_name = "change_polygons"
        vec_ok, vec_dt, vec_stats = raster_mask_to_vector(
            udbx_path=tmp_udbx,
            dataset_name=raster_ds_name,
            out_dataset_name=vec_ds_name,
            threshold=128,
            min_area=min_change_area,
            smooth_boundary=smooth,
        )
        result["vector_stats"] = vec_stats

        if not vec_ok:
            print("  矢量转换失败，保留栅格结果。")
            if out_path != tmp_udbx:
                try:
                    import shutil
                    for ext in ["", ".udbx", ".udd"]:
                        src = tmp_udbx + ext if ext else tmp_udbx
                        if os.path.exists(src):
                            shutil.copy2(src, out_path + ext if ext else out_path)
                except Exception:
                    pass
            result["success"] = True
            result["output"] = tmp_udbx
            result["duration_s"] = (datetime.now() - start_time).total_seconds()
            _remember_last_params(before_path, after_path, out_path,
                                 model_key, gpu, out_format,
                                 classify=classify,
                                 min_change_area=min_change_area)
            return True, result

        if classify and vec_ok:
            print(f"\n  [3/4] 建筑物分割 (前后期分别提取)...")

            before_seg_udbx = os.path.join(tmp_udbx_dir, f"_{base_name}_buildings_before.udbx")
            after_seg_udbx = os.path.join(tmp_udbx_dir, f"_{base_name}_buildings_after.udbx")

            seg1_ok, _ = run_building_seg_inference(
                image_path=before_path,
                out_udbx=before_seg_udbx,
                gpu=gpu,
                batch_size=batch_size,
                offset=offset,
            )
            seg2_ok, _ = run_building_seg_inference(
                image_path=after_path,
                out_udbx=after_seg_udbx,
                gpu=gpu,
                batch_size=batch_size,
                offset=offset,
            )

            if seg1_ok and seg2_ok:
                print(f"\n  [4/4] 变化类型分类...")
                classify_ok, counts = classify_changes_on_vector(
                    change_udbx=tmp_udbx,
                    change_dataset=vec_ds_name,
                    before_seg_udbx=before_seg_udbx,
                    after_seg_udbx=after_seg_udbx,
                )
                result["change_stats"] = {
                    **result["change_stats"],
                    "classification": counts,
                    "before_seg_udbx": before_seg_udbx,
                    "after_seg_udbx": after_seg_udbx,
                }
            else:
                print(f"\n  [4/4] 变化类型分类 — 跳过（建筑物分割不可用）")
                try:
                    from iobjectspy import DatasourceConnectionInfo, Workspace, FieldInfo, FieldType
                    _ws = Workspace()
                    try:
                        _conn = DatasourceConnectionInfo()
                        _conn.set_server(tmp_udbx)
                        _ds = _ws.open_datasource(_conn)
                        _dt = _ds[vec_ds_name]
                        if _dt.is_available_field_name("change_type"):
                            _dt.create_field(FieldInfo("change_type", FieldType.WTEXT, 20, caption="变化类型"))
                        _rd = _dt.get_recordset()
                        _rd.move_first()
                        while not _rd.is_eof():
                            _rd.edit()
                            _rd.set_value("change_type", "未分类")
                            _rd.update()
                            _rd.move_next()
                        result["change_stats"]["classification"] = {
                            "未分类": _dt.get_record_count(), "总计": _dt.get_record_count()
                        }
                    finally:
                        _ws.close()
                except Exception:
                    result["change_stats"]["classification"] = {"未分类": 0, "总计": 0}
            step = "4/4"
        else:
            step = "3/3"

        print(f"\n  [{step}] 保存最终结果...")

        if out_path != tmp_udbx:
            try:
                import shutil
                for ext in ["", ".udbx", ".udd"]:
                    src = tmp_udbx + ext if ext else tmp_udbx
                    dst = out_path + ext if ext else out_path
                    if os.path.exists(src) and src != dst:
                        shutil.copy2(src, dst)
                print(f"    输出: {out_path}")
            except Exception as e:
                print(f"    复制失败: {e}，结果保留在 {tmp_udbx}")
                out_path = tmp_udbx

        result["output"] = out_path
        result["success"] = True
        result["duration_s"] = (datetime.now() - start_time).total_seconds()

        print()
        print("=" * 66)
        print("  [OK] 增强版变化检测完成")
        print(f"  耗时: {result['duration_s']:.1f} 秒")
        print(f"  输出: {out_path}")
        print(f"  变化区域: {vec_stats.get('polygon_count', 0)} 个")
        if vec_stats.get('total_area_m2', 0) > 0:
            print(f"  变化面积: {vec_stats['total_area_m2']:.1f} m^2")
        if classify and "classification" in result.get("change_stats", {}):
            cls = result["change_stats"]["classification"]
            if "新增建筑" in cls:
                print(f"  变化类型: "
                      f"新增 {cls.get('新增建筑', 0)} | "
                      f"消失 {cls.get('消失地物', 0)} | "
                      f"变更 {cls.get('属性变更', 0)} | "
                      f"其他 {cls.get('其他变化', 0)}")
        print("=" * 66)
        print()

        _remember_last_params(before_path, after_path, out_path,
                             model_key, gpu, out_format,
                             classify=classify,
                             min_change_area=min_change_area)

        return True, result

    except RuntimeError as e:
        result["error"] = str(e)
        print(f"\n[错误] 运行时错误: {e}")
        return False, result
    except Exception as e:
        result["error"] = str(e)
        print(f"\n[错误] {type(e).__name__}: {e}")
        return False, result


# ============================================================================
# 4.7 文件夹批量处理框架
# ============================================================================

class FolderBatchProcessor:
    """文件夹批量处理框架。"""

    def __init__(
        self,
        model_key: str = "building",
        gpu: int = 0,
        output_dir: str = None,
        classify: bool = True,
        out_format: str = "udbx",
        batch_size: int = 1,
        offset: int = None,
        min_change_area: float = 0,
        smooth: bool = True,
    ):
        self.model_key = model_key
        self.gpu = gpu
        self.output_dir = output_dir
        self.classify = classify
        self.out_format = out_format
        self.batch_size = batch_size
        self.offset = offset
        self.min_change_area = min_change_area
        self.smooth = smooth

        self.tasks: List[Dict[str, object]] = []
        self.status: Dict[str, object] = {
            "batch_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "start_time": None,
            "end_time": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "pair_mode": None,
            "source_folder": None,
            "config": {
                "model": model_key,
                "gpu": gpu,
                "classify": classify,
                "out_format": out_format,
                "min_change_area": min_change_area,
            },
            "tasks": [],
        }
        self._status_file = None
        self._progress_callback = None

    def scan_pairs(
        self,
        folder: str,
        mode: str = "subdirs",
        pattern: Tuple[str, str] = None,
    ) -> int:
        if not os.path.isdir(folder):
            print(f"[错误] 文件夹不存在: {folder}")
            return 0

        self.status["pair_mode"] = mode
        self.status["source_folder"] = os.path.abspath(folder)
        self.tasks = []

        if mode == "subdirs":
            self._scan_subdirs(folder)
        elif mode == "pattern":
            self._scan_pattern(folder, pattern)
        elif mode == "pairs_file":
            self._scan_pairs_file(folder)
        else:
            print(f"[错误] 不支持的配对模式: '{mode}'")
            return 0

        self.status["total"] = len(self.tasks)
        print(f"\n  扫描完成: 发现 {len(self.tasks)} 对影像")
        return len(self.tasks)

    def _scan_subdirs(self, folder: str):
        before_dir = os.path.join(folder, "before")
        after_dir = os.path.join(folder, "after")

        if not os.path.isdir(before_dir):
            print(f"[错误] 'before' 子文件夹不存在: {before_dir}")
            return
        if not os.path.isdir(after_dir):
            print(f"[错误] 'after' 子文件夹不存在: {after_dir}")
            return

        IMG_EXTS = {".tif", ".tiff", ".img", ".geotiff", ".png", ".jpg", ".jpeg"}

        before_files = {}
        for f in sorted(os.listdir(before_dir)):
            name, ext = os.path.splitext(f)
            if ext.lower() in IMG_EXTS:
                before_files[name] = os.path.join(before_dir, f)

        after_files = {}
        for f in sorted(os.listdir(after_dir)):
            name, ext = os.path.splitext(f)
            if ext.lower() in IMG_EXTS:
                after_files[name] = os.path.join(after_dir, f)

        print(f"  扫描配对 (subdirs 模式):")
        print(f"    before/ : {len(before_files)} 个影像")
        print(f"    after/  : {len(after_files)} 个影像")

        matched = set(before_files.keys()) & set(after_files.keys())
        for name in sorted(matched):
            task = self._make_task(before_files[name], after_files[name], name)
            self.tasks.append(task)

    def _scan_pattern(self, folder: str, pattern: Tuple[str, str] = None):
        if pattern is None:
            candidates = [
                ("_T1", "_T2"),
                ("_before", "_after"),
                ("_pre", "_post"),
                ("_2020", "_2024"),
            ]
        else:
            candidates = [pattern]

        IMG_EXTS = {".tif", ".tiff", ".img", ".geotiff", ".png", ".jpg", ".jpeg"}
        all_files = sorted([
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in IMG_EXTS
        ])

        for pat_before, pat_after in candidates:
            before_map = {}
            after_map = {}
            for f in all_files:
                name, ext = os.path.splitext(f)
                full = os.path.join(folder, f)
                if pat_before in name:
                    key = name.replace(pat_before, "")
                    before_map[key] = full
                elif pat_after in name:
                    key = name.replace(pat_after, "")
                    after_map[key] = full

            matched = set(before_map.keys()) & set(after_map.keys())
            if matched:
                print(f"  扫描配对 (pattern 模式: *{pat_before}* / *{pat_after}*):")
                print(f"    匹配: {len(matched)} 对")
                for key in sorted(matched):
                    task = self._make_task(before_map[key], after_map[key], key)
                    self.tasks.append(task)
                return

        print(f"[错误] 无法找到可配对的影像对")

    def _scan_pairs_file(self, folder: str):
        json_path = os.path.join(folder, "pairs.json")
        csv_path = os.path.join(folder, "pairs.csv")

        if os.path.exists(json_path):
            import json as _json
            with open(json_path, "r", encoding="utf-8") as f:
                pairs = _json.load(f)
            for i, pair in enumerate(pairs):
                before = pair.get("before", pair.get("t1", ""))
                after = pair.get("after", pair.get("t2", ""))
                name = pair.get("name", f"task_{i+1:03d}")
                if before and after:
                    task = self._make_task(before, after, name)
                    self.tasks.append(task)
        elif os.path.exists(csv_path):
            import csv
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for i, row in enumerate(reader):
                    if not row or row[0].startswith("#"):
                        continue
                    row = [c.strip() for c in row]
                    if len(row) >= 2:
                        name = row[2] if len(row) >= 3 else f"task_{i+1:03d}"
                        task = self._make_task(row[0], row[1], name)
                        self.tasks.append(task)
        else:
            print(f"[错误] 找不到 pairs.json 或 pairs.csv")

    def _make_task(self, before: str, after: str, name: str) -> Dict[str, object]:
        if self.output_dir:
            out_name = f"{name}_change.{self.out_format}"
            out_path = os.path.join(self.output_dir, out_name)
        else:
            parent = os.path.dirname(os.path.abspath(before))
            out_path = os.path.join(parent, f"{name}_change.{self.out_format}")

        return {
            "id": len(self.tasks) + 1,
            "name": name,
            "before": before,
            "after": after,
            "output": out_path,
            "status": "pending",
            "start_time": None,
            "end_time": None,
            "duration_s": 0.0,
            "change_stats": {},
            "error": None,
        }

    def process_all(self, progress_callback=None) -> bool:
        if not self.tasks:
            print("[警告] 没有待处理的任务")
            return False

        self._progress_callback = progress_callback
        self.status["start_time"] = datetime.now().isoformat()
        self._init_status_file()

        print()
        print("=" * 66)
        print("  文件夹批量处理")
        print("=" * 66)
        print(f"  任务数量:   {len(self.tasks)}")
        print(f"  模型:       {self.model_key}")
        print(f"  设备:       {'GPU ' + str(self.gpu) if self.gpu >= 0 else 'CPU'}")
        print("=" * 66)

        all_success = True

        for i, task in enumerate(self.tasks):
            idx = i + 1
            print(f"\n{'─' * 60}")
            print(f"  [{idx}/{len(self.tasks)}] {task['name']}")

            task["status"] = "running"
            task["start_time"] = datetime.now().isoformat()
            self._save_task_status(task)

            try:
                ok, result = run_enhanced_inference(
                    before_path=str(task["before"]),
                    after_path=str(task["after"]),
                    out_path=str(task["output"]),
                    model_key=self.model_key,
                    gpu=self.gpu,
                    classify=self.classify,
                    min_change_area=self.min_change_area,
                    smooth=self.smooth,
                    batch_size=self.batch_size,
                    offset=self.offset,
                    out_format=self.out_format,
                )

                task["end_time"] = datetime.now().isoformat()
                if task["start_time"]:
                    t0 = datetime.fromisoformat(task["start_time"])
                    task["duration_s"] = (datetime.now() - t0).total_seconds()
                task["change_stats"] = result.get("change_stats", {})
                task["error"] = result.get("error")

                if ok:
                    task["status"] = "success"
                    self.status["completed"] += 1
                    print(f"  [{idx}/{len(self.tasks)}] ✅ 完成 ({task['duration_s']:.0f}s)")
                else:
                    task["status"] = "failed"
                    self.status["failed"] += 1
                    all_success = False
                    print(f"  [{idx}/{len(self.tasks)}] ❌ 失败: {task['error']}")

            except Exception as e:
                task["status"] = "failed"
                task["end_time"] = datetime.now().isoformat()
                task["error"] = str(e)
                self.status["failed"] += 1
                all_success = False
                print(f"  [{idx}/{len(self.tasks)}] ❌ 异常: {e}")

            self._save_task_status(task)

            if progress_callback:
                try:
                    progress_callback(idx, task)
                except Exception:
                    pass

        self.status["end_time"] = datetime.now().isoformat()
        self._save_status_summary()
        self._print_summary()

        return all_success

    def _init_status_file(self):
        if self.output_dir:
            status_dir = self.output_dir
        elif self.tasks:
            status_dir = os.path.dirname(os.path.abspath(str(self.tasks[0]["output"])))
        else:
            status_dir = "."
        os.makedirs(status_dir, exist_ok=True)
        self._status_file = os.path.join(
            status_dir,
            f"batch_status_{self.status['batch_id']}.json"
        )

    def _save_task_status(self, task: Dict[str, object]):
        self._write_status()

    def _save_status_summary(self):
        self._write_status()
        print(f"\n  状态文件: {self._status_file}")

    def _write_status(self):
        if not self._status_file:
            return
        try:
            out = {
                **{k: v for k, v in self.status.items() if k != "tasks"},
                "tasks": self.tasks,
            }
            with open(self._status_file, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            print(f"  [警告] 写入状态文件失败: {e}")

    def _print_summary(self):
        total = self.status["total"]
        ok = self.status["completed"]
        fail = self.status["failed"]

        print()
        print("=" * 66)
        print("  批量处理汇总")
        print("=" * 66)
        print(f"  总任务数:   {total}")
        print(f"  成功:       {ok}")
        print(f"  失败:       {fail}")
        print(f"  成功占比:   {ok / total * 100:.1f}%" if total > 0 else "  无任务")

        if fail > 0:
            print(f"  ⚠ 有 {fail} 个任务失败，详见状态文件。")
        else:
            print(f"  ✅ 全部任务完成！")
        print()

    def save_report(self, path: str = None) -> str:
        if path is None:
            if self.output_dir:
                path = os.path.join(self.output_dir,
                                    f"batch_report_{self.status['batch_id']}.json")
            else:
                path = f"batch_report_{self.status['batch_id']}.json"

        try:
            report = {
                **{k: v for k, v in self.status.items() if k != "tasks"},
                "tasks": self.tasks,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False, default=str)
            print(f"  报告已保存: {path}")
            return path
        except Exception as e:
            print(f"  [错误] 保存报告失败: {e}")
            return ""

    def get_failed_tasks(self) -> List[Dict[str, object]]:
        return [t for t in self.tasks if t["status"] == "failed"]

    def get_successful_tasks(self) -> List[Dict[str, object]]:
        return [t for t in self.tasks if t["status"] == "success"]


# ============================================================================
# 4.7 文件夹批量处理的便利函数
# ============================================================================

def run_folder_batch(
    folder: str,
    mode: str = "subdirs",
    model_key: str = "building",
    gpu: int = 0,
    classify: bool = True,
    min_change_area: float = 0,
    out_format: str = "udbx",
    output_dir: str = None,
    pattern: Tuple[str, str] = None,
) -> bool:
    bp = FolderBatchProcessor(
        model_key=model_key,
        gpu=gpu,
        output_dir=output_dir,
        classify=classify,
        out_format=out_format,
        batch_size=1,
        offset=None,
        min_change_area=min_change_area,
        smooth=True,
    )

    count = bp.scan_pairs(folder, mode=mode, pattern=pattern)
    if count == 0:
        print("\n[错误] 未发现可配对的影像")
        return False

    ok = bp.process_all()

    cfg = load_config()
    cfg["batch_folder"]["last_folder"] = folder
    cfg["batch_folder"]["pair_mode"] = mode
    if output_dir:
        cfg["batch_folder"]["output_dir"] = output_dir
    save_config(cfg)

    bp.save_report()
    return ok


# ============================================================================
# 4.8 多格式导出
# ============================================================================

def export_vector_multiformat(
    udbx_path: str,
    output_dir: str,
    formats: List[str],
    vector_dataset: Optional[str] = None,
    raster_dataset: Optional[str] = None,
) -> Tuple[bool, dict]:
    """将 UDBX 结果导出为多种格式。

    formats 支持: geojson / shp(shapefile) / kml / csv / tif(geotiff)
    矢量格式导出第一个矢量数据集（如 change_polygons），GeoTIFF 导出第一个栅格数据集（变化掩膜）。
    返回 (是否至少成功一项, {格式: 输出路径或失败原因})。
    """
    results: Dict[str, str] = {}
    if not os.path.exists(udbx_path):
        print(f"\n[错误] 数据源不存在: {udbx_path}")
        return False, results

    os.makedirs(output_dir, exist_ok=True)

    try:
        from iobjectspy import DatasourceConnectionInfo, Workspace, conversion, DatasetVector
    except ImportError:
        print("[警告] 无法导入 iobjectspy，导出失败。")
        return False, results

    ws = Workspace()
    try:
        conn = DatasourceConnectionInfo()
        conn.set_server(udbx_path)
        conn.set_driver("UDBX")
        ds = ws.open_datasource(conn)

        vec_dt = None
        raster_dt = None
        for d in ds.datasets:
            if vector_dataset and d.name == vector_dataset:
                vec_dt = d
            if raster_dataset and d.name == raster_dataset:
                raster_dt = d
            if vec_dt is None and isinstance(d, DatasetVector):
                vec_dt = d
            if raster_dt is None and not isinstance(d, DatasetVector):
                raster_dt = d

        base = os.path.splitext(os.path.basename(udbx_path))[0]

        for fmt in formats:
            fmt = (fmt or "").lower().strip()
            try:
                if fmt in ("geojson", "json"):
                    if vec_dt is None:
                        results[fmt] = "无矢量数据集"
                        continue
                    out = os.path.join(output_dir, f"{base}.geojson")
                    conversion.export_to_geojson(vec_dt, out, is_over_write=True)
                    results[fmt] = out
                elif fmt in ("shp", "shapefile"):
                    if vec_dt is None:
                        results[fmt] = "无矢量数据集"
                        continue
                    out = os.path.join(output_dir, f"{base}.shp")
                    conversion.export_to_shape(vec_dt, out, is_over_write=True)
                    results[fmt] = out
                elif fmt == "kml":
                    if vec_dt is None:
                        results[fmt] = "无矢量数据集"
                        continue
                    out = os.path.join(output_dir, f"{base}.kml")
                    conversion.export_to_kml(vec_dt, out, is_over_write=True)
                    results[fmt] = out
                elif fmt == "csv":
                    if vec_dt is None:
                        results[fmt] = "无矢量数据集"
                        continue
                    out = os.path.join(output_dir, f"{base}.csv")
                    conversion.export_to_csv(vec_dt, out, is_over_write=True)
                    results[fmt] = out
                elif fmt in ("tif", "tiff", "geotiff"):
                    if raster_dt is None:
                        results[fmt] = "无栅格数据集"
                        continue
                    out = os.path.join(output_dir, f"{base}_mask.tif")
                    conversion.export_to_tif(raster_dt, out, is_over_write=True)
                    results[fmt] = out
                else:
                    results[fmt] = "不支持的格式"
            except Exception as e:
                results[fmt] = f"失败: {e}"

        print("\n  导出结果:")
        any_ok = False
        for fmt, r in results.items():
            # 导出可能静默失败（如 KML 要求地理坐标系，返回 False 但不抛异常），
            # 因此用「输出文件是否真实存在」作为成功判据。
            ok = isinstance(r, str) and os.path.exists(r)
            any_ok = any_ok or ok
            print(f"    [{fmt}] {'[OK]' if ok else '[--]'} {r}")
        return any_ok, results

    except Exception as e:
        print(f"[错误] 导出异常: {e}")
        return False, results
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ============================================================================
# 5. 环境自检
# ============================================================================

def run_self_check() -> bool:
    cfg = load_config()

    print()
    print("=" * 60)
    print("  环境自检")
    print("=" * 60)

    checks = []

    py = sys.executable
    print(f"\n  Python: {py}")
    print(f"  版本:    {sys.version.split()[0]}")

    jh = cfg.get("java_home", "")
    jh_ok = os.path.isdir(jh) if jh else False
    status = "[OK]" if jh_ok else "[FAIL]"
    print(f"  JAVA_HOME: {status} {jh if jh else '(未配置)'}")
    checks.append(("JAVA_HOME", jh_ok))

    iob = cfg.get("iobjects_bin", "")
    bin_ok = os.path.isdir(iob) if iob else False
    status = "[OK]" if bin_ok else "[FAIL]"
    print(f"  iObjects Bin: {status} {iob if iob else '(未配置)'}")
    checks.append(("iObjects Bin", bin_ok))

    ml = cfg.get("resources_ml", "")
    model_dir = os.path.join(ml, "model") if ml else ""
    ml_ok = os.path.isdir(model_dir) if model_dir else False
    status = "[OK]" if ml_ok else "[FAIL]"
    print(f"  模型目录: {status} {model_dir if model_dir else '(未配置)'}")
    checks.append(("模型资源", ml_ok))

    try:
        import iobjectspy
        print(f"  iobjectspy: [OK] 版本 {iobjectspy.__version__}")
        checks.append(("iobjectspy", True))
    except ImportError:
        print(f"  iobjectspy: [FAIL] 未安装")
        checks.append(("iobjectspy", False))

    try:
        import torch
        try:
            cuda_ok = torch.cuda.is_available()
            gpu_count = torch.cuda.device_count() if cuda_ok else 0
            status = f"[OK] ({gpu_count} 个GPU)" if cuda_ok else "[FAIL] (仅 CPU)"
        except Exception:
            cuda_ok = False
            status = "- (CUDA 不可用)"
        print(f"  CUDA:      {status}")
        checks.append(("CUDA", cuda_ok))
    except ImportError:
        print(f"  CUDA:      - (未安装 PyTorch)")
        checks.append(("CUDA", False))

    print(f"\n  模型文件检查:")
    for key, info in AVAILABLE_MODELS.items():
        full_path = _resolve_model_path(info["filename"])
        exists = os.path.exists(full_path)
        status = "[OK]" if exists else "[FAIL]"
        print(f"    [{key}] {status} {info['name']}")
        checks.append((f"模型-{key}", exists))

    all_ok = all(ok for _, ok in checks)
    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n  ---")
    print(f"  总计: {passed}/{total} 通过")
    if all_ok:
        print(f"  结论: [OK] 环境就绪")
    else:
        failed = [name for name, ok in checks if not ok]
        print(f"  结论: [FAIL] 以下项目未通过 — {', '.join(failed)}")
    print("=" * 60)
    print()
    return all_ok


# ============================================================================
# 6. 配置向导
# ============================================================================

def run_setup_wizard():
    """启动 PyQt5 图形化配置工具（独立，不依赖 SuperMap）"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_config.py")
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        print("[错误] 需要 PyQt5: pip install PyQt5")
        print("或使用命令行配置: python change_detection.py setup --text")
        return
    import subprocess
    subprocess.run([sys.executable, script])


# ============================================================================
# 7. 交互菜单
# ============================================================================

def interactive_menu():
    while True:
        print()
        print("  ╔══════════════════════════════════════════════════════╗")
        print("  ║       通用变化检测工具 — SuperMap iObjects            ║")
        print("  ║       第24届 SuperMap 杯高校 GIS 大赛                 ║")
        print("  ╚══════════════════════════════════════════════════════╝")
        print()
        print("    [1] 环境自检")
        print("    [2] 查看模型列表")
        print("    [3] 运行变化检测    基础版：推理 → 栅格输出")
        print("    [4] 批量处理        从 CSV 文件批量运行")
        print("    [5] 查看帮助")
        print("    [6] 配置向导")
        print("    [7] 图形界面")
        print("    [8] 增强检测 (+矢量) 推理 → 矢量 → 变化分类")
        print("    [9] 文件夹批量")
        print("    [0] 退出")
        print()
        print("  " + "-" * 56)

        try:
            choice = input("  请输入数字 (0-9): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已退出。")
            break

        if choice == "0":
            print("  已退出。")
            break
        elif choice == "1":
            print()
            try:
                run_self_check()
            except Exception as e:
                print(f"\n  [错误] 自检过程出现异常: {e}")
        elif choice == "2":
            print()
            list_models()
        elif choice == "3":
            _menu_run_single()
        elif choice == "4":
            _menu_run_batch()
        elif choice == "5":
            _menu_show_help()
        elif choice == "6":
            run_setup_wizard()
        elif choice == "7":
            print("\n  正在启动图形界面...")
            try:
                from change_detection_ui import main as ui_main
                ui_main()
            except ImportError:
                print("  [错误] 无法加载图形界面")
        elif choice == "8":
            _menu_run_enhanced()
        elif choice == "9":
            _menu_run_folder_batch()
        else:
            print("  无效选择，请重新输入。")

        try:
            input("\n  按回车键返回菜单...")
        except (EOFError, KeyboardInterrupt):
            print("\n  已退出。")
            break


def _menu_show_help():
    print()
    parser = build_parser()
    parser.print_help()
    print()


def _menu_run_single():
    cfg = load_config()
    last = cfg.get("last_params", DEFAULT_CONFIG["last_params"])

    def _hint(key: str, fallback: str) -> str:
        val = last.get(key, fallback)
        return f" [{val}]" if val else ""

    print()
    print("  " + "-" * 56)
    print("  运行变化检测")
    print("  " + "-" * 56)
    print()

    before = input(f"  前期影像路径 (T1){_hint('before', '')}: ").strip()
    if not before:
        before = last.get("before", "")
    if not before:
        print("  [错误] 前期影像路径不能为空！")
        return

    after = input(f"  后期影像路径 (T2){_hint('after', '')}: ").strip()
    if not after:
        after = last.get("after", "")
    if not after:
        print("  [错误] 后期影像路径不能为空！")
        return

    default_out = last.get("out", "result.udbx")
    out = input(f"  输出文件路径{_hint('out', 'result.udbx')}: ").strip()
    if not out:
        out = default_out

    default_model = last.get("model", "building")
    model = input(f"  模型 (building/building-seg/landcover){_hint('model', 'building')}: ").strip()
    if not model:
        model = default_model

    default_gpu = str(last.get("gpu", 0))
    gpu_str = input(f"  GPU 编号 (0/1/...，-1=CPU){_hint('gpu', '0')}: ").strip()
    try:
        gpu = int(gpu_str) if gpu_str else int(default_gpu)
    except ValueError:
        gpu = 0

    default_fmt = last.get("out_format", "udbx")
    out_format = input(f"  输出格式 (udbx/tif){_hint('out_format', 'udbx')}: ").strip()
    if not out_format:
        out_format = default_fmt

    print()
    print("  " + "-" * 56)
    print(f"    前期影像:   {before}")
    print(f"    后期影像:   {after}")
    print(f"    输出路径:   {out}")
    print(f"    模型:       {model}")
    print(f"    GPU:        {gpu}")
    print(f"    输出格式:   {out_format}")
    print("  " + "-" * 56)

    confirm = input("  确认执行? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  已取消。")
        return

    print()
    print("  正在运行，请稍候...")
    print()

    ok = run_single_inference(
        before_path=before,
        after_path=after,
        out_path=out,
        model_key=model,
        gpu=gpu,
        batch_size=1,
        offset=None,
        result_type="grid",
        out_dataset_name="predict_change",
        out_format=out_format,
    )

    if ok:
        _remember_last_params(before, after, out, model, gpu, out_format)
        print("  [记忆] 本次参数已保存")


def _menu_run_batch():
    cfg = load_config()
    last = cfg.get("last_params", DEFAULT_CONFIG["last_params"])
    default_model = last.get("model", "building")
    default_gpu = str(last.get("gpu", 0))

    def _hint(val: str) -> str:
        return f" [{val}]" if val else ""

    print()
    print("  " + "-" * 56)
    print("  批量处理")
    print("  " + "-" * 56)
    print()

    csv = input("  CSV 文件路径: ").strip()
    if not csv:
        print("  [错误] CSV 路径不能为空！")
        return

    model = input(f"  模型 (building/building-seg/landcover){_hint(default_model)}: ").strip()
    if not model:
        model = default_model

    gpu_str = input(f"  GPU 编号{_hint(default_gpu)}: ").strip()
    try:
        gpu = int(gpu_str) if gpu_str else int(default_gpu)
    except ValueError:
        gpu = 0

    confirm = input("  确认执行? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  已取消。")
        return

    print()
    print("  正在批量处理...")
    print()

    ok = run_batch_inference(
        csv_path=csv,
        model_key=model,
        gpu=gpu,
        batch_size=1,
        offset=None,
        result_type="grid",
        out_format="udbx",
    )


def _menu_run_enhanced():
    cfg = load_config()
    last = cfg.get("last_params", DEFAULT_CONFIG["last_params"])

    def _hint(key: str, fallback: str) -> str:
        val = last.get(key, fallback)
        return f" [{val}]" if val else ""

    print()
    print("  " + "-" * 56)
    print("  增强版变化检测 — 矢量输出 + 变化分类")
    print("  " + "-" * 56)
    print()

    before = input(f"  前期影像路径 (T1){_hint('before', '')}: ").strip()
    if not before:
        before = last.get("before", "")
    if not before:
        print("  [错误] 前期影像路径不能为空！")
        return

    after = input(f"  后期影像路径 (T2){_hint('after', '')}: ").strip()
    if not after:
        after = last.get("after", "")
    if not after:
        print("  [错误] 后期影像路径不能为空！")
        return

    default_out = last.get("out", "result.udbx")
    out = input(f"  输出文件路径{_hint('out', 'result.udbx')}: ").strip()
    if not out:
        out = default_out

    default_model = last.get("model", "building")
    model = input(f"  模型 (building/building-seg/landcover){_hint('model', 'building')}: ").strip()
    if not model:
        model = default_model

    default_gpu = str(last.get("gpu", 0))
    gpu_str = input(f"  GPU 编号 (0/1/...，-1=CPU){_hint('gpu', '0')}: ").strip()
    try:
        gpu = int(gpu_str) if gpu_str else int(default_gpu)
    except ValueError:
        gpu = 0

    default_cls = "Y" if last.get("classify", True) else "n"
    cls_str = input(f"  启用变化类型分类? (Y/n) [{default_cls}]: ").strip().lower()
    if not cls_str:
        classify = last.get("classify", True)
    else:
        classify = cls_str != "n"

    default_min = str(last.get("min_change_area", 0))
    min_str = input(f"  最小变化面积 (m^2, 0=不过滤){_hint('min_change_area', '0')}: ").strip()
    try:
        min_area = float(min_str) if min_str else float(default_min)
    except ValueError:
        min_area = 0

    default_fmt = last.get("out_format", "udbx")
    out_format = input(f"  输出格式 (udbx/tif){_hint('out_format', 'udbx')}: ").strip()
    if not out_format:
        out_format = default_fmt

    print()
    print("  " + "-" * 56)
    print(f"    前期影像:   {before}")
    print(f"    后期影像:   {after}")
    print(f"    输出路径:   {out}")
    print(f"    模型:       {model}")
    print(f"    GPU:        {gpu}")
    print(f"    变化分类:   {'启用' if classify else '禁用'}")
    print(f"    最小面积:   {min_area} m^2")
    print(f"    输出格式:   {out_format}")
    print("  " + "-" * 56)

    confirm = input("  确认执行? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  已取消。")
        return

    print()
    print("  正在运行，请稍候...")
    print()

    ok, result = run_enhanced_inference(
        before_path=before,
        after_path=after,
        out_path=out,
        model_key=model,
        gpu=gpu,
        classify=classify,
        min_change_area=min_area,
        smooth=True,
        batch_size=1,
        offset=None,
        out_format=out_format,
    )

    if ok:
        print("  [记忆] 本次参数已保存")


def _menu_run_folder_batch():
    cfg = load_config()
    batch_cfg = cfg.get("batch_folder", {})
    last = cfg.get("last_params", DEFAULT_CONFIG["last_params"])

    def _hint(val: str) -> str:
        return f" [{val}]" if val else ""

    print()
    print("  " + "-" * 56)
    print("  文件夹批量处理")
    print("  " + "-" * 56)
    print()

    default_folder = batch_cfg.get("last_folder", "")
    folder = input(f"  数据文件夹路径{_hint(default_folder)}: ").strip()
    if not folder:
        folder = default_folder
    if not folder:
        print("  [错误] 文件夹路径不能为空！")
        return

    default_mode = batch_cfg.get("pair_mode", "subdirs")
    mode = input(f"  配对模式 (subdirs/pattern/pairs_file) [{default_mode}]: ").strip()
    if not mode:
        mode = default_mode

    default_model = last.get("model", "building")
    model = input(f"  模型 (building/building-seg/landcover){_hint(default_model)}: ").strip()
    if not model:
        model = default_model

    default_gpu = str(last.get("gpu", 0))
    gpu_str = input(f"  GPU 编号{_hint(default_gpu)}: ").strip()
    try:
        gpu = int(gpu_str) if gpu_str else int(default_gpu)
    except ValueError:
        gpu = 0

    default_cls = "Y" if last.get("classify", True) else "n"
    cls_str = input(f"  启用变化类型分类? (Y/n) [{default_cls}]: ").strip().lower()
    classify = default_cls == "Y" if not cls_str else cls_str != "n"

    default_out_dir = batch_cfg.get("output_dir", "")
    out_dir = input(f"  统一输出目录 (留空=各影像所在目录){_hint(default_out_dir)}: ").strip()
    if not out_dir:
        out_dir = default_out_dir or None

    print()
    print("  " + "-" * 56)
    print(f"    数据文件夹: {folder}")
    print(f"    配对模式:   {mode}")
    print(f"    模型:       {model}")
    print(f"    GPU:        {gpu}")
    print(f"    变化分类:   {'启用' if classify else '禁用'}")
    if out_dir:
        print(f"    输出目录:   {out_dir}")
    print("  " + "-" * 56)

    confirm = input("  确认执行? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  已取消。")
        return

    print()
    print("  正在批量处理...")
    print()

    run_folder_batch(
        folder=folder,
        mode=mode,
        model_key=model,
        gpu=gpu,
        classify=classify,
        min_change_area=0,
        out_format="udbx",
        output_dir=out_dir,
    )


# ============================================================================
# 8. 命令行参数定义
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="change_detection.py",
        description="通用变化检测工具 — 基于 SuperMap iObjects Python",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python change_detection.py setup         # 首次配置
              python change_detection.py check         # 环境自检
              python change_detection.py models        # 查看模型
              python change_detection.py menu          # 交互菜单
              python change_detection.py run --before 2020.tif --after 2024.tif --out result.udbx
              python change_detection.py batch --csv tasks.csv
        """),
    )

    sub = parser.add_subparsers(dest="command", title="子命令")

    p_run = sub.add_parser("run", help="单次变化检测推理")
    p_run.add_argument("--before", "-b", type=str, required=True)
    p_run.add_argument("--after", "-a", type=str, required=True)
    p_run.add_argument("--out", "-o", type=str, required=True)
    p_run.add_argument("--model", "-m", type=str, default="building",
                       choices=list(AVAILABLE_MODELS.keys()))
    p_run.add_argument("--gpu", "-g", type=int, default=0)
    p_run.add_argument("--batch-size", type=int, default=1)
    p_run.add_argument("--offset", type=int, default=None)
    p_run.add_argument("--result-type", type=str, default="grid",
                       choices=["grid", "region"])
    p_run.add_argument("--out-dataset-name", type=str, default="predict_change")
    p_run.add_argument("--out-format", "-f", type=str, default="udbx",
                       choices=["udbx", "tif"])

    p_batch = sub.add_parser("batch", help="批量变化检测推理")
    p_batch.add_argument("--csv", "-c", type=str, required=True)
    p_batch.add_argument("--model", "-m", type=str, default="building",
                         choices=list(AVAILABLE_MODELS.keys()))
    p_batch.add_argument("--gpu", "-g", type=int, default=0)
    p_batch.add_argument("--batch-size", type=int, default=1)
    p_batch.add_argument("--offset", type=int, default=None)
    p_batch.add_argument("--result-type", type=str, default="grid",
                         choices=["grid", "region"])
    p_batch.add_argument("--out-format", "-f", type=str, default="udbx",
                         choices=["udbx", "tif"])

    sub.add_parser("models", help="列出所有可用模型")
    sub.add_parser("check", help="运行环境自检")
    sub.add_parser("setup", help="首次使用配置向导")
    sub.add_parser("menu", help="交互式菜单模式")
    sub.add_parser("ui", help="启动图形界面")

    p_run_vec = sub.add_parser("run-vec", help="增强版变化检测（矢量输出 + 变化分类）")
    p_run_vec.add_argument("--before", "-b", type=str, required=True)
    p_run_vec.add_argument("--after", "-a", type=str, required=True)
    p_run_vec.add_argument("--out", "-o", type=str, required=True)
    p_run_vec.add_argument("--model", "-m", type=str, default="building",
                           choices=list(AVAILABLE_MODELS.keys()))
    p_run_vec.add_argument("--gpu", "-g", type=int, default=0)
    p_run_vec.add_argument("--batch-size", type=int, default=1)
    p_run_vec.add_argument("--offset", type=int, default=None)
    p_run_vec.add_argument("--out-format", "-f", type=str, default="udbx",
                           choices=["udbx", "tif"])
    p_run_vec.add_argument("--no-classify", action="store_true")
    p_run_vec.add_argument("--min-area", type=float, default=0)
    p_run_vec.add_argument("--no-smooth", action="store_true")

    p_bf = sub.add_parser("batch-folder", help="文件夹批量处理")
    p_bf.add_argument("--folder", "-d", type=str, required=True)
    p_bf.add_argument("--mode", type=str, default="subdirs",
                      choices=["subdirs", "pattern", "pairs_file"])
    p_bf.add_argument("--pattern", type=str, nargs=2, default=None)
    p_bf.add_argument("--model", "-m", type=str, default="building",
                      choices=list(AVAILABLE_MODELS.keys()))
    p_bf.add_argument("--gpu", "-g", type=int, default=0)
    p_bf.add_argument("--batch-size", type=int, default=1)
    p_bf.add_argument("--offset", type=int, default=None)
    p_bf.add_argument("--out-format", "-f", type=str, default="udbx",
                      choices=["udbx", "tif"])
    p_bf.add_argument("--output_dir", "-O", type=str, default=None)
    p_bf.add_argument("--no-classify", action="store_true")
    p_bf.add_argument("--min-area", type=float, default=0)
    p_bf.add_argument("--no-smooth", action="store_true")

    return parser


# ============================================================================
# 9. 主入口
# ============================================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "models":
        list_models()
    elif args.command == "check":
        ok = run_self_check()
        if sys.stdin.isatty():
            print()
            try:
                input("  按回车键退出...")
            except (EOFError, KeyboardInterrupt):
                pass
        sys.exit(0 if ok else 1)
    elif args.command == "ui":
        try:
            from change_detection_ui import main as ui_main
        except ImportError as e:
            print(f"[错误] 无法加载图形界面: {e}")
            print("请确保已安装 PyQt5: pip install PyQt5")
            sys.exit(1)
        try:
            ui_main()
        except Exception as e:
            print(f"[错误] 图形界面启动失败: {e}")
            sys.exit(1)
    elif args.command == "setup":
        run_setup_wizard()
    elif args.command == "menu":
        interactive_menu()
    elif args.command == "run":
        offset = args.offset
        if offset is None:
            model_info = get_model_info(args.model)
            offset = model_info.get("offset", 128)

        ok = run_single_inference(
            before_path=args.before,
            after_path=args.after,
            out_path=args.out,
            model_key=args.model,
            gpu=args.gpu,
            batch_size=args.batch_size,
            offset=offset,
            result_type=args.result_type,
            out_dataset_name=args.out_dataset_name,
            out_format=args.out_format,
        )
        if ok:
            _remember_last_params(args.before, args.after, args.out,
                                 args.model, args.gpu, args.out_format)
        sys.exit(0 if ok else 1)
    elif args.command == "batch":
        ok = run_batch_inference(
            csv_path=args.csv,
            model_key=args.model,
            gpu=args.gpu,
            batch_size=args.batch_size,
            offset=args.offset,
            result_type=args.result_type,
            out_format=args.out_format,
        )
        sys.exit(0 if ok else 1)
    elif args.command == "run-vec":
        offset = args.offset
        if offset is None:
            model_info = get_model_info(args.model)
            offset = model_info.get("offset", 128)

        ok, result = run_enhanced_inference(
            before_path=args.before,
            after_path=args.after,
            out_path=args.out,
            model_key=args.model,
            gpu=args.gpu,
            classify=not args.no_classify,
            min_change_area=args.min_area,
            smooth=not args.no_smooth,
            batch_size=args.batch_size,
            offset=offset,
            out_format=args.out_format,
        )
        sys.exit(0 if ok else 1)
    elif args.command == "batch-folder":
        pattern = tuple(args.pattern) if args.pattern else None
        ok = run_folder_batch(
            folder=args.folder,
            mode=args.mode,
            model_key=args.model,
            gpu=args.gpu,
            classify=not args.no_classify,
            min_change_area=args.min_area,
            out_format=args.out_format,
            output_dir=args.output_dir,
            pattern=pattern,
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()