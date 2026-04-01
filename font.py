from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from PIL import Image

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

PACK_DIR = Path("pack")
FONT_DEFINITION = PACK_DIR / "assets/minecraft/font/default.json"
OUTPUT_FONT_DIR = Path("staging/target/rp/font")
OUTPUT_MAPPING_FILE = Path("staging/target/font_map.json")
MAX_REFERENCE_DEPTH = 6


def _log(message: str) -> None:
    print(f"[FONT] {message}", flush=True)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _iter_root_font_definitions() -> Iterable[Path]:
    seen: Set[Path] = set()
    ordered: List[Path] = []

    if FONT_DEFINITION.exists() and FONT_DEFINITION.is_file():
        ordered.append(FONT_DEFINITION)

    for candidate in sorted(PACK_DIR.glob("assets/**/font/default.json")):
        if candidate.is_file():
            ordered.append(candidate)

    for pattern in (
        "assets/**/font/*.json",
        "assets/**/font/**/*.json",
        "assets/**/font/*.yml",
        "assets/**/font/**/*.yml",
        "assets/**/font/*.yaml",
        "assets/**/font/**/*.yaml",
    ):
        for candidate in sorted(PACK_DIR.glob(pattern)):
            if candidate.is_file():
                ordered.append(candidate)

    for font_file in ordered:
        try:
            resolved = font_file.resolve()
        except OSError:
            resolved = font_file
        if resolved in seen:
            continue
        seen.add(resolved)
        yield font_file


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


def _decode_char_token(token: str) -> Optional[str]:
    raw = token.strip()
    if not raw:
        return None

    if len(raw) == 1:
        return raw

    if raw.isdigit():
        try:
            return chr(int(raw))
        except Exception:
            return None

    hex_value = None
    lowered = raw.lower()
    if lowered.startswith("0x"):
        hex_value = raw[2:]
    elif lowered.startswith("u+"):
        hex_value = raw[2:]
    elif lowered.startswith("u{") and raw.endswith("}"):
        hex_value = raw[2:-1]
    elif lowered.startswith("\\u{") and raw.endswith("}"):
        hex_value = raw[3:-1]
    elif lowered.startswith("\\u"):
        hex_value = raw[2:]
    elif lowered.startswith("u") and len(raw) in (5, 7):
        hex_value = raw[1:]

    if hex_value:
        try:
            return chr(int(hex_value, 16))
        except Exception:
            pass

    if raw.startswith("&#x") and raw.endswith(";"):
        try:
            return chr(int(raw[3:-1], 16))
        except Exception:
            return None

    if raw.startswith("&#") and raw.endswith(";"):
        try:
            return chr(int(raw[2:-1], 10))
        except Exception:
            return None

    if lowered.startswith("\\x") and len(raw) >= 4:
        try:
            return chr(int(raw[2:], 16))
        except Exception:
            return None

    if lowered.startswith("\\u{") and raw.endswith("}"):
        try:
            return chr(int(raw[3:-1], 16))
        except Exception:
            return None

    if lowered.startswith("\\u") and len(raw) >= 6:
        try:
            return chr(int(raw[2:], 16))
        except Exception:
            return None

    if raw.startswith("\\U") and len(raw) >= 10:
        try:
            return chr(int(raw[2:10], 16))
        except Exception:
            return None

    if "\\" in raw:
        try:
            decoded = bytes(raw, "utf-8").decode("unicode_escape")
            if decoded:
                return decoded[0]
        except Exception:
            pass

    lowered = raw.lower()
    if lowered in ("space", "whitespace", "nbsp"):
        return " "
    if lowered in ("tab", "\\t"):
        return "\t"
    if lowered in ("newline", "linefeed", "\\n"):
        return "\n"

    return None


