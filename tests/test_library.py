from types import SimpleNamespace
from unittest.mock import MagicMock

import modules.builder  # noqa: F401 -- pre-import to break plex<->builder circular import
import modules.library as library_module
from modules.library import Library


def test_validate_image_size_uses_file_size_not_compare_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "MAX_IMAGE_SIZE", 100)
    image_path = tmp_path / "poster.jpg"
    image_path.write_bytes(b"x" * 50)
    image = SimpleNamespace(location=str(image_path), compare=f"{image_path}:50:123456")

    assert Library.validate_image_size(SimpleNamespace(), image) is True


def test_validate_image_size_rejects_oversized_local_image(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "MAX_IMAGE_SIZE", 50)
    monkeypatch.setattr(library_module, "logger", MagicMock())
    image_path = tmp_path / "poster.jpg"
    image_path.write_bytes(b"x" * 50)
    image = SimpleNamespace(location=str(image_path), compare="cache-fingerprint")

    assert Library.validate_image_size(SimpleNamespace(), image) is False
    library_module.logger.error.assert_called_once()
