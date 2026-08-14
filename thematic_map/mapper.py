# -*- coding: utf-8 -*-
"""
专题图模块
负责：贾思雨
功能：将变化检测/地物分类结果（.udbx）自动生成专题图图片（含图例+比例尺+边框+指北针）
"""

import os
import random
import sys
import time

# Windows 控制台默认 GBK，print 表情符号（✅/❌/⚠️）会抛 UnicodeEncodeError 导致崩溃；
# 这里把 stdout/stderr 重配为 UTF-8，对重定向或无控制台的情况静默跳过。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ====== 从 config.json 读取 Java 路径（避免硬编码本机路径） ======
def _load_java_env():
    """读取与 change_detection 同目录的 config.json 里的 Java 路径。"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        import json
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("java_home", ""), cfg.get("iobjects_bin", "")
    except Exception:
        return "", ""


_JAVA_HOME, _IOBJECTS_BIN = _load_java_env()

if _JAVA_HOME:
    os.environ["JAVA_HOME"] = _JAVA_HOME
    os.environ["PATH"] = os.path.join(_JAVA_HOME, "bin") + ";" + os.environ.get("PATH", "")
# =================================

from iobjectspy import DatasourceConnectionInfo, Datasource, env, CursorType, QueryParameter, GeoStyle, Color
from iobjectspy.mapping import Map, LayerSettingVector

from PIL import Image, ImageDraw, ImageFont


def _next_output_path(path: str) -> str:
    """若输出文件已存在，自动在扩展名前追加 _02、_03… 序号，避免覆盖之前的结果。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base}_{i:02d}{ext}"):
        i += 1
    return f"{base}_{i:02d}{ext}"


