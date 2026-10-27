# 开发者上手指南

本文面向第一次接触 autoLabel 的维护者。先用 `python main.py` 启动界面，再按下面的调用链阅读代码；界面目前只提供 Qt 工作台。

## 从哪里开始

1. `main.py` 准备打包后的标准输出和多进程环境，然后调用 `gui/qt_workbench.py`。
2. `gui/qt_workbench.py` 保持稳定导入路径，实际窗口在 `gui/workbench.py` 的 `Workbench`。
3. `Workbench` 管理项目切换、页面切换和跨页刷新；具体操作在 `gui/pages/`。
4. 页面把业务请求交给 `application/`。这里负责组织模型推理、预标注版本、审核队列和训练流程。
5. `core.py` 保留原导入入口、检测后端和 VOC 原子写入（含 `_write_voc_tree`）；XML 读取、质量规则、数据集及训练执行按职责放在 `engine/`。
6. `project.py` 保留旧导入接口；项目清单、应用级最近项目和项目级 SQLite 实现分别在 `storage/`。

```text
main.py → Workbench → 各页面
                       ├─ AutoPage → application/ → core.py → engine/
                       ├─ ReviewPage → canvas.py + storage/ + engine/annotations.py
                       └─ ExportPage → application/training.py → engine/
```

## 模块职责

| 位置 | 阅读重点 |
| --- | --- |
| `config.py` | 资源路径、默认模型和检测/审核阈值；区分源码运行与打包运行。 |
| `application/contracts.py`、`workflow_contracts.py` | 预标注与工作流的数据契约。 |
| `application/model_registry.py`、`preannotation.py` | 模型调用、VOC 写入、版本和风险记录。 |
| `application/quick_workflow.py`、`quick_operations.py` | 快速标注各步骤及其执行顺序。 |
| `application/high_recall.py`、`recall_evaluation.py` | 双模型候选、切片补扫、候选来源与漏检评估；改动时同时检查 `tests/test_high_recall.py`。 |
| `core.py` | 检测后端选择、VOC 扩展字段的原子写入及兼容导出；现有调用方可继续从这里导入。 |
| `engine/image_io.py` | 中文路径图片读取，以及第三方模型权重使用的临时工作目录。 |
| `engine/annotations.py` | VOC XML 读取、质量规则和审核风险分；XML 写入在 `core.py`。 |
| `engine/datasets.py`、`training.py` | YOLO 检测数据集导出、训练就绪检查和训练执行。 |
| `storage/manifest.py` | 可复制的项目清单和项目内路径校验。 |
| `storage/workspace.py` | 应用级 `workspace.db`，只保存最近项目与上次项目。 |
| `storage/project_store.py` | 每个项目的 `autolabel.db`，保存类别、状态、备注、版本、风险索引与训练记录。 |
| `gui/workbench.py` | 无边框窗口和项目生命周期。 |
| `gui/pages/review_page.py` | 审核页控件组装、当前图片加载、类别修改和保存/通过/跳过。 |
| `gui/pages/review_drawer.py`、`review_thumbnails.py`、`review_navigation.py` | 审核清单、缩略图窗口和长按翻页。它们作为 Mixin 使用 `ReviewPage` 初始化的状态。 |
| `gui/canvas.py` | 画布坐标、选框、拖动、绘制、缩放及撤销/重做。 |
| `gui/ui.py`、`icons.py`、`theme.py` | 通用 Qt 控件、图标和样式；`ui.py` 保留旧导入。 |

`groundingdino/` 是模型相关源码，不属于工作台的重构入口。先在 `application/`、`engine/` 和 `gui/` 找业务代码。

## 一张图片的生命周期

1. `AutoPage` 导入图片并发起 `QuickAnnotationWorkflow`；模型由 `ImageModelRegistry` 加载。
2. 快速流程可通过双模型和切片补扫收集候选；`ImagePreannotationService` 把候选框、来源和风险标记写入 `annotations/*.xml`，同时记录可恢复版本与风险索引，再把图片送入待审核。
3. `ReviewPage` 从 XML 加载框到 `ImageCanvas`。画布只负责本次编辑及撤销栈；保存后由页面写回 XML，并更新项目数据库中的质量记录。
4. “通过”更改项目数据库中的审核状态，不改动原始图片。“已跳过”可稍后移回待审核。
5. `ExportPage` 只把已通过图片交给 `engine/datasets.py`。导出先写临时目录，完成后才替换正式 `yolo_dataset/`。
6. 可选的训练由 `engine/training.py` 执行，结果路径记录在项目数据库中。

**数据边界：** `images/` 保存原图，`annotations/` 保存当前 VOC XML，`annotation_versions/` 保存历史快照；`autolabel.db` 保存流程元数据。不要把数据库当作框几何的唯一来源。

## 修改时先找哪里

- **新增模型后端：** 先看 `core.Detector`、`core.load_models`，再看 `application/model_registry.py` 的模型选择与结果归一化。
- **修改 XML 或质量规则：** 写入看 `core.save_as_voc_xml` 和 `_write_voc_tree`，读取与质量规则看 `engine/annotations.py`；同时检查来源字段、旧 XML、版本与风险索引。
- **修改审核交互：** 看 `gui/canvas.py` 的鼠标事件，再看 `review_page.py` 的保存和状态流转；清单、缩略图分别在对应模块。
- **新增项目字段：** 看 `storage/project_store.py` 的 `_initialize` 和迁移逻辑，兼顾已存在的数据库。
- **改样式或图标：** 改 `gui/theme.py` 或 `gui/icons.py`；不要在业务页面复制整份样式表。

## 验证

在仓库根目录执行：

```powershell
python -m py_compile main.py core.py project.py gui\qt_workbench.py
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest discover -s tests -p 'test_*.py'
```

自动测试覆盖存储、审核、画布、工作流与训练控制。改动鼠标交互后，还应在有显示环境下手工检查图片缩放/平移、框移动与八点缩放、新建框、撤销/重做、保存及审核状态。改动训练后，使用实际本地权重做一次小数据集冒烟验证；离屏测试中的训练模型是替身。

为了便于回溯，每次重构保持外部导入路径与 XML/SQLite 格式稳定；先跑上面的基线，再按职责逐步改动。
