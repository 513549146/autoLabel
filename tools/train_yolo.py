# -*- coding: utf-8 -*-
"""命令行微调训练 YOLO（GUI 用户可在「微调」页操作）。

用法:
    python tools/train_yolo.py --data yolo_dataset/dataset.yaml --model yolov8s.pt --epochs 100 --imgsz 640
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import finetune


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="dataset.yaml 路径")
    ap.add_argument("--model", default="yolov8s.pt", help="预训练权重")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    finetune.train_yolo(
        args.data, args.model, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=args.device,
    )
    print("\n训练完成。若使用 GUI，可在「微调」页勾选自动复制 best.pt；命令行请手动复制 runs/detect/train/weights/best.pt 到 weights/yolo_best.pt")


if __name__ == "__main__":
    main()
