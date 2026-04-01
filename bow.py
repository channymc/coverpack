from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bow_util import Bow_Util


def _log(message: str) -> None:
    print(f"[BOW] {message}", flush=True)


def _load_bow_overrides() -> List[Tuple[str, Dict[str, object]]]:
    bow_model_file = Path("pack/assets/minecraft/models/item/bow.json")
    if not bow_model_file.exists():
        return []

    try:
        with bow_model_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return []

    overrides = data.get("overrides")
    if not isinstance(overrides, list):
        return []

    output: List[Tuple[str, Dict[str, object]]] = []
    for override in overrides:
        if not isinstance(override, dict):
            continue
        predicate = override.get("predicate")
        model = override.get("model")
        if isinstance(predicate, dict) and isinstance(model, str):
            output.append((model, predicate))
    return output


def _cache_override(model: str, predicate: Dict[str, object]) -> None:
    if model in {"item/bow", "item/bow_pulling_0", "item/bow_pulling_1", "item/bow_pulling_2"}:
        return
    if "custom_model_data" not in predicate:
        return

    index = 0
    if predicate.get("pulling") == 1:
        index = 1
        pull = predicate.get("pull")
        try:
            pull_value = float(pull)
            if pull_value <= 0.65:
                index = 2
            elif pull_value > 0.65:
                index = 3
        except Exception:
            pass

    cache_path = Path(f"cache/bow/{predicate['custom_model_data']}.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        cache_path.write_text("{}", encoding="utf-8")

    try:
        with cache_path.open("r", encoding="utf-8") as file:
            cached = json.load(file)
    except Exception:
        cached = {}

    cached["check"] = int(cached.get("check", 0)) + 1
    cached[f"texture_{index}"] = model
    with cache_path.open("w", encoding="utf-8") as file:
        json.dump(cached, file, indent=2)


def _find_attachable(namespace: str, path: str) -> Optional[str]:
    candidates = glob.glob(f"staging/target/rp/attachables/{namespace}/{path}*.json")
    suffix = f"{Path(path).name}."
    for candidate in candidates:
        if suffix in candidate:
            return candidate
    return None


def run() -> None:
    overrides = _load_bow_overrides()
    if not overrides:
        _log("No bow overrides found; skipping")
        return

    for model, predicate in overrides:
        _cache_override(model, predicate)

    Bow_Util.animation()
    Bow_Util.rendercontrollers()

    gmdllist: List[str] = []
    for cache_file in glob.glob("cache/bow/*.json"):
        try:
            with open(cache_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception:
            continue

        if data.get("check") != 4:
            continue

        textures: List[str] = []
        geometry: List[str] = []
        mfile = None
        mdefault = menchanted = gmdl = None
        animations = None
        animate = None
        pre_animation = None

        for i in range(4):
            texture_ref = data.get(f"texture_{i}")
            if not isinstance(texture_ref, str) or ":" not in texture_ref:
                continue
            namespace, path = texture_ref.split(":", 1)

            attachable = _find_attachable(namespace, path)
            if not attachable:
                continue

            with open(attachable, "r", encoding="utf-8") as file:
                dataA = json.load(file)

            description = dataA["minecraft:attachable"]["description"]
            textures.append(description["textures"]["default"])

            model_files = glob.glob(f"staging/target/rp/models/blocks/{namespace}/{path}.json")
            if not model_files:
                continue

            is_2d_bow = Bow_Util.is2Dbow(model_files[0])
            if is_2d_bow:
                geometry.append("geometry.bow_standby" if i == 0 else f"geometry.bow_pulling_{i - 1}")
            else:
                geometry.append(description["geometry"]["default"])

            if i == 0:
                if is_2d_bow:
                    animate = [
                        {"wield": "c.is_first_person"},
                        {"third_person": "!c.is_first_person"},
                        {"wield_first_person_pull": "query.main_hand_item_use_duration > 0.0f && c.is_first_person"},
                    ]
                    pre_animation = [
                        "v.charge_amount = math.clamp((q.main_hand_item_max_duration - (q.main_hand_item_use_duration - q.frame_alpha + 1.0)) / 10.0, 0.0, 1.0f);",
                        "v.total_frames = 3;",
                        "v.step = v.total_frames / 60;",
                        "v.frame = query.is_using_item ? math.clamp((v.frame ?? 0) + v.step, 1, v.total_frames) : 0;",
                    ]
                else:
                    animate = [
                        {"thirdperson_main_hand": "v.main_hand && !c.is_first_person"},
                        {"thirdperson_off_hand": "v.off_hand && !c.is_first_person"},
                        {"thirdperson_head": "v.head && !c.is_first_person"},
                        {"firstperson_main_hand": "v.main_hand && c.is_first_person"},
                        {"firstperson_off_hand": "v.off_hand && c.is_first_person"},
                        {"firstperson_head": "c.is_first_person && v.head"},
                    ]
                    pre_animation = [
                        "v.charge_amount = math.clamp((q.main_hand_item_max_duration - (q.main_hand_item_use_duration - q.frame_alpha + 1.0)) / 10.0, 0.0, 1.0f);",
                        "v.total_frames = 3;",
                        "v.step = v.total_frames / 60;",
                        "v.frame = query.is_using_item ? math.clamp((v.frame ?? 0) + v.step, 1, v.total_frames) : 0;",
                        "v.main_hand = c.item_slot == 'main_hand';",
                        "v.off_hand = c.item_slot == 'off_hand';",
                        "v.head = c.item_slot == 'head';",
                    ]

                mfile = attachable
                mdefault = description["materials"]["default"]
                menchanted = description["materials"]["enchanted"]
                gmdl = description["identifier"].split(":", 1)[1]
                animations = description["animations"]
                animations["wield"] = "animation.player.bow_custom.first_person"
                animations["third_person"] = "animation.player.bow_custom"
                animations["wield_first_person_pull"] = "animation.bow.wield_first_person_pull"
                gmdllist.append(f"geyser_custom:{gmdl}")
                Bow_Util.item_texture(gmdl, textures[0])
            else:
                if attachable != mfile and os.path.exists(attachable):
                    os.remove(attachable)

        if len(textures) != 4 or len(geometry) != 4:
            continue

        if mfile and gmdl and mdefault and menchanted and animations and animate and pre_animation:
            Bow_Util.write(mfile, gmdl, textures, geometry, mdefault, menchanted, animations, animate, pre_animation)

    Bow_Util.acontroller(gmdllist)
    _log(f"Processed {len(gmdllist)} custom bows")


if __name__ == "__main__":
    run()
