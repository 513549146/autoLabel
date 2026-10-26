"""Ultralytics 训练执行：进度回调、取消与结果路径。"""

from __future__ import annotations

import os
import sys
import time

import config
from .image_io import _working_directory


class TrainingCancelled(Exception):
    """用户请求停止训练时抛出，供上层区分取消和失败。"""


def train_yolo_dataset(
    data_yaml, base_model, epochs, runs_dir, on_progress=None, on_stage=None, on_batch=None, should_stop=None
):
    """执行 YOLO 训练，返回训练目录与最佳权重路径。"""
    if not os.path.isfile(data_yaml):
        raise FileNotFoundError("请先导出训练集（未找到 data.yaml）")
    try:
        epochs = int(epochs)
    except (TypeError, ValueError) as error:
        raise ValueError("训练轮数必须是正整数") from error
    if epochs < 1:
        raise ValueError("训练轮数必须大于 0")

    def check_stop():
        if should_stop and should_stop():
            raise TrainingCancelled("训练已按请求停止")

    def stage(message):
        if on_stage:
            on_stage(message)

    check_stop()
    data_yaml = os.path.abspath(data_yaml)
    runs_dir = os.path.abspath(runs_dir)
    if not os.path.isabs(base_model):
        candidates = (
            os.path.join(config.WEIGHTS_DIR, base_model),
            os.path.join(config.BUNDLE_DIR, base_model),
            os.path.abspath(base_model),
        )
        base_model = next((path for path in candidates if os.path.isfile(path)), base_model)
    if getattr(sys, "frozen", False) and not os.path.isfile(base_model):
        raise FileNotFoundError(f"发布包缺少预训练模型 {base_model}；请重新获取完整安装包")
    amp_reference = os.path.join(config.BUNDLE_DIR, "yolo26n.pt")
    if getattr(sys, "frozen", False) and not os.path.isfile(amp_reference):
        raise FileNotFoundError("发布包缺少混合精度检查模型 yolo26n.pt；请重新获取完整安装包")
    dataset_root = os.path.dirname(os.path.abspath(data_yaml))
    image_dirs = [os.path.join(dataset_root, "images", split) for split in ("train", "val")]
    train_images = 0
    if all(os.path.isdir(path) for path in image_dirs):
        counts = [sum(name.lower().endswith(config.IMG_EXT) for name in os.listdir(path)) for path in image_dirs]
        train_images = counts[0]
        stage(f"数据集就绪：训练 {counts[0]} 张，验证 {counts[1]} 张")
    stage("正在加载预训练模型")
    from ultralytics import YOLO

    os.makedirs(runs_dir, exist_ok=True)
    model = YOLO(base_model)
    check_stop()
    stage("正在检查数据集并扫描图片与标签；首次运行可能较久")
    # Windows 打包版启动工作进程较慢；小数据集少开进程，大数据集再增加并行度。
    train_workers = None
    if os.name == "nt":
        train_workers = min(4 if train_images >= 512 else 2, max(1, (os.cpu_count() or 2) // 2))
        stage(f"数据加载计划：{train_workers} 个训练工作进程（大数据集自动增加）")
    batch_state = {"done": 0, "last_report": 0.0, "last_epoch": 0}

    def on_pretrain_start(trainer):
        check_stop()
        stage("正在读取训练配置并准备模型")
        get_dataloader = getattr(trainer, "get_dataloader", None)
        if callable(get_dataloader):

            def reported_get_dataloader(*args, **kwargs):
                check_stop()
                split = "验证" if kwargs.get("mode") == "val" else "训练"
                stage(f"正在扫描{split}集并启动数据加载进程")
                loader = get_dataloader(*args, **kwargs)
                check_stop()
                stage(f"{split}集数据加载器已就绪")
                return loader

            trainer.get_dataloader = reported_get_dataloader

    def on_pretrain_end(trainer):
        check_stop()
        stage("训练与验证数据加载器已就绪，准备开始训练")

    def on_train_start(trainer):
        check_stop()
        train_count = len(trainer.train_loader.dataset)
        val_count = len(trainer.test_loader.dataset)
        stage(f"开始训练：训练 {train_count} 张，验证 {val_count} 张，共 {epochs} 轮")

    def on_epoch_start(trainer):
        check_stop()
        batch_state["done"] = 0
        batch_state["last_report"] = 0.0
        stage(f"第 {int(trainer.epoch) + 1}/{trainer.epochs} 轮：训练批次进行中")

    def on_batch_end(trainer):
        check_stop()
        batch_state["done"] += 1
        total = len(trainer.train_loader)
        now = time.monotonic()
        if on_batch and (batch_state["done"] in (1, total) or now - batch_state["last_report"] >= 1.5):
            on_batch(int(trainer.epoch) + 1, int(trainer.epochs), batch_state["done"], total)
            batch_state["last_report"] = now

    def on_epoch_train_end(trainer):
        check_stop()
        stage(f"第 {int(trainer.epoch) + 1}/{trainer.epochs} 轮：训练批次完成，正在验证与保存")

    def on_fit_epoch_end(trainer):
        check_stop()
        current = int(getattr(trainer, "epoch", -1)) + 1
        if current <= batch_state["last_epoch"] or current > int(trainer.epochs):
            return  # Ultralytics also calls this during final best-model evaluation.
        batch_state["last_epoch"] = current
        details = []
        try:
            losses = trainer.label_loss_items(getattr(trainer, "tloss", None), prefix="train")
            details.extend(f"{name}={float(value):.4f}" for name, value in losses.items())
        except Exception:
            pass
        try:
            for name, value in (getattr(trainer, "metrics", {}) or {}).items():
                if "map" in str(name).lower():
                    details.append(f"{name}={float(value):.4f}")
        except Exception:
            pass
        if on_progress:
            on_progress(current, int(trainer.epochs), " · ".join(details))

    model.add_callback("on_pretrain_routine_start", on_pretrain_start)
    model.add_callback("on_pretrain_routine_end", on_pretrain_end)
    model.add_callback("on_train_start", on_train_start)
    model.add_callback("on_train_epoch_start", on_epoch_start)
    model.add_callback("on_train_batch_start", lambda trainer: check_stop())
    model.add_callback("on_train_batch_end", on_batch_end)
    model.add_callback("on_train_epoch_end", on_epoch_train_end)
    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    model.add_callback("on_val_batch_end", lambda validator: check_stop())
    model.add_callback("on_train_end", lambda trainer: stage("训练完成，正在整理最优权重"))
    try:
        train_options = {
            "data": data_yaml,
            "epochs": epochs,
            "project": runs_dir,
            "name": "finetune",
            "exist_ok": False,
            "verbose": True,
        }
        if train_workers is not None:
            train_options["workers"] = train_workers
        # Ultralytics 的 AMP 检查按裸文件名寻找 yolo26n.pt；临时切换目录，
        # 直接使用打包资源，避免重复复制或联网下载。
        if os.path.isfile(amp_reference):
            with _working_directory(config.BUNDLE_DIR):
                model.train(**train_options)
        else:
            model.train(**train_options)
        check_stop()
    except TrainingCancelled:
        trainer = getattr(model, "trainer", None)
        if trainer is not None:
            for name in ("train_loader", "test_loader"):
                loader = getattr(trainer, name, None)
                if hasattr(loader, "close"):
                    try:
                        loader.close()
                    except Exception:
                        pass
        raise
    run_dir = str(model.trainer.save_dir)
    best = os.path.join(run_dir, "weights", "best.pt")
    if not os.path.isfile(best):
        raise FileNotFoundError("训练结束但未找到 best.pt")
    return {"run_dir": run_dir, "best_weights": best}
