"""Model installation is explicit, validated, and never exposes partial files."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.model_library import DOWNLOADABLE_MODELS, InstalledModel, ModelLibrary
from application.workflow_contracts import QuickAnnotationConfig


class _Response:
    url = "https://release-assets.githubusercontent.com/model.pt"
    headers = {"Content-Length": "7"}

    def __init__(self):
        self._chunks = [b"weights", b""]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return self._chunks.pop(0)


class ModelLibraryTests(unittest.TestCase):
    def test_project_enable_state_and_managed_removal(self):
        class Project:
            values = {}

            def metadata_value(self, key, default):
                return self.values.get(key, default)

            def set_metadata_value(self, key, value):
                self.values[key] = value

        with tempfile.TemporaryDirectory() as directory:
            library = ModelLibrary(directory)
            offer = DOWNLOADABLE_MODELS[0]
            path = library.download_path(offer)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"downloaded")
            model = next(item for item in library.installed() if item.path == str(path))
            project = Project()
            self.assertTrue(library.is_enabled(project, model))
            library.set_enabled(project, model, False)
            self.assertFalse(library.is_enabled(project, model))
            library.set_enabled(project, model, True)
            self.assertTrue(library.is_enabled(project, model))
            library.remove(model)
            self.assertFalse(path.exists())
            self.assertIn(offer, DOWNLOADABLE_MODELS)

    def test_bundled_model_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ModelLibrary(directory)
            model = InstalledModel("built in", "yoloe", "C:/weights/model.pt", "应用自带", "")
            with self.assertRaisesRegex(ValueError, "安全删除"):
                library.remove(model)

    def test_official_bundled_model_can_be_deleted_and_reinstalled(self):
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / "weights"
            weights.mkdir()
            offer = next(item for item in DOWNLOADABLE_MODELS if item.filename == "yolo11n.pt")
            bundled = weights / offer.filename
            bundled.write_bytes(b"checkpoint")
            library = ModelLibrary(Path(directory) / "library")
            model = InstalledModel(offer.title, offer.provider_id, str(bundled), "应用自带", offer.detail)
            with patch("application.model_library.config.WEIGHTS_DIR", str(weights)):
                self.assertTrue(library.can_remove(model))
                library.remove(model)
            self.assertFalse(bundled.exists())
            self.assertFalse(library.download_path(offer).exists())
            self.assertIn(offer, DOWNLOADABLE_MODELS)

    def test_import_copies_a_validated_detection_checkpoint_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "customer.pt"
            source.write_bytes(b"checkpoint")
            library = ModelLibrary(root / "library")
            with patch.object(ModelLibrary, "validate_checkpoint") as validate:
                first = library.import_checkpoint(source)
                second = library.import_checkpoint(source)
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), b"checkpoint")
            self.assertEqual(validate.call_count, 2)
            self.assertIn(str(first), [model.path for model in library.installed()])

    def test_import_rejects_an_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "customer.onnx"
            source.write_bytes(b"data")
            with self.assertRaisesRegex(ValueError, ".pt"):
                ModelLibrary.validate_checkpoint(source, "custom_yolo")

    def test_download_commits_only_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ModelLibrary(directory)
            offer = DOWNLOADABLE_MODELS[0]
            progress = []
            with patch("application.model_library.urllib.request.urlopen", return_value=_Response()), \
                 patch.object(ModelLibrary, "validate_checkpoint") as validate:
                path = library.download(offer, progress.append)
            self.assertEqual(path.read_bytes(), b"weights")
            self.assertEqual(progress[-1], 100)
            validate.assert_called_once()
            self.assertEqual(list(path.parent.glob(".download-*")), [])

    def test_failed_validation_removes_the_partial_download(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ModelLibrary(directory)
            offer = DOWNLOADABLE_MODELS[0]
            with patch("application.model_library.urllib.request.urlopen", return_value=_Response()), \
                 patch.object(ModelLibrary, "validate_checkpoint", side_effect=ValueError("invalid")):
                with self.assertRaisesRegex(ValueError, "invalid"):
                    library.download(offer)
            self.assertFalse(library.download_path(offer).exists())
            self.assertEqual(list(library.download_path(offer).parent.glob(".download-*")), [])

    def test_custom_yoloe_path_survives_configuration_normalization(self):
        settings = QuickAnnotationConfig(primary_provider_id="yoloe", model_weights_path="C:/models/yoloe.pt")
        self.assertEqual(settings.normalized().model_weights_path, "C:/models/yoloe.pt")


if __name__ == "__main__":
    unittest.main()
