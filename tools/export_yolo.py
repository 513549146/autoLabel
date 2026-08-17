# -*- coding: utf-8 -*-
"""命令行导出 VOC -> YOLO 训练集（GUI 用户可在「微调」页操作）。

用法:
    python tools/export_yolo.py --images images --annotations annotations --out yolo_dataset [--val-ratio 0.2] [--classes "cat,dog,person"]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import finetune


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, help="图片目录")
    ap.add_argument("--annotations", required=True, help="VOC 标注目录")
    ap.add_argument("--out", required=True, help="输出数据集目录")
    ap.add_argument("--classes", default=None, help="类别，逗号分隔（缺省自动收集）")
    ap.add_argument("--val-ratio", type=float, default=0.2, help="验证集比例")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    finetune.export_voc_to_yolo(
        args.images, args.annotations, args.out,
        classes=args.classes, val_ratio=args.val_ratio, seed=args.seed,
    )


if __name__ == "__main__":
    main()
