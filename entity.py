from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set

PACK_DIR = Path("pack")
TARGET_RP_DIR = Path("staging/target/rp")
OUTPUT_FILE = Path("staging/entity_map.json")

MODEL_PATTERNS = (
    "assets/**/models/entity/**/*.json",
    "assets/**/models/entity/*.json",
    "assets/**/models/**/*.geo.json",
    "assets/**/models/**/geo/*.json",
    "assets/**/geo/**/*.json",
    "assets/**/geo/*.json",
)
ANIMATION_PATTERNS = (
    "assets/**/animations/**/*.json",
    "assets/**/animations/*.json",
)
CONTROLLER_PATTERNS = (
    "assets/**/animation_controllers/**/*.json",
    "assets/**/animation_controllers/*.json",
)
ENTITY_DEFINITION_PATTERNS = (
    "assets/**/entity/**/*.json",
    "assets/**/entity/*.json",
)
ATTACHABLE_PATTERNS = (
    "assets/**/attachables/**/*.json",
    "assets/**/attachables/*.json",
)
RENDER_CONTROLLER_PATTERNS = (
    "assets/**/render_controllers/**/*.json",
    "assets/**/render_controllers/*.json",
)
MATERIAL_PATTERNS = (
    "assets/**/materials/**/*.material",
    "assets/**/materials/**/*.json",
    "assets/**/materials/*.material",
    "assets/**/materials/*.json",
)
TEXTURE_PATTERNS = (
    "assets/**/textures/entity/**/*.png",
    "assets/**/textures/entity/*.png",
    "assets/**/textures/entity/**/*.tga",
    "assets/**/textures/entity/*.tga",
)
MODELENGINE_HINTS = ("modelengine", "model_engine", "entitymodel", "entity_model")


def _log(message: str) -> None:
    print(f"[ENTITY] {message}", flush=True)


def _iter_files(patterns: Iterable[str]) -> Iterable[Path]:
    seen: Set[Path] = set()
    for pattern in patterns:
        for file_path in PACK_DIR.glob(pattern):
            if file_path.is_file() and file_path not in seen:
                seen.add(file_path)
                yield file_path


def _safe_load(path: Path) -> Optional[Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, (dict, list)) else None
    except Exception:
        return None


