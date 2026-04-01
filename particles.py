from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

PACK_DIR = Path("pack")
TARGET_PARTICLE_DIR = Path("staging/target/rp/particles")
TARGET_TEXTURE_DIR = Path("staging/target/rp/textures/particle")
OUTPUT_FILE = Path("staging/particle_map.json")

TEXTURE_KEYS = {
    "texture",
    "textures",
    "texture_path",
    "texture_file",
    "texture_name",
    "base_texture",
    "atlas_texture",
    "material",
    "billboard_texture",
    "flipbook_texture",
}
PLACEHOLDER_PATTERN = re.compile(r"%[^%]+%|\$\{[^{}]+\}|\{[^{}]+\}|<[^<>]+>")
REFERENCE_KEYS = {
    "event",
    "events",
    "timeline",
    "curve",
    "curves",
    "material",
    "materials",
    "emitter",
    "emitters",
    "animation",
    "animations",
}


def _log(message: str) -> None:
    print(f"[PARTICLE] {message}", flush=True)


def _tokenize_text(value: str) -> List[str]:
    lowered = value.strip().lower()
    if not lowered:
        return []
    return [token for token in re.split(r"[^a-z0-9]+", lowered) if token]


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
    if any(ch in raw for ch in (" ", "\t", "\n", "{", "}")) and "/" not in raw and ":" not in raw:
        return False
    if "=>" in raw or "==" in raw:
        return False
    return "/" in raw or ":" in raw or "texture" in lowered


def _extract_texture_candidates_from_text(value: str) -> List[str]:
    raw = value.strip()
    if not raw:
        return []

    out: Set[str] = set()
    if _looks_like_texture_ref(raw):
        out.add(raw)

    compact = raw.replace("\\", "/")
    for match in re.findall(r"(?:[a-z0-9_]+:)?[a-z0-9_.\-/]+", compact, flags=re.IGNORECASE):
        token = match.strip().strip("'\"")
        if not token:
            continue
        if _looks_like_texture_ref(token):
            out.add(token)

    return sorted(out)


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_string_values(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = value.strip()
        return [raw] if raw else []

    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_extract_string_values(item))
        return out

    if isinstance(value, dict):
        out: List[str] = []
        for item in value.values():
            out.extend(_extract_string_values(item))
        return out

    return []


def _looks_like_named_ref(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith(("http://", "https://", "#")):
        return False
    lowered = raw.lower()
    if lowered in ("none", "null", "false", "true"):
        return False
    return any(token in raw for token in (":", ".", "/", "_")) or len(raw) >= 3


def _iter_named_refs(node: Any) -> Iterable[str]:
    if isinstance(node, list):
        for item in node:
            yield from _iter_named_refs(item)
        return

    if not isinstance(node, dict):
        return

    for key, value in node.items():
        key_lower = str(key).strip().lower()
        if key_lower in REFERENCE_KEYS or any(token in key_lower for token in REFERENCE_KEYS):
            for raw in _extract_string_values(value):
                if _looks_like_named_ref(raw):
                    yield raw
        yield from _iter_named_refs(value)


def _iter_particle_files() -> Iterable[Path]:
    seen: Set[Path] = set()
    for file_path in PACK_DIR.glob("assets/**/particles/**/*.json"):
        if file_path.is_file() and file_path not in seen:
            seen.add(file_path)
            yield file_path
    for file_path in PACK_DIR.glob("assets/**/particles/*.json"):
        if file_path.is_file() and file_path not in seen:
            seen.add(file_path)
            yield file_path


def _namespace_and_relative(path: Path, anchor: str) -> Optional[tuple[str, str]]:
    parts = path.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        namespace = parts[assets_index + 1]
        anchor_index = parts.index(anchor, assets_index + 1)
        rel = "/".join(parts[anchor_index + 1 :])
    except Exception:
        return None

    if not rel:
        return None
    return namespace, rel


def _copy_particle_file(path: Path) -> Optional[str]:
    parsed = _namespace_and_relative(path, "particles")
    if not parsed:
        return None

    namespace, rel = parsed
    destination = TARGET_PARTICLE_DIR / namespace / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    return destination.relative_to(Path("staging/target/rp")).as_posix()


def _iter_texture_refs(node: Any) -> Iterable[str]:
    if isinstance(node, list):
        for item in node:
            yield from _iter_texture_refs(item)
        return

    if not isinstance(node, dict):
        return

    for key, value in node.items():
        key_lower = str(key).lower()
        if key_lower in TEXTURE_KEYS or "texture" in key_lower:
            if isinstance(value, str) and value.strip():
                for ref in _extract_texture_candidates_from_text(value):
                    yield ref
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str) and entry.strip():
                        for ref in _extract_texture_candidates_from_text(entry):
                            yield ref
                    elif isinstance(entry, dict):
                        nested_lower = {str(item).strip().lower(): item for item in entry.keys()}
                        for nested_key in ("texture", "path", "file", "value", "texture_path", "texture_file", "texture_name"):
                            original = nested_lower.get(nested_key)
                            if original is None:
                                continue
                            nested = entry.get(original)
                            if isinstance(nested, str):
                                for ref in _extract_texture_candidates_from_text(nested):
                                    yield ref
            elif isinstance(value, dict):
                value_lower = {str(item).strip().lower(): item for item in value.keys()}
                for nested_key in ("texture", "path", "file", "value", "texture_path", "texture_file", "texture_name"):
                    original = value_lower.get(nested_key)
                    if original is None:
                        continue
                    nested = value.get(original)
                    if isinstance(nested, str):
                        for ref in _extract_texture_candidates_from_text(nested):
                            yield ref
        yield from _iter_texture_refs(value)


