# autoLabel - 自动化标注工具

基于 [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO) 的自动目标检测标注工具，输入图片与文本提示词，输出 VOC 格式的 XML 标注文件，可直接用 LabelImg 打开。

## 目录结构

```
autoLabel/
├── main.py               # 入口，运行 GUI
├── config.py             # 路径与参数配置
├── core.py               # 核心逻辑（模型加载、检测、VOC 读写、批处理）
├── finetune.py           # 微调逻辑（VOC→YOLO 导出、YOLO 训练）
├── gui/                  # 图形界面
│   ├── app.py            # 主窗口（标签页容器）
│   ├── auto_tab.py       # 「自动标注」页（含检测模型选择）
│   ├── review_tab.py     # 「人工审核」页（含缩放/平移）
│   └── finetune_tab.py   # 「微调」页（可视化导出/训练）
├── tools/                # 命令行微调脚本
│   ├── export_yolo.py    # VOC → YOLO 训练集导出
│   └── train_yolo.py     # Ultralytics YOLO 微调训练
├── requirements.txt      # Python 依赖
├── groundingdino/        # 旧版 Grounding DINO 库源码（已内置）
├── weights/              # 模型权重
│   ├── grounding-dino-base/  # Grounding DINO 1.5（推荐）
│   ├── groundingdino_swint_ogc.pth  # 旧版 GD
│   ├── yolo_best.pt      # 微调后的 YOLO 权重（训练后放入）
│   ├── sam_hq_vit_h.pth
│   └── bert-base-uncased/  # 本地 bert 文本编码器（离线可用）
├── images/               # 输入图片
└── annotations/          # 输出 VOC XML 标注
```

## 安装

1. 安装依赖：

```bash
pip install -r requirements.txt
```

> `groundingdino/` 库源码已内置到项目中，无需再单独克隆安装。若需 GPU 加速，请按 PyTorch 官网安装带 CUDA 的 `torch` 版本。

2. （可选）如需使用 SAM-HQ 分割，安装 [segment-anything-hq](https://github.com/SysCV/sam-hq)：

```bash
git clone https://github.com/SysCV/sam-hq.git
cd sam-hq
pip install -e .
```

## 使用方法

### 图形界面（默认）

```bash
python main.py
```

运行后弹出图形界面，包含三个标签页：

- **自动标注**：选择输入/输出目录、检测模型、填写提示词与阈值，点击「开始标注」批量生成 VOC 预标注，进度与日志实时显示。
- **人工审核**：打开图片目录后逐张查看/修正标注框。左侧图片列表、中间画布、右侧框列表（三栏可拖动），左键拖拽画框、点击选中（列表/画布双向同步）、Delete 删除错误框、下拉框改类别、保存；滚轮缩放、中键拖动平移、`+/−/适应窗口/实际大小` 控制缩放，「重新标注此图」对当前图片重新运行检测。
- **微调**：可视化完成微调全流程——① 导出 VOC→YOLO 训练集，② 选择预训练模型/参数开始训练（进度实时显示），训练完成自动把 best.pt 复制为 `weights/yolo_best.pt`。

### 检测模型

在「自动标注」页可切换检测模型：

| 模型 | 说明 |
| --- | --- |
| Grounding DINO 1.5（推荐） | 新版开源零样本模型，精度明显高于旧版 |
| Grounding DINO（旧版） | 2023 年 SwinT-OGC，精度较低 |
| YOLO（微调） | 用你的数据微调后的闭集检测，可达工业级 |

### 命令行

如需命令行批量处理，可调用：

```python
from core import batch_process

batch_process("images", "annotations", "person . dog . cat")
```

## 微调工作流（提升到工业级）

零样本模型只是冷启动，工业级精度需要微调。可直接在 GUI「微调」页操作，也可用命令行。

流程：

1. 用「自动标注 + 人工审核」修正出一批高质量 VOC 标注（几百张）。
2. 导出为 YOLO 训练集（GUI「微调」页第一步，或命令行）：

```bash
python tools/export_yolo.py --images images --annotations annotations --out yolo_dataset
```

3. 微调训练（需 `pip install ultralytics`；GUI「微调」页第二步，或命令行）：

```bash
python tools/train_yolo.py --data yolo_dataset/dataset.yaml --model yolov8s.pt --epochs 100 --imgsz 640
```

4. 训练完成后把 `best.pt` 放到 `weights/yolo_best.pt`（GUI 勾选「自动复制」即可），然后在「自动标注」页把检测模型切换为「YOLO（微调）」。

迭代：用微调后的模型继续预标注 → 人工少量修正 → 再训练，几轮后精度持续上升。

## 配置说明

所有配置集中在 `config.py`：

| 参数 | 说明 |
| --- | --- |
| `DETECTOR` | 检测模型：`gd15` / `gd_ogc` / `yolo` |
| `PROMPT` | 文本提示词，多个类别用 ` . ` 分隔（**用英文**） |
| `BOX_THRESHOLD` | 目标框置信度阈值 |
| `TEXT_THRESHOLD` | 文本匹配置信度阈值 |
| `INPUT_DIR` | 输入图片目录（默认 `images/`） |
| `OUTPUT_DIR` | 输出标注目录（默认 `annotations/`） |
| `GD15_DIR` | Grounding DINO 1.5 权重目录 |
| `YOLO_WEIGHTS` | 微调后 YOLO 权重路径（`weights/yolo_best.pt`） |
| `GROUNDINGDINO_WEIGHTS` | 旧版 Grounding DINO 权重路径 |
| `BERT_ENCODER_DIR` | 本地 bert 文本编码器目录（默认 `weights/bert-base-uncased`） |
| `SAM_WEIGHTS` | SAM-HQ 权重路径（预留） |

## 关于类别名

Grounding DINO 的检测权重是用英文 bert（`bert-base-uncased`）训练的，文本编码器只认英文，中文提示词会被映射成 `[UNK]`（输出 `unk`）。因此提示词与类别名统一用英文，例如 `cat . dog . person`。若确实需要中文标签，可在人工审核阶段或导出后再批量替换。
