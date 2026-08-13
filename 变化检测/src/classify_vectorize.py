
# -*- coding: utf-8 -*-
"""
地物分类 & 栅格转矢量模块
第24届 SuperMap 杯高校 GIS 大赛

功能：
  1. 地物分类推理（输入影像→输出分类栅格）
  2. 栅格转矢量（分类栅格→矢量，使用 SuperMap analyst.raster_to_vector）
"""

import os
import sys
import json
import warnings
from datetime import datetime
from typing import Dict, Optional

warnings.filterwarnings("ignore")

# 修复 Windows GBK 控制台下 print 无法编码的字符（✅/² 等）导致 UnicodeEncodeError 崩溃的问题
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# 允许从父目录导入 change_detection 的配置函数
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from change_detection import (
        load_config,
        get_model_info,
        _check_supermap_import,
        AVAILABLE_MODELS,
    )
except ImportError:
    print("[错误] 无法导入 change_detection.py，请确保它在同一目录下。")
    sys.exit(1)


# ============================================================================
# 1. 地物分类函数
# ============================================================================

def run_classify(
    input_image: str,
    output_raster: str,
    model_key: str = "landcover",
    gpu: int = 0,
    batch_size: int = 1,
    offset: Optional[int] = None,
) -> bool:
    """
    独立地物分类函数
    """
    if not os.path.exists(input_image):
        print(f"\n[错误] 影像不存在: {input_image}")
        return False

    model_info = get_model_info(model_key)
    if offset is None:
        offset = model_info.get("offset", 128)

    out_dir = os.path.dirname(os.path.abspath(output_raster))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if not _check_supermap_import():
        return False

    try:
        from iobjectspy.ml.vision import ImageryInference

        print()
        print("=" * 60)
        print("  [地物分类] 推理")
        print("=" * 60)
        print(f"  模型:       {model_info['name']}")
        print(f"  输入影像:   {input_image}")
        print(f"  输出栅格:   {output_raster}")
        print(f"  设备:       {'GPU ' + str(gpu) if gpu >= 0 else 'CPU'}")
        print("=" * 60)
        print()

        gpus = [gpu] if gpu >= 0 else []

        print("[1/3] 加载模型...")
        model = ImageryInference(
            model_path=model_info["path"],
            gpus=gpus,
            batch_size=batch_size,
        )

        print("[2/3] 执行分类推理...")
        start_time = datetime.now()

        # 分类结果是栅格（grid），需先写入临时 UDBX，再导出为 GeoTIFF。
        # 若直接把 .tif 路径传给 out_data，SuperMap 会把它当成数据源目录，
        # 在 classify.tif/ 文件夹里生成 classify_result.tif，造成输出路径混乱。
        tmp_udbx = os.path.splitext(output_raster)[0] + ".udbx"
        for _ext in ("", ".udbx", ".udd"):
            _p = tmp_udbx + _ext
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except Exception:
                    pass

        try:
            model.multi_classify_infer(
                input_data=input_image,
                out_data=tmp_udbx,
                out_dataset_name="classify_result",
                offset=offset,
                result_type="grid",
            )
        except PermissionError as _pe:
            # SuperMap 清理临时文件夹失败，但结果已写入临时 UDBX
            if not os.path.exists(tmp_udbx):
                raise
            print(f"      推理完成（临时目录清理失败，可忽略）: {_pe}")

        print("      正在导出为 GeoTIFF...")
        from iobjectspy import conversion, DatasourceConnectionInfo, Workspace
        _ws = Workspace()
        try:
            _conn = DatasourceConnectionInfo()
            _conn.set_server(tmp_udbx)
            _conn.set_driver("UDBX")
            _ds = _ws.open_datasource(_conn)
            _dt = _ds["classify_result"]
            conversion.export_to_tif(_dt, output_raster)
        finally:
            _ws.close()

        # 清理临时 UDBX
        for _ext in ("", ".udbx", ".udd"):
            _p = tmp_udbx + _ext
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except Exception:
                    pass

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"      耗时: {elapsed:.1f} 秒")

        print("[3/3] 保存完成")
        print("=" * 60)
        print("  [OK] 地物分类成功")
        print("=" * 60)
        print()
        return True

    except RuntimeError as e:
        print(f"\n[错误] 推理运行时错误: {e}")
        return False
    except PermissionError as e:
        # SuperMap 在结果写出后清理临时文件夹可能抛 PermissionError（临时 tif 仍被占用），
        # 此时结果其实已生成，按成功处理。
        if os.path.exists(output_raster):
            print(f"\n[提示] 结果已写出（SuperMap 临时目录清理失败，可忽略）: {e}")
            return True
        print(f"\n[错误] {type(e).__name__}: {e}")
        return False
    except Exception as e:
        print(f"\n[错误] {type(e).__name__}: {e}")
        return False


