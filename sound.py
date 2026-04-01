from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PACK_DIR = Path("pack")
TARGET_SOUNDS_DIR = Path("staging/target/rp/sounds")
OUTPUT_FILE = TARGET_SOUNDS_DIR / "sound_definitions.json"
AUDIO_EXTENSIONS = (".ogg", ".wav", ".mp3", ".flac")


def _log(message: str) -> None:
    print(f"[SOUND] {message}", flush=True)


def _parse_namespace(sound_file: Path) -> str:
    parts = sound_file.parts
    try:
        assets_index = parts.index("assets")
        return parts[assets_index + 1]
    except Exception:
        return "minecraft"


def _split_sound_reference(reference: str, default_ns: str) -> Tuple[str, str]:
    ref = reference.strip()
    if ref.startswith("sounds/"):
        ref = ref[len("sounds/") :]
    if ":" in ref:
        namespace, path = ref.split(":", 1)
        return namespace, path
    return default_ns, ref


def _normalize_sound_path(sound_path: str) -> str:
    normalized = sound_path.strip().replace("\\", "/").lstrip("/")
    for marker in ("?", "#"):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[0]
    if normalized.startswith("sounds/"):
        normalized = normalized[len("sounds/"):]
    for extension in AUDIO_EXTENSIONS:
        if normalized.lower().endswith(extension):
            normalized = normalized[: -len(extension)]
            break
    normalized = re.sub(r"/+", "/", normalized)
    return normalized


