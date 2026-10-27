"""Create a tiny original fixture for equation/table/image preview (not an arXiv paper)."""
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from paper_translator.common import file_sha, save_json
from paper_translator.extract import extract
from paper_translator.split import split


def main():
    project = Path(__file__).resolve().parents[1]
    work = project / "workspace" / "preview-fixture"
    source = work / "source" / "tex"
    if not source.exists():
        shutil.copytree(project / "tests" / "fixtures" / "tiny", source)
        img = Image.new("RGB", (480, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.line((40, 80, 440, 80), fill="#64748b", width=3)
        draw.rounded_rectangle((175, 35, 305, 125), 10, fill="#1e3a8a")
        draw.text((222, 70), "H", fill="white", font_size=26)
        img.save(source / "figure.png")
        save_json(work / "manifest.json", {"schema_version": 1, "paper_id": "preview-fixture-v1", "version": "v1", "source_kind": "tex", "files": [{"path": p.relative_to(work).as_posix(), "sha256": file_sha(p)} for p in source.rglob("*") if p.is_file()]})
    extract(work)
    split(work)
    print(work)


if __name__ == "__main__":
    main()
