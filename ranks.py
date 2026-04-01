from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

PACK_DIR = Path("pack")
TARGET_ICON_DIR = Path("staging/target/rp/textures/ranks")
OUTPUT_FILE = Path("staging/rank_map.json")

RANK_NAME_KEYS = ("rank", "name", "group", "title", "prefix")
PERMISSION_KEYS = ("permission", "permissions", "perm", "node", "nodes", "group", "groups")
CONDITION_PERMISSION_KEYS = (
    "condition",
    "conditions",
    "if",
    "when",
    "requirement",
    "requirements",
    "permission_condition",
    "permission_conditions",
)
ICON_KEYS = (
    "icon",
    "icons",
    "texture",
    "textures",
    "sprite",
    "image",
    "path",
    "file",
    "icon_path",
    "badge_texture",
    "material",
    "badge",
    "display_icon",
)
PERMISSION_ICON_CONTAINER_KEYS = (
    "permission_icons",
    "permission_icon",
    "permission_textures",
    "permission_texture",
    "icons_by_permission",
    "icon_by_permission",
    "permission_icon_map",
    "permission_badges",
    "perm_icons",
    "icons_by_perm",
)
CONFIG_PATTERNS = (
    "**/*rank*.yml",
    "**/*rank*.yaml",
    "**/*rank*.json",
    "**/*permission*.yml",
    "**/*permission*.yaml",
    "**/*permission*.json",
    "**/*group*.yml",
    "**/*group*.yaml",
    "**/*group*.json",
)
ICON_HINTS = ("rank", "badge", "prefix", "group", "role", "tag", "vip", "donor", "staff")
DEFAULT_ICON_STATE_KEYS = {
    "default",
    "active",
    "selected",
    "hover",
    "normal",
    "off",
    "on",
    "enabled",
    "disabled",
}
PLACEHOLDER_PATTERN = re.compile(r"%[^%]+%|\$\{[^{}]+\}|\{[^{}]+\}|<[^<>]+>")


def _log(message: str) -> None:
    print(f"[RANKS] {message}", flush=True)


