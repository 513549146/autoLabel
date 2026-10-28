"""Export local human-review time and edit counts for a frozen image manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from application.review_reporting import build_review_activity_report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从项目 SQLite 汇总人工审核活跃时间与修改动作")
    parser.add_argument("--project", type=Path, required=True, help="项目根目录")
    parser.add_argument("--image-list", type=Path, required=True, help="冻结的图片文件名清单，每行一张")
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径；默认打印到终端")
    args = parser.parse_args(argv)
    names = [line.strip() for line in args.image_list.read_text(encoding="utf-8-sig").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    report = build_review_activity_report(args.project, names)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