def _looks_like_sound_reference(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in {
        "format_version",
        "version",
        "namespace",
        "category",
        "subtitle",
        "replace",
        "stream",
        "sounds",
    }:
        return False
    if raw.startswith(("http://", "https://", "#")):
        return False
    if any(ch in raw for ch in (" ", "\t", "\n", "{", "}", "[", "]")):
        return False
    return "/" in raw or ":" in raw or "." in raw


def _resolve_audio_file(namespace: str, sound_path: str) -> Optional[Path]:
    normalized = _normalize_sound_path(sound_path)
    if not normalized:
        return None

    candidate_refs = [normalized]
    if "/" not in normalized and "." in normalized:
        dotted = normalized.replace(".", "/")
        if dotted and dotted not in candidate_refs:
            candidate_refs.append(dotted)

    candidates: List[Path] = []
    for candidate_ref in candidate_refs:
        base = PACK_DIR / "assets" / namespace / "sounds" / candidate_ref
        candidates.append(base)
        if base.suffix == "":
            for ext in AUDIO_EXTENSIONS:
                candidates.append(base.with_suffix(ext))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    candidate_basenames = [Path(candidate_ref).name for candidate_ref in candidate_refs if candidate_ref]
    candidate_stems = sorted({Path(name).stem for name in candidate_basenames if name})
    if not candidate_stems:
        return None

    root = PACK_DIR / "assets" / namespace / "sounds"
    if root.exists():
        for stem in candidate_stems:
            for ext in AUDIO_EXTENSIONS:
                for candidate in root.glob(f"**/{stem}{ext}"):
                    if candidate.exists() and candidate.is_file():
                        return candidate

    for candidate_ref in candidate_refs:
        for ext in AUDIO_EXTENSIONS:
            for candidate in PACK_DIR.glob(f"assets/**/sounds/**/{candidate_ref}{ext}"):
                if candidate.exists() and candidate.is_file():
                    return candidate

    for stem in candidate_stems:
        for ext in AUDIO_EXTENSIONS:
            for candidate in PACK_DIR.glob(f"assets/**/sounds/**/{stem}{ext}"):
                if candidate.exists() and candidate.is_file():
                    return candidate
    return None


def _asset_namespace(path: Path) -> Optional[str]:
    parts = path.parts
    try:
        assets_index = parts.index("assets")
        return parts[assets_index + 1]
    except Exception:
        return None


def _copy_audio(namespace: str, sound_path: str) -> Optional[str]:
    source = _resolve_audio_file(namespace, sound_path)
    if source is None and namespace != "minecraft":
        source = _resolve_audio_file("minecraft", sound_path)
    if source is None:
        return None

    source_namespace = _asset_namespace(source) or namespace or "minecraft"

    sounds_root = PACK_DIR / "assets" / source_namespace / "sounds"

    try:
        relative = source.relative_to(sounds_root)
    except Exception:
        relative = Path(_normalize_sound_path(sound_path))

    normalized = relative.with_suffix("").as_posix().lstrip("/")
    if not normalized:
        return None

    output_key = normalized
    if source_namespace != "minecraft":
        output_key = f"{source_namespace}/{output_key}"

    destination = TARGET_SOUNDS_DIR / f"{output_key}{source.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return f"sounds/{output_key}"


def _iter_sound_entries(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_sound_entries(item)
        return

    if isinstance(value, dict):
        lower = {str(key).strip().lower(): key for key in value.keys()}
        for key in (
            "sounds",
            "sound",
            "entries",
            "variants",
            "values",
            "clips",
            "files",
            "sound_entries",
            "soundevents",
            "sound_events",
            "events",
        ):
            original = lower.get(key)
            if original is None:
                continue
            yield from _iter_sound_entries(value.get(original))
            return

        for key in ("name", "path", "file"):
            original = lower.get(key)
            if original is None:
                continue
            nested = value.get(original)
            if isinstance(nested, (str, dict, list)):
                yield value
                return

        mapped_entries: List[Any] = []
        for raw_key, raw_item in value.items():
            key_text = str(raw_key).strip()
            if not _looks_like_sound_reference(key_text):
                continue

            if isinstance(raw_item, dict):
                candidate = dict(raw_item)
                candidate.setdefault("name", key_text)
                mapped_entries.append(candidate)
                continue

            if isinstance(raw_item, (int, float, bool)):
                mapped_entries.append({"name": key_text, "weight": int(raw_item)})
                continue

            if isinstance(raw_item, str):
                marker = raw_item.strip().lower()
                if marker in ("false", "disable", "disabled", "deny", "denied", "0"):
                    continue
                mapped_entries.append({"name": key_text})
                continue

            mapped_entries.append({"name": key_text})

        if mapped_entries:
            for entry in mapped_entries:
                yield entry
            return

        for nested in value.values():
            if isinstance(nested, (str, dict, list)):
                yield from _iter_sound_entries(nested)
        return

    if value is not None:
        yield value


def _convert_sound_entry(raw_entry: Any, default_ns: str) -> Optional[Any]:
    if isinstance(raw_entry, str):
        ref = raw_entry.strip()
        if not ref:
            return None
        ns, path = _split_sound_reference(ref, default_ns)
        copied = _copy_audio(ns, path)
        return copied

    if not isinstance(raw_entry, dict):
        return None

    lower = {str(key).strip().lower(): key for key in raw_entry.keys()}

    def _pick(*keys: str) -> Any:
        for key in keys:
            original = lower.get(key)
            if original is not None:
                return raw_entry.get(original)
        return None

    name = _pick("name", "sound", "path", "file")
    if isinstance(name, dict):
        nested_lower = {str(key).strip().lower(): key for key in name.keys()}
        for nested_key in ("name", "sound", "path", "file", "value"):
            original = nested_lower.get(nested_key)
            if original is None:
                continue
            nested_value = name.get(original)
            if isinstance(nested_value, str):
                name = nested_value
                break

    if not isinstance(name, str):
        return None

    ns, path = _split_sound_reference(name, default_ns)
    copied = _copy_audio(ns, path)
    if not copied:
        return None

    converted = dict(raw_entry)
    converted["name"] = copied
    for alias in ("sound", "path", "file"):
        original = lower.get(alias)
        if original is not None:
            converted[original] = copied
    return converted


def _iter_event_payloads(data: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
    metadata_keys = {
        "format_version",
        "version",
        "pack_format",
        "namespace",
        "meta",
        "metadata",
    }

    def _walk(node: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
        if isinstance(node, dict):
            for event_name, event_payload in node.items():
                key = str(event_name)
                key_lower = key.lower()
                if not prefix and key_lower in metadata_keys:
                    continue
                if key_lower in ("sound_definitions", "events"):
                    yield from _walk(event_payload, prefix)
                    continue

                nested_name = f"{prefix}.{key}" if prefix else key
                if isinstance(event_payload, dict):
                    payload_keys = {str(item).strip().lower() for item in event_payload.keys()}
                    if any(token in payload_keys for token in ("sounds", "sound", "entries", "variants", "values", "name", "file", "path")):
                        yield nested_name, event_payload
                    else:
                        yield from _walk(event_payload, nested_name)
                elif isinstance(event_payload, (list, str)):
                    yield nested_name, event_payload
        elif isinstance(node, list) and prefix:
            yield prefix, node

    yield from _walk(data)


def _normalize_event_payload(event_payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(event_payload, (str, list)):
        return {"sounds": event_payload}

    if not isinstance(event_payload, dict):
        return None

    normalized = dict(event_payload)
    lower = {str(key).strip().lower(): key for key in normalized.keys()}

    sounds_key = lower.get("sounds")
    sounds = normalized.get(sounds_key) if sounds_key is not None else None
    if sounds is None:
        for key in (
            "sound",
            "entries",
            "variants",
            "values",
            "clips",
            "files",
            "sound_entries",
            "soundevents",
            "sound_events",
            "events",
            "sound_effects",
            "soundeffects",
            "sound_list",
        ):
            original = lower.get(key)
            if original is not None:
                sounds = normalized.get(original)
                break

    if sounds is None:
        for key in ("name", "file", "path"):
            original = lower.get(key)
            if original is None:
                continue
            value = normalized.get(original)
            if isinstance(value, (str, list, dict)):
                sounds = value
                break

    if sounds is None:
        return None

    normalized["sounds"] = sounds
    return normalized


def _extract_emitted_sound_refs(entry: Any) -> Set[str]:
    out: Set[str] = set()
    if isinstance(entry, str):
        raw = entry.strip()
        if raw.startswith("sounds/"):
            out.add(raw)
        return out

    if isinstance(entry, dict):
        for key in ("name", "sound", "path", "file"):
            value = entry.get(key)
            if isinstance(value, str):
                raw = value.strip()
                if raw.startswith("sounds/"):
                    out.add(raw)
    return out


def _unique_event_key(base_key: str, definitions: Dict[str, Any]) -> str:
    if base_key not in definitions:
        return base_key

    suffix = 1
    while True:
        candidate = f"{base_key}.{suffix}"
        if candidate not in definitions:
            return candidate
        suffix += 1


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _iter_raw_audio_files() -> Iterable[Path]:
    seen: Set[Path] = set()
    for extension in ("*.ogg", "*.wav", "*.mp3", "*.flac"):
        for sound_file in PACK_DIR.glob(f"assets/**/sounds/**/{extension}"):
            if not sound_file.is_file():
                continue
            if sound_file in seen:
                continue
            seen.add(sound_file)
            yield sound_file


def _raw_audio_namespace_and_path(sound_file: Path) -> Optional[Tuple[str, str]]:
    parts = sound_file.parts
    try:
        assets_index = parts.index("assets")
        namespace = parts[assets_index + 1]
        sounds_index = parts.index("sounds", assets_index + 1)
        relative = Path(*parts[sounds_index + 1 :]).with_suffix("").as_posix()
        if not relative:
            return None
        return namespace, relative
    except Exception:
        return None


def run() -> None:
    sound_files = sorted(PACK_DIR.glob("assets/**/sounds.json"))
    raw_sound_files = list(_iter_raw_audio_files())
    if not sound_files and not raw_sound_files:
        _log("No sounds.json files found; skipping")
        return

    TARGET_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    output: Dict[str, Any] = {"format_version": "1.14.0", "sound_definitions": {}}
    converted_events = 0
    unresolved_sounds: List[str] = []
    parse_failures: List[str] = []
    emitted_sound_refs: Set[str] = set()

    for sound_file in sound_files:
        namespace = _parse_namespace(sound_file)
        data = _load_json(sound_file)
        if data is None:
            _log(f"Invalid sound file skipped: {sound_file}")
            parse_failures.append(sound_file.relative_to(PACK_DIR).as_posix())
            continue

        for event_name, event_payload in _iter_event_payloads(data):
            normalized_payload = _normalize_event_payload(event_payload)
            if not isinstance(normalized_payload, dict):
                continue

            base_event_key = f"{namespace}:{event_name}"
            event_key = _unique_event_key(base_event_key, output["sound_definitions"])
            category = normalized_payload.get("category")
            sounds = normalized_payload.get("sounds")

            if sounds is None:
                continue

            converted_sounds: List[Any] = []
            for raw_sound in _iter_sound_entries(sounds):
                converted = _convert_sound_entry(raw_sound, namespace)
                if converted is not None:
                    converted_sounds.append(converted)
                    emitted_sound_refs.update(_extract_emitted_sound_refs(converted))
                else:
                    unresolved_sounds.append(f"{event_key}:{raw_sound}")

            if not converted_sounds:
                continue

            output["sound_definitions"][event_key] = {
                "category": category if isinstance(category, str) else "neutral",
                "sounds": converted_sounds,
            }

            payload_lower = {str(key).strip().lower(): key for key in normalized_payload.keys()}
            for passthrough in ("subtitle", "replace", "stream", "max_distance", "min_distance"):
                original = payload_lower.get(passthrough)
                if original is not None:
                    output["sound_definitions"][event_key][passthrough] = normalized_payload.get(original)

            converted_events += 1

    diagnostics = TARGET_SOUNDS_DIR / "sound_diagnostics.json"
    unresolved_unique = sorted(set(unresolved_sounds))
    parse_failure_set = set(parse_failures)
    unresolved_total = len(unresolved_unique) + len(parse_failure_set)
    if unresolved_total > 0:
        with diagnostics.open("w", encoding="utf-8") as file:
            json.dump(
                {
                    "unresolved_count": unresolved_total,
                    "unresolved_ref_count": unresolved_total,
                    "missing_ref_count": unresolved_total,
                    "parse_failure_count": len(parse_failure_set),
                    "parse_failures": sorted(parse_failure_set),
                    "unresolved": unresolved_unique,
                },
                file,
                indent=2,
                ensure_ascii=False,
            )
    elif diagnostics.exists():
        diagnostics.unlink()

    raw_added_count = 0
    for sound_file in raw_sound_files:
        ns_path = _raw_audio_namespace_and_path(sound_file)
        if not ns_path:
            continue
        namespace, normalized_path = ns_path

        copied = _copy_audio(namespace, normalized_path)
        if not copied:
            continue
        if copied in emitted_sound_refs:
            continue

        emitted_sound_refs.add(copied)
        base_event_key = f"{namespace}:{normalized_path.replace('/', '.')}"
        event_key = _unique_event_key(base_event_key, output["sound_definitions"])
        output["sound_definitions"][event_key] = {
            "category": "neutral",
            "sounds": [copied],
        }
        converted_events += 1
        raw_added_count += 1

    output["converted_event_count"] = converted_events
    output["sound_event_count"] = len(output.get("sound_definitions", {}))
    output["parse_failure_count"] = len(parse_failure_set)
    output["parse_failures"] = sorted(parse_failure_set)
    output["unresolved_count"] = unresolved_total
    output["unresolved_ref_count"] = unresolved_total
    output["missing_ref_count"] = unresolved_total
    output["raw_audio_added_count"] = raw_added_count
    output["source_sound_file_count"] = len(sound_files)
    output["source_raw_audio_file_count"] = len(raw_sound_files)
    output["unresolved"] = unresolved_unique

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)

    _log(f"Converted {converted_events} sound events")


if __name__ == "__main__":
    run()