def _safe_load(path: Path) -> Optional[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            return data if isinstance(data, (dict, list)) else None
        except Exception:
            return None

    if yaml is None:
        return None

    try:
        data = yaml.safe_load(text)
        return data if isinstance(data, (dict, list)) else None
    except Exception:
        return None


def _iter_candidate_configs() -> Iterable[Path]:
    seen: Set[Path] = set()
    for pattern in CONFIG_PATTERNS:
        for file_path in PACK_DIR.glob(pattern):
            if not file_path.is_file():
                continue
            if file_path in seen:
                continue
            seen.add(file_path)
            yield file_path


def _normalize_permission(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if any(sep in raw for sep in (",", ";", "|")):
            out: List[str] = []
            chunks = raw.replace(";", ",").replace("|", ",").split(",")
            for chunk in chunks:
                out.extend(_normalize_permission(chunk))
            return out
        return [raw]

    if isinstance(value, dict):
        out: List[str] = []
        for key, enabled in value.items():
            node = str(key).strip()
            if not node:
                continue

            if isinstance(enabled, bool):
                if enabled:
                    out.append(node)
                continue

            if isinstance(enabled, (int, float)):
                if int(enabled) != 0:
                    out.append(node)
                continue

            if isinstance(enabled, str):
                marker = enabled.strip().lower()
                if marker in ("false", "deny", "denied", "no", "0"):
                    continue
                out.append(node)
                continue

            if isinstance(enabled, (dict, list)):
                out.append(node)
                out.extend(_normalize_permission(enabled))
                continue

            out.append(node)
        return out

    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_normalize_permission(item))
        return out
    return []


def _extract_icon_refs(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []

        if any(sep in raw for sep in (",", ";", "|")) and "/" not in raw and raw.count(":") <= 1:
            out: List[str] = []
            chunks = raw.replace(";", ",").replace("|", ",").split(",")
            for chunk in chunks:
                out.extend(_extract_icon_refs(chunk))
            return out
        return [raw]

    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_extract_icon_refs(item))
        return out

    if isinstance(value, dict):
        out: List[str] = []
        for key, nested in value.items():
            key_lower = str(key).lower()
            if any(token in key_lower for token in ICON_KEYS) or key_lower in DEFAULT_ICON_STATE_KEYS:
                out.extend(_extract_icon_refs(nested))
            elif isinstance(nested, (dict, list)):
                out.extend(_extract_icon_refs(nested))
        return out
    return []


def _extract_permission_icon_map(value: Any) -> Dict[str, List[str]]:
    out: Dict[str, Set[str]] = {}

    def _add(permission: str, refs: List[str]) -> None:
        key = _normalize_permission_candidate(permission)
        if not key or not _looks_like_permission_key(key):
            return
        out.setdefault(key, set())
        for ref in refs:
            ref_text = str(ref).strip()
            if ref_text:
                out[key].add(ref_text)

    if isinstance(value, list):
        for item in value:
            _merge_permission_icon_refs(out, _extract_permission_icon_map(item))
        return {key: sorted(values) for key, values in sorted(out.items())}

    if not isinstance(value, dict):
        return {}

    lower = {str(k).strip().lower(): k for k in value.keys()}

    explicit_permissions: List[str] = []
    for key in PERMISSION_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        explicit_permissions.extend(_normalize_permission(value.get(original)))
    for key in CONDITION_PERMISSION_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        explicit_permissions.extend(_extract_condition_permissions(value.get(original)))

    explicit_refs: List[str] = []
    for key in ICON_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        explicit_refs.extend(_extract_icon_refs(value.get(original)))
    for alias in ("value", "ref", "icon_ref", "texture_ref", "path", "file"):
        original = lower.get(alias)
        if original is None:
            continue
        explicit_refs.extend(_extract_icon_refs(value.get(original)))

    if explicit_permissions and explicit_refs:
        dedup_refs = sorted(set(explicit_refs))
        for permission in sorted(set(explicit_permissions)):
            _add(permission, dedup_refs)

    for raw_key, nested in value.items():
        key_text = str(raw_key).strip()
        key_permissions = _extract_condition_permissions(key_text)
        if not key_permissions and _looks_like_permission_key(key_text):
            normalized_key = _normalize_permission_candidate(key_text)
            if normalized_key:
                key_permissions = [normalized_key]

        if key_permissions:
            refs = _extract_icon_refs(nested)
            if refs:
                for permission in key_permissions:
                    _add(permission, refs)

        if isinstance(nested, (dict, list)):
            _merge_permission_icon_refs(out, _extract_permission_icon_map(nested))

    return {key: sorted(values) for key, values in sorted(out.items()) if values}


def _merge_permission_icon_refs(target: Dict[str, Set[str]], source: Dict[str, List[str]]) -> None:
    for permission, refs in source.items():
        permission_key = str(permission).strip()
        if not permission_key:
            continue
        target.setdefault(permission_key, set())
        for ref in refs:
            ref_str = str(ref).strip()
            if ref_str:
                target[permission_key].add(ref_str)


def _normalize_permission_candidate(value: str) -> str:
    key = value.strip().lower().strip("'\"")
    if not key:
        return ""

    while key.startswith(("!", "+", "-")) and len(key) > 1:
        key = key[1:].strip()

    for wrapper in ("has_permission(", "haspermission(", "permission(", "perm(", "node(", "group("):
        if key.startswith(wrapper) and key.endswith(")"):
            key = key[len(wrapper) : -1].strip().strip("'\"")
            break

    for prefix in ("permission:", "perm:", "node:", "group:"):
        if key.startswith(prefix):
            key = key[len(prefix) :].strip()

    return key


def _looks_like_permission_key(value: str) -> bool:
    key = _normalize_permission_candidate(value)
    if not key:
        return False

    if "=" in key and not key.startswith("http"):
        parts = [part.strip() for part in key.split("=") if part.strip()]
        for part in parts:
            if _looks_like_permission_key(part):
                return True

    if key in DEFAULT_ICON_STATE_KEYS:
        return False
    if any(token in key for token in ("/", "\\", ".png", ".tga", "textures", "http://", "https://")):
        return False
    if any(ch in key for ch in (" ", "\t", "{", "}", "[", "]")):
        return False
    if "." in key or ":" in key or "*" in key:
        return True
    return key.startswith(("perm", "permission", "rank", "group", "lp_", "luckperms"))


def _extract_condition_permissions(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []

        normalized = raw
        normalized = re.sub(r"\b(?:and|or|not)\b", " ", normalized, flags=re.IGNORECASE)
        normalized = normalized.replace("&&", " ").replace("||", " ")

        out: Set[str] = set()
        token_candidates = re.findall(r"[A-Za-z0-9_.*:!+\-]+", normalized)
        token_candidates.extend(re.findall(r"['\"]([^'\"]+)['\"]", raw))
        for candidate in token_candidates:
            normalized_candidate = _normalize_permission_candidate(candidate)
            if _looks_like_permission_key(normalized_candidate):
                out.add(normalized_candidate)
        return sorted(out)

    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_extract_condition_permissions(item))
        return sorted(set(out))

    if isinstance(value, dict):
        out: List[str] = []
        for key, nested in value.items():
            key_text = _normalize_permission_candidate(str(key).strip())
            if _looks_like_permission_key(key_text):
                out.append(key_text)
            out.extend(_extract_condition_permissions(nested))
        return sorted(set(out))

    return []


def _extract_permission_icon_refs(node: Any) -> Dict[str, List[str]]:
    out: Dict[str, Set[str]] = {}

    if isinstance(node, list):
        for item in node:
            _merge_permission_icon_refs(out, _extract_permission_icon_refs(item))
        return {key: sorted(values) for key, values in sorted(out.items())}

    if not isinstance(node, dict):
        return {}

    lower = {str(k).lower(): k for k in node.keys()}

    explicit_permissions: List[str] = []
    for permission_key in PERMISSION_KEYS:
        original = lower.get(permission_key)
        if original is None:
            continue
        explicit_permissions.extend(_normalize_permission(node.get(original)))

    for condition_key in CONDITION_PERMISSION_KEYS:
        original = lower.get(condition_key)
        if original is None:
            continue
        explicit_permissions.extend(_extract_condition_permissions(node.get(original)))

    explicit_icon_refs: List[str] = []
    for icon_key in ICON_KEYS:
        original = lower.get(icon_key)
        if original is None:
            continue
        explicit_icon_refs.extend(_extract_icon_refs(node.get(original)))

    if explicit_permissions and explicit_icon_refs:
        dedup_icons = sorted(set(explicit_icon_refs))
        for permission in sorted(set(explicit_permissions)):
            if not _looks_like_permission_key(permission):
                continue
            out.setdefault(permission, set()).update(dedup_icons)

    for key, value in node.items():
        key_str = str(key).strip()
        permission_keys = _extract_condition_permissions(key_str)
        if not permission_keys and _looks_like_permission_key(key_str):
            normalized_permission = _normalize_permission_candidate(key_str)
            if normalized_permission:
                permission_keys = [normalized_permission]
        if permission_keys:
            refs = _extract_icon_refs(value)
            if refs:
                for permission in permission_keys:
                    out.setdefault(permission, set()).update(refs)

    for icon_key in ICON_KEYS:
        original = lower.get(icon_key)
        if original is None:
            continue
        icon_node = node.get(original)
        if not isinstance(icon_node, dict):
            continue

        for condition_key, condition_value in icon_node.items():
            permissions = _extract_condition_permissions(str(condition_key))
            if not permissions:
                continue
            refs = _extract_icon_refs(condition_value)
            if refs:
                for permission in permissions:
                    out.setdefault(permission, set()).update(refs)

    for container_key in PERMISSION_ICON_CONTAINER_KEYS:
        original = lower.get(container_key)
        if original is None:
            continue
        _merge_permission_icon_refs(out, _extract_permission_icon_map(node.get(original)))

    for key, value in node.items():
        key_lower = str(key).strip().lower()
        if "permission" in key_lower and ("icon" in key_lower or "texture" in key_lower or "badge" in key_lower):
            _merge_permission_icon_refs(out, _extract_permission_icon_map(value))

    for value in node.values():
        if isinstance(value, (dict, list)):
            _merge_permission_icon_refs(out, _extract_permission_icon_refs(value))

    return {key: sorted(values) for key, values in sorted(out.items())}


def _tokenize_text(value: str) -> List[str]:
    lowered = value.strip().lower()
    if not lowered:
        return []
    tokens = re.split(r"[^a-z0-9]+", lowered)
    return [token for token in tokens if token]


def _permission_tokens(permission: str) -> List[str]:
    raw = permission.strip().lower()
    if not raw:
        return []
    normalized = raw.replace("*", " ").replace(":", " ").replace(".", " ").replace("!", " ")
    return _tokenize_text(normalized)


def _entry_tokens(entry: Dict[str, Any]) -> List[str]:
    tokens: Set[str] = set()

    rank = entry.get("rank")
    if isinstance(rank, str):
        tokens.update(_tokenize_text(rank))

    for permission in entry.get("permissions", []):
        if isinstance(permission, str):
            tokens.update(_tokenize_text(permission))
            tokens.update(_permission_tokens(permission))

    permission_icons = entry.get("permission_icon_refs")
    if isinstance(permission_icons, dict):
        for permission in permission_icons.keys():
            if isinstance(permission, str):
                tokens.update(_tokenize_text(permission))
                tokens.update(_permission_tokens(permission))
        for refs in permission_icons.values():
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, str):
                    tokens.update(_tokenize_text(ref))

    icon_refs = entry.get("icon_refs")
    if isinstance(icon_refs, list):
        for icon_ref in icon_refs:
            if isinstance(icon_ref, str):
                tokens.update(_tokenize_text(icon_ref))

    for key in ("source", "path"):
        raw_value = entry.get(key)
        if isinstance(raw_value, str):
            tokens.update(_tokenize_text(raw_value))

    return sorted(tokens)


def _extract_namespace(path: Path) -> Optional[str]:
    parts = path.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        return parts[assets_index + 1]
    except Exception:
        return None


def _iter_texture_files() -> Iterable[Path]:
    for texture in PACK_DIR.glob("assets/**/textures/**/*"):
        if not texture.is_file():
            continue
        if texture.suffix.lower() not in (".png", ".tga"):
            continue
        yield texture


def _build_texture_index() -> Dict[str, List[Path]]:
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for texture in _iter_texture_files():
        namespace = _extract_namespace(texture)
        if not namespace:
            continue
        grouped[namespace].append(texture)

    for namespace in grouped:
        grouped[namespace] = sorted(grouped[namespace])
    return dict(grouped)


def _expand_dynamic_ref(reference: str, token_pool: List[str]) -> Set[str]:
    expanded: Set[str] = {reference}
    if not PLACEHOLDER_PATTERN.search(reference):
        return expanded

    collapsed = PLACEHOLDER_PATTERN.sub("", reference).replace("//", "/").strip()
    if collapsed and collapsed != reference:
        expanded.add(collapsed)

    normalized_tokens = [token for token in token_pool if token]
    for token in token_pool:
        if not token:
            continue
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


def _fallback_texture_candidates(reference: str, namespace: str, token_pool: List[str], texture_index: Dict[str, List[Path]]) -> List[Path]:
    raw = reference.strip().replace("\\", "/").lower()
    if not raw:
        return []

    for marker in ("textures/", "minecraft:", f"{namespace.lower()}:"):
        if raw.startswith(marker):
            raw = raw[len(marker) :]

    raw = PLACEHOLDER_PATTERN.sub(" ", raw)
    for ext in (".png", ".tga", ".jpg", ".jpeg"):
        if raw.endswith(ext):
            raw = raw[: -len(ext)]
            break

    raw_parts = [part for part in re.split(r"[^a-z0-9]+", raw) if len(part) >= 2]
    primary_tokens = sorted(set(raw_parts))
    secondary_tokens = sorted(
        {
            token
            for token in token_pool
            if isinstance(token, str)
            for token in [token.strip().lower()]
            if len(token) >= 2 and token not in primary_tokens
        }
    )

    if not primary_tokens and not secondary_tokens:
        return []

    raw_stem = Path(raw).name if raw else ""

    searchable: List[Path] = []
    seen: Set[Path] = set()

    namespace_order: List[str] = [namespace]
    if namespace != "minecraft":
        namespace_order.append("minecraft")
    for candidate_ns in sorted(texture_index.keys()):
        if candidate_ns not in namespace_order:
            namespace_order.append(candidate_ns)

    for candidate_ns in namespace_order:
        for texture in texture_index.get(candidate_ns, []):
            if texture in seen:
                continue
            seen.add(texture)
            searchable.append(texture)

    scored: List[tuple[int, str, Path]] = []
    for texture in searchable:
        rel_lower = texture.relative_to(PACK_DIR).as_posix().lower()

        primary_hits = sum(1 for token in primary_tokens if token in rel_lower)
        if primary_tokens and primary_hits == 0:
            continue

        secondary_hits = sum(1 for token in secondary_tokens if token in rel_lower)
        score = primary_hits * 100 + secondary_hits * 10

        if raw_stem:
            if rel_lower.endswith(f"/{raw_stem}.png") or rel_lower.endswith(f"/{raw_stem}.tga"):
                score += 75
            elif f"/{raw_stem}." in rel_lower:
                score += 30

        if score <= 0:
            continue
        scored.append((score, rel_lower, texture))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:8]]