# ============================================================================
# 2. 栅格转矢量函数（使用 analyst.raster_to_vector）
# ============================================================================

def run_vectorize(
    input_raster: str,
    output_vector: str,
    class_map_json: Optional[str] = None,
    min_area: float = 0.0,
    simplify_tolerance: float = 0.0,
) -> bool:
    """
    独立栅格转矢量函数
    使用 SuperMap analyst.raster_to_vector
    """
    if not os.path.exists(input_raster):
        print(f"\n[错误] 栅格不存在: {input_raster}")
        return False

    # 确保输出目录存在
    out_dir = os.path.dirname(os.path.abspath(output_vector))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # 如果输出文件已存在，先删除
    if os.path.exists(output_vector):
        try:
            os.remove(output_vector)
        except:
            pass

    if not _check_supermap_import():
        return False

    # 创建临时目录
    temp_dir = os.path.abspath(os.path.join(os.path.dirname(output_vector), "__temp_vectorize"))
    os.makedirs(temp_dir, exist_ok=True)
    temp_udbx = os.path.join(temp_dir, "grid.udbx").replace("\\", "/")
    temp_grid_name = "grid"

    try:
        from iobjectspy import (
            Workspace, DatasourceConnectionInfo,
            conversion, DatasetType, analyst
        )
        from iobjectspy import FieldInfo, DatasetVector, FieldType

        print()
        print("=" * 60)
        print("  [栅格转矢量]（analyst.raster_to_vector）")
        print("=" * 60)
        print(f"  输入栅格:     {input_raster}")
        print(f"  输出矢量:     {output_vector}")
        print("=" * 60)
        print()

        # ----- 步骤1：创建临时数据源 -----
        print("[1/4] 创建临时数据源...")
        temp_workspace = Workspace()
        conn_info = DatasourceConnectionInfo()
        conn_info.set_server(temp_udbx)
        conn_info.set_driver("UDBX")
        if os.path.exists(temp_udbx.replace("/", "\\")):
            os.remove(temp_udbx.replace("/", "\\"))
        temp_ds = temp_workspace.create_datasource(conn_info)
        if temp_ds is None:
            print("[错误] 无法创建临时数据源")
            return False
        print("      临时数据源创建成功")

        # ----- 步骤2：导入栅格 -----
        print("[2/4] 导入栅格到临时数据源...")
        conversion.import_tif(
            source_file=input_raster,
            output=temp_ds,
            out_dataset_name=temp_grid_name,
        )
        grid_ds = None
        for dataset in temp_ds.datasets:
            if dataset.name == temp_grid_name:
                grid_ds = dataset
                break
        if grid_ds is None:
            print("[错误] 栅格导入失败")
            return False
        print("      栅格导入完成")

        # ----- 步骤3：创建输出数据源 -----
        print("[3/4] 创建输出数据源...")
        out_workspace = Workspace()
        out_conn = DatasourceConnectionInfo()
        out_conn.set_server(output_vector)
        out_conn.set_driver("UDBX")
        out_ds = out_workspace.create_datasource(out_conn)
        if out_ds is None:
            print("[错误] 无法创建输出数据源")
            return False
        print("      输出数据源创建成功")

        # ================================================================
        # 【修复】步骤4：执行矢量化（使用 analyst.raster_to_vector）
        # 修复内容：
        #   1. 先打印栅格字段信息，便于调试
        #   2. 移除错误的 value_field="类别编码" 参数
        #   3. 让 SuperMap 自动处理字段映射
        # ================================================================
        print("[4/4] 执行栅格转矢量...")

        # 栅格为 DatasetImage/Grid，没有矢量意义的字段列表；值字段固定用 "value"。
        print("      值字段: value（分类栅格像元值）")

        # 调用 analyst.raster_to_vector（value_field 为必填参数，命名输出矢量中存储栅格值的字段）
        analyst.raster_to_vector(
            input_data=grid_ds,
            value_field="value",
            out_dataset_type=DatasetType.REGION,
            back_or_no_value=0,
            is_thin_raster=True,
            # 如果简化容差大于0则启用平滑
            smooth_method=0 if simplify_tolerance > 0 else None,
            smooth_degree=simplify_tolerance if simplify_tolerance > 0 else None,
            out_data=out_ds,
            out_dataset_name="vector_result"
        )
        print("      矢量化完成")

        # 先关闭输出工作空间让结果落盘，再重新打开添加字段。
        # 直接在 create_datasource 返回的 out_ds 上 create_field 有时不生效，重开最稳妥。
        out_workspace.close()

        out_workspace = Workspace()
        out_conn2 = DatasourceConnectionInfo()
        out_conn2.set_server(output_vector)
        out_conn2.set_driver("UDBX")
        out_ds2 = out_workspace.open_datasource(out_conn2)

        # 检查结果
        vector_ds = None
        for d in out_ds2.datasets:
            if isinstance(d, DatasetVector):
                vector_ds = d
                break

        if vector_ds is not None:
            # SuperMap 字段名只允许字母/数字/下划线，中文字段名会被拒绝，
            # 因此用英文名 + 中文别名（caption）来承载「类别」「面积」。
            # is_available_field_name(name) 在名字「可用（未被占用）」时返回 True，
            # 因此要在为 True 时才创建字段（之前的 `not` 逻辑写反了，导致字段从未创建）。
            try:
                if vector_ds.is_available_field_name("category"):
                    vector_ds.create_field(FieldInfo("category", FieldType.WTEXT, 20, caption="类别"))
                    print("      已添加「类别(category)」字段。")
            except Exception as e:
                print(f"      添加类别字段失败: {e}")
            try:
                if vector_ds.is_available_field_name("area_m2"):
                    vector_ds.create_field(FieldInfo("area_m2", FieldType.DOUBLE, caption="面积"))
                    print("      已添加「面积(area_m2)」字段。")
            except Exception as e:
                print(f"      添加面积字段失败: {e}")
            # 填充面积字段（几何面积）。编辑记录需先 rd.edit() 进入编辑态，再 set_value + update。
            try:
                rd = vector_ds.get_recordset()
                rd.move_first()
                while not rd.is_eof():
                    g = rd.get_geometry()
                    if g is not None and hasattr(g, "area"):
                        rd.edit()
                        rd.set_value("area_m2", g.area)
                        rd.update()
                    rd.move_next()
                print("      面积字段已填充。")
            except Exception as e:
                print(f"      填充面积字段失败: {e}")
            print(f"      ✅ 矢量数据集生成成功，记录数: {vector_ds.get_record_count()}")
        else:
            print("      ⚠️ 未找到矢量数据集")

        temp_workspace.close()
        out_workspace.close()

        # 清理临时文件
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("      临时文件已清理")

        print("=" * 60)
        print("  [OK] 栅格转矢量成功")
        print("=" * 60)
        print()
        return True

    except Exception as e:
        print(f"\n[错误] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
        return False


# ============================================================================
# 3. 命令行入口
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="classify_vectorize.py",
        description="地物分类 & 栅格转矢量工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_cls = sub.add_parser("classify", help="地物分类")
    p_cls.add_argument("-i", "--input", required=True, help="输入影像路径")
    p_cls.add_argument("-o", "--output", required=True, help="输出分类栅格路径（.tif）")
    p_cls.add_argument("--model", default="landcover", choices=list(AVAILABLE_MODELS.keys()), help="模型名")
    p_cls.add_argument("--gpu", type=int, default=0, help="GPU编号")
    p_cls.add_argument("--batch-size", type=int, default=1, help="批大小")
    p_cls.add_argument("--offset", type=int, default=None, help="滑动窗口偏移量")

    p_vec = sub.add_parser("vectorize", help="栅格转矢量")
    p_vec.add_argument("-r", "--raster", required=True, help="输入分类栅格路径")
    p_vec.add_argument("-o", "--output", default=None, help="输出矢量路径（.udbx）")
    p_vec.add_argument("--class-map", type=str, default=None, help='类别映射 JSON')
    p_vec.add_argument("--min-area", type=float, default=0.0, help="最小图斑面积（平方米）")
    p_vec.add_argument("--simplify", type=float, default=0.0, help="简化容差（米）")

    args = parser.parse_args()

    if args.command == "classify":
        ok = run_classify(
            input_image=args.input,
            output_raster=args.output,
            model_key=args.model,
            gpu=args.gpu,
            batch_size=args.batch_size,
            offset=args.offset,
        )
        sys.exit(0 if ok else 1)
    elif args.command == "vectorize":
        output = args.output
        if output is None:
            base = os.path.splitext(os.path.basename(args.raster))[0]
            output = os.path.join(os.path.dirname(args.raster), f"{base}_vector.udbx")
            print(f"[提示] 未指定输出路径，自动生成为: {output}")
        ok = run_vectorize(
            input_raster=args.raster,
            output_vector=output,
            class_map_json=args.class_map,
            min_area=args.min_area,
            simplify_tolerance=args.simplify,
        )
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()