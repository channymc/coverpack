import glob
import json
import os
from pathlib import Path
from typing import Optional, Tuple


def _split_model(model: str) -> Tuple[str, str]:
    model = model.strip()
    if ":" in model:
        namespace, path = model.split(":", 1)
        return namespace, path
    return "minecraft", model


def _mapping_file(namespace: str, block: str) -> Path:
    safe_ns = namespace.replace(":", "_")
    return Path(f"staging/target/geyser_block_{safe_ns}_{block}_mappings.json")


def get_am_file(model: str) -> Optional[str]:
    namespace, path = _split_model(model)
    files = glob.glob(f"staging/target/rp/attachables/{namespace}/{path}*.json")
    base_name = Path(path).name
    for file_path in files:
        if f"{base_name}." in file_path:
            return file_path
    return None


def write_animated_cube() -> None:
    data = {
        "format_version": "1.8.0",
        "animations": {
            "animation.geo_cube.thirdperson_main_hand": {
                "loop": True,
                "bones": {"block": {"rotation": [-20, 145, -10], "position": [0, 14, -6], "scale": [0.375, 0.375, 0.375]}},
            },
            "animation.geo_cube.thirdperson_off_hand": {
                "loop": True,
                "bones": {"block": {"rotation": [20, 40, 20], "position": [0, 13, -6], "scale": [0.375, 0.375, 0.375]}},
            },
            "animation.geo_cube.head": {"loop": True, "bones": {"block": {"position": [0, 19.9, 0], "scale": 0.625}}},
            "animation.geo_cube.firstperson_main_hand": {
                "loop": True,
                "bones": {"block": {"rotation": [140, 45, 15], "position": [-1, 17, 0], "scale": [0.52, 0.52, 0.52]}},
            },
            "animation.geo_cube.firstperson_off_hand": {
                "loop": True,
                "bones": {"block": {"rotation": [-5, 45, -5], "position": [-17.5, 17.5, 15], "scale": [0.52, 0.52, 0.52]}},
            },
        },
    }
    path = Path("staging/target/rp/animations/cube.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file)


def write_geometry_cube() -> None:
    data = {
        "format_version": "1.19.40",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": "geometry.cube",
                    "texture_width": 16,
                    "texture_height": 16,
                    "visible_bounds_width": 2,
                    "visible_bounds_height": 2.5,
                    "visible_bounds_offset": [0, 0.75, 0],
                },
                "bones": [
                    {
                        "name": "block",
                        "binding": "c.item_slot == 'head' ? 'head' : q.item_slot_to_bone_name(c.item_slot)",
                        "pivot": [0, 8, 0],
                        "cubes": [
                            {
                                "origin": [-8, 0, -8],
                                "size": [16, 16, 16],
                                "uv": {
                                    "north": {"uv": [0, 0], "uv_size": [16, 16]},
                                    "east": {"uv": [0, 0], "uv_size": [16, 16]},
                                    "south": {"uv": [0, 0], "uv_size": [16, 16]},
                                    "west": {"uv": [0, 0], "uv_size": [16, 16]},
                                    "up": {"uv": [16, 16], "uv_size": [-16, -16]},
                                    "down": {"uv": [16, 16], "uv_size": [-16, -16]},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path = Path("staging/target/rp/models/blocks/cube.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file)


def write_mapping_block(block: str, namespace: str = "minecraft") -> None:
    block_key = f"{namespace}:{block}"
    data = {
        "format_version": 1,
        "blocks": {
            block_key: {
                "name": block,
                "geometry": "geometry.cube",
                "included_in_creative_inventory": False,
                "only_override_states": True,
                "place_air": True,
                "state_overrides": {},
            }
        },
    }

    path = _mapping_file(namespace, block)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def register_block(
    block: str,
    gmdl: str,
    state: str,
    texture: str,
    block_material: str,
    geometry: str,
    namespace: str = "minecraft",
) -> None:
    path = _mapping_file(namespace, block)
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    block_key = f"{namespace}:{block}"
    data.setdefault("blocks", {}).setdefault(block_key, {}).setdefault("state_overrides", {})
    data["blocks"][block_key]["state_overrides"][state] = {
        "name": f"block_{gmdl}",
        "display_name": f"block_{gmdl}",
        "geometry": geometry,
        "material_instances": {
            "*": {
                "texture": texture,
                "render_method": block_material,
                "face_dimming": True,
                "ambient_occlusion": True,
            }
        },
    }

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def regsister_block(block: str, gmdl: str, state: str, texture: str, block_material: str, geometry: str) -> None:
    register_block(block, gmdl, state, texture, block_material, geometry, namespace="minecraft")


def create_terrain_texture(gmdl: str, texture_file: str) -> str:
    path = Path("staging/target/rp/textures/terrain_texture.json")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    texture_key = f"block_{gmdl}"
    data.setdefault("texture_data", {})[texture_key] = {"textures": texture_file}

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    return texture_key


def get_geometry_block(model: str) -> str:
    namespace, path = _split_model(model)
    matches = glob.glob(f"staging/target/rp/models/blocks/{namespace}/{path}.json")
    if not matches:
        return "geometry.cube"

    geometry_file = matches[0]
    try:
        with open(geometry_file, "r", encoding="utf-8") as file:
            raw = file.read()
        if not raw.strip():
            os.remove(geometry_file)
            return "geometry.cube"
        data = json.loads(raw)
        return data["minecraft:geometry"][0]["description"]["identifier"]
    except Exception:
        return "geometry.cube"