def _resolve_icon_sources(icon_ref: str, default_ns: str, token_pool: List[str], texture_index: Dict[str, List[Path]]) -> List[Path]:
    resolved: Set[Path] = set()

    for candidate_ref in _expand_dynamic_ref(icon_ref, token_pool):
        source = _resolve_texture_ref(candidate_ref, default_ns=default_ns)
        if source:
            resolved.add(source)

    if resolved:
        return sorted(resolved)

    fallback = _fallback_texture_candidates(icon_ref, default_ns, token_pool, texture_index)
    return sorted(set(fallback))


def _resolve_texture_ref(texture_ref: str, default_ns: str = "minecraft") -> Optional[Path]:
    ref = texture_ref.strip().replace("\\", "/")
    if not ref:
        return None
    if ref.startswith(("http://", "https://")):
        return None

    if ref.startswith("textures/"):
        ref = ref[len("textures/") :]

    if ":" in ref:
        namespace, rel = ref.split(":", 1)
    else:
        namespace, rel = default_ns, ref

    rel = rel.lstrip("/")

    namespace_order: List[str] = [namespace]
    if default_ns not in namespace_order:
        namespace_order.append(default_ns)
    if "minecraft" not in namespace_order:
        namespace_order.append("minecraft")

    for candidate_ns in namespace_order:
        base = PACK_DIR / "assets" / candidate_ns / "textures" / rel
        if base.exists() and base.is_file():
            return base

        if base.suffix == "":
            png = base.with_suffix(".png")
            if png.exists():
                return png
            tga = base.with_suffix(".tga")
            if tga.exists():
                return tga

            if "/" not in rel:
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


def _copy_icon(source: Path) -> Optional[str]:
    parts = source.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        namespace = parts[assets_index + 1]
        rel = "/".join(parts[assets_index + 3 :])
    except Exception:
        return None

    destination = TARGET_ICON_DIR / namespace / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

    source_meta = source.with_suffix(source.suffix + ".mcmeta")
    if source_meta.exists():
        target_meta = destination.with_suffix(destination.suffix + ".mcmeta")
        shutil.copyfile(source_meta, target_meta)

    no_suffix = destination.with_suffix("").as_posix()
    return no_suffix.replace("staging/target/rp/", "")


def _extract_rank_entries(node: Any, source: str, out: List[Dict[str, Any]], path: str = "root") -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _extract_rank_entries(item, source, out, f"{path}[{index}]")
        return

    if not isinstance(node, dict):
        return

    lower = {str(k).lower(): k for k in node.keys()}
    rank_name = None
    permissions: List[str] = []
    icon_refs: List[str] = []
    permission_icon_refs: Dict[str, List[str]] = {}

    for key in RANK_NAME_KEYS:
        original = lower.get(key)
        if original is not None:
            value = node.get(original)
            if isinstance(value, str) and value.strip():
                rank_name = value.strip()
                break

    for key in PERMISSION_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        permissions.extend(_normalize_permission(node.get(original)))

    for key in ICON_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        icon_refs.extend(_extract_icon_refs(node.get(original)))

    permission_icon_refs = _extract_permission_icon_refs(node)
    if not permission_icon_refs and permissions and icon_refs:
        permission_icon_refs = {permission: sorted(set(icon_refs)) for permission in permissions}

    if rank_name or permissions or icon_refs or permission_icon_refs:
        out.append(
            {
                "rank": rank_name,
                "permissions": sorted(set(permissions)),
                "icon_refs": sorted(set(icon_refs)),
                "permission_icon_refs": {
                    key: sorted(set(value))
                    for key, value in sorted(permission_icon_refs.items())
                    if value
                },
                "source": source,
                "path": path,
            }
        )

    for key, value in node.items():
        _extract_rank_entries(value, source, out, f"{path}.{key}")


