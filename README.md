# 城市变化检测与地图更新工具

第 24 届 SuperMap 杯高校 GIS 大赛开发组作品。输入两期遥感影像，自动识别变化区域，输出带分类属性的矢量 polygon，支持批量处理、专题制图和统计报告。

---

## 如何打开软件

### 第一步：安装 SuperMap 三件套

从大赛官网或 SuperMap 官网下载三个包，放到同一个目录（建议 `F:/supermap/`）：

| 组件 | 解压后大概长这样 | 约大小 |
| --- | --- | --- |
| **SuperMap iObjects Python (GPU)** | `…/conda/python.exe` | ~8 GB |
| **SuperMap iObjects Java** | `…/jre1.8_x64/` 和 `…/Bin/` | ~4 GB |
| **ML 资源包** | `…/resources_ml/model/` | ~5 GB |

### 第二步：安装 PyQt5

打开终端（PowerShell 或 cmd），用 SuperMap 自带的 Python 装：

```powershell
F:/supermap/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe -m pip install PyQt5
```

### 第三步：打开图形化配置工具

```powershell
cd 变化检测/src
python setup_config.py
```

> 如果上面命令报 "No module named PyQt5"，说明还没装第二步。也可以用任意一个装过 PyQt5 的 Python 来打开它 —— `setup_config.py` 不依赖 SuperMap，只用来编辑配置文件。

打开后界面如下：

- **四个路径输入框**，每个右边有「浏览」按钮
- 填完一个路径自动检测是否存在（✅ / ❌）
- 点击「**自动检测**」可扫描 C/D/E/F 盘的标准安装位置
- 点击「**保存配置**」会写入 `config.json`，然后窗口自动关闭

### 第四步：启动主界面

配置保存后，在同一个终端里运行：

```powershell
python change_detection.py ui
```

首次启动约 60 秒（SuperMap 初始化，复制 34 个 JAR 文件），后续启动几秒即可。

### 其他打开方式

```powershell
# 交互菜单（命令行选单，不需要 PyQt5）
python change_detection.py menu

# 命令行直接跑
python change_detection.py run -b 2020.tif -a 2024.tif -o result.udbx

# 增强检测（矢量输出 + 变化分类）
python change_detection.py run-vec -b 2020.tif -a 2024.tif -o result.udbx
```

---

## 配置详情（`config.json`）

四个路径保存在 `变化检测/src/config.json`，手动编辑也可以：

```json
{
    "python_path":  "F:/supermap/supermap-iobjectspy-env-gpu-2026-win64/conda/python.exe",
    "java_home":    "F:/supermap/supermap-iobjectsjava-2026-win-all/jre1.8_x64",
    "iobjects_bin": "F:/supermap/supermap-iobjectsjava-2026-win-all/Bin",
    "resources_ml": "F:/supermap/supermap-iobjectspy-resources_ml-2025u1/resources_ml"
}
```

`setup_config.py` 填了 `python_path` 后会自动推断其他三个路径（如果它们的目录结构跟标准安装一致的话）。

---

## 变化类型

| 类型 | 判断逻辑 |
| --- | --- |
| 新增建筑 | T1 无建筑物 → T2 出现建筑物 |
| 消失地物 | T1 有建筑物 → T2 建筑物消失 |
| 属性变更 | T1/T2 均有建筑物且位于变化区域（改建/扩建） |
| 其他变化 | 非建筑类地表变化（道路、水体、植被等） |

输出矢量数据集每个 polygon 包含 `change_type` 和 `area_m2` 属性。

---

## 功能状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 基础变化检测 | 已实现 | SiamSFNet 模型，输出栅格二值掩膜 |
| 增强变化检测 | 已实现 | 栅格→矢量 + 四类变化自动分类 |
| CSV 批量处理 | 已实现 | 任务清单 CSV 逐对推理 |
| 文件夹批量处理 | 已实现 | 自动扫描配对 → 顺序推理 → 状态追踪 → 汇总报告 |
| 变化类型分类 | 已实现 | 新增建筑 / 消失地物 / 属性变更 / 其他变化 |
| PyQt5 图形界面 | 已实现 | GIS 风格菜单栏 + 工具栏，后台线程，日志实时滚动 |
| 独立配置工具 | 已实现 | `setup_config.py`：零 SuperMap 依赖，图形化配 config.json |
| 批量管线与报告 | 已实现 | 状态统计、Excel/图表/Word/PDF 报告 |
| 地物分类与矢量化 | 已实现 | 单时相地物分类 + 栅格转矢量 |
| 专题图生成 | 已实现 | 按变化类型分层设色输出专题地图 |
| CLI 命令行 | 已实现 | 7 个子命令，参数化调用 |
| 交互菜单 | 已实现 | 9 选项终端菜单，记忆上次参数 |

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
│   │   ├── change_detection_ui.py       # PyQt5 图形界面（主入口）
│   │   ├── main_pyqt.py                 # PyQt5 旧版（已被 change_detection_ui.py 取代）
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