def generate_thematic_map(
        udbx_path: str,
        dataset_name: str,
        output_image: str,
        theme_field: str = None,
        width: int = 1200,
        height: int = 900
) -> bool:
    """
    生成专题图（含图例+比例尺+边框+指北针）
    """
    # Java环境配置（从 config.json 读取，避免硬编码本机路径）
    if _JAVA_HOME:
        os.environ["JAVA_HOME"] = _JAVA_HOME
    if _IOBJECTS_BIN:
        env.set_iobjects_java_path(_IOBJECTS_BIN)

    # 自动改名：若目标文件已存在，追加 _02/_03… 序号，避免覆盖之前的结果
    output_image = _next_output_path(output_image)

    temp_path = output_image.replace(".png", f"_temp_{str(int(time.time() * 1000))[-6:]}.png")

    try:
        # 1. 打开数据源
        conn_info = DatasourceConnectionInfo()
        conn_info.set_server(udbx_path)
        conn_info.set_driver("UDBX")
        ds = Datasource.open(conn_info)
        if ds is None:
            print(f"❌ 打开数据源失败: {udbx_path}")
            return False

        print("数据源中的数据集列表:")
        dataset_list = []
        for i, d in enumerate(ds.datasets):
            dataset_list.append(d.name)
            print(f"  {i}: {d.name} (类型: {type(d).__name__})")

        # 如果 dataset_name 为空或不在数据集中，尝试自动选择第一个矢量数据集
        if dataset_name not in dataset_list:
            print(f"⚠️ 数据集 '{dataset_name}' 不存在，尝试自动选择...")
            for d in ds.datasets:
                if 'Vector' in str(type(d)) or 'Region' in str(type(d)):
                    dataset_name = d.name
                    print(f"  自动选择: {dataset_name}")
                    break
            if dataset_name not in dataset_list:
                print(f"❌ 未找到可用数据集")
                return False

        dataset = ds[dataset_name]
        print(f"✅ 已打开数据集: {dataset_name}")
        print(f"   记录数: {dataset.get_record_count()}")

        # ================================================================
        # 获取字段信息
        # ================================================================
        print("数据集字段列表:")
        field_names = []
        try:
            field_infos = dataset.field_infos
            for fi in field_infos:
                field_names.append(fi.name)
                print(f"  - {fi.name} ({fi.type})")
        except Exception as e:
            print(f"   ⚠️ 读取字段失败: {e}")

        # 自动选择专题图字段
        if theme_field is None:
            possible_fields = ['类别编码', '类别', 'value', 'grid_value', 'code', 'class', 'type', 'Type', 'Class',
                               'SMID']
            for f in possible_fields:
                if f in field_names:
                    theme_field = f
                    break
            if theme_field is None and field_names:
                theme_field = field_names[0]
        print(f"✅ 使用专题字段: {theme_field}")

        # ================================================================
        # 获取字段唯一值
        # ================================================================
        unique_values = []
        try:
            rd = dataset.get_recordset(False, CursorType.STATIC)
            rd.move_first()
            while not rd.is_eof():
                try:
                    val = rd.get_value(theme_field)
                    if val not in unique_values and val is not None:
                        unique_values.append(val)
                except Exception:
                    pass
                rd.move_next()
            rd.close()
            print(f"   唯一值数量: {len(unique_values)}")
        except Exception as e:
            print(f"   ⚠️ 无法读取字段值: {e}")
            unique_values = [1, 2, 3, 4, 5, 6, 7, 0]

        if not unique_values:
            unique_values = [1, 2, 3, 4, 5, 6, 7, 0]
            print(f"   使用默认唯一值: {unique_values}")

        # ================================================================
        # 【你的原始颜色映射】
        # ================================================================
        # 地物分类（landcover 模型）真实类别：multi_cls_landcover.sdm 定义 8 类
        # 0=背景 1=建筑 2=耕地 3=水体 4=道路 5=裸土 6=林地 7=草地
        # 栅格转矢量时背景(0)被当作无值排除，矢量数据中背景像元值可能为 255。
        color_map = {
            1: {"name": "建筑", "color": [230, 40, 40]},
            2: {"name": "耕地", "color": [140, 200, 80]},
            3: {"name": "水体", "color": [40, 140, 230]},
            4: {"name": "道路", "color": [255, 220, 40]},
            5: {"name": "裸土", "color": [190, 150, 110]},
            6: {"name": "林地", "color": [20, 120, 55]},
            7: {"name": "草地", "color": [150, 220, 110]},
            0: {"name": "背景", "color": [210, 210, 210]},
            255: {"name": "背景", "color": [210, 210, 210]},
        }

        extra_colors = [
            [255, 100, 150], [150, 50, 200], [0, 200, 200],
            [255, 150, 50], [100, 200, 100], [200, 100, 200]
        ]

        # ================================================================
        # 2. 创建地图
        # ================================================================
        print("创建地图...")
        map_obj = Map()
        map_obj.set_name(f"专题图_{dataset_name}")

        # 获取数据源的投影并设置给地图
        prj = ds.prj_coordsys
        if prj:
            map_obj.set_prj(prj)
            print("✅ 已设置地图投影")

        # ================================================================
        # 【核心】按类别分层设色
        # 该版本 iobjectspy 的 Map/Layer 不支持用 ThemeUnique 直接渲染专题图，
        # 因此改为「每个唯一值一个图层 + 显示过滤 + 各自填充色」的方式实现。
        # ================================================================
        try:
            for val in unique_values:
                if val in color_map:
                    c = color_map[val]["color"]
                else:
                    idx = list(unique_values).index(val)
                    c = extra_colors[idx % len(extra_colors)]
                    color_map[val] = {"name": f"类别{val}", "color": c}

                layer_setting = LayerSettingVector()
                style = GeoStyle()
                style.set_fill_fore_color(Color((c[0], c[1], c[2])))
                style.set_fill_opaque_rate(80)
                style.set_line_color(Color((0, 0, 0)))
                style.set_line_width(0.1)
                layer_setting.set_style(style)

                map_obj.add_dataset(dataset, True, layer_setting)
                cat_layer = map_obj.get_layer(0)
                qp = QueryParameter()
                qp.set_attribute_filter(f"{theme_field} = {val}")
                cat_layer.set_display_filter(qp)

            print(f"✅ 已应用专题图，{len(unique_values)} 个类别")
        except Exception as e:
            print(f"   ⚠️ 设置专题图失败: {e}")

        # 刷新地图
        try:
            map_obj.refresh()
        except:
            pass

        # 先缩放到全图，再放大 2.5 倍
        map_obj.view_entire()
        map_obj.zoom(2.5)
        print(f"✅ 已缩放至全图并放大 2.5 倍")

        # 设置输出尺寸
        map_obj.set_image_size(width, height)

        # 导出底图
        map_obj.output_to_file(temp_path)
        print(f"✅ 底图已生成: {temp_path}")

        # 仅把实际出现在数据中的类别按编码升序（背景放最后）传给图例，
        # 避免 color_map 里 0 与 255 两个背景键重复，导致图例出现两条「背景」。
        legend_map = {}
        for val in sorted(unique_values, key=lambda v: (v in (0, 255), v)):
            if val in color_map:
                legend_map[val] = color_map[val]
        add_legend_and_scale(temp_path, output_image, legend_map, width, height)

        # 删除临时文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

        print(f"✅ 专题图已生成: {output_image}")
        return True

    except Exception as e:
        print(f"❌ 专题图生成失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def add_legend_and_scale(
        input_path: str,
        output_path: str,
        color_map: dict,
        map_width: int,
        map_height: int
):
    """
    在图片上绘制图例、比例尺、边框、指北针
    """
    # 打开底图
    img = Image.open(input_path)
    draw = ImageDraw.Draw(img)

    # 尝试加载字体
    try:
        font = ImageFont.truetype("simhei.ttf", 20)
        font_small = ImageFont.truetype("simhei.ttf", 16)
        font_north = ImageFont.truetype("simhei.ttf", 24)
    except:
        font = ImageFont.load_default()
        font_small = font
        font_north = font

    # ---- 边框 ----
    draw.rectangle(
        [5, 5, map_width - 5, map_height - 5],
        outline=(0, 0, 0),
        width=3
    )

    # ---- 指北针（左上角） ----
    north_x = 80
    north_y = 160

    draw.ellipse(
        [north_x - 50, north_y - 50, north_x + 50, north_y + 50],
        outline=(0, 0, 0),
        width=2,
        fill=(255, 255, 255, 200)
    )

    draw.polygon(
        [north_x, north_y - 45, north_x - 25, north_y, north_x + 25, north_y],
        fill=(255, 0, 0, 220),
        outline=(0, 0, 0)
    )
    draw.polygon(
        [north_x, north_y + 45, north_x - 25, north_y, north_x + 25, north_y],
        fill=(180, 180, 180, 220),
        outline=(0, 0, 0)
    )

    draw.ellipse(
        [north_x - 6, north_y - 6, north_x + 6, north_y + 6],
        fill=(0, 0, 0),
        outline=(0, 0, 0)
    )

    draw.text((north_x - 10, north_y - 65), "N", fill=(255, 0, 0), font=font_north)
    draw.text((north_x - 8, north_y + 52), "S", fill=(0, 0, 0), font=font_small)
    draw.text((north_x + 35, north_y - 10), "E", fill=(0, 0, 0), font=font_small)
    draw.text((north_x - 50, north_y - 10), "W", fill=(0, 0, 0), font=font_small)

    # ---- 图例（右上角） ----
    legend_x = map_width - 200
    legend_y = 60
    item_height = 40
    padding = 10

    legend_height = len(color_map) * item_height + padding * 2 + 30
    draw.rectangle(
        [legend_x - padding, legend_y - padding,
         legend_x + 180, legend_y + legend_height],
        fill=(255, 255, 255, 220),
        outline=(0, 0, 0, 255)
    )

    draw.text((legend_x, legend_y), "地物类型", fill=(0, 0, 0), font=font)
    legend_y += 30

    for key, info in color_map.items():
        color = info["color"]
        name = info["name"]
        draw.rectangle(
            [legend_x, legend_y, legend_x + 30, legend_y + 20],
            fill=(color[0], color[1], color[2]),
            outline=(0, 0, 0)
        )
        draw.text(
            (legend_x + 40, legend_y - 2),
            name,
            fill=(0, 0, 0),
            font=font_small
        )
        legend_y += item_height

    # ---- 比例尺（左下角） ----
    scale_x = 60
    scale_y = map_height - 120

    draw.rectangle(
        [scale_x - 20, scale_y - 10, scale_x + 260, scale_y + 50],
        fill=(255, 255, 255, 200),
        outline=(0, 0, 0)
    )

    bar_length = 200
    draw.rectangle(
        [scale_x, scale_y, scale_x + bar_length, scale_y + 10],
        fill=(0, 0, 0)
    )

    for i in range(0, 6):
        x = scale_x + i * (bar_length // 5)
        draw.line([x, scale_y - 5, x, scale_y + 15], fill=(0, 0, 0), width=2)
        text = str(i * 100)
        if i == 0:
            offset = -5
        elif i == 5:
            offset = -10
        else:
            offset = -10
        draw.text((x + offset, scale_y + 20), text, fill=(0, 0, 0), font=font_small)

    draw.text((scale_x + bar_length + 15, scale_y + 20), "米", fill=(0, 0, 0), font=font_small)

    img.save(output_path)
    print(f"✅ 图例和比例尺已添加")


def batch_generate_thematic_maps(
        udbx_path: str,
        output_dir: str,
        dataset_names: list = None,
        width: int = 1200,
        height: int = 900
) -> bool:
    """
    批量生成数据集中所有矢量数据集的专题图
    """
    os.makedirs(output_dir, exist_ok=True)

    conn_info = DatasourceConnectionInfo()
    conn_info.set_server(udbx_path)
    conn_info.set_driver("UDBX")
    ds = Datasource.open(conn_info)
    if ds is None:
        print(f"❌ 打开数据源失败: {udbx_path}")
        return False

    if dataset_names is None:
        dataset_names = []
        for d in ds.datasets:
            type_str = str(type(d))
            if 'Vector' in type_str or 'Region' in type_str or 'Line' in type_str:
                dataset_names.append(d.name)

    if not dataset_names:
        print("❌ 未找到矢量数据集")
        return False

    print(f"找到 {len(dataset_names)} 个矢量数据集:")
    for name in dataset_names:
        print(f"  - {name}")

    success_count = 0
    for name in dataset_names:
        output_path = os.path.join(output_dir, f"{name}_专题图.png")
        print(f"\n生成: {name} -> {output_path}")
        ok = generate_thematic_map(
            udbx_path=udbx_path,
            dataset_name=name,
            output_image=output_path,
            width=width,
            height=height
        )
        if ok:
            success_count += 1

    print(f"\n完成: {success_count}/{len(dataset_names)} 个专题图生成成功")
    return success_count > 0


if __name__ == "__main__":
    generate_thematic_map(
        udbx_path=r"D:\项目根目录\classification\classify_result.udbx",
        dataset_name="vector_result",
        output_image="专题图.png"
    )