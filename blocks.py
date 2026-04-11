from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import blocks_util

PACK_DIR = Path("pack")
OUTPUT_FILE = Path("staging/block_map.json")

def _log(message: str) -> None:
    print(f"[BLOCKS] {message}", flush=True)


def _iter_blockstate_files() -> Iterable[Tuple[str, Path]]:
    assets_dir = PACK_DIR / "assets"
    if not assets_dir.exists():
        return []

    files = []
    seen: Set[Path] = set()
    for namespace_dir in assets_dir.iterdir():
        if not namespace_dir.is_dir():
            continue
        blockstates_dir = namespace_dir / "blockstates"
        if not blockstates_dir.exists():
            continue
        for file in sorted(blockstates_dir.glob("**/*.json")):
            if file in seen:
                continue
            seen.add(file)
            files.append((namespace_dir.name, file))
    return files


def _extract_models(variant: object) -> List[str]:
    out: List[str] = []

    if isinstance(variant, dict):
        for key, value in variant.items():
            key_lower = str(key).strip().lower()

            if key_lower == "model" and isinstance(value, str) and value:
                out.append(value)
                continue

            if key_lower == "models" and isinstance(value, list):
                out.extend(_extract_models(value))
                continue

            if key_lower == "apply":
                out.extend(_extract_models(value))
                continue

            if isinstance(value, (dict, list)):
                out.extend(_extract_models(value))

    if isinstance(variant, list):
        for item in variant:
            out.extend(_extract_models(item))

    seen: Set[str] = set()
    deduped: List[str] = []
    for model in out:
        if model in seen:
            continue
        seen.add(model)
        deduped.append(model)
    return deduped


def _normalize_state_value(value: object) -> List[str]:
    if isinstance(value, bool):
        return ["true" if value else "false"]

    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_normalize_state_value(item))
        return [item for item in out if item]

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if any(token in raw.lower() for token in ("|", " or ")):
            return [part.strip() for part in re.split(r"\|\||\bor\b|\|", raw, flags=re.IGNORECASE) if part.strip()]
        return [raw]

    if value is None:
        return []

    return [str(value)]


