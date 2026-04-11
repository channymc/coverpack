from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _log(message: str) -> None:
    print(f"[SHIELD] {message}", flush=True)


def _find_attachable(namespace: str, path: str) -> Optional[str]:
    files = glob.glob(f"staging/target/rp/attachables/{namespace}/{path}*.json")
    suffix = f"{Path(path).name}."
    for file_path in files:
        if suffix in file_path:
            return file_path
    return None


def _cache_overrides() -> None:
    shield_model = Path("pack/assets/minecraft/models/item/shield.json")
    if not shield_model.exists():
        return

    with shield_model.open("r", encoding="utf-8") as file:
        data = json.load(file)

    overrides = data.get("overrides")
    if not isinstance(overrides, list):
        return

    for override in overrides:
        if not isinstance(override, dict):
            continue

        model = override.get("model")
        predicate = override.get("predicate")
        if not isinstance(model, str) or model == "item/shield":
            continue
        if not isinstance(predicate, dict) or "custom_model_data" not in predicate:
            continue

        cache_path = Path(f"cache/shield/{predicate['custom_model_data']}.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not cache_path.exists():
            cache_path.write_text("{}", encoding="utf-8")

        try:
            with cache_path.open("r", encoding="utf-8") as file:
                cache_data = json.load(file)
        except Exception:
            cache_data = {}

        if "blocking" in predicate:
            cache_data["blocking"] = model
        else:
            cache_data["default"] = model
        cache_data["check"] = int(cache_data.get("check", 0)) + 1

        with cache_path.open("w", encoding="utf-8") as file:
            json.dump(cache_data, file, indent=2)


def run() -> None:
    _cache_overrides()
    processed = 0

    for cache_file in glob.glob("cache/shield/*.json"):
        with open(cache_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        if data.get("check") != 2:
            continue
        if not isinstance(data.get("default"), str) or not isinstance(data.get("blocking"), str):
            continue

        animation: Dict[str, str] = {}
        animate: List[Dict[str, str]] = []
        safe_attachable = None
        attachable_data = None

        for state in ["default", "blocking"]:
            model_ref = data[state]
            if ":" in model_ref:
                namespace, path = model_ref.split(":", 1)
            else:
                namespace, path = "minecraft", model_ref

            attachable_file = _find_attachable(namespace, path)
            if not attachable_file:
                continue

            with open(attachable_file, "r", encoding="utf-8") as file:
                current_data = json.load(file)

            description = current_data["minecraft:attachable"]["description"]
            animation_item = description.get("animations", {})
            gmdl = description.get("identifier", "")

            if state == "default":
                safe_attachable = attachable_file
                attachable_data = current_data
                animation["mainhand.first_person"] = animation_item.get("firstperson_main_hand", "")
                animation["mainhand.thierd_person"] = animation_item.get("thirdperson_main_hand", "")
                animation["offhand.first_person"] = animation_item.get("firstperson_off_hand", "")
                animation["offhand.thierd_person"] = animation_item.get("thirdperson_off_hand", "")
                animate = [
                    {"mainhand.thierd_person.block": f"!c.is_first_person && c.item_slot == 'main_hand' && q.is_item_name_any('slot.weapon.mainhand', '{gmdl}') && query.is_sneaking"},
                    {"mainhand.first_person.block": f"c.is_first_person && c.item_slot == 'main_hand' && q.is_item_name_any('slot.weapon.mainhand', '{gmdl}') && query.is_sneaking"},
                    {"mainhand.first_person": f"c.is_first_person && c.item_slot == 'main_hand' && q.is_item_name_any('slot.weapon.mainhand', '{gmdl}') && !query.is_sneaking"},
                    {"mainhand.thierd_person": f"!c.is_first_person && c.item_slot == 'main_hand' && q.is_item_name_any('slot.weapon.mainhand', '{gmdl}') && !query.is_sneaking"},
                    {"offhand.thierd_person.block": f"!c.is_first_person && c.item_slot == 'off_hand' && q.is_item_name_any('slot.weapon.offhand', '{gmdl}') && query.is_sneaking"},
                    {"offhand.first_person.block": f"c.is_first_person && c.item_slot == 'off_hand' && q.is_item_name_any('slot.weapon.offhand', '{gmdl}') && query.is_sneaking"},
                    {"offhand.first_person": f"c.is_first_person && c.item_slot == 'off_hand' && q.is_item_name_any('slot.weapon.offhand', '{gmdl}') && !query.is_sneaking"},
                    {"offhand.thierd_person": f"!c.is_first_person && c.item_slot == 'off_hand' && q.is_item_name_any('slot.weapon.offhand', '{gmdl}') && !query.is_sneaking"},
                ]
            else:
                animation["mainhand.first_person.block"] = animation_item.get("firstperson_main_hand", "")
                animation["mainhand.thierd_person.block"] = animation_item.get("thirdperson_main_hand", "")
                animation["offhand.first_person.block"] = animation_item.get("firstperson_off_hand", "")
                animation["offhand.thierd_person.block"] = animation_item.get("thirdperson_off_hand", "")
                if attachable_file != safe_attachable and os.path.exists(attachable_file):
                    os.remove(attachable_file)

        if not safe_attachable or not attachable_data:
            continue

        description = attachable_data["minecraft:attachable"]["description"]
        description["animations"] = animation
        description.setdefault("scripts", {})["animate"] = animate

        with open(safe_attachable, "w", encoding="utf-8") as file:
            json.dump(attachable_data, file)
        processed += 1

    _log(f"Processed {processed} custom shields")


if __name__ == "__main__":
    run()