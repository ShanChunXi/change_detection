# 城市变化检测与地图更新工具

第 24 届 SuperMap 杯高校 GIS 大赛开发组作品。基于 SuperMap iObjects Python 和深度学习模型，输入两期遥感影像即可自动识别变化区域，输出带分类属性的矢量 polygon，并支持批量处理、专题制图和统计报告生成。

## 功能状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 基础变化检测 | 已实现 | SiamSFNet 模型，输出栅格二值掩膜 |
| 增强变化检测 | 已实现 | 栅格→矢量转换 + 四类变化自动分类 |
| CSV 批量处理 | 已实现 | 从任务清单 CSV 逐对执行基础推理 |
| 文件夹批量处理 | 已实现 | 自动扫描配对影像 → 顺序推理 → 状态追踪 → 汇总报告 |
| 变化类型分类 | 已实现 | 新增建筑 / 消失地物 / 属性变更 / 其他变化 |
| PyQt5 图形界面 | 已实现 | 四模式切换、后台线程、进度追踪 |
| 批量管线与报告 | 已实现 | 状态统计、Excel/图表/Word/PDF 报告 |
| 地物分类与矢量化 | 已实现 | 单时相地物分类 + 栅格转矢量 |
| 专题图生成 | 已实现 | 按变化类型分层设色输出专题地图 |
| CLI 命令行 | 已实现 | 7 个子命令，参数化调用 |
| 交互菜单 | 已实现 | 9 个选项的终端菜单，记忆上次参数 |

## 变化类型

| 类型 | 判断逻辑 |
| --- | --- |
| 新增建筑 | T1 无建筑物 → T2 出现建筑物 |
| 消失地物 | T1 有建筑物 → T2 建筑物消失 |
| 属性变更 | T1/T2 均有建筑物且位于变化区域（改建/扩建） |
| 其他变化 | 非建筑类地表变化（道路、水体、植被等） |

输出矢量数据集的每个 polygon 包含 `change_type`（变化类型）和 `area_m2`（面积）属性字段。

## 环境要求