def _iter_nodes(node: Any) -> Iterator[Dict[str, Any]]:
    stack: List[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append(value)


def _extract_string_refs(data: Any, key_tokens: Iterable[str]) -> List[str]:
    refs: Set[str] = set()
    tokens = tuple(token.lower() for token in key_tokens)

    def _collect_string_values(value: Any) -> List[str]:
        if isinstance(value, str) and value.strip():
            raw = value.strip()
            out: Set[str] = {raw}

            parts = re.split(r"[\s,;(){}\[\]<>|&]+", raw)
            for part in parts:
                candidate = part.strip().strip("'\"")
                if not candidate:
                    continue
                lowered = candidate.lower()
                if any(token in lowered for token in tokens):
                    out.add(candidate)

            if len(raw) >= 3:
                for match in re.findall(r"(?:[a-z0-9_]+:)?[a-z0-9_.\-/]+", raw, flags=re.IGNORECASE):
                    candidate = match.strip().strip("'\"")
                    if not candidate:
                        continue
                    lowered = candidate.lower()
                    if any(token in lowered for token in tokens):
                        out.add(candidate)
            return sorted(out)

        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                out.extend(_collect_string_values(item))
            return out
        if isinstance(value, dict):
            out: List[str] = []
            for item in value.values():
                out.extend(_collect_string_values(item))
            return out
        return []

    for node in _iter_nodes(data):
        for key, value in node.items():
            key_lower = str(key).strip().lower()
            if not any(token in key_lower for token in tokens):
                continue

            for ref in _collect_string_values(value):
                refs.add(ref)

    return sorted(refs)


def _extract_named_key_refs(data: Any, container_tokens: Iterable[str]) -> List[str]:
    refs: Set[str] = set()
    tokens = tuple(token.lower() for token in container_tokens)

    for node in _iter_nodes(data):
        for key, value in node.items():
            key_lower = str(key).strip().lower()
            if not any(token in key_lower for token in tokens):
                continue

            if isinstance(value, dict):
                for nested_key in value.keys():
                    nested_text = str(nested_key).strip()
                    if nested_text:
                        refs.add(nested_text)
                for nested_value in value.values():
                    if isinstance(nested_value, str) and nested_value.strip():
                        refs.add(nested_value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        refs.add(item.strip())
                    elif isinstance(item, dict):
                        for nested_key in item.keys():
                            nested_text = str(nested_key).strip()
                            if nested_text:
                                refs.add(nested_text)
                        for nested_value in item.values():
                            if isinstance(nested_value, str) and nested_value.strip():
                                refs.add(nested_value.strip())
            elif isinstance(value, str) and value.strip():
                refs.add(value.strip())
    return sorted(refs)


def _extract_entity_definition_refs(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "geometry_refs": _extract_string_refs(data, ("geometry", "model")),
        "animation_refs": _extract_string_refs(data, ("animation",)),
        "animation_controller_refs": _extract_string_refs(data, ("controller", "animation_controller")),
        "render_controller_refs": _extract_string_refs(data, ("render_controller", "rendercontrollers")),
        "texture_refs": _extract_string_refs(data, ("texture", "textures")),
        "material_refs": _extract_string_refs(data, ("material", "materials")),
        "particle_refs": _extract_string_refs(data, ("particle",)),
        "sound_refs": _extract_string_refs(data, ("sound", "sounds")),
        "event_refs": _extract_named_key_refs(data, ("event", "events")),
        "component_group_refs": _extract_named_key_refs(data, ("component_group", "component_groups")),
        "script_refs": _extract_string_refs(data, ("script", "scripts")),
        "loot_refs": _extract_string_refs(data, ("loot", "loot_table", "loot_tables")),
        "spawn_rule_refs": _extract_string_refs(data, ("spawn_rule", "spawn_rules")),
    }


def _extract_render_controller_refs(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "geometry_refs": _extract_string_refs(data, ("geometry", "model")),
        "texture_refs": _extract_string_refs(data, ("texture", "textures")),
        "material_refs": _extract_string_refs(data, ("material", "materials")),
        "part_visibility_refs": _extract_string_refs(data, ("part_visibility",)),
    }


def _extract_animation_refs(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "animation_refs": _extract_string_refs(data, ("animation",)),
        "particle_refs": _extract_string_refs(data, ("particle",)),
        "sound_refs": _extract_string_refs(data, ("sound", "sounds")),
        "timeline_refs": _extract_string_refs(data, ("timeline", "event", "events")),
        "material_refs": _extract_string_refs(data, ("material", "materials")),
        "texture_refs": _extract_string_refs(data, ("texture", "textures")),
        "state_refs": _extract_named_key_refs(data, ("state", "states")),
    }


def _extract_animation_controller_refs(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "animation_refs": _extract_string_refs(data, ("animation",)),
        "particle_refs": _extract_string_refs(data, ("particle",)),
        "sound_refs": _extract_string_refs(data, ("sound", "sounds")),
        "state_refs": _extract_string_refs(data, ("state", "states", "transition", "transitions")),
        "texture_refs": _extract_string_refs(data, ("texture", "textures")),
        "material_refs": _extract_string_refs(data, ("material", "materials")),
        "transition_refs": _extract_named_key_refs(data, ("transition", "transitions")),
    }


def _extract_attachable_refs(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "geometry_refs": _extract_string_refs(data, ("geometry", "model")),
        "animation_refs": _extract_string_refs(data, ("animation",)),
        "animation_controller_refs": _extract_string_refs(data, ("controller", "animation_controller")),
        "render_controller_refs": _extract_string_refs(data, ("render_controller", "rendercontrollers")),
        "texture_refs": _extract_string_refs(data, ("texture", "textures")),
        "material_refs": _extract_string_refs(data, ("material", "materials")),
        "particle_refs": _extract_string_refs(data, ("particle",)),
        "sound_refs": _extract_string_refs(data, ("sound", "sounds")),
        "script_refs": _extract_string_refs(data, ("script", "scripts")),
    }


def _namespace_and_relative(path: Path, anchor: str) -> Optional[tuple[str, str]]:
    parts = path.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        namespace_index = assets_index + 1
        namespace = parts[namespace_index]
        anchor_index = parts.index(anchor, namespace_index + 1)
        rel = "/".join(parts[anchor_index + 1 :])
    except Exception:
        return None

    if not rel:
        return None
    return namespace, rel


def _copy_to_target(
    path: Path,
    root_folder: str,
    anchor: str,
    strip_suffix: bool = False,
    copy_sidecar_meta: bool = False,
) -> Optional[str]:
    ns_rel = _namespace_and_relative(path, anchor)
    if not ns_rel:
        return None

    namespace, rel = ns_rel
    destination = TARGET_RP_DIR / root_folder / namespace / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)

    if copy_sidecar_meta:
        source_meta = path.with_suffix(path.suffix + ".mcmeta")
        if source_meta.exists():
            target_meta = destination.with_suffix(destination.suffix + ".mcmeta")
            shutil.copyfile(source_meta, target_meta)

    relative = destination.relative_to(TARGET_RP_DIR)
    if strip_suffix:
        return relative.with_suffix("").as_posix()
    return relative.as_posix()


def _asset_namespace_from_path(path: Path, fallback: str = "minecraft") -> str:
    parts = path.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        candidate = parts[assets_index + 1].strip()
        if candidate:
            return candidate
    except Exception:
        pass
    return fallback


def _all_texture_namespaces() -> List[str]:
    assets_root = PACK_DIR / "assets"
    if not assets_root.exists() or not assets_root.is_dir():
        return []

    namespaces: List[str] = []
    for candidate in assets_root.iterdir():
        if not candidate.is_dir():
            continue
        if not (candidate / "textures").exists():
            continue
        namespaces.append(candidate.name)
    return sorted(set(namespaces))


def _looks_like_texture_ref(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    lowered = raw.lower()
    if raw.startswith(("http://", "https://", "#")):
        return False
    if lowered in ("none", "null", "false", "true"):
        return False
    if lowered.startswith(("query.", "variable.", "temp.", "math.")):
        return False
    return "/" in raw or ":" in raw or "texture" in lowered


def _resolve_texture_source(reference: str, default_namespace: str = "minecraft") -> Optional[Path]:
    ref = reference.strip().replace("\\", "/")
    if not ref:
        return None
    if ref.startswith(("http://", "https://", "#")):
        return None

    if ":" in ref:
        namespace, rel = ref.split(":", 1)
    else:
        namespace, rel = default_namespace, ref

    rel = rel.lstrip("/")
    if rel.startswith("textures/"):
        rel = rel[len("textures/") :]

    namespace_order: List[str] = [namespace]
    if default_namespace not in namespace_order:
        namespace_order.append(default_namespace)
    if "minecraft" not in namespace_order:
        namespace_order.append("minecraft")
    for candidate_ns in _all_texture_namespaces():
        if candidate_ns not in namespace_order:
            namespace_order.append(candidate_ns)

    for candidate_ns in namespace_order:
        base = PACK_DIR / "assets" / candidate_ns / "textures" / rel
        candidates = [base]
        if base.suffix == "":
            candidates.extend([base.with_suffix(".png"), base.with_suffix(".tga")])

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        if base.suffix == "" and "/" not in rel:
            texture_root = PACK_DIR / "assets" / candidate_ns / "textures"
            if texture_root.exists():
                for extension in (".png", ".tga"):
                    for candidate in texture_root.glob(f"**/{rel}{extension}"):
                        if candidate.exists() and candidate.is_file():
                            return candidate

    basename = Path(rel).name
    if basename:
        stem = Path(basename).stem
        for extension in (".png", ".tga"):
            for candidate in PACK_DIR.glob(f"assets/**/textures/**/{stem}{extension}"):
                if candidate.exists() and candidate.is_file():
                    return candidate
    return None


def _copy_or_get_texture_mapping(
    source: Path,
    texture_copy_map: Dict[Path, str],
    texture_entries: List[str],
) -> Optional[str]:
    existing = texture_copy_map.get(source)
    if existing:
        return existing

    try:
        source_rel = source.relative_to(PACK_DIR).as_posix().lower()
    except Exception:
        source_rel = source.as_posix().lower()
    if "/textures/entity/" in source_rel:
        mapped = _copy_to_target(
            source,
            "textures/entity",
            "entity",
            strip_suffix=True,
            copy_sidecar_meta=True,
        )
    else:
        mapped = _copy_to_target(
            source,
            "textures",
            "textures",
            strip_suffix=True,
            copy_sidecar_meta=True,
        )
    if not mapped:
        return None

    texture_copy_map[source] = mapped
    texture_entries.append(mapped)
    return mapped


def _resolve_and_copy_texture_refs(
    default_namespace: str,
    refs: Iterable[str],
    texture_copy_map: Dict[Path, str],
    texture_entries: List[str],
    unresolved_texture_refs: Set[str],
) -> List[str]:
    resolved: List[str] = []

    for raw_ref in refs:
        if not isinstance(raw_ref, str):
            continue

        ref = raw_ref.strip()
        if not ref or not _looks_like_texture_ref(ref):
            continue

        unresolved_key = ref if ":" in ref else f"{default_namespace}:{ref}"
        source = _resolve_texture_source(ref, default_namespace=default_namespace)
        if not source:
            unresolved_texture_refs.add(unresolved_key)
            continue

        mapped = _copy_or_get_texture_mapping(source, texture_copy_map, texture_entries)
        if mapped:
            resolved.append(mapped)
        else:
            unresolved_texture_refs.add(unresolved_key)

    return sorted(set(resolved))


def _extract_model_identifiers(model_json: Any, fallback: str) -> List[str]:
    identifiers: List[str] = []

    if not isinstance(model_json, dict):
        return [fallback]

    geometry = model_json.get("minecraft:geometry")
    if isinstance(geometry, list):
        for entry in geometry:
            if not isinstance(entry, dict):
                continue
            description = entry.get("description")
            if not isinstance(description, dict):
                continue
            identifier = description.get("identifier")
            if isinstance(identifier, str) and identifier.strip():
                identifiers.append(identifier.strip())

    legacy_geometry = model_json.get("geometry")
    if isinstance(legacy_geometry, list):
        for entry in legacy_geometry:
            if not isinstance(entry, dict):
                continue
            description = entry.get("description")
            if isinstance(description, dict):
                identifier = description.get("identifier")
                if isinstance(identifier, str) and identifier.strip():
                    identifiers.append(identifier.strip())

    if isinstance(legacy_geometry, dict):
        for key in legacy_geometry.keys():
            key_text = str(key).strip()
            if key_text:
                identifiers.append(key_text)

    for key in model_json.keys():
        key_text = str(key).strip()
        if key_text.startswith("geometry."):
            identifiers.append(key_text)
        elif key_text.startswith("minecraft:geometry."):
            identifiers.append(key_text.split(":", 1)[-1])

    for node in _iter_nodes(model_json):
        if "identifier" in node:
            identifier = node.get("identifier")
            if isinstance(identifier, str) and identifier.strip():
                id_text = identifier.strip()
                if id_text.startswith("geometry.") or id_text.startswith("minecraft:geometry"):
                    identifiers.append(id_text)

    if not identifiers:
        identifiers.append(fallback)

    return sorted(set(identifiers))


def _collect_modelengine_files() -> List[str]:
    copied: List[str] = []
    for path in PACK_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PACK_DIR).as_posix().lower()
        if not any(hint in rel for hint in MODELENGINE_HINTS):
            continue
        if path.suffix.lower() not in (
            ".json",
            ".png",
            ".tga",
            ".anim",
            ".bbmodel",
            ".mcmeta",
            ".material",
            ".txt",
            ".yml",
            ".yaml",
        ):
            continue

        destination = TARGET_RP_DIR / "modelengine" / path.relative_to(PACK_DIR)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        copied.append(destination.relative_to(TARGET_RP_DIR).as_posix())

    return sorted(set(copied))


def run() -> None:
    model_entries: List[Dict[str, Any]] = []
    animation_entries: List[Dict[str, Any]] = []
    controller_entries: List[Dict[str, Any]] = []
    attachable_entries: List[Dict[str, Any]] = []
    definition_entries: List[Dict[str, Any]] = []
    render_controller_entries: List[Dict[str, Any]] = []
    texture_entries: List[str] = []
    material_entries: List[str] = []
    texture_copy_map: Dict[Path, str] = {}
    parse_failures: List[str] = []
    unresolved_sources: Set[str] = set()
    unresolved_texture_refs: Set[str] = set()

    for model_file in _iter_files(MODEL_PATTERNS):
        model_data = _safe_load(model_file)
        if model_data is None:
            parse_failures.append(model_file.relative_to(PACK_DIR).as_posix())
            continue

        model_source = model_file.relative_to(PACK_DIR).as_posix()
        source_lower = model_source.lower()
        model_anchor = "entity"
        if "/models/entity/" not in source_lower and "/geo/" in source_lower:
            model_anchor = "geo"

        mapped = _copy_to_target(model_file, "models/entity", model_anchor)
        if not mapped:
            unresolved_sources.add(model_source)
            continue

        ns_rel = _namespace_and_relative(model_file, model_anchor)
        namespace = ns_rel[0] if ns_rel else "minecraft"
        fallback_identifier = f"{namespace}:{model_file.stem}"
        identifiers = _extract_model_identifiers(model_data, fallback_identifier)
        model_texture_refs = _extract_string_refs(model_data, ("texture", "textures"))
        resolved_model_textures = _resolve_and_copy_texture_refs(
            namespace,
            model_texture_refs,
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )

        model_entries.append(
            {
                "source": model_source,
                "output": mapped,
                "identifiers": identifiers,
                "texture_refs": sorted(set(model_texture_refs)),
                "resolved_texture_refs": resolved_model_textures,
            }
        )

    for animation_file in _iter_files(ANIMATION_PATTERNS):
        animation_data = _safe_load(animation_file)
        if animation_data is None:
            parse_failures.append(animation_file.relative_to(PACK_DIR).as_posix())
        mapped = _copy_to_target(animation_file, "animations/imported", "animations")
        if not mapped:
            unresolved_sources.add(animation_file.relative_to(PACK_DIR).as_posix())
            continue

        refs = _extract_animation_refs(animation_data or {})
        default_ns = _asset_namespace_from_path(animation_file)
        resolved_textures = _resolve_and_copy_texture_refs(
            default_ns,
            refs.get("texture_refs", []),
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )
        animation_entries.append(
            {
                "source": animation_file.relative_to(PACK_DIR).as_posix(),
                "output": mapped,
                "resolved_texture_refs": resolved_textures,
                **refs,
            }
        )

    for controller_file in _iter_files(CONTROLLER_PATTERNS):
        controller_data = _safe_load(controller_file)
        if controller_data is None:
            parse_failures.append(controller_file.relative_to(PACK_DIR).as_posix())
        mapped = _copy_to_target(controller_file, "animation_controllers/imported", "animation_controllers")
        if not mapped:
            unresolved_sources.add(controller_file.relative_to(PACK_DIR).as_posix())
            continue

        refs = _extract_animation_controller_refs(controller_data or {})
        default_ns = _asset_namespace_from_path(controller_file)
        resolved_textures = _resolve_and_copy_texture_refs(
            default_ns,
            refs.get("texture_refs", []),
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )
        controller_entries.append(
            {
                "source": controller_file.relative_to(PACK_DIR).as_posix(),
                "output": mapped,
                "resolved_texture_refs": resolved_textures,
                **refs,
            }
        )

    for attachable_file in _iter_files(ATTACHABLE_PATTERNS):
        attachable_data = _safe_load(attachable_file)
        if attachable_data is None:
            parse_failures.append(attachable_file.relative_to(PACK_DIR).as_posix())
        mapped = _copy_to_target(attachable_file, "attachables/imported", "attachables")
        if not mapped:
            unresolved_sources.add(attachable_file.relative_to(PACK_DIR).as_posix())
            continue

        refs = _extract_attachable_refs(attachable_data or {})
        default_ns = _asset_namespace_from_path(attachable_file)
        resolved_textures = _resolve_and_copy_texture_refs(
            default_ns,
            refs.get("texture_refs", []),
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )
        attachable_entries.append(
            {
                "source": attachable_file.relative_to(PACK_DIR).as_posix(),
                "output": mapped,
                "resolved_texture_refs": resolved_textures,
                **refs,
            }
        )

    for definition_file in _iter_files(ENTITY_DEFINITION_PATTERNS):
        definition_data = _safe_load(definition_file)
        if definition_data is None:
            parse_failures.append(definition_file.relative_to(PACK_DIR).as_posix())
        mapped = _copy_to_target(definition_file, "entity/imported", "entity")
        if not mapped:
            unresolved_sources.add(definition_file.relative_to(PACK_DIR).as_posix())
            continue

        refs = _extract_entity_definition_refs(definition_data or {})
        default_ns = _asset_namespace_from_path(definition_file)
        resolved_textures = _resolve_and_copy_texture_refs(
            default_ns,
            refs.get("texture_refs", []),
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )
        definition_entries.append(
            {
                "source": definition_file.relative_to(PACK_DIR).as_posix(),
                "output": mapped,
                "resolved_texture_refs": resolved_textures,
                **refs,
            }
        )

    for render_controller_file in _iter_files(RENDER_CONTROLLER_PATTERNS):
        controller_data = _safe_load(render_controller_file)
        if controller_data is None:
            parse_failures.append(render_controller_file.relative_to(PACK_DIR).as_posix())
        mapped = _copy_to_target(render_controller_file, "render_controllers/imported", "render_controllers")
        if not mapped:
            unresolved_sources.add(render_controller_file.relative_to(PACK_DIR).as_posix())
            continue

        refs = _extract_render_controller_refs(controller_data or {})
        default_ns = _asset_namespace_from_path(render_controller_file)
        resolved_textures = _resolve_and_copy_texture_refs(
            default_ns,
            refs.get("texture_refs", []),
            texture_copy_map,
            texture_entries,
            unresolved_texture_refs,
        )
        render_controller_entries.append(
            {
                "source": render_controller_file.relative_to(PACK_DIR).as_posix(),
                "output": mapped,
                "resolved_texture_refs": resolved_textures,
                **refs,
            }
        )

    for texture_file in _iter_files(TEXTURE_PATTERNS):
        mapped = _copy_or_get_texture_mapping(texture_file, texture_copy_map, texture_entries)
        if mapped:
            continue

    for material_file in _iter_files(MATERIAL_PATTERNS):
        mapped = _copy_to_target(material_file, "materials/imported", "materials")
        if mapped:
            material_entries.append(mapped)

    modelengine_entries = _collect_modelengine_files()
    parse_failure_set = set(parse_failures)
    unresolved_source_set = set(unresolved_sources)
    unresolved_texture_set = set(unresolved_texture_refs)
    unresolved_total = len(parse_failure_set.union(unresolved_source_set).union(unresolved_texture_set))

    payload = {
        "model_count": len(model_entries),
        "animation_count": len(animation_entries),
        "animation_controller_count": len(controller_entries),
        "attachable_count": len(attachable_entries),
        "entity_definition_count": len(definition_entries),
        "render_controller_count": len(render_controller_entries),
        "texture_count": len(texture_entries),
        "material_count": len(material_entries),
        "modelengine_asset_count": len(modelengine_entries),
        "parse_failure_count": len(parse_failure_set),
        "parse_failures": sorted(parse_failure_set),
        "unresolved_texture_ref_count": len(unresolved_texture_set),
        "unresolved_texture_refs": sorted(unresolved_texture_set),
        "unresolved_ref_count": unresolved_total,
        "missing_ref_count": unresolved_total,
        "unresolved_sources": sorted(unresolved_source_set),
        "models": sorted(model_entries, key=lambda item: item.get("source", "")),
        "animations": sorted(animation_entries, key=lambda item: item.get("source", "")),
        "animation_controllers": sorted(controller_entries, key=lambda item: item.get("source", "")),
        "attachables": sorted(attachable_entries, key=lambda item: item.get("source", "")),
        "entity_definitions": sorted(definition_entries, key=lambda item: item.get("source", "")),
        "render_controllers": sorted(render_controller_entries, key=lambda item: item.get("source", "")),
        "textures": sorted(set(texture_entries)),
        "materials": sorted(set(material_entries)),
        "modelengine_assets": modelengine_entries,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    _log(
        f"Imported {len(model_entries)} entity models, {len(animation_entries)} animations, "
        f"{len(attachable_entries)} attachables, {len(definition_entries)} definitions, "
        f"{len(render_controller_entries)} render controllers, "
        f"{len(texture_entries)} entity textures, {len(material_entries)} materials"
    )


if __name__ == "__main__":
    run()