def _load_font_definition(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None

    if path.suffix.lower() == ".json":
        return _load_json(path)

    if yaml is None:
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_provider_type(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "bitmap"
    return value.split(":", 1)[-1].strip().lower()


def _namespace_from_source(source_name: Any, fallback: str = "minecraft") -> str:
    if not isinstance(source_name, str):
        return fallback
    parts = source_name.replace("\\", "/").split("/")
    try:
        assets_index = parts.index("assets")
        candidate = parts[assets_index + 1].strip()
        if candidate:
            return candidate
    except Exception:
        pass
    return fallback


def _resolve_font_reference(reference: str, default_namespace: str = "minecraft") -> Optional[Path]:
    raw = reference.strip().replace("\\", "/")
    if not raw:
        return None

    if ":" in raw:
        namespace, rel = raw.split(":", 1)
    else:
        namespace, rel = default_namespace, raw

    rel = rel.lstrip("/")
    if rel.startswith("font/"):
        rel = rel[len("font/") :]

    base = PACK_DIR / "assets" / namespace / "font" / rel
    if base.suffix:
        return base if base.exists() else None

    for suffix in (".json", ".yml", ".yaml"):
        candidate = base.with_suffix(suffix)
        if candidate.exists():
            return candidate

    basename = Path(rel).name
    if basename:
        if Path(basename).suffix.lower() in (".json", ".yml", ".yaml"):
            for candidate in PACK_DIR.glob(f"assets/**/font/**/{basename}"):
                if candidate.exists() and candidate.is_file():
                    return candidate
        else:
            stem = Path(basename).stem
            for suffix in (".json", ".yml", ".yaml"):
                for candidate in PACK_DIR.glob(f"assets/**/font/**/{stem}{suffix}"):
                    if candidate.exists() and candidate.is_file():
                        return candidate
    return None


def _resolve_font_binary(reference: str, default_namespace: str = "minecraft") -> Optional[Path]:
    raw = reference.strip().replace("\\", "/")
    if not raw:
        return None

    if ":" in raw:
        namespace, rel = raw.split(":", 1)
    else:
        namespace, rel = default_namespace, raw

    rel = rel.lstrip("/")
    if rel.startswith("font/"):
        rel = rel[len("font/") :]

    base = PACK_DIR / "assets" / namespace / "font" / rel
    candidates: List[Path] = [base]
    if base.suffix == "":
        candidates.extend([base.with_suffix(".bin"), base.with_suffix(".dat")])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    if base.suffix == "" and "/" not in rel:
        font_root = PACK_DIR / "assets" / namespace / "font"
        if font_root.exists():
            for extension in ("", ".bin", ".dat"):
                for candidate in font_root.glob(f"**/{rel}{extension}"):
                    if candidate.exists() and candidate.is_file():
                        return candidate

    basename = Path(rel).name
    if basename:
        if Path(basename).suffix:
            for candidate in PACK_DIR.glob(f"assets/**/font/**/{basename}"):
                if candidate.exists() and candidate.is_file():
                    return candidate
        else:
            stem = Path(basename).stem
            for extension in ("", ".bin", ".dat"):
                for candidate in PACK_DIR.glob(f"assets/**/font/**/{stem}{extension}"):
                    if candidate.exists() and candidate.is_file():
                        return candidate
    return None


def _legacy_template_candidates(template_ref: str, page_hex: str) -> List[str]:
    raw = template_ref.strip()
    if not raw:
        return []

    candidates: Set[str] = set()
    lower_hex = page_hex.lower()
    upper_hex = page_hex.upper()

    if "%s" in raw:
        candidates.add(raw.replace("%s", lower_hex))
        candidates.add(raw.replace("%s", upper_hex))
    if "%1$s" in raw:
        candidates.add(raw.replace("%1$s", lower_hex))
        candidates.add(raw.replace("%1$s", upper_hex))
    if "%02x" in raw:
        candidates.add(raw.replace("%02x", lower_hex))
    if "%1$02x" in raw:
        candidates.add(raw.replace("%1$02x", lower_hex))
    if "%02X" in raw:
        candidates.add(raw.replace("%02X", upper_hex))
    if "%1$02X" in raw:
        candidates.add(raw.replace("%1$02X", upper_hex))
    if "{page}" in raw:
        candidates.add(raw.replace("{page}", lower_hex))
        candidates.add(raw.replace("{page}", upper_hex))
    if "{}" in raw:
        candidates.add(raw.replace("{}", lower_hex))
        candidates.add(raw.replace("{}", upper_hex))

    if not candidates:
        candidates.add(raw)
    return sorted(candidates)


def _resolve_legacy_unicode_page(template_ref: str, page_hex: str, default_namespace: str = "minecraft") -> Optional[Path]:
    for candidate_ref in _legacy_template_candidates(template_ref, page_hex):
        texture = _resolve_texture(candidate_ref, default_namespace=default_namespace)
        if texture is not None:
            return texture
    return None


def _legacy_size_bounds(size_byte: int) -> Tuple[int, int, int]:
    left = (size_byte >> 4) & 0x0F
    right = size_byte & 0x0F
    if right < left:
        return left, right, 0
    return left, right, (right - left) + 1


def _collect_providers(
    font_data: Dict[str, Any],
    source_name: str,
    visited: Set[Path],
    parse_failures: Optional[Set[str]] = None,
    depth: int = 0,
) -> List[Dict[str, Any]]:
    providers = font_data.get("providers")
    if not isinstance(providers, list):
        return []

    out: List[Dict[str, Any]] = []
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            continue

        provider_type = _normalize_provider_type(provider.get("type"))
        entry = dict(provider)
        entry["_provider_type"] = provider_type
        entry["_provider_source"] = source_name
        entry["_provider_index"] = index
        out.append(entry)

        if provider_type != "reference" or depth >= MAX_REFERENCE_DEPTH:
            continue

        reference = provider.get("id") or provider.get("font") or provider.get("file")
        if not isinstance(reference, str):
            continue

        source_namespace = _namespace_from_source(source_name)
        nested_path = _resolve_font_reference(reference, default_namespace=source_namespace)
        if not nested_path:
            continue

        resolved = nested_path.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)

        nested_data = _load_font_definition(nested_path)
        if not nested_data:
            _log(f"Failed to parse referenced font: {nested_path}")
            if parse_failures is not None:
                try:
                    parse_failures.add(nested_path.relative_to(PACK_DIR).as_posix())
                except Exception:
                    parse_failures.add(nested_path.as_posix())
            continue

        try:
            nested_source = nested_path.relative_to(PACK_DIR).as_posix()
        except Exception:
            nested_source = nested_path.as_posix()
        out.extend(_collect_providers(nested_data, nested_source, visited, parse_failures, depth + 1))

    return out


def _resolve_texture(texture_ref: str, default_namespace: str = "minecraft") -> Optional[Path]:
    raw = texture_ref.strip().replace("\\", "/")
    if not raw:
        return None

    if ":" in raw:
        namespace, rel = raw.split(":", 1)
    else:
        namespace, rel = default_namespace, raw

    rel = rel.lstrip("/")
    if rel.startswith("textures/"):
        rel = rel[len("textures/") :]

    namespace_order: List[str] = [namespace]
    if "minecraft" not in namespace_order:
        namespace_order.append("minecraft")
    for candidate_ns in _all_texture_namespaces():
        if candidate_ns not in namespace_order:
            namespace_order.append(candidate_ns)

    for candidate_ns in namespace_order:
        candidate = PACK_DIR / "assets" / candidate_ns / "textures" / rel
        if candidate.exists() and candidate.is_file():
            return candidate

        if candidate.suffix == "":
            png_candidate = candidate.with_suffix(".png")
            if png_candidate.exists():
                return png_candidate
            tga_candidate = candidate.with_suffix(".tga")
            if tga_candidate.exists():
                return tga_candidate

            if "/" not in rel:
                texture_root = PACK_DIR / "assets" / candidate_ns / "textures"
                if texture_root.exists():
                    for extension in (".png", ".tga"):
                        for fallback in texture_root.glob(f"**/{rel}{extension}"):
                            if fallback.exists() and fallback.is_file():
                                return fallback

    basename = Path(rel).name
    if basename:
        stem = Path(basename).stem
        for extension in (".png", ".tga"):
            for fallback in PACK_DIR.glob(f"assets/**/textures/**/{stem}{extension}"):
                if fallback.exists() and fallback.is_file():
                    return fallback
    return None


def _cell_dimensions(image: Image.Image, rows: List[str]) -> Optional[Tuple[int, int]]:
    valid_rows = [row for row in rows if isinstance(row, str)]
    if not valid_rows:
        return None

    columns = max(len(row) for row in valid_rows)
    if columns <= 0:
        return None

    cell_w = image.width // columns
    cell_h = image.height // len(valid_rows)
    if cell_w <= 0 or cell_h <= 0:
        return None
    return cell_w, cell_h


def _measure_width(tile: Image.Image) -> int:
    bbox = tile.getbbox()
    if not bbox:
        return 0
    return max(0, bbox[2] - bbox[0])


def _measure_bounds(tile: Image.Image) -> Tuple[int, int, int]:
    bbox = tile.getbbox()
    if not bbox:
        return 0, tile.width, 0

    left, _, right, _ = bbox
    width = max(0, right - left)
    right_bearing = max(0, tile.width - right)
    return max(0, left), right_bearing, width


def _bucket_and_slot(codepoint: int) -> Optional[Tuple[str, int]]:
    if codepoint > 0xFFFF:
        return None
    return f"{(codepoint >> 8) & 0xFF:02X}", codepoint & 0xFF


def _glyph_key(codepoint: int) -> str:
    if codepoint <= 0xFFFF:
        return f"U+{codepoint:04X}"
    return f"U+{codepoint:06X}"


def _codepoint_aliases(codepoint: int) -> Tuple[str, ...]:
    width = 4 if codepoint <= 0xFFFF else 6
    return (
        f"U+{codepoint:04X}",
        f"U+{codepoint:06X}",
        f"u+{codepoint:04x}",
        f"u+{codepoint:06x}",
        f"0x{codepoint:04X}",
        f"0x{codepoint:06X}",
        f"0x{codepoint:04x}",
        f"0x{codepoint:06x}",
        f"\\u{codepoint:04X}",
        f"\\u{codepoint:06X}",
        f"\\u{codepoint:04x}",
        f"\\u{codepoint:06x}",
        f"\\U{codepoint:08X}",
        f"\\U{codepoint:08x}",
        f"\\u{{{codepoint:X}}}",
        f"\\u{{{codepoint:x}}}",
        f"u{codepoint:04X}",
        f"u{codepoint:06X}",
        f"u{codepoint:04x}",
        f"u{codepoint:06x}",
        f"&#x{codepoint:X};",
        f"&#x{codepoint:x};",
        f"&#{codepoint};",
        str(codepoint),
        f"{codepoint:0{width}X}",
        f"{codepoint:0{width}x}",
    )


def _codepoint_from_token(value: Any) -> Optional[int]:
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            if len(raw) == 1:
                decoded_single = _decode_char_token(raw)
                if isinstance(decoded_single, str) and decoded_single:
                    return ord(decoded_single[0])

            lowered = raw.lower()
            if lowered.startswith(("u+", "\\u", "\\x", "u{", "0x", "&#x", "&#")) or raw.startswith("\\U"):
                decoded_prefixed = _decode_char_token(raw)
                if isinstance(decoded_prefixed, str) and decoded_prefixed:
                    return ord(decoded_prefixed[0])

    parsed = _coerce_int(value)
    if parsed is not None and 0 <= parsed <= 0x10FFFF:
        return parsed

    if isinstance(value, str):
        decoded = _decode_char_token(value)
        if isinstance(decoded, str) and decoded:
            return ord(decoded[0])
    return None


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


def _coerce_advance_value(value: Any) -> Optional[int]:
    coerced = _coerce_int(value)
    if coerced is not None:
        return coerced

    if isinstance(value, dict):
        for key in ("advance", "width", "value", "size", "offset"):
            nested = value.get(key)
            nested_coerced = _coerce_int(nested)
            if nested_coerced is not None:
                return nested_coerced

    if isinstance(value, list):
        for item in value:
            nested = _coerce_advance_value(item)
            if nested is not None:
                return nested
    return None


def _normalize_bitmap_rows(chars: Any) -> List[str]:
    if isinstance(chars, str):
        raw = chars.replace("\r\n", "\n").replace("\r", "\n")
        if "\\" in raw:
            try:
                decoded = bytes(raw, "utf-8").decode("unicode_escape")
                if decoded:
                    raw = decoded
            except Exception:
                pass

        if "\n" in raw:
            return [line for line in raw.split("\n") if line]
        return [raw] if raw else []

    if isinstance(chars, dict):
        ordered_keys = sorted(
            chars.keys(),
            key=lambda item: (
                0 if _coerce_int(item) is not None else 1,
                _coerce_int(item) if _coerce_int(item) is not None else str(item),
            ),
        )
        ordered_rows = [chars[key] for key in ordered_keys]
        return _normalize_bitmap_rows(ordered_rows)

    if not isinstance(chars, list):
        return []

    out: List[str] = []
    for row in chars:
        if isinstance(row, str):
            raw_row = row
            if "\\" in raw_row:
                try:
                    decoded_row = bytes(raw_row, "utf-8").decode("unicode_escape")
                    if decoded_row:
                        raw_row = decoded_row
                except Exception:
                    pass
            if raw_row:
                out.append(raw_row)
            continue

        if isinstance(row, list):
            row_chars: List[str] = []
            for token in row:
                if isinstance(token, str):
                    decoded = _decode_char_token(token)
                    if decoded:
                        row_chars.append(decoded)
            if row_chars:
                out.append("".join(row_chars))
    return out


def _advance_from_provider(provider: Dict[str, Any], char: str, width: int) -> int:
    advances = provider.get("advances")
    if not isinstance(advances, dict):
        return width

    codepoint = ord(char)
    exact_keys: List[Any] = [char, codepoint]
    exact_keys.extend(_codepoint_aliases(codepoint))
    exact_keys.extend(
        [
            f"{codepoint:X}",
            f"{codepoint:x}",
            f"0x{codepoint:X}",
            f"0x{codepoint:x}",
            f"U+{codepoint:X}",
            f"u+{codepoint:x}",
        ]
    )
    for key in exact_keys:
        if key in advances:
            coerced = _coerce_advance_value(advances.get(key))
            if coerced is not None:
                return coerced

    normalized_advances: Dict[str, Any] = {}
    for key, value in advances.items():
        key_text = str(key).strip().lower()
        if key_text:
            normalized_advances[key_text] = value

        parsed_key = _coerce_int(key)
        if parsed_key is not None:
            normalized_advances[str(parsed_key)] = value
            normalized_advances[f"0x{parsed_key:x}"] = value
            normalized_advances[f"u+{parsed_key:x}"] = value

    normalized_keys = {alias.strip().lower() for alias in _codepoint_aliases(codepoint)}
    normalized_keys.update(
        {
            str(codepoint),
            f"{codepoint:x}",
            f"{codepoint:X}".lower(),
            f"0x{codepoint:x}",
            f"u+{codepoint:x}",
            f"\\u{codepoint:x}",
            char.strip().lower(),
        }
    )
    for key in normalized_keys:
        if key in normalized_advances:
            coerced = _coerce_advance_value(normalized_advances.get(key))
            if coerced is not None:
                return coerced

    for raw_key, raw_value in advances.items():
        if not isinstance(raw_key, str):
            continue

        key_text = raw_key.strip()
        if not key_text:
            continue

        bounds: Optional[Tuple[str, str]] = None
        if ".." in key_text:
            start_text, end_text = key_text.split("..", 1)
            bounds = (start_text, end_text)
        elif "~" in key_text:
            start_text, end_text = key_text.split("~", 1)
            bounds = (start_text, end_text)
        elif "-" in key_text and not key_text.startswith("-"):
            start_text, end_text = key_text.split("-", 1)
            if start_text.strip() and end_text.strip():
                bounds = (start_text, end_text)

        if not bounds:
            continue

        start_codepoint = _codepoint_from_token(bounds[0].strip())
        end_codepoint = _codepoint_from_token(bounds[1].strip())
        if start_codepoint is None or end_codepoint is None:
            continue

        low, high = sorted((start_codepoint, end_codepoint))
        if low <= codepoint <= high:
            coerced = _coerce_advance_value(raw_value)
            if coerced is not None:
                return coerced

    for default_key in ("default", "fallback", "*"):
        if default_key in advances:
            coerced = _coerce_advance_value(advances.get(default_key))
            if coerced is not None:
                return coerced
        if default_key in normalized_advances:
            coerced = _coerce_advance_value(normalized_advances.get(default_key))
            if coerced is not None:
                return coerced
    return width


def _space_codepoints_from_key(raw_char: Any) -> List[int]:
    codepoints: List[int] = []

    if isinstance(raw_char, str):
        key_text = raw_char.strip()
        if key_text and any(token in key_text for token in ("..", "~")):
            parsed_single = None
        elif key_text and "-" in key_text and not key_text.startswith("-"):
            start_text, end_text = key_text.split("-", 1)
            parsed_single = None if start_text.strip() and end_text.strip() else _codepoint_from_token(raw_char)
        else:
            parsed_single = _codepoint_from_token(raw_char)
    else:
        parsed_single = _codepoint_from_token(raw_char)

    if parsed_single is not None:
        return [parsed_single]

    if not isinstance(raw_char, str):
        return []

    key_text = raw_char.strip()
    if not key_text:
        return []

    bounds: Optional[Tuple[str, str]] = None
    if ".." in key_text:
        start_text, end_text = key_text.split("..", 1)
        bounds = (start_text, end_text)
    elif "~" in key_text:
        start_text, end_text = key_text.split("~", 1)
        bounds = (start_text, end_text)
    elif "-" in key_text and not key_text.startswith("-"):
        start_text, end_text = key_text.split("-", 1)
        if start_text.strip() and end_text.strip():
            bounds = (start_text, end_text)

    if not bounds:
        return []

    start_codepoint = _codepoint_from_token(bounds[0].strip())
    end_codepoint = _codepoint_from_token(bounds[1].strip())
    if start_codepoint is None or end_codepoint is None:
        return []

    low, high = sorted((start_codepoint, end_codepoint))
    span = high - low
    if span > 0x2000:
        return []

    for codepoint in range(low, high + 1):
        if 0 <= codepoint <= 0x10FFFF:
            codepoints.append(codepoint)
    return codepoints


def _write_bucket_sheet(bucket: str, tiles: Dict[int, Image.Image], cell_w: int, cell_h: int) -> None:
    OUTPUT_FONT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGBA", (cell_w * 16, cell_h * 16), (0, 0, 0, 0))

    for slot in range(256):
        tile = tiles.get(slot)
        if tile is None:
            tile = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
        if tile.size != (cell_w, cell_h):
            normalized = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
            offset = ((cell_w - tile.width) // 2, (cell_h - tile.height) // 2)
            normalized.paste(tile, offset)
            tile = normalized

        x = (slot % 16) * cell_w
        y = (slot // 16) * cell_h
        sheet.paste(tile, (x, y))

    output = OUTPUT_FONT_DIR / f"glyph_{bucket}.png"
    sheet.save(output, "PNG")


def _variant_score(variant: Dict[str, Any]) -> int:
    provider = str(variant.get("provider", "")).strip().lower()
    score = 0

    if provider == "bitmap":
        score += 600
    elif provider in ("legacy_unicode", "unicode"):
        score += 550
    elif provider == "space":
        score += 200
    else:
        score += 350

    if variant.get("texture"):
        score += 120
    if variant.get("bucket"):
        score += 40

    width = _coerce_int(variant.get("width"))
    if width is not None:
        score += min(max(width, 0), 64)

    advance = _coerce_int(variant.get("advance"))
    if advance is not None:
        score += 40
        if advance < 0:
            score -= 20
        else:
            score += min(advance, 48)

    return score


def run() -> None:
    root_fonts = list(_iter_root_font_definitions())
    if not root_fonts:
        _log("No font/default.json files found; skipping")
        return

    providers: List[Dict[str, Any]] = []
    visited: Set[Path] = set()
    parse_failures: Set[str] = set()

    for font_file in root_fonts:
        data = _load_font_definition(font_file)
        if not data:
            try:
                parse_failures.add(font_file.relative_to(PACK_DIR).as_posix())
            except Exception:
                parse_failures.add(font_file.as_posix())
            continue

        try:
            resolved = font_file.resolve()
        except OSError:
            resolved = font_file

        if resolved in visited:
            continue
        visited.add(resolved)

        try:
            source_name = font_file.relative_to(PACK_DIR).as_posix()
        except Exception:
            source_name = font_file.as_posix()
        providers.extend(_collect_providers(data, source_name, visited, parse_failures))

    if not providers:
        _log("No providers found in font definition")
        return

    bucket_tiles: Dict[str, Dict[int, Image.Image]] = defaultdict(dict)
    bucket_size: Dict[str, Tuple[int, int]] = {}
    glyph_variants: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    provider_type_counter: Counter[str] = Counter()
    bitmap_providers_used = 0
    legacy_unicode_providers_used = 0
    missing_bitmap_textures: Set[str] = set()
    failed_bitmap_textures: Set[str] = set()
    missing_unicode_size_maps: Set[str] = set()
    failed_unicode_size_maps: Set[str] = set()
    failed_unicode_pages: Set[str] = set()

    for provider in providers:
        provider_type = str(provider.get("_provider_type", "bitmap"))
        provider_type_counter[provider_type] += 1

        if provider_type == "bitmap":
            texture_ref = provider.get("file")
            chars = provider.get("chars")
            if not isinstance(texture_ref, str):
                continue

            source_namespace = _namespace_from_source(provider.get("_provider_source"))
            source_texture = _resolve_texture(texture_ref, default_namespace=source_namespace)
            if source_texture is None:
                _log(f"Missing bitmap texture: {texture_ref}")
                missing_bitmap_textures.add(texture_ref)
                continue

            try:
                with Image.open(source_texture) as image_file:
                    image = image_file.convert("RGBA")
            except Exception:
                _log(f"Failed to load texture: {source_texture}")
                failed_bitmap_textures.add(str(source_texture))
                continue

            row_strings = _normalize_bitmap_rows(chars)
            if not row_strings:
                continue
            dimensions = _cell_dimensions(image, row_strings)
            if not dimensions:
                continue

            cell_w, cell_h = dimensions
            ascent = _coerce_int(provider.get("ascent"))
            height = _coerce_int(provider.get("height"))
            bitmap_providers_used += 1

            for row_index, row in enumerate(row_strings):
                for col_index, char in enumerate(row):
                    if char == "\u0000":
                        continue

                    codepoint = ord(char)
                    key = _glyph_key(codepoint)
                    bucket_slot = _bucket_and_slot(codepoint)

                    left = col_index * cell_w
                    top = row_index * cell_h
                    right = left + cell_w
                    bottom = top + cell_h

                    tile = image.crop((left, top, right, bottom)).copy()
                    left_bearing, right_bearing, width = _measure_bounds(tile)
                    advance = _advance_from_provider(provider, char, width)

                    bucket = None
                    slot_xy = None
                    texture_name = None
                    if bucket_slot:
                        bucket, slot = bucket_slot
                        bucket_tiles[bucket][slot] = tile
                        prev_size = bucket_size.get(bucket, (0, 0))
                        bucket_size[bucket] = (max(prev_size[0], tile.width), max(prev_size[1], tile.height))
                        slot_xy = [slot % 16, slot // 16]
                        texture_name = f"font/glyph_{bucket}"

                    glyph_variants[key].append(
                        {
                            "codepoint": codepoint,
                            "char": char,
                            "bucket": bucket,
                            "slot": slot_xy,
                            "width": width,
                            "advance": advance,
                            "left_bearing": left_bearing,
                            "right_bearing": right_bearing,
                            "negative_space": advance < 0,
                            "texture": texture_name,
                            "source_texture": texture_ref,
                            "provider": "bitmap",
                            "provider_source": provider.get("_provider_source"),
                            "provider_index": provider.get("_provider_index"),
                            "height": height,
                            "ascent": ascent,
                            "cell_width": cell_w,
                            "cell_height": cell_h,
                        }
                    )

        elif provider_type in ("legacy_unicode", "unicode", "legacy"):
            sizes_ref = provider.get("sizes")
            template_ref = provider.get("template")
            if not isinstance(sizes_ref, str) or not isinstance(template_ref, str):
                continue

            source_namespace = _namespace_from_source(provider.get("_provider_source"))
            sizes_path = _resolve_font_binary(sizes_ref, default_namespace=source_namespace)
            if not sizes_path:
                _log(f"Missing legacy unicode size map: {sizes_ref}")
                missing_unicode_size_maps.add(sizes_ref)
                continue

            try:
                size_map = sizes_path.read_bytes()
            except Exception:
                _log(f"Failed to read legacy unicode size map: {sizes_path}")
                failed_unicode_size_maps.add(str(sizes_path))
                continue

            if not size_map:
                continue

            ascent = _coerce_int(provider.get("ascent"))
            height = _coerce_int(provider.get("height"))
            legacy_unicode_providers_used += 1

            for page in range(256):
                page_hex = f"{page:02X}"
                page_texture = _resolve_legacy_unicode_page(
                    template_ref,
                    page_hex,
                    default_namespace=source_namespace,
                )
                if page_texture is None:
                    continue

                try:
                    with Image.open(page_texture) as image_file:
                        image = image_file.convert("RGBA")
                except Exception:
                    _log(f"Failed to load legacy unicode page: {page_texture}")
                    failed_unicode_pages.add(str(page_texture))
                    continue

                cell_w = max(1, image.width // 16)
                cell_h = max(1, image.height // 16)

                for slot in range(256):
                    codepoint = (page << 8) + slot
                    if codepoint >= len(size_map):
                        break

                    size_byte = int(size_map[codepoint])
                    if size_byte <= 0:
                        continue

                    left_hint, _, width_hint = _legacy_size_bounds(size_byte)
                    if width_hint <= 0:
                        continue

                    col = slot % 16
                    row = slot // 16
                    left = col * cell_w
                    top = row * cell_h
                    right = left + cell_w
                    bottom = top + cell_h

                    tile = image.crop((left, top, right, bottom)).copy()
                    left_bearing, right_bearing, measured_width = _measure_bounds(tile)
                    width = measured_width if measured_width > 0 else width_hint
                    if measured_width <= 0 and left_hint > 0:
                        left_bearing = min(left_hint, max(0, cell_w - 1))
                        right_bearing = max(0, cell_w - (left_bearing + width))

                    char = chr(codepoint)
                    advance = _advance_from_provider(provider, char, width)
                    key = _glyph_key(codepoint)

                    bucket_slot = _bucket_and_slot(codepoint)
                    bucket = None
                    slot_xy = None
                    texture_name = None
                    if bucket_slot:
                        bucket, bucket_index = bucket_slot
                        bucket_tiles[bucket][bucket_index] = tile
                        prev_size = bucket_size.get(bucket, (0, 0))
                        bucket_size[bucket] = (max(prev_size[0], tile.width), max(prev_size[1], tile.height))
                        slot_xy = [bucket_index % 16, bucket_index // 16]
                        texture_name = f"font/glyph_{bucket}"

                    try:
                        source_texture_rel = page_texture.relative_to(PACK_DIR).as_posix()
                    except Exception:
                        source_texture_rel = page_texture.as_posix()

                    glyph_variants[key].append(
                        {
                            "codepoint": codepoint,
                            "char": char,
                            "bucket": bucket,
                            "slot": slot_xy,
                            "width": width,
                            "advance": advance,
                            "left_bearing": left_bearing,
                            "right_bearing": right_bearing,
                            "negative_space": advance < 0,
                            "texture": texture_name,
                            "source_texture": source_texture_rel,
                            "provider": "legacy_unicode",
                            "provider_source": provider.get("_provider_source"),
                            "provider_index": provider.get("_provider_index"),
                            "height": height,
                            "ascent": ascent,
                            "cell_width": cell_w,
                            "cell_height": cell_h,
                        }
                    )

        elif provider_type == "space":
            advances = provider.get("advances")
            if not isinstance(advances, dict):
                continue

            for raw_char, raw_advance in advances.items():
                advance = _coerce_int(raw_advance)
                if advance is None:
                    continue

                for codepoint in _space_codepoints_from_key(raw_char):
                    try:
                        char = chr(codepoint)
                    except Exception:
                        continue

                    key = _glyph_key(codepoint)
                    glyph_variants[key].append(
                        {
                            "codepoint": codepoint,
                            "char": char,
                            "bucket": None,
                            "slot": None,
                            "width": max(0, advance),
                            "advance": advance,
                            "left_bearing": 0,
                            "right_bearing": 0,
                            "negative_space": advance < 0,
                            "texture": None,
                            "source_texture": None,
                            "provider": "space",
                            "provider_source": provider.get("_provider_source"),
                            "provider_index": provider.get("_provider_index"),
                        }
                    )

    for bucket, tiles in bucket_tiles.items():
        width, height = bucket_size.get(bucket, (16, 16))
        _write_bucket_sheet(bucket, tiles, max(1, width), max(1, height))

    glyph_map: Dict[str, Dict[str, Any]] = {}
    char_to_codepoint: Dict[str, str] = {}
    codepoint_to_char: Dict[str, str] = {}
    char_to_codepoints: Dict[str, List[str]] = defaultdict(list)
    codepoint_to_chars: Dict[str, List[str]] = defaultdict(list)
    glyph_aliases: Dict[str, str] = {}
    negative_spaces: List[Dict[str, Any]] = []
    variable_width: List[Dict[str, Any]] = []
    supplementary_count = 0

    for key, variants in sorted(glyph_variants.items()):
        if not variants:
            continue

        ranked_variants = [
            dict(item[1])
            for item in sorted(
                enumerate(variants),
                key=lambda item: (-_variant_score(item[1]), item[0]),
            )
        ]

        effective = dict(ranked_variants[0])
        effective["variant_count"] = len(ranked_variants)
        if len(ranked_variants) > 1:
            effective["fallbacks"] = [dict(v) for v in ranked_variants[1:]]

        glyph_map[key] = effective
        char = effective.get("char")
        codepoint = _coerce_int(effective.get("codepoint"))
        if isinstance(char, str):
            codepoint_to_char[key] = char
            char_to_codepoint.setdefault(char, key)
            if key not in char_to_codepoints[char]:
                char_to_codepoints[char].append(key)
            if char not in codepoint_to_chars[key]:
                codepoint_to_chars[key].append(char)

        if codepoint is not None:
            for alias in _codepoint_aliases(codepoint):
                glyph_aliases.setdefault(alias, key)
            if codepoint > 0xFFFF:
                supplementary_count += 1

        for variant in ranked_variants:
            advance = _coerce_int(variant.get("advance"))
            width = _coerce_int(variant.get("width"))
            provider = variant.get("provider")
            provider_index = _coerce_int(variant.get("provider_index"))
            signature = {
                "codepoint": key,
                "provider": provider,
                "provider_index": provider_index,
                "advance": advance,
                "width": width,
            }
            if advance is not None and advance < 0:
                negative_spaces.append(signature)
            if advance is not None and width is not None and advance != width:
                variable_width.append(signature)

    if not glyph_map:
        _log("No glyphs extracted from font providers")
        return

    OUTPUT_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    missing_ref_count = (
        len(missing_bitmap_textures)
        + len(failed_bitmap_textures)
        + len(missing_unicode_size_maps)
        + len(failed_unicode_size_maps)
        + len(failed_unicode_pages)
        + len(parse_failures)
    )

    output_payload = {
        "providers_used": bitmap_providers_used,
        "providers_used_total": len(providers),
        "legacy_unicode_providers_used": legacy_unicode_providers_used,
        "provider_types": dict(provider_type_counter),
        "root_font_count": len(root_fonts),
        "parse_failure_count": len(parse_failures),
        "parse_failures": sorted(parse_failures),
        "buckets": sorted(bucket_tiles.keys()),
        "glyph_count": len(glyph_map),
        "glyph_variant_count": sum(len(variants) for variants in glyph_variants.values()),
        "supplementary_glyph_count": supplementary_count,
        "glyph_map": glyph_map,
        "glyph_variants": glyph_variants,
        "char_to_codepoint": char_to_codepoint,
        "codepoint_to_char": codepoint_to_char,
        "char_to_codepoints": {k: v for k, v in sorted(char_to_codepoints.items())},
        "codepoint_to_chars": {k: v for k, v in sorted(codepoint_to_chars.items())},
        "glyph_bi_map": {
            "char_to_codepoints": {k: v for k, v in sorted(char_to_codepoints.items())},
            "codepoint_to_chars": {k: v for k, v in sorted(codepoint_to_chars.items())},
        },
        "full_glyph_mapping": glyph_map,
        "glyph_aliases": glyph_aliases,
        "missing_texture_ref_count": len(missing_bitmap_textures) + len(failed_bitmap_textures) + len(failed_unicode_pages),
        "missing_ref_count": missing_ref_count,
        "unresolved_ref_count": missing_ref_count,
        "missing_bitmap_textures": sorted(missing_bitmap_textures),
        "failed_bitmap_textures": sorted(failed_bitmap_textures),
        "missing_legacy_unicode_size_maps": sorted(missing_unicode_size_maps),
        "failed_legacy_unicode_size_maps": sorted(failed_unicode_size_maps),
        "failed_legacy_unicode_pages": sorted(failed_unicode_pages),
        "negative_space_count": len(negative_spaces),
        "variable_width_count": len(variable_width),
        "negative_spaces": sorted(
            negative_spaces,
            key=lambda item: (
                str(item.get("codepoint", "")),
                _coerce_int(item.get("provider_index")) or -1,
                _coerce_int(item.get("advance")) or 0,
            ),
        ),
        "variable_width": sorted(
            variable_width,
            key=lambda item: (
                str(item.get("codepoint", "")),
                _coerce_int(item.get("provider_index")) or -1,
                _coerce_int(item.get("advance")) or 0,
            ),
        ),
    }
    with OUTPUT_MAPPING_FILE.open("w", encoding="utf-8") as file:
        json.dump(output_payload, file, indent=2, ensure_ascii=False)

    _log(
        "Generated "
        f"{len(bucket_tiles)} glyph sheets, {len(glyph_map)} mapped glyphs, "
        f"{len(negative_spaces)} negative-space entries"
    )


if __name__ == "__main__":
    run()