def _collect_hint_icons() -> List[str]:
    copied: List[str] = []
    for texture in PACK_DIR.glob("assets/**/textures/**/*"):
        if not texture.is_file():
            continue
        if texture.suffix.lower() not in (".png", ".tga"):
            continue

        rel = texture.relative_to(PACK_DIR).as_posix().lower()
        if not any(hint in rel for hint in ICON_HINTS):
            continue

        copied_path = _copy_icon(texture)
        if copied_path:
            copied.append(copied_path)
    return sorted(set(copied))


def run() -> None:
    TARGET_ICON_DIR.mkdir(parents=True, exist_ok=True)
    texture_index = _build_texture_index()

    raw_entries: List[Dict[str, Any]] = []
    copied_icons: Set[str] = set()
    unresolved_icon_refs: Set[str] = set()
    unresolved_permission_icon_refs: Dict[str, Set[str]] = defaultdict(set)
    parsed_files = 0
    parse_failures: List[str] = []

    for config_file in _iter_candidate_configs():
        data = _safe_load(config_file)
        if data is None:
            parse_failures.append(config_file.relative_to(PACK_DIR).as_posix())
            continue

        parsed_files += 1
        rel_source = config_file.relative_to(PACK_DIR).as_posix()
        _extract_rank_entries(data, rel_source, raw_entries)

    dedup_entries: Dict[str, Dict[str, Any]] = {}
    for entry in raw_entries:
        permission_icon_refs = entry.get("permission_icon_refs", {})
        key = "|".join(
            [
                str(entry.get("source", "")),
                str(entry.get("path", "")),
                str(entry.get("rank", "")),
                ",".join(sorted(entry.get("permissions", []))),
                ",".join(sorted(entry.get("icon_refs", []))),
                json.dumps(permission_icon_refs, sort_keys=True, ensure_ascii=False),
            ]
        )
        dedup_entries[key] = entry

    entries = [dedup_entries[key] for key in sorted(dedup_entries.keys())]

    for entry in entries:
        dynamic_icons: List[str] = []
        permission_icon_textures: Dict[str, List[str]] = {}
        source_path = entry.get("source", "")
        namespace = "minecraft"
        if isinstance(source_path, str) and source_path.startswith("assets/"):
            chunks = source_path.split("/")
            if len(chunks) > 1:
                namespace = chunks[1]

        token_pool = _entry_tokens(entry)

        for icon_ref in entry.get("icon_refs", []):
            if not isinstance(icon_ref, str):
                continue

            resolved_sources = _resolve_icon_sources(icon_ref, namespace, token_pool, texture_index)
            if not resolved_sources:
                unresolved_icon_refs.add(icon_ref)
            for source_icon in resolved_sources:
                mapped = _copy_icon(source_icon)
                if mapped:
                    copied_icons.add(mapped)
                    dynamic_icons.append(mapped)

        permission_icon_refs = entry.get("permission_icon_refs")
        if isinstance(permission_icon_refs, dict):
            for permission, refs in permission_icon_refs.items():
                if not isinstance(permission, str):
                    continue
                if not isinstance(refs, list):
                    continue

                permission_dynamic: List[str] = []
                permission_tokens = sorted(set(token_pool + _tokenize_text(permission)))
                for icon_ref in refs:
                    if not isinstance(icon_ref, str):
                        continue

                    resolved_sources = _resolve_icon_sources(icon_ref, namespace, permission_tokens, texture_index)
                    if not resolved_sources:
                        unresolved_permission_icon_refs[permission].add(icon_ref)
                    for source_icon in resolved_sources:
                        mapped = _copy_icon(source_icon)
                        if mapped:
                            copied_icons.add(mapped)
                            dynamic_icons.append(mapped)
                            permission_dynamic.append(mapped)

                if permission_dynamic:
                    permission_icon_textures[permission] = sorted(set(permission_dynamic))

        entry["icon_textures"] = sorted(set(dynamic_icons))
        entry["permission_icon_textures"] = {
            key: value for key, value in sorted(permission_icon_textures.items()) if value
        }

    for icon in _collect_hint_icons():
        copied_icons.add(icon)

    animated_icon_count = sum(1 for _ in TARGET_ICON_DIR.glob("**/*.mcmeta") if _.is_file())
    unresolved_permission_icon_ref_count = sum(
        len(value)
        for value in unresolved_permission_icon_refs.values()
        if isinstance(value, set)
    )
    parse_failure_set = set(parse_failures)
    unresolved_ref_count = len(unresolved_icon_refs) + unresolved_permission_icon_ref_count + len(parse_failure_set)

    payload = {
        "parsed_files": parsed_files,
        "parse_failure_count": len(parse_failure_set),
        "parse_failures": sorted(parse_failure_set),
        "rank_entry_count": len(entries),
        "permission_count": len(
            {
                permission
                for entry in entries
                for permission in entry.get("permissions", [])
                if isinstance(permission, str) and permission
            }
        ),
        "icon_count": len(copied_icons),
        "animated_icon_count": animated_icon_count,
        "unresolved_icon_ref_count": len(unresolved_icon_refs),
        "unresolved_permission_icon_ref_count": unresolved_permission_icon_ref_count,
        "unresolved_ref_count": unresolved_ref_count,
        "missing_ref_count": unresolved_ref_count,
        "permission_icon_mapping_count": sum(
            len(entry.get("permission_icon_textures", {}))
            for entry in entries
            if isinstance(entry.get("permission_icon_textures"), dict)
        ),
        "entries": entries,
        "icons": sorted(copied_icons),
        "unresolved_icon_refs": sorted(unresolved_icon_refs),
        "unresolved_permission_icon_refs": {
            key: sorted(value)
            for key, value in sorted(unresolved_permission_icon_refs.items())
            if value
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    _log(f"Mapped {len(entries)} rank entries and {len(copied_icons)} rank icons")


if __name__ == "__main__":
    run()
