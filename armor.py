from __future__ import annotations

import glob
import json
import shutil
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from jproperties import Properties

ITEM_TYPES = ["leather_helmet", "leather_chestplate", "leather_leggings", "leather_boots"]
ARMOR_SLOT = {
    0: "helmet",
    1: "chestplate",
    2: "leggings",
    3: "boots",
}


def _log(message: str) -> None:
    print(f"[ARMOR] {message}", flush=True)


def _split_model(model_ref: str) -> Tuple[str, str]:
    if ":" in model_ref:
        namespace, path = model_ref.split(":", 1)
        return namespace, path
    return "minecraft", model_ref


def _write_player_attachable(path: Path, gmdl: str, layer: str, slot_index: int) -> None:
    armor_type = ARMOR_SLOT.get(slot_index, "helmet")
    data = {
        "format_version": "1.10.0",
        "minecraft:attachable": {
            "description": {
                "identifier": f"geyser_custom:{gmdl}.player",
                "item": {f"geyser_custom:{gmdl}": "query.owner_identifier == 'minecraft:player'"},
                "materials": {
                    "default": "armor_leather",
                    "enchanted": "armor_leather_enchanted",
                },
                "textures": {
                    "default": f"textures/armor_layer/{layer}",
                    "enchanted": "textures/misc/enchanted_item_glint",
                },
                "geometry": {"default": f"geometry.player.armor.{armor_type}"},
                "scripts": {"parent_setup": "variable.helmet_layer_visible = 0.0;"},
                "render_controllers": ["controller.render.armor"],
            }
        },
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file)


def _iter_overrides(item_type: str) -> Iterable[Dict[str, object]]:
    item_model_file = Path(f"pack/assets/minecraft/models/item/{item_type}.json")
    if not item_model_file.exists():
        return []

    try:
        with item_model_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return []

    overrides = data.get("overrides")
    if not isinstance(overrides, list):
        return []
    return [entry for entry in overrides if isinstance(entry, dict)]


def _find_attachable(namespace: str, model_path: str) -> Optional[Path]:
    candidates = glob.glob(f"staging/target/rp/attachables/{namespace}/{model_path}*.json")
    suffix = f"{Path(model_path).name}."
    for candidate in candidates:
        if suffix in candidate:
            return Path(candidate)
    return None


def _load_optifine_layer(properties_file: Path, slot_index: int) -> Optional[str]:
    if not properties_file.exists():
        return None
    props = Properties()
    try:
        with properties_file.open("rb") as file:
            props.load(file)
    except Exception:
        return None

    key = "texture.leather_layer_2" if slot_index == 2 else "texture.leather_layer_1"
    value = props.get(key)
    if value is None or not getattr(value, "data", ""):
        return None
    return str(value.data).split(".")[0]


def _copy_model_texture(namespace: str, model_path: str) -> None:
    model_file = Path(f"pack/assets/{namespace}/models/{model_path}.json")
    if not model_file.exists():
        return

    try:
        with model_file.open("r", encoding="utf-8") as file:
            model_data = json.load(file)
        texture_ref = model_data.get("textures", {}).get("layer1")
        if not isinstance(texture_ref, str):
            return
    except Exception:
        return

    if ":" in texture_ref:
        tex_namespace, tex_path = texture_ref.split(":", 1)
    else:
        tex_namespace, tex_path = namespace, texture_ref

    source = Path(f"pack/assets/{tex_namespace}/textures/{tex_path}.png")
    if not source.exists():
        return

    destination = Path(f"staging/target/rp/textures/{namespace}/{model_path}.png")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copyfile(source, destination)


def run() -> None:
    converted = 0
    armor_root = Path("pack/assets/minecraft/optifine/cit/ia_generated_armors")
    target_layer_dir = Path("staging/target/rp/textures/armor_layer")
    target_layer_dir.mkdir(parents=True, exist_ok=True)

    for slot_index, item_type in enumerate(ITEM_TYPES):
        for override in _iter_overrides(item_type):
            predicate = override.get("predicate")
            model_ref = override.get("model")

            if not isinstance(predicate, dict) or "custom_model_data" not in predicate:
                continue
            if not isinstance(model_ref, str) or not model_ref.strip():
                continue

            namespace, model_path = _split_model(model_ref)
            model_name = model_path.split("/")[-1]
            if model_name in ITEM_TYPES:
                continue

            properties_name = f"{namespace}_{model_name}.properties"
            layer = _load_optifine_layer(armor_root / properties_name, slot_index)
            if not layer:
                continue

            layer_texture = armor_root / f"{layer}.png"
            if layer_texture.exists():
                destination_layer = target_layer_dir / f"{layer}.png"
                if not destination_layer.exists():
                    shutil.copyfile(layer_texture, destination_layer)

            _copy_model_texture(namespace, model_path)
            attachable_path = _find_attachable(namespace, model_path)
            if not attachable_path:
                continue

            try:
                with attachable_path.open("r", encoding="utf-8") as file:
                    attachable_data = json.load(file)
                identifier = attachable_data["minecraft:attachable"]["description"]["identifier"]
                gmdl = identifier.split(":", 1)[1]
            except Exception as exc:
                _log(f"Failed to read attachable {attachable_path}: {exc}")
                continue

            player_attachable = attachable_path.with_suffix(".player.json")
            _write_player_attachable(player_attachable, gmdl, layer, slot_index)
            converted += 1

    _log(f"Generated {converted} armor player attachables")


if __name__ == "__main__":
    run()
