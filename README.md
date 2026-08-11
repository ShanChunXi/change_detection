# 城市变化检测与地图更新工具

**第24届 SuperMap 杯高校 GIS 大赛 开发组**

基于 SuperMap iObjects Python 的遥感影像变化检测工具，输入两期影像，自动识别变化区域并输出分类矢量结果。

---

## 项目结构

```
├── change_detection/        # 核心变化检测模块 (张硕岐)
│   ├── src/
│   │   ├── change_detection.py      # 推理引擎
│   │   ├── change_detection_ui.py   # 图形界面
│   │   ├── main_pyqt.py             # PyQt5 主入口
│   │   ├── classify_vectorize.py    # 地物分类与矢量化
│   │   ├── mapper.py                # 专题图生成
│   │   └── config.json              # 环境配置
│   ├── run.bat                      # Windows 启动脚本
│   ├── 模块说明.md
│   └── 函数调用说明.md
│
├── batch/                   # 批量处理模块 (李昌辉)
│   ├── 01_源代码/
│   ├── 02_接口说明/
│   ├── 03_输入输出样例/
│   ├── 04_运行说明/
│   └── 05_测试与校验/
│
├── classification/          # 地物分类模块
│   ├── classify_vectorize.py
│   └── 地物分类与栅格矢量化模块说明.txt
│
└── thematic_map/            # 专题图模块
    ├── mapper.py
    └── 专题图模块说明.txt
```

## 快速开始

```bash
# 交互菜单
python change_detection/src/change_detection.py menu

# 增强版检测（矢量输出 + 变化分类）
python change_detection/src/change_detection.py run-vec -b 2020.tif -a 2024.tif -o result.udbx

# 文件夹批量处理
python change_detection/src/change_detection.py batch-folder --folder D:/data --mode subdirs

# 图形界面
python change_detection/src/change_detection.py ui
```

## 环境依赖

- SuperMap iObjects Python (GPU)
- SuperMap iObjects Java
- ML 资源包 (预训练模型)

## 变化类型

| 类型 | 含义 |
|------|------|
| 新增建筑 | T1 无建筑，T2 出现建筑 |
| 消失地物 | T1 有建筑，T2 建筑消失 |
| 属性变更 | T1/T2 都有建筑但位于变化区域 |
| 其他变化 | 非建筑类地表变化 |
