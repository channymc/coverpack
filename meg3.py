from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Optional

from PIL import Image


def _log(message: str) -> None:
    print(f"[MEG3] {message}", flush=True)


def run() -> None:
    files = glob.glob("staging/target/rp/attachables/modelengine/**/*.json", recursive=True)
    if not files:
        _log("No ModelEngine attachables found; skipping")
        return

    texture_done = set()
    converted = 0

    for file in files:
        texture_path: Optional[Path] = None
        try:
            with open(file, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            description = data["minecraft:attachable"]["description"]
            texture_file = description["textures"]["default"]
            texture_path = Path(f"staging/target/rp/{texture_file}.png")

            if os.getenv("ATTACHABLE_MATERIAL") != "entity_emissive_alpha_one_sided":
                description.setdefault("materials", {})
                description["materials"]["default"] = "entity_emissive_alpha_one_sided"
                description["materials"]["enchanted"] = "entity_emissive_alpha_one_sided"
                with open(file, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)

            if texture_file in texture_done or not texture_path.exists():
                continue

            image = Image.open(texture_path).convert("RGBA")
            image.putalpha(51)
            pixels = image.load()
            for x in range(image.width):
                for y in range(image.height):
                    if pixels[x, y] == (0, 0, 0, 51):
                        pixels[x, y] = (0, 0, 0, 0)
            image.save(texture_path)

            texture_done.add(texture_file)
            converted += 1
        except Exception as exc:
            _log(f"Failed processing {file}: {exc}")
            if texture_path is not None:
                _log(f"Texture candidate: {texture_path}")

    _log(f"Adjusted {converted} ModelEngine textures")


if __name__ == "__main__":
    run()