def _texture_tokens(reference: str) -> List[str]:
    raw = reference.strip().replace("\\", "/").lower()
    if not raw:
        return []

    for marker in ("textures/", "minecraft:"):
        if raw.startswith(marker):
            raw = raw[len(marker) :]

    raw = PLACEHOLDER_PATTERN.sub(" ", raw)
    for ext in (".png", ".tga", ".jpg", ".jpeg"):
        if raw.endswith(ext):
            raw = raw[: -len(ext)]
            break

    return [part for part in re.split(r"[^a-z0-9]+", raw) if len(part) >= 2]


def _expand_texture_ref(reference: str, token_pool: List[str]) -> Set[str]:
    expanded: Set[str] = {reference}
    if not PLACEHOLDER_PATTERN.search(reference):
        return expanded

    collapsed = PLACEHOLDER_PATTERN.sub("", reference).replace("//", "/").strip()
    if collapsed and collapsed != reference:
        expanded.add(collapsed)

    normalized_tokens = [token for token in token_pool if token]
    for token in normalized_tokens:
        candidate = PLACEHOLDER_PATTERN.sub(token, reference)
        if candidate != reference:
            expanded.add(candidate)

    placeholders = list(PLACEHOLDER_PATTERN.finditer(reference))
    for placeholder in placeholders:
        for token in normalized_tokens:
            candidate = f"{reference[:placeholder.start()]}{token}{reference[placeholder.end():]}"
            if candidate != reference:
                expanded.add(candidate)
    return expanded


def _fallback_texture_candidates(namespace: str, reference: str) -> List[Path]:
    tokens = _texture_tokens(reference)
    if not tokens:
        return []

    raw = reference.strip().replace("\\", "/").lower()
    for marker in ("textures/", f"{namespace.lower()}:", "minecraft:"):
        if raw.startswith(marker):
            raw = raw[len(marker) :]
    raw_stem = Path(raw).stem if raw else ""

    searchable_roots = [PACK_DIR / "assets" / namespace / "textures"]
    if namespace != "minecraft":
        searchable_roots.append(PACK_DIR / "assets" / "minecraft" / "textures")
    for candidate_ns in _all_texture_namespaces():
        root = PACK_DIR / "assets" / candidate_ns / "textures"
        if root not in searchable_roots:
            searchable_roots.append(root)

    scored: List[tuple[int, str, Path]] = []
    for texture_root in searchable_roots:
        if not texture_root.exists():
            continue
        for candidate in texture_root.glob("**/*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in (".png", ".tga"):
                continue

            rel_lower = candidate.relative_to(texture_root).as_posix().lower()
            token_hits = sum(1 for token in tokens if token in rel_lower)
            if token_hits == 0:
                continue

            score = token_hits * 100
            if raw_stem:
                if rel_lower.endswith(f"/{raw_stem}.png") or rel_lower.endswith(f"/{raw_stem}.tga"):
                    score += 75
                elif f"/{raw_stem}." in rel_lower:
                    score += 30

            scored.append((score, rel_lower, candidate))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:8]]