def _merge_state_maps(base_maps: List[Dict[str, str]], branch_maps: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not base_maps:
        return [dict(item) for item in branch_maps]
    if not branch_maps:
        return [dict(item) for item in base_maps]

    merged: List[Dict[str, str]] = []
    for base in base_maps:
        for branch in branch_maps:
            candidate = dict(base)
            conflict = False
            for key, value in branch.items():
                existing = candidate.get(key)
                if existing is not None and existing != value:
                    conflict = True
                    break
                candidate[key] = value
            if not conflict:
                merged.append(candidate)
    return merged


def _state_maps_from_when(when: object) -> List[Dict[str, str]]:
    if when is None:
        return [{}]

    if isinstance(when, dict):
        lower_map = {str(k).strip().lower(): k for k in when.keys()}

        and_key = lower_map.get("and")
        and_branch = when.get(and_key) if and_key is not None else None
        if isinstance(and_branch, (list, dict)):
            branch_list = and_branch if isinstance(and_branch, list) else [and_branch]
            merged = [{}]
            for branch in branch_list:
                merged = _merge_state_maps(merged, _state_maps_from_when(branch))
            return merged or [{}]

        or_key = lower_map.get("or")
        or_branch = when.get(or_key) if or_key is not None else None
        if isinstance(or_branch, (list, dict)):
            branch_list = or_branch if isinstance(or_branch, list) else [or_branch]
            out: List[Dict[str, str]] = []
            for branch in branch_list:
                out.extend(_state_maps_from_when(branch))
            return out or [{}]

        merged = [{}]
        for key in sorted(when.keys()):
            values = _normalize_state_value(when.get(key))
            if not values:
                continue
            branch_maps = [{str(key): value} for value in values]
            merged = _merge_state_maps(merged, branch_maps)
        return merged or [{}]

    if isinstance(when, list):
        out: List[Dict[str, str]] = []
        for item in when:
            out.extend(_state_maps_from_when(item))
        return out or [{}]

    if isinstance(when, str):
        raw = when.strip()
        if not raw:
            return [{}]

        or_chunks = [chunk.strip() for chunk in re.split(r"\|\||\bor\b", raw, flags=re.IGNORECASE) if chunk.strip()]
        if len(or_chunks) > 1:
            out: List[Dict[str, str]] = []
            for chunk in or_chunks:
                out.extend(_state_maps_from_when(chunk))
            return out or [{}]

        if "=" in raw:
            merged = [{}]
            for part in raw.split(","):
                key, sep, value = part.partition("=")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip()
                if not key or not value:
                    continue

                value_options = _normalize_state_value(value)
                if not value_options:
                    continue

                branch_maps = [{key: option} for option in value_options]
                merged = _merge_state_maps(merged, branch_maps)
            return merged or [{}]
        return [{"__raw__": raw}]

    return [{}]


def _state_key_from_when(when: object) -> List[str]:
    state_maps = _state_maps_from_when(when)
    keys: Set[str] = set()
    for state_map in state_maps:
        if not state_map:
            keys.add("default")
            continue
        if len(state_map) == 1 and "__raw__" in state_map:
            raw_key = str(state_map.get("__raw__", "")).strip()
            if raw_key:
                keys.add(raw_key)
                continue
        pairs = [f"{key}={state_map[key]}" for key in sorted(state_map.keys())]
        keys.add(",".join(pairs))
    return sorted(keys) if keys else ["default"]


def _expand_variant_state_key(state_key: object) -> List[str]:
    raw = str(state_key).strip()
    if not raw:
        return ["default"]
    if "=" not in raw:
        return [raw]

    option_groups: List[List[str]] = []
    for part in raw.split(","):
        segment = part.strip()
        if not segment:
            continue

        key, sep, value = segment.partition("=")
        if not sep:
            option_groups.append([segment])
            continue

        key = key.strip()
        value = value.strip()
        if not key:
            continue

        raw_values = [chunk.strip() for chunk in re.split(r"\|\||\bor\b|\|", value, flags=re.IGNORECASE) if chunk.strip()]
        if not raw_values:
            raw_values = _normalize_state_value(value)
        if not raw_values:
            option_groups.append([f"{key}={value}" if value else key])
            continue
        option_groups.append([f"{key}={item}" for item in raw_values])

    if not option_groups:
        return [raw]

    combinations: List[str] = [""]
    for group in option_groups:
        next_combinations: List[str] = []
        for prefix in combinations:
            for item in group:
                if not item:
                    continue
                if prefix:
                    next_combinations.append(f"{prefix},{item}")
                else:
                    next_combinations.append(item)
        combinations = next_combinations

    if not combinations:
        return [raw]
    return sorted(set(combinations))


def _iter_state_models(data: Dict[str, object]) -> Iterable[Tuple[str, str]]:
    emitted: Set[Tuple[str, str]] = set()

    variants = data.get("variants")
    if isinstance(variants, dict):
        for state_key, variant_value in variants.items():
            expanded_state_keys = _expand_variant_state_key(state_key)
            for normalized_key in expanded_state_keys:
                normalized_key = normalized_key.strip() or "default"
                for model in _extract_models(variant_value):
                    pair = (normalized_key, model)
                    if pair in emitted:
                        continue
                    emitted.add(pair)
                    yield pair

    multipart = data.get("multipart")
    if isinstance(multipart, list):
        for part in multipart:
            if not isinstance(part, dict):
                continue
            when = part.get("when")
            applies = part.get("apply")
            models = _extract_models(applies)
            if not models:
                continue
            state_keys = _state_key_from_when(when)
            for state_key in state_keys:
                for model in models:
                    pair = (state_key, model)
                    if pair in emitted:
                        continue
                    emitted.add(pair)
                    yield pair


def _normalize_tripwire_state(state_key: str) -> str:
    parts = state_key.split(",")
    if len(parts) < 7:
        return state_key
    return f"{parts[0]},{parts[4]},{parts[1]},{parts[2]},{parts[6]},{parts[3]},{parts[5]}"


def _patch_attachable_for_cube(attachable_path: str, data: Dict[str, object]) -> None:
    attachable = data.get("minecraft:attachable")
    if not isinstance(attachable, dict):
        return
    description = attachable.get("description")
    if not isinstance(description, dict):
        return

    geometry = description.get("geometry")
    if not isinstance(geometry, dict):
        geometry = {}
        description["geometry"] = geometry
    geometry["default"] = "geometry.cube"

    description["animations"] = {
        "thirdperson_main_hand": "animation.geo_cube.thirdperson_main_hand",
        "thirdperson_off_hand": "animation.geo_cube.thirdperson_off_hand",
        "thirdperson_head": "animation.geo_cube.head",
        "firstperson_main_hand": "animation.geo_cube.firstperson_main_hand",
        "firstperson_off_hand": "animation.geo_cube.firstperson_off_hand",
        "firstperson_head": "animation.geyser_custom.disable",
    }

    with open(attachable_path, "w", encoding="utf-8") as file:
        json.dump(data, file)


def run() -> None:
    blockstate_files = list(_iter_blockstate_files())
    if not blockstate_files:
        _log("No blockstates found; skipping")
        return

    blocks_util.write_animated_cube()
    blocks_util.write_geometry_cube()

    block_material = os.getenv("BLOCK_MATERIAL", "alpha_test")
    converted = 0
    converted_blocks: Set[str] = set()
    converted_state_keys: Set[str] = set()
    unresolved_model_refs: Set[str] = set()
    unresolved_sources: Set[str] = set()
    parse_failures: List[str] = []

    for namespace, blockstate_file in blockstate_files:
        block_name = blockstate_file.stem
        if namespace == "minecraft" and block_name == "fire":
            continue

        blocks_util.write_mapping_block(block_name, namespace=namespace)

        try:
            with blockstate_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            _log(f"Failed to parse {blockstate_file}: {exc}")
            parse_failures.append(f"{blockstate_file.relative_to(PACK_DIR).as_posix()}: {exc}")
            continue

        variants = data.get("variants")
        multipart = data.get("multipart")
        if not isinstance(variants, dict) and not isinstance(multipart, list):
            unresolved_sources.add(blockstate_file.relative_to(PACK_DIR).as_posix())
            continue

        for state_key, model_ref in _iter_state_models(data):
            if not model_ref:
                continue
            if "block/original" in model_ref or "block/tripwire_attached_n" in model_ref:
                continue

            attachable_path = blocks_util.get_am_file(model_ref)
            if not attachable_path:
                unresolved_model_refs.add(model_ref)
                unresolved_sources.add(blockstate_file.relative_to(PACK_DIR).as_posix())
                continue

            try:
                with open(attachable_path, "r", encoding="utf-8") as file:
                    attachable_data = json.load(file)
            except Exception as exc:
                _log(f"Failed to parse attachable {attachable_path}: {exc}")
                unresolved_model_refs.add(model_ref)
                unresolved_sources.add(blockstate_file.relative_to(PACK_DIR).as_posix())
                continue

            try:
                description = attachable_data["minecraft:attachable"]["description"]
                gmdl = description["identifier"].split(":", 1)[1]
                texture_ref = description["textures"]["default"]
            except Exception as exc:
                _log(f"Missing attachable fields in {attachable_path}: {exc}")
                unresolved_model_refs.add(model_ref)
                unresolved_sources.add(blockstate_file.relative_to(PACK_DIR).as_posix())
                continue

            geometry = blocks_util.get_geometry_block(model_ref)
            texture = blocks_util.create_terrain_texture(gmdl, texture_ref)

            if geometry == "geometry.cube":
                _patch_attachable_for_cube(attachable_path, attachable_data)

            final_state_key = state_key
            if namespace == "minecraft" and block_name == "tripwire":
                final_state_key = _normalize_tripwire_state(state_key)

            blocks_util.register_block(
                block_name,
                gmdl,
                final_state_key,
                texture,
                block_material,
                geometry,
                namespace=namespace,
            )
            converted += 1
            converted_blocks.add(f"{namespace}:{block_name}")
            converted_state_keys.add(f"{namespace}:{block_name}|{final_state_key}")

    payload = {
        "parse_failure_count": len(parse_failures),
        "parse_failures": sorted(parse_failures),
        "unresolved_source_count": len(unresolved_sources),
        "unresolved_sources": sorted(unresolved_sources),
        "blockstate_file_count": len(blockstate_files),
        "block_count": len(converted_blocks),
        "converted_variant_count": converted,
        "state_key_count": len(converted_state_keys),
        "unresolved_ref_count": len(unresolved_model_refs) + len(unresolved_sources) + len(parse_failures),
        "missing_ref_count": len(unresolved_model_refs) + len(unresolved_sources) + len(parse_failures),
        "unresolved_model_ref_count": len(unresolved_model_refs),
        "unresolved_model_refs": sorted(unresolved_model_refs),
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    _log(f"Converted {converted} block variants")


if __name__ == "__main__":
    run()
