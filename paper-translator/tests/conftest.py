from pathlib import Path
import shutil

import pytest
from PIL import Image

from paper_translator.common import file_sha, save_json
from paper_translator.extract import extract
from paper_translator.split import split


@pytest.fixture
def work(tmp_path):
    destination = tmp_path / "paper"
    source = destination / "source" / "tex"
    shutil.copytree(Path(__file__).parent / "fixtures" / "tiny", source)
    Image.new("RGB", (80, 40), "navy").save(source / "figure.png")
    files = [{"path": p.relative_to(destination).as_posix(), "sha256": file_sha(p)} for p in source.rglob("*") if p.is_file()]
    save_json(destination / "manifest.json", {"paper_id": "fixture-v1", "source_kind": "tex", "files": files})
    extract(destination)
    split(destination)
    return destination
