"""跨模块回归：审核 XML 仍能通过旧入口导出目标检测数据集。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import core


class ExportContractTests(unittest.TestCase):
    def test_voc_roundtrip_and_yolo_export_keep_coordinates_and_classes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            annotations = root / "annotations"
            images.mkdir()
            annotations.mkdir()

            # 两张图确保训练集和验证集都会生成；中文文件名覆盖 Windows 路径读取。
            filenames = ("样本一.png", "样本二.png")
            for filename in filenames:
                Image.new("RGB", (100, 50), "white").save(images / filename)
                core.save_as_voc_xml(
                    [{"name": "defect", "bbox": [10, 5, 30, 25], "origin": "verified"}],
                    str(images / filename),
                    str(annotations),
                )

            original = core.load_voc_xml(str(annotations / "样本一.xml"))
            self.assertEqual(original[0]["bbox"], [10, 5, 30, 25])
            self.assertEqual(original[0]["origin"], "verified")

            result = core.export_yolo_dataset(
                str(images),
                str(annotations),
                filenames,
                str(root / "yolo_dataset"),
                categories=["defect"],
                validation_ratio=0.5,
            )
            self.assertEqual(result["classes"], ["defect"])
            self.assertEqual((result["train_images"], result["val_images"], result["objects"]), (1, 1, 2))
            for filename in filenames:
                label_files = list((root / "yolo_dataset" / "labels").glob(f"*/{Path(filename).stem}.txt"))
                self.assertEqual(len(label_files), 1)
                self.assertEqual(label_files[0].read_text(encoding="utf-8"), "0 0.200000 0.300000 0.200000 0.400000")
            self.assertIn('0: "defect"', Path(result["data_yaml"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