def _resolve_texture(namespace: str, reference: str, token_pool: Optional[List[str]] = None) -> Optional[Path]:
    token_pool = token_pool or []

    for candidate_ref in _expand_texture_ref(reference, token_pool):
        ref = candidate_ref.strip().replace("\\", "/")
        if not ref:
            continue
        if ref.startswith(("http://", "https://")):
            continue

        if ref.startswith("textures/"):
            ref = ref[len("textures/") :]

        if ":" in ref:
            ns, rel = ref.split(":", 1)
        else:
            ns, rel = namespace, ref

        rel = rel.lstrip("/")
        namespace_order: List[str] = [ns]
        if namespace not in namespace_order:
            namespace_order.append(namespace)
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

    for fallback in _fallback_texture_candidates(namespace, reference):
        if fallback.exists() and fallback.is_file():
            return fallback
    return None


def _copy_texture(path: Path) -> Optional[str]:
    parsed = _namespace_and_relative(path, "textures")
    if not parsed:
        return None

    namespace, rel = parsed
    destination = TARGET_TEXTURE_DIR / namespace / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)

    source_meta = path.with_suffix(path.suffix + ".mcmeta")
    if source_meta.exists():
        target_meta = destination.with_suffix(destination.suffix + ".mcmeta")
        shutil.copyfile(source_meta, target_meta)

    return destination.with_suffix("").relative_to(Path("staging/target/rp")).as_posix()


def _texture_animation_metadata(path: Path) -> Optional[Dict[str, Any]]:
    meta_path = path.with_suffix(path.suffix + ".mcmeta")
    if not meta_path.exists():
        return None

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    animation = data.get("animation")
    if not isinstance(animation, dict):
        return None

    metadata: Dict[str, Any] = {
        "frametime": animation.get("frametime"),
        "frames": animation.get("frames"),
        "interpolate": animation.get("interpolate"),
    }
    return metadata


def _extract_identifier(data: Dict[str, Any], fallback: str) -> str:
    particle_effect = data.get("particle_effect")
    if isinstance(particle_effect, dict):
        description = particle_effect.get("description")
        if isinstance(description, dict):
            identifier = description.get("identifier")
            if isinstance(identifier, str) and identifier.strip():
                return identifier.strip()
    return fallback


