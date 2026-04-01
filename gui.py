from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

PACK_DIR = Path("pack")
TARGET_GUI_DIR = Path("staging/target/rp/textures/gui")
OUTPUT_FILE = Path("staging/gui_map.json")

CONFIG_PATTERNS = (
    "**/*gui*.yml",
    "**/*gui*.yaml",
    "**/*gui*.json",
    "**/*menu*.yml",
    "**/*menu*.yaml",
    "**/*menu*.json",
    "**/*inventory*.yml",
    "**/*inventory*.yaml",
    "**/*inventory*.json",
)
GUI_HINTS = (
    "gui",
    "menu",
    "inventory",
    "hud",
    "slot",
    "hotbar",
    "chest",
    "anvil",
    "shop",
    "page",
    "screen",
)
MODEL_HINTS = ("gui", "menu", "inventory", "slot", "screen", "panel", "hud")
MAX_SLOT_INDEX = 255
SLOT_KEYS = ("slot", "slots", "index", "indices", "position", "positions", "slot_range", "range")
PAGE_KEYS = ("page", "current_page", "menu_page", "page_index", "pageid", "screen_page")
PAGE_COUNT_KEYS = ("pages", "page_count", "max_page", "max_pages", "total_pages", "page_total", "size")
TEXTURE_KEYS = (
    "texture",
    "textures",
    "background",
    "bg",
    "sprite",
    "image",
    "icon",
    "file",
    "path",
)
CMD_PATTERN = re.compile(r"(?:custom[_ ]?model[_ ]?data|custommodeldata|cmd|model[_ ]?data)\s*[:=]\s*(-?\d+)", re.IGNORECASE)
INT_TOKEN_PATTERN = re.compile(r"-?(?:0x[0-9a-fA-F]+|\d+)")
PAGE_FROM_TEXT_PATTERN = re.compile(r"(?:page|p)\s*[_:\- ]\s*(\d+)", re.IGNORECASE)
SLOT_FROM_KEY_PATTERN = re.compile(r"(?:slot|index|position|pos)\s*[_:\- ]\s*(\d+)", re.IGNORECASE)
PAGE_FROM_KEY_PATTERN = re.compile(r"(?:page|p)\s*[_:\- ]\s*(\d+)", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(r"%[^%]+%|\$\{[^{}]+\}|\{[^{}]+\}|<[^<>]+>")
SLOT_MAPPING_HINT_KEYS = (
    "item",
    "items",
    "button",
    "buttons",
    "icon",
    "icons",
    "model",
    "models",
    "custom_model_data",
    "custommodeldata",
    "cmd",
)


def _log(message: str) -> None:
    print(f"[GUI] {message}", flush=True)


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


def _iter_config_files() -> Iterable[Path]:
    seen: Set[Path] = set()
    for pattern in CONFIG_PATTERNS:
        for file_path in PACK_DIR.glob(pattern):
            if not file_path.is_file():
                continue
            if file_path in seen:
                continue
            seen.add(file_path)
            yield file_path


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw, 0)
        except Exception:
            try:
                return int(float(raw))
            except Exception:
                return None
    return None


def _parse_int_token(token: str) -> Optional[int]:
    raw = token.strip()
    if not raw:
        return None
    try:
        return int(raw, 0)
    except Exception:
        try:
            return int(float(raw))
        except Exception:
            return None