- Windows 10/11
- [SuperMap iObjects Python (GPU 版)](https://www.supermap.com) — 核心推理环境，含 `iobjectspy`
- SuperMap iObjects Java — JRE + Bin 目录
- ML 资源包 — 含预训练模型（SiamSFNet、SegFormer）
- Python 3.10（SuperMap 自带）
- [uv](https://docs.astral.sh/uv/)（推荐，管理外围依赖）

`pyproject.toml` 中声明了 PyQt5、rasterio、openpyxl 等外围依赖。核心依赖 `iobjectspy` 为 SuperMap 专有包，不在 PyPI 上，需使用 SuperMap 自带的 Python 环境。

## 安装与启动

### 1. 配置环境

首次使用需设置 `变化检测/src/config.json` 中的四个路径：

```json
{
    "java_home":    "D:/supermap安装包/java/.../jre1.8_x64",
    "iobjects_bin": "D:/supermap安装包/java/.../Bin",
    "resources_ml": "D:/supermap安装包/resources/.../resources_ml",
    "python_path":  "D:/supermap安装包/python/.../python.exe"
}
```

也可以直接运行 `run.bat`，启动脚本会自动搜索标准安装路径，找不到时进入交互式配置向导。

### 2. 安装外围依赖

```powershell
# 使用 uv（推荐）
uv sync

# 或使用 pip
D:/supermap安装包/python/.../python.exe -m pip install PyQt5 numpy rasterio openpyxl matplotlib Pillow python-docx
```

### 3. 启动

```powershell
# 交互菜单（最常用）
python 变化检测/src/change_detection.py menu

# 图形界面
python 变化检测/src/change_detection.py ui

# 双击启动
run.bat
```

首次启动约需 60 秒（SuperMap 初始化 JAR 文件），后续启动会更快。

## 使用方式

### 命令行

```bash
# 基础版：仅输出栅格掩膜
python 变化检测/src/change_detection.py run -b 2020.tif -a 2024.tif -o result.udbx

# 增强版：矢量输出 + 变化分类（推荐）
python 变化检测/src/change_detection.py run-vec -b 2020.tif -a 2024.tif -o result.udbx

# 增强版 + 过滤碎斑（最小面积 50 m²）
python 变化检测/src/change_detection.py run-vec -b 2020.tif -a 2024.tif -o result.udbx --min-area 50

# 增强版：仅矢量、不分类（更快）
python 变化检测/src/change_detection.py run-vec -b 2020.tif -a 2024.tif -o result.udbx --no-classify

# CSV 批量
python 变化检测/src/change_detection.py batch --csv tasks.csv

# 文件夹批量（自动配对 + 汇总报告）
python 变化检测/src/change_detection.py batch-folder --folder D:/data/xuzhou --mode subdirs

# 环境自检 / 模型列表 / 配置向导
python 变化检测/src/change_detection.py check
python 变化检测/src/change_detection.py models
python 变化检测/src/change_detection.py setup
```

### 交互菜单

```
[1] 环境自检      [2] 查看模型列表     [3] 运行变化检测 (基础)
[4] 批量处理       [5] 查看帮助         [6] 配置向导
[7] 图形界面       [8] 增强检测 (+矢量)  [9] 文件夹批量
[0] 退出
```

每次成功运行后参数自动记忆到 `config.json`，下次自动回填。

### Python API

供其他模块在代码中直接调用：

```python
from change_detection import run_enhanced_inference

ok, result = run_enhanced_inference(
    before_path="D:/data/2020.tif",
    after_path="D:/data/2024.tif",
    out_path="D:/result/change.udbx",
    model_key="building",
    gpu=0,
    classify=True,
    min_change_area=50,
)

# result["change_stats"]["classification"]
# {"新增建筑": 45, "消失地物": 23, "属性变更": 12, "其他变化": 76}
```

详细 API 文档见 [函数调用说明](变化检测/函数调用说明.md)。

### 图形界面

支持四种模式切换：单次检测 / 增强检测 / 批量 CSV / 文件夹批量。后台线程执行推理，界面不会卡死，日志实时滚动。

## 可用模型

| Key | 模型 | 输入 | 用途 |
| --- | --- | --- | --- |
| `building` | SiamSFNet | 两期影像 | 建筑物变化检测（主要使用） |
| `building-seg` | SegFormer | 单张影像 | 建筑物分割（变化分类时内部调用） |
| `landcover` | 多类别 | 单张影像 | 地物分类（地物分类模块使用） |

## 项目结构

```text
change_detection/
├── 变化检测/                     # 核心变化检测模块（张硕岐）
│   ├── src/
│   │   ├── change_detection.py          # 推理引擎、CLI、交互菜单
│   │   ├── change_detection_ui.py       # PyQt5 图形界面
│   │   ├── main_pyqt.py                 # PyQt5 主入口（四模式切换）
│   │   ├── classify_vectorize.py        # 地物分类与栅格矢量化
│   │   ├── mapper.py                    # 专题图生成
│   │   └── config.json                  # 环境路径与记忆参数
│   ├── run.bat                          # Windows 一键启动脚本
│   ├── 模块说明.md                       # 模块功能文档
│   └── 函数调用说明.md                   # API 文档
│
├── batch/                          # 批量处理模块（李昌辉）
│   ├── 01_源代码/                       # batch_controller / li_batch_api / report_generator / result_statistics
│   ├── 02_接口说明/
│   ├── 03_输入输出样例/
│   ├── 04_运行说明/
│   ├── 05_测试与校验/
│   └── requirements.txt
│
├── classification/                 # 地物分类模块
│   ├── classify_vectorize.py
│   └── 地物分类与栅格矢量化模块说明.txt
│
├── thematic_map/                   # 专题图模块（贾思雨）
│   ├── mapper.py
│   └── 专题图模块说明.txt
│
├── pyproject.toml                  # 项目元数据、依赖声明、ruff 配置
├── .python-version                 # Python 3.10
├── README.md
└── .gitignore
```

## 模块协作关系

```
                 ┌─────────────────────┐
                 │   变化检测 (张硕岐)    │
                 │   推理引擎 + GUI      │
                 └─────────┬───────────┘
                           │
          输出: UDBX 矢量 (change_polygons)
          ├─ change_type: 新增建筑/消失地物/属性变更/其他变化
          ├─ area_m2: 面积
          └─ SmID: 要素ID
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 专题图 (贾思雨)│  │ 批量管线 (李昌辉)│ │ 精度验证 (李晨曦)│
│              │  │              │  │              │
│ 读取 polygon  │  │ 循环调用推理   │  │ 读取 polygon  │
│ 分层设色渲染  │  │ 统计 + 报告   │  │ 地物分类交叉  │
└──────────────┘  └──────────────┘  └──────────────┘
```

## 注意事项

- **启动慢**：SuperMap 首次初始化需复制 34 个 JAR 文件，约 60 秒，请耐心等待。
- **输出全黑**：变化检测输出是 0/1 二值掩膜，普通图片查看器中 1 也接近黑色，请用 QGIS 或 SuperMap iDesktop 打开。
- **分类全是"未分类"**：说明 building-seg 模型缺失或 API 不兼容，分类步骤被跳过。变化检测结果本身不受影响。
- **显存不足**：GPU 选 `-1` 改用 CPU，或减小 `batch_size`。
- **换电脑**：只需修改 `变化检测/src/config.json` 中的四个路径。

## 开发检查

```powershell
uv run ruff check .
```