def run() -> None:
    TARGET_PARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_TEXTURE_DIR.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    copied_textures: Set[str] = set()
    animated_texture_map: Dict[str, Dict[str, Any]] = {}
    parse_failures: List[str] = []
    unresolved_sources: Set[str] = set()

    for particle_file in _iter_particle_files():
        data = _safe_load_json(particle_file)
        if data is None:
            parse_failures.append(particle_file.relative_to(PACK_DIR).as_posix())
            continue

        ns_rel = _namespace_and_relative(particle_file, "particles")
        if not ns_rel:
            unresolved_sources.add(particle_file.relative_to(PACK_DIR).as_posix())
            continue

        namespace, _ = ns_rel
        output_file = _copy_particle_file(particle_file)
        if not output_file:
            unresolved_sources.add(particle_file.relative_to(PACK_DIR).as_posix())
            continue

        fallback_identifier = f"{namespace}:{particle_file.stem}"
        identifier = _extract_identifier(data, fallback_identifier)

        textures: List[str] = []
        texture_refs = sorted(set(_iter_texture_refs(data)))
        named_refs = sorted(set(_iter_named_refs(data)))
        event_refs = sorted(ref for ref in named_refs if "event" in ref.lower() or ref.lower().startswith("particle."))
        curve_refs = sorted(ref for ref in named_refs if "curve" in ref.lower())
        material_refs = sorted(ref for ref in named_refs if "material" in ref.lower())
        emitter_refs = sorted(ref for ref in named_refs if "emitter" in ref.lower() or "spawn" in ref.lower())
        animation_refs = sorted(ref for ref in named_refs if "anim" in ref.lower())
        categorized_refs = set(event_refs + curve_refs + material_refs + emitter_refs + animation_refs)
        other_named_refs = sorted(ref for ref in named_refs if ref not in categorized_refs)

        token_pool: Set[str] = set()
        token_pool.update(_tokenize_text(identifier))
        token_pool.update(_tokenize_text(particle_file.stem))
        token_pool.update(_tokenize_text(particle_file.relative_to(PACK_DIR).as_posix()))
        for named_ref in named_refs:
            token_pool.update(_tokenize_text(named_ref))

        missing_refs: List[str] = []
        for texture_ref in texture_refs:
            source_texture = _resolve_texture(namespace, texture_ref, sorted(token_pool))
            if not source_texture:
                missing_refs.append(texture_ref)
                continue
            animation_meta = _texture_animation_metadata(source_texture)
            mapped = _copy_texture(source_texture)
            if mapped:
                textures.append(mapped)
                copied_textures.add(mapped)
                if animation_meta:
                    animated_texture_map[mapped] = animation_meta

        entries.append(
            {
                "source": particle_file.relative_to(PACK_DIR).as_posix(),
                "output": output_file,
                "identifier": identifier,
                "texture_refs": texture_refs,
                "event_refs": event_refs,
                "curve_refs": curve_refs,
                "material_refs": material_refs,
                "emitter_refs": emitter_refs,
                "animation_refs": animation_refs,
                "other_named_refs": other_named_refs,
                "missing_texture_refs": missing_refs,
                "textures": sorted(set(textures)),
                "animated_textures": {
                    key: animated_texture_map[key]
                    for key in sorted(set(textures))
                    if key in animated_texture_map
                },
            }
        )

    unresolved_texture_ref_count = sum(len(entry.get("missing_texture_refs", [])) for entry in entries)
    parse_failure_set = set(parse_failures)
    unresolved_source_set = set(unresolved_sources)
    unresolved_total = unresolved_texture_ref_count + len(parse_failure_set) + len(unresolved_source_set)

    payload = {
        "particle_file_count": len(entries),
        "texture_count": len(copied_textures),
        "animated_texture_count": len(animated_texture_map),
        "named_ref_count": sum(
            len(entry.get("event_refs", []))
            + len(entry.get("curve_refs", []))
            + len(entry.get("material_refs", []))
            + len(entry.get("emitter_refs", []))
            + len(entry.get("animation_refs", []))
            + len(entry.get("other_named_refs", []))
            for entry in entries
        ),
        "event_ref_count": sum(len(entry.get("event_refs", [])) for entry in entries),
        "curve_ref_count": sum(len(entry.get("curve_refs", [])) for entry in entries),
        "material_ref_count": sum(len(entry.get("material_refs", [])) for entry in entries),
        "emitter_ref_count": sum(len(entry.get("emitter_refs", [])) for entry in entries),
        "animation_ref_count": sum(len(entry.get("animation_refs", [])) for entry in entries),
        "other_named_ref_count": sum(len(entry.get("other_named_refs", [])) for entry in entries),
        "parse_failure_count": len(parse_failure_set),
        "parse_failures": sorted(parse_failure_set),
        "unresolved_source_count": len(unresolved_source_set),
        "unresolved_sources": sorted(unresolved_source_set),
        "unresolved_texture_ref_count": unresolved_texture_ref_count,
        "unresolved_ref_count": unresolved_total,
        "missing_ref_count": unresolved_total,
        "entries": sorted(entries, key=lambda item: item.get("identifier", "")),
        "textures": sorted(copied_textures),
        "animated_textures": {key: animated_texture_map[key] for key in sorted(animated_texture_map.keys())},
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    _log(f"Imported {len(entries)} particle files and {len(copied_textures)} particle textures")


if __name__ == "__main__":
    run()
