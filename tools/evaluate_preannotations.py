"""Compare a sealed human gold XML directory with pre-annotation XML files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from application.recall_evaluation import evaluate_recall  # noqa: E402


def load_directory(directory: Path) -> dict:
    if not directory.is_dir():
        raise ValueError(f"目录不存在：{directory}")
    return {path.stem: core.load_voc_xml(str(path)) for path in sorted(directory.glob("*.xml"))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="在独立人工真值集上评估预标注漏检")
    parser.add_argument("--ground-truth", type=Path, required=True, help="人工真值 VOC XML 目录")
    parser.add_argument("--predictions", type=Path, required=True, help="待评估的预标注 VOC XML 目录")
    parser.add_argument("--iou", type=float, default=0.5, help="正确匹配所需的 IoU，默认 0.5")
    parser.add_argument("--min-recall", action="append", default=[], metavar="CLASS=VALUE",
                        help="关键类别最低召回率，可重复指定")
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args(argv)
    truth = load_directory(args.ground_truth)
    if not truth:
        parser.error("人工真值目录没有 XML 文件")
    proposed = load_directory(args.predictions)
    # Predictions for images outside the sealed gold set are not scored.
    report = evaluate_recall(truth, {name: proposed.get(name, ()) for name in truth}, args.iou)
    limits = {}
    for item in args.min_recall:
        try:
            name, value = item.split("=", 1)
            limits[name.strip()] = float(value)
            if not name.strip() or not 0 <= limits[name.strip()] <= 1:
                raise ValueError
        except ValueError:
            parser.error(f"无效的 --min-recall：{item}")
    failures = []
    for name, minimum in limits.items():
        actual = report["classes"].get(name, {}).get("recall")
        if actual is None or actual < minimum:
            failures.append({"class": name, "required": minimum, "actual": actual})
    report["gate_failures"] = failures
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