def _normalize_key_token(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _is_cmd_key(key: Any) -> bool:
    key_text = _normalize_key_token(key)
    if not key_text:
        return False

    if key_text in {
        "custom_model_data",
        "custommodeldata",
        "cmd",
        "model_data",
        "modelid",
        "minecraft:custom_model_data",
    }:
        return True

    if key_text.endswith(":custom_model_data"):
        return True
    if key_text.startswith("cmd_") or key_text.endswith("_cmd"):
        return True
    if "custom" in key_text and "model" in key_text and "data" in key_text:
        return True
    return False


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


def _tokenize_text(value: str) -> List[str]:
    lowered = value.strip().lower()
    if not lowered:
        return []
    return [token for token in re.split(r"[^a-z0-9]+", lowered) if token]


def _extract_int_values(value: Any) -> List[int]:
    if isinstance(value, str):
        direct = _coerce_int(value)
        out: List[int] = [direct] if direct is not None else []

        for match in CMD_PATTERN.finditer(value):
            parsed = _coerce_int(match.group(1))
            if parsed is not None:
                out.append(parsed)

        for token in INT_TOKEN_PATTERN.findall(value):
            parsed = _parse_int_token(token)
            if parsed is not None:
                out.append(parsed)
        return sorted(set(out))

    if isinstance(value, list):
        out: List[int] = []
        for item in value:
            out.extend(_extract_int_values(item))
        return out

    if isinstance(value, dict):
        out: List[int] = []
        for item in value.values():
            out.extend(_extract_int_values(item))
        return out

    coerced = _coerce_int(value)
    return [coerced] if coerced is not None else []


def _extract_cmd_values(node: Any) -> List[int]:
    out: Set[int] = set()

    if not isinstance(node, (dict, list)):
        return [value for value in _extract_int_values(node)]

    for candidate in _iter_nodes(node):
        for key, value in candidate.items():
            if not _is_cmd_key(key):
                continue
            for parsed in _extract_int_values(value):
                out.add(parsed)
    return sorted(out)


def _is_likely_model_ref(value: str) -> bool:
    lowered = value.lower()
    return "/" in value or ":" in value or any(hint in lowered for hint in MODEL_HINTS)


def _looks_like_texture_ref(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith(("http://", "https://", "#")):
        return False
    return "/" in raw or ":" in raw or "texture" in raw.lower() or "gui" in raw.lower()


def _slot_from_row_col(row: int, col: int, width: int = 9) -> Optional[int]:
    if row < 0 or col < 0 or width <= 0:
        return None

    candidates = []
    if row > 0 and col > 0:
        candidates.append((row - 1, col - 1))
    candidates.append((row, col))

    for row_idx, col_idx in candidates:
        slot = (row_idx * width) + col_idx
        if 0 <= slot <= MAX_SLOT_INDEX:
            return slot
    return None


def _normalize_slot_index(slot: int) -> Optional[int]:
    if 0 <= slot <= MAX_SLOT_INDEX:
        return slot
    return None


def _parse_slot_value(value: Any) -> List[int]:
    if isinstance(value, int):
        normalized = _normalize_slot_index(value)
        return [normalized] if normalized is not None else []

    if isinstance(value, str):
        raw = value.strip()
        slot_key = SLOT_FROM_KEY_PATTERN.search(raw)
        if slot_key:
            slot = _coerce_int(slot_key.group(1))
            if slot is not None:
                normalized = _normalize_slot_index(slot)
                return [normalized] if normalized is not None else []

        if raw.isdigit():
            normalized = _normalize_slot_index(int(raw))
            return [normalized] if normalized is not None else []

        if any(sep in raw for sep in (",", ";", "|")):
            chunks = raw.replace(";", ",").replace("|", ",").split(",")
            out: List[int] = []
            for chunk in chunks:
                out.extend(_parse_slot_value(chunk))
            return sorted(set(out))

        normalized_range = raw.replace("..", "-").replace(":", "-").replace(" to ", "-")
        if "-" in normalized_range:
            start_text, end_text = normalized_range.split("-", 1)
            start = _coerce_int(start_text)
            end = _coerce_int(end_text)
            if start is not None and end is not None:
                low, high = sorted((start, end))
                if 0 <= low <= MAX_SLOT_INDEX and 0 <= high <= MAX_SLOT_INDEX:
                    return [slot for slot in range(low, high + 1) if 0 <= slot <= MAX_SLOT_INDEX]

        compact = re.fullmatch(r"r(\d+)c(\d+)", raw.lower())
        if compact:
            row = _coerce_int(compact.group(1))
            col = _coerce_int(compact.group(2))
            if row is not None and col is not None:
                slot = _slot_from_row_col(row, col)
                return [slot] if slot is not None else []

        compact_flexible = re.search(r"r\s*(\d+)\D+c\s*(\d+)", raw.lower())
        if compact_flexible:
            row = _coerce_int(compact_flexible.group(1))
            col = _coerce_int(compact_flexible.group(2))
            if row is not None and col is not None:
                slot = _slot_from_row_col(row, col)
                return [slot] if slot is not None else []

        row_col = re.search(r"row\s*(\d+)\D+col(?:umn)?\s*(\d+)", raw.lower())
        if row_col:
            row = _coerce_int(row_col.group(1))
            col = _coerce_int(row_col.group(2))
            if row is not None and col is not None:
                slot = _slot_from_row_col(row, col)
                return [slot] if slot is not None else []

        xy = re.search(r"x\s*(\d+)\D+y\s*(\d+)", raw.lower())
        if xy:
            col = _coerce_int(xy.group(1))
            row = _coerce_int(xy.group(2))
            if row is not None and col is not None:
                slot = _slot_from_row_col(row, col)
                return [slot] if slot is not None else []

        parsed_slots: List[int] = []
        for token in INT_TOKEN_PATTERN.findall(raw):
            parsed = _parse_int_token(token)
            if parsed is None:
                continue
            normalized = _normalize_slot_index(parsed)
            if normalized is not None:
                parsed_slots.append(normalized)
        if len(parsed_slots) == 1:
            return [parsed_slots[0]]
        return []

    if isinstance(value, list):
        out: List[int] = []
        for item in value:
            out.extend(_parse_slot_value(item))
        return sorted(set(out))

    if isinstance(value, dict):
        out: List[int] = []
        lower = {str(k).strip().lower(): k for k in value.keys()}

        for key_str in lower.keys():
            if key_str.isdigit():
                normalized = _normalize_slot_index(int(key_str))
                if normalized is not None:
                    out.append(normalized)
            slot_match = SLOT_FROM_KEY_PATTERN.search(key_str)
            if slot_match:
                parsed = _coerce_int(slot_match.group(1))
                if parsed is not None:
                    normalized = _normalize_slot_index(parsed)
                    if normalized is not None:
                        out.append(normalized)

        for key in SLOT_KEYS:
            original = lower.get(key)
            if original is not None:
                out.extend(_parse_slot_value(value.get(original)))

        row = _coerce_int(value.get(lower["row"])) if "row" in lower else None
        if "column" in lower:
            col = _coerce_int(value.get(lower["column"]))
        elif "col" in lower:
            col = _coerce_int(value.get(lower["col"]))
        else:
            col = None
        if row is not None and col is not None:
            slot = _slot_from_row_col(row, col)
            if slot is not None:
                out.append(slot)

        rows = _parse_slot_value(value.get(lower["rows"])) if "rows" in lower else []
        cols = _parse_slot_value(value.get(lower["columns"])) if "columns" in lower else []
        if rows and cols:
            for row_index in rows:
                for col_index in cols:
                    slot = _slot_from_row_col(row_index, col_index)
                    if slot is not None:
                        out.append(slot)

        if "x" in lower and ("y" in lower or "row" in lower):
            x_val = _coerce_int(value.get(lower["x"]))
            y_key = lower["y"] if "y" in lower else lower["row"]
            y_val = _coerce_int(value.get(y_key))
            if x_val is not None and y_val is not None:
                slot = _slot_from_row_col(y_val, x_val)
                if slot is not None:
                    out.append(slot)
        return sorted(set(out))

    return []


def _extract_slots(node: Any, slots: Set[int]) -> None:
    if isinstance(node, list):
        for item in node:
            _extract_slots(item, slots)
        return

    if not isinstance(node, dict):
        return

    for key, value in node.items():
        key_str = str(key).strip().lower()
        if key_str.isdigit():
            slot = int(key_str)
            if 0 <= slot <= MAX_SLOT_INDEX:
                slots.add(slot)

        key_match = SLOT_FROM_KEY_PATTERN.search(key_str)
        if key_match:
            slot = _coerce_int(key_match.group(1))
            if slot is not None and 0 <= slot <= MAX_SLOT_INDEX:
                slots.add(slot)

        if key_str in SLOT_KEYS:
            for slot in _parse_slot_value(value):
                slots.add(slot)

        _extract_slots(value, slots)


def _extract_model_refs(node: Any) -> List[str]:
    refs: Set[str] = set()
    for candidate in _iter_nodes(node):
        for key, value in candidate.items():
            key_lower = str(key).strip().lower()
            if "model" not in key_lower and key_lower not in {"material", "materials", "item", "items"}:
                continue
            for ref in _extract_string_values(value):
                if _is_likely_model_ref(ref):
                    refs.add(ref)
    return sorted(refs)


def _extract_texture_refs(node: Any) -> List[str]:
    refs: Set[str] = set()
    for candidate in _iter_nodes(node):
        for key, value in candidate.items():
            key_lower = str(key).strip().lower()
            if key_lower not in TEXTURE_KEYS and "texture" not in key_lower and "background" not in key_lower:
                continue
            for ref in _extract_string_values(value):
                if _looks_like_texture_ref(ref):
                    refs.add(ref)
    return sorted(refs)


def _extract_page_number_from_text(value: str) -> Optional[int]:
    if not value:
        return None
    parsed = _coerce_int(value)
    if parsed is not None and parsed >= 0:
        return parsed

    match = PAGE_FROM_TEXT_PATTERN.search(value)
    if not match:
        match = PAGE_FROM_KEY_PATTERN.search(value)
    if match:
        return _coerce_int(match.group(1))
    return None


def _extract_page_container_values(value: Any) -> List[int]:
    pages: Set[int] = set()

    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            key_lower = key_text.strip().lower()

            parsed_key = _extract_page_number_from_text(key_text)
            if parsed_key is not None and parsed_key >= 0:
                pages.add(parsed_key)

            if key_lower in PAGE_KEYS or "page" in key_lower or key_lower in {"id", "index", "pageid", "page_index"}:
                for nested_page in _extract_int_values(nested):
                    if nested_page >= 0:
                        pages.add(nested_page)

            if isinstance(nested, (dict, list)):
                for nested_page in _extract_page_container_values(nested):
                    if nested_page >= 0:
                        pages.add(nested_page)
        return sorted(pages)

    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                for nested_page in _extract_page_container_values(item):
                    if nested_page >= 0:
                        pages.add(nested_page)
            else:
                parsed = _extract_page_number_from_text(str(item))
                if parsed is not None and parsed >= 0:
                    pages.add(parsed)
        return sorted(pages)

    if isinstance(value, str):
        parsed = _extract_page_number_from_text(value)
        if parsed is not None and parsed >= 0:
            pages.add(parsed)

    return sorted(pages)


def _extract_page_values(node: Dict[str, Any], source: str, path: str, lower: Dict[str, Any]) -> List[int]:
    pages: Set[int] = set()

    for key in PAGE_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        parsed = _extract_int_values(node.get(original))
        for page in parsed:
            if page >= 0:
                pages.add(page)

    for key_lower, original in lower.items():
        if key_lower in PAGE_COUNT_KEYS:
            continue
        if "page" not in key_lower and key_lower not in {"p", "pg"}:
            continue

        parsed_key_page = _extract_page_number_from_text(key_lower)
        if parsed_key_page is not None and parsed_key_page >= 0:
            pages.add(parsed_key_page)

        page_value = node.get(original)
        for parsed_page in _extract_int_values(page_value):
            if parsed_page >= 0:
                pages.add(parsed_page)

        if isinstance(page_value, dict):
            for nested_key in page_value.keys():
                parsed_nested = _extract_page_number_from_text(str(nested_key))
                if parsed_nested is not None and parsed_nested >= 0:
                    pages.add(parsed_nested)
            for nested_page in _extract_page_container_values(page_value):
                if nested_page >= 0:
                    pages.add(nested_page)

    for key in PAGE_COUNT_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        for nested_page in _extract_page_container_values(node.get(original)):
            if nested_page >= 0:
                pages.add(nested_page)

    for candidate in (source, path):
        parsed = _extract_page_number_from_text(candidate)
        if parsed is not None and parsed >= 0:
            pages.add(parsed)

    return sorted(pages)


def _source_namespace(source: str) -> str:
    if source.startswith("assets/"):
        parts = source.split("/")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    return "minecraft"


def _extract_animation(node: Dict[str, Any], lower: Dict[str, Any]) -> Dict[str, Any]:
    animation: Dict[str, Any] = {}

    for root_key in ("animation", "animations"):
        original = lower.get(root_key)
        if original is None:
            continue
        animation_root = node.get(original)
        if isinstance(animation_root, dict):
            for key in (
                "frametime",
                "frames",
                "frame_time",
                "interpolate",
                "loop",
                "speed",
                "ticks_per_frame",
                "duration",
                "fps",
                "frame_rate",
                "ping_pong",
                "reverse",
                "sequence",
            ):
                if key in animation_root:
                    animation[key] = animation_root.get(key)

            nested_states: Dict[str, Dict[str, Any]] = {}
            for state_key, state_value in animation_root.items():
                if not isinstance(state_value, dict):
                    continue
                if not any(
                    token in state_value
                    for token in (
                        "frames",
                        "frame",
                        "frametime",
                        "frame_time",
                        "duration",
                        "loop",
                        "speed",
                    )
                ):
                    continue
                nested_states[state_key] = {
                    field: state_value.get(field)
                    for field in (
                        "frametime",
                        "frame_time",
                        "frames",
                        "frame",
                        "duration",
                        "loop",
                        "speed",
                        "interpolate",
                    )
                    if field in state_value
                }
            if nested_states:
                animation["states"] = nested_states
        elif isinstance(animation_root, list):
            animation[root_key] = animation_root

    for key in (
        "frames",
        "frame",
        "frame_time",
        "fps",
        "frame_rate",
        "interval",
        "ticks",
        "animated",
        "loop",
        "duration",
        "interpolate",
        "ping_pong",
        "reverse",
        "sequence",
    ):
        original = lower.get(key)
        if original is not None:
            animation[key] = node.get(original)
    return animation


def _extract_slot_mappings(node: Any, source: str, path: str = "root") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if isinstance(node, list):
        for index, item in enumerate(node):
            out.extend(_extract_slot_mappings(item, source, f"{path}[{index}]"))
        return out

    if not isinstance(node, dict):
        return out

    lower = {str(k).lower(): k for k in node.keys()}
    slots: Set[int] = set()

    for key, value in node.items():
        key_lower = str(key).strip().lower()
        if key_lower.isdigit():
            normalized = _normalize_slot_index(int(key_lower))
            if normalized is not None:
                slots.add(normalized)

        if key_lower in SLOT_KEYS:
            for slot in _parse_slot_value(value):
                slots.add(slot)

    if "x" in lower and ("y" in lower or "row" in lower):
        x_val = _coerce_int(node.get(lower["x"]))
        y_key = lower["y"] if "y" in lower else lower["row"]
        y_val = _coerce_int(node.get(y_key))
        if x_val is not None and y_val is not None:
            slot = _slot_from_row_col(y_val, x_val)
            if slot is not None:
                slots.add(slot)

    if slots:
        model_refs = _extract_model_refs(node)
        texture_refs = _extract_texture_refs(node)
        cmd_values = _extract_cmd_values(node)

        page_values = _extract_page_values(node, source, path, lower)

        has_hint_key = any(hint in lower for hint in SLOT_MAPPING_HINT_KEYS)
        if has_hint_key or model_refs or texture_refs or cmd_values or page_values:
            for slot in sorted(slots):
                out.append(
                    {
                        "slot": slot,
                        "path": path,
                        "page_values": sorted(set(page_values)),
                        "custom_model_data": sorted(set(cmd_values)),
                        "model_refs": sorted(set(model_refs)),
                        "texture_refs": sorted(set(texture_refs)),
                    }
                )

    for key, value in node.items():
        out.extend(_extract_slot_mappings(value, source, f"{path}.{key}"))

    dedup: Dict[str, Dict[str, Any]] = {}
    for entry in out:
        dedup_key = "|".join(
            [
                str(entry.get("path", "")),
                str(entry.get("slot", "")),
                ",".join(str(v) for v in entry.get("page_values", [])),
                ",".join(str(v) for v in entry.get("custom_model_data", [])),
                ",".join(str(v) for v in entry.get("model_refs", [])),
                ",".join(str(v) for v in entry.get("texture_refs", [])),
            ]
        )
        dedup[dedup_key] = entry
    return [dedup[key] for key in sorted(dedup.keys())]


def _extract_gui_entries(node: Any, source: str, out: List[Dict[str, Any]], path: str = "root") -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _extract_gui_entries(item, source, out, f"{path}[{index}]")
        return

    if not isinstance(node, dict):
        return

    lower = {str(k).lower(): k for k in node.keys()}
    slots: Set[int] = set()
    _extract_slots(node, slots)

    title = None
    for key in ("title", "name", "display", "display_name", "gui"):
        original = lower.get(key)
        if original is None:
            continue
        value = node.get(original)
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break

    page_values = _extract_page_values(node, source, path, lower)
    page = page_values[0] if page_values else None

    page_count = None
    for key in PAGE_COUNT_KEYS:
        original = lower.get(key)
        if original is None:
            continue
        value = node.get(original)
        if isinstance(value, (list, dict)):
            page_count = len(value)
            break
        parsed = _coerce_int(value)
        if parsed is not None and parsed > 0:
            page_count = parsed
            break

    if page_count is None and page_values:
        page_count = max(page_values) + 1

    model_refs: List[str] = _extract_model_refs(node)
    texture_refs: List[str] = _extract_texture_refs(node)

    cmd_values = _extract_cmd_values(node)

    animation = _extract_animation(node, lower)
    slot_mappings = _extract_slot_mappings(node, source, path)

    has_gui_hint = any(hint in path.lower() for hint in GUI_HINTS)
    has_gui_hint = has_gui_hint or any(hint in source.lower() for hint in GUI_HINTS)
    has_gui_hint = has_gui_hint or any(hint in str(value).lower() for value in node.values() if isinstance(value, str))

    if has_gui_hint or title or slots or model_refs or cmd_values or page_count or texture_refs or slot_mappings:
        source_ns = _source_namespace(source)
        out.append(
            {
                "source": source,
                "namespace": source_ns,
                "path": path,
                "title": title,
                "page": page,
                "page_values": page_values,
                "page_count": page_count,
                "slots": sorted(slots),
                "custom_model_data": sorted(set(cmd_values)),
                "model_refs": sorted(set(model_refs)),
                "texture_refs": sorted(set(texture_refs)),
                "animation": animation,
                "slot_mappings": slot_mappings,
            }
        )

    for key, value in node.items():
        _extract_gui_entries(value, source, out, f"{path}.{key}")


def _copy_gui_texture(texture: Path) -> Optional[Dict[str, Any]]:
    parts = texture.as_posix().split("/")
    try:
        assets_index = parts.index("assets")
        namespace = parts[assets_index + 1]
        rel = "/".join(parts[assets_index + 3 :])
    except Exception:
        return None

    destination = TARGET_GUI_DIR / namespace / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(texture, destination)

    output_path = destination.with_suffix("").as_posix().replace("staging/target/rp/", "")
    metadata: Dict[str, Any] = {
        "source": texture.relative_to(PACK_DIR).as_posix(),
        "output": output_path,
        "animated": False,
    }

    mcmeta_file = texture.with_suffix(texture.suffix + ".mcmeta")
    if mcmeta_file.exists():
        target_meta = destination.with_suffix(destination.suffix + ".mcmeta")
        shutil.copyfile(mcmeta_file, target_meta)
        try:
            meta_data = json.loads(mcmeta_file.read_text(encoding="utf-8"))
            animation = meta_data.get("animation")
            if isinstance(animation, dict):
                metadata["animated"] = True
                metadata["animation"] = {
                    "frametime": animation.get("frametime"),
                    "frames": animation.get("frames"),
                    "interpolate": animation.get("interpolate"),
                }
        except Exception:
            pass

    return metadata


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


def _resolve_texture_ref(texture_ref: str, default_ns: str = "minecraft", token_pool: Optional[List[str]] = None) -> Optional[Path]:
    token_pool = token_pool or []

    for candidate_ref in _expand_texture_ref(texture_ref, token_pool):
        ref = candidate_ref.strip().replace("\\", "/")
        if not ref:
            continue
        if ref.startswith(("http://", "https://", "#")):
            continue

        if ":" in ref:
            namespace, rel = ref.split(":", 1)
        else:
            namespace, rel = default_ns, ref

        if rel.startswith("textures/"):
            rel = rel[len("textures/") :]

        rel = rel.lstrip("/")

        namespace_order: List[str] = [namespace]
        if default_ns not in namespace_order:
            namespace_order.append(default_ns)
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

        for fallback in _fallback_texture_candidates(namespace, candidate_ref):
            if fallback.exists() and fallback.is_file():
                return fallback
    return None


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


def _fallback_texture_candidates(namespace: str, reference: str) -> List[Path]:
    tokens = _texture_tokens(reference)
    if not tokens:
        return []

    roots: List[Path] = [PACK_DIR / "assets" / namespace / "textures"]
    if namespace != "minecraft":
        roots.append(PACK_DIR / "assets" / "minecraft" / "textures")
    for candidate_ns in _all_texture_namespaces():
        root = PACK_DIR / "assets" / candidate_ns / "textures"
        if root not in roots:
            roots.append(root)

    out: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.glob("**/*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in (".png", ".tga"):
                continue

            rel_lower = candidate.relative_to(root).as_posix().lower()
            if not any(token in rel_lower for token in tokens):
                continue
            out.append(candidate)
            if len(out) >= 8:
                return out
    return out


def _collect_gui_textures(explicit_refs: Optional[Set[str]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    textures: List[Dict[str, Any]] = []
    explicit_ref_set = explicit_refs or set()
    unresolved_refs: Set[str] = set()

    for ref in sorted(explicit_ref_set):
        namespace = "minecraft"
        if ":" in ref:
            namespace = ref.split(":", 1)[0] or "minecraft"

        token_pool: Set[str] = set(_texture_tokens(ref))
        token_pool.update(_tokenize_text(ref))
        token_pool.update({"gui", "menu", "inventory", "hud", "slot", "screen"})

        source = _resolve_texture_ref(ref, namespace, sorted(token_pool))
        if not source:
            unresolved_refs.add(ref)
            continue
        copied = _copy_gui_texture(source)
        if copied:
            copied["reference"] = ref
            textures.append(copied)

    for texture in PACK_DIR.glob("assets/**/textures/**/*"):
        if not texture.is_file():
            continue
        if texture.suffix.lower() not in (".png", ".tga"):
            continue

        rel = texture.relative_to(PACK_DIR).as_posix().lower()
        if not any(hint in rel for hint in GUI_HINTS):
            continue

        copied = _copy_gui_texture(texture)
        if copied:
            textures.append(copied)

    dedup: Dict[str, Dict[str, Any]] = {}
    for entry in textures:
        dedup[entry["output"]] = entry
    return [dedup[key] for key in sorted(dedup.keys())], sorted(unresolved_refs)


def _extract_model_ref(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, dict):
        lower = {str(key).strip().lower(): key for key in value.keys()}
        for key in ("model", "path", "id", "name", "value"):
            original = lower.get(key)
            if original is None:
                continue
            nested = value.get(original)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
            if isinstance(nested, (dict, list)):
                resolved = _extract_model_ref(nested)
                if resolved:
                    return resolved

    if isinstance(value, list):
        for item in value:
            resolved = _extract_model_ref(item)
            if resolved:
                return resolved
    return None


def _is_cmd_property(value: Any) -> bool:
    token = str(value).strip().lower().replace("-", "_")
    if not token:
        return False
    token = token.split(":", 1)[-1]
    return token in {
        "custom_model_data",
        "custommodeldata",
        "cmd",
        "model_data",
        "model_id",
        "modelid",
    }


def _extract_cmd_from_predicate(predicate: Any) -> Optional[int]:
    if not isinstance(predicate, dict):
        return None
    values = _extract_cmd_values(predicate)
    if values:
        return values[0]
    return None


def _iter_dispatch_nodes(raw: Any, default_key: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
            elif isinstance(item, str):
                out.append({"model": item})
        return out

    if isinstance(raw, dict):
        for raw_selector, raw_item in raw.items():
            if isinstance(raw_item, dict):
                candidate = dict(raw_item)
            else:
                candidate = {"model": raw_item}
            candidate.setdefault(default_key, raw_selector)
            out.append(candidate)
        return out

    if isinstance(raw, str):
        out.append({"model": raw})
    return out


def _extract_dispatch_cmd_values(entry: Dict[str, Any], preferred_key: str) -> List[int]:
    cmd_values: List[int] = []

    preferred = entry.get(preferred_key)
    cmd_values.extend(_extract_int_values(preferred))

    for key in ("threshold", "when", "value", "min", "max", "index", "id"):
        if key == preferred_key:
            continue
        cmd_values.extend(_extract_int_values(entry.get(key)))

    cmd_values.extend(_extract_cmd_values(entry))
    return sorted(set(cmd_values))


def _collect_model_mappings() -> List[Dict[str, Any]]:
    mappings: List[Dict[str, Any]] = []
    seen: Set[Path] = set()

    for pattern in ("assets/**/models/item/*.json", "assets/**/models/item/**/*.json"):
        for model_file in PACK_DIR.glob(pattern):
            if not model_file.is_file() or model_file in seen:
                continue
            seen.add(model_file)

            data = _safe_load(model_file)
            if not data:
                continue

            overrides = data.get("overrides")
            if not isinstance(overrides, list):
                continue

            source = model_file.relative_to(PACK_DIR).as_posix()
            item = model_file.stem

            for override in overrides:
                if not isinstance(override, dict):
                    continue

                predicate = override.get("predicate")
                model_ref = override.get("model")
                if not isinstance(predicate, dict) or not isinstance(model_ref, str):
                    continue

                cmd_values = sorted(set(_extract_cmd_values(predicate) + _extract_cmd_values(override)))
                if not cmd_values:
                    continue

                for cmd in cmd_values:
                    mappings.append(
                        {
                            "source": source,
                            "item": item,
                            "custom_model_data": cmd,
                            "model": model_ref.strip(),
                        }
                    )

    for pattern in ("assets/**/items/*.json", "assets/**/items/**/*.json"):
        for item_file in PACK_DIR.glob(pattern):
            if not item_file.is_file() or item_file in seen:
                continue
            seen.add(item_file)

            data = _safe_load(item_file)
            if not isinstance(data, dict):
                continue

            source = item_file.relative_to(PACK_DIR).as_posix()
            item = item_file.stem

            for node in _iter_nodes(data):
                node_type = str(node.get("type", "")).strip().lower()
                if _is_cmd_property(node.get("property", "")):
                    if node_type in {"minecraft:range_dispatch", "range_dispatch", "minecraft:range", "range"}:
                        iter_entries: List[Dict[str, Any]] = []
                        for key in ("entries", "dispatch", "ranges", "cases"):
                            iter_entries.extend(_iter_dispatch_nodes(node.get(key), "threshold"))

                        for entry in iter_entries:
                            cmd_values = _extract_dispatch_cmd_values(entry, "threshold")
                            model_ref = _extract_model_ref(entry.get("model"))
                            if not model_ref:
                                model_ref = _extract_model_ref(entry.get("value"))
                            if not cmd_values or not model_ref:
                                continue
                            for cmd in cmd_values:
                                mappings.append(
                                    {
                                        "source": source,
                                        "item": item,
                                        "custom_model_data": cmd,
                                        "model": model_ref,
                                    }
                                )

                    if node_type in {"minecraft:select", "select", "minecraft:switch", "switch", "minecraft:condition", "condition"}:
                        iter_cases: List[Dict[str, Any]] = []
                        for key in ("cases", "entries", "options", "children", "dispatch"):
                            iter_cases.extend(_iter_dispatch_nodes(node.get(key), "when"))

                        for case in iter_cases:
                            model_ref = _extract_model_ref(case.get("model"))
                            if not model_ref:
                                model_ref = _extract_model_ref(case.get("value"))
                            if not model_ref:
                                continue

                            cmd_values = _extract_dispatch_cmd_values(case, "when")
                            for cmd in cmd_values:
                                mappings.append(
                                    {
                                        "source": source,
                                        "item": item,
                                        "custom_model_data": cmd,
                                        "model": model_ref,
                                    }
                                )

                model_ref = _extract_model_ref(node.get("model"))
                if not model_ref:
                    continue

                cmd_values = _extract_cmd_values(node)
                if not cmd_values:
                    continue

                for cmd in sorted(set(cmd_values)):
                    mappings.append(
                        {
                            "source": source,
                            "item": item,
                            "custom_model_data": cmd,
                            "model": model_ref,
                        }
                    )

    dedup: Dict[str, Dict[str, Any]] = {}
    for entry in mappings:
        key = f"{entry['source']}|{entry['item']}|{entry['custom_model_data']}|{entry['model']}"
        dedup[key] = entry
    return [dedup[key] for key in sorted(dedup.keys())]


def run() -> None:
    TARGET_GUI_DIR.mkdir(parents=True, exist_ok=True)

    gui_entries: List[Dict[str, Any]] = []
    parsed_configs = 0
    parse_failures: List[str] = []

    for config_file in _iter_config_files():
        data = _safe_load(config_file)
        if data is None:
            parse_failures.append(config_file.relative_to(PACK_DIR).as_posix())
            continue

        parsed_configs += 1
        source = config_file.relative_to(PACK_DIR).as_posix()
        _extract_gui_entries(data, source, gui_entries)

    texture_refs: Set[str] = set()
    for entry in gui_entries:
        refs = entry.get("texture_refs")
        if not isinstance(refs, list):
            continue

        namespace = entry.get("namespace")
        if not isinstance(namespace, str) or not namespace.strip():
            namespace = "minecraft"

        for ref in refs:
            if isinstance(ref, str) and ref.strip():
                normalized_ref = ref.strip()
                if ":" not in normalized_ref and namespace != "minecraft":
                    normalized_ref = f"{namespace}:{normalized_ref}"
                texture_refs.add(normalized_ref)

    textures, unresolved_texture_refs = _collect_gui_textures(explicit_refs=texture_refs)
    model_mappings = _collect_model_mappings()

    slot_union: Set[int] = set()
    pages: Set[int] = set()
    declared_page_count = 0
    for entry in gui_entries:
        for slot in entry.get("slots", []):
            if isinstance(slot, int):
                slot_union.add(slot)
        page = entry.get("page")
        if isinstance(page, int):
            pages.add(page)
        for page_value in entry.get("page_values", []):
            if isinstance(page_value, int):
                pages.add(page_value)
        page_count = entry.get("page_count")
        if isinstance(page_count, int) and page_count > 0:
            declared_page_count = max(declared_page_count, page_count)

    parse_failure_set = set(parse_failures)
    unresolved_total = len(unresolved_texture_refs) + len(parse_failure_set)

    payload = {
        "parsed_files": parsed_configs,
        "parse_failure_count": len(parse_failure_set),
        "parse_failures": sorted(parse_failure_set),
        "gui_entry_count": len(gui_entries),
        "model_mapping_count": len(model_mappings),
        "texture_count": len(textures),
        "unresolved_texture_ref_count": len(unresolved_texture_refs),
        "unresolved_ref_count": unresolved_total,
        "missing_ref_count": unresolved_total,
        "animated_texture_count": sum(1 for texture in textures if texture.get("animated") is True),
        "animated_entry_count": sum(
            1
            for entry in gui_entries
            if isinstance(entry.get("animation"), dict) and bool(entry.get("animation"))
        ),
        "slot_count": len(slot_union),
        "slot_mapping_count": sum(
            len(entry.get("slot_mappings", []))
            for entry in gui_entries
            if isinstance(entry.get("slot_mappings"), list)
        ),
        "page_count": max(len(pages), declared_page_count),
        "declared_page_count": declared_page_count,
        "multi_page_entry_count": sum(
            1
            for entry in gui_entries
            if isinstance(entry.get("page_values"), list) and len(entry.get("page_values", [])) > 1
        ),
        "slots": sorted(slot_union),
        "pages": sorted(pages),
        "entries": gui_entries,
        "model_mappings": model_mappings,
        "textures": textures,
        "unresolved_texture_refs": unresolved_texture_refs,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    _log(
        f"Mapped {len(gui_entries)} GUI entries, {len(model_mappings)} CMD model mappings, "
        f"{len(textures)} GUI textures"
    )


if __name__ == "__main__":
    run()
