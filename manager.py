from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import traceback
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT_DIR = Path(__file__).resolve().parent
STAGING_DIR = ROOT_DIR / "staging"
INPUT_PACK_ZIP = STAGING_DIR / "input_pack.zip"
PACK_WORK_DIR = ROOT_DIR / "pack"
PLUGIN_CONTEXT_FILE = STAGING_DIR / "plugin_context.json"

PLUGIN_MARKERS: Dict[str, List[str]] = {
    "craftengine": ["craftengine", "craft_engine"],
    "nexo": ["nexo", "nexomodel"],
    "itemadder": ["itemadder", "ia_generated", "items_packs"],
    "oraxen": ["oraxen", "pack/obfuscated"],
    "modelengine": ["modelengine", "model_engine"],
    "mmoitems": ["mmoitems", "mmo_items"],
    "mmocore": ["mmocore", "mmo_core"],
    "mmo": ["mythiclib", "mmoproject"],
    "mythicmobs": ["mythicmobs", "mythic_mobs"],
    "luckperms": ["luckperms", "permissions.yml", "groups.yml"],
    "advancedgui": ["deluxemenus", "trmenu", "excellentcrates", "zmenu", "chestcommands", "shopgui"],
}


def _log(kind: str, message: str) -> None:
    print(f"[MANAGER:{kind}] {message}", flush=True)


def _env_or_auto(env_name: str, auto_default: bool) -> bool:
    val = os.getenv(env_name)
    if val is None:
        return auto_default
    return str(val).strip().lower() == "true"


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _resolved_input_pack() -> Path:
    override = os.getenv("INPUT_PACK_PATH", "").strip()
    if override:
        override_path = Path(override)
        if not override_path.is_absolute():
            override_path = (ROOT_DIR / override_path).resolve()
        return override_path
    return INPUT_PACK_ZIP


def _iter_dir_depth(root: Path, max_depth: int):
    queue = [(root, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if depth > max_depth:
            continue
        if depth > 0:
            yield cur
        if depth == max_depth:
            continue
        try:
            for child in cur.iterdir():
                if child.is_dir():
                    queue.append((child, depth + 1))
        except OSError:
            continue


def _normalize_pack_root() -> None:
    if (PACK_WORK_DIR / "pack.mcmeta").exists():
        return

    nested_roots = [p for p in _iter_dir_depth(PACK_WORK_DIR, 4) if (p / "pack.mcmeta").exists()]
    if len(nested_roots) != 1:
        return

    nested_root = nested_roots[0]
    _log("INFO", f"Normalizing nested pack root: {nested_root}")
    for child in list(nested_root.iterdir()):
        target = PACK_WORK_DIR / child.name
        if target.exists():
            continue
        shutil.move(str(child), str(target))

    try:
        nested_root.rmdir()
    except OSError:
        pass


def _copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists() or source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _sync_script_mapping_from_workspace() -> None:
    script_json = STAGING_DIR / "script.json"
    sprites_json = STAGING_DIR / "sprites.json"
    _copy_if_exists(PACK_WORK_DIR / "script.json", script_json)
    _copy_if_exists(PACK_WORK_DIR / "sprites.json", sprites_json)

    if script_json.exists() and not sprites_json.exists():
        shutil.copyfile(script_json, sprites_json)
    elif sprites_json.exists() and not script_json.exists():
        shutil.copyfile(sprites_json, script_json)


def _prepare_pack_workspace() -> bool:
    input_pack = _resolved_input_pack()
    if not _exists(input_pack):
        _log("WARN", f"{input_pack} not found; skipping post-processing hooks")
        return False

    if PACK_WORK_DIR.exists():
        shutil.rmtree(PACK_WORK_DIR, ignore_errors=True)
    PACK_WORK_DIR.mkdir(parents=True, exist_ok=True)

    if input_pack.is_dir():
        _log("INFO", f"Copying pack directory into workspace: {input_pack}")
        for child in input_pack.iterdir():
            target = PACK_WORK_DIR / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
    else:
        try:
            with zipfile.ZipFile(input_pack, "r") as file:
                file.extractall(PACK_WORK_DIR)
        except zipfile.BadZipFile:
            _log("WARN", f"Invalid zip archive: {input_pack}")
            return False
        except OSError as exc:
            _log("WARN", f"Failed to extract {input_pack}: {exc}")
            return False
        _log("INFO", f"Extracted {input_pack} into ./pack workspace")

    _normalize_pack_root()
    _sync_script_mapping_from_workspace()
    return True


def _copy_mapping_from_directory(directory: Path) -> None:
    if not directory.exists() or not directory.is_dir():
        return

    script_json = STAGING_DIR / "script.json"
    sprites_json = STAGING_DIR / "sprites.json"
    _copy_if_exists(directory / "script.json", script_json)
    _copy_if_exists(directory / "sprites.json", sprites_json)


def _ensure_script_mapping() -> None:
    script_json = STAGING_DIR / "script.json"
    sprites_json = STAGING_DIR / "sprites.json"
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    if script_json.exists() and not sprites_json.exists():
        shutil.copyfile(script_json, sprites_json)
        return
    if sprites_json.exists() and not script_json.exists():
        shutil.copyfile(sprites_json, script_json)
        return
    if script_json.exists() and sprites_json.exists():
        return

    input_pack = _resolved_input_pack()
    sg_script = ROOT_DIR / "sg.py"
    if not sg_script.exists() or not input_pack.exists():
        return

    _log("INFO", "No script mapping found; running sg.py automatically")
    run_env = os.environ.copy()
    run_env["SG_OUTPUT_DIR"] = str(STAGING_DIR)

    result = subprocess.run(
        [sys.executable, str(sg_script), str(input_pack)],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        env=run_env,
    )

    if result.returncode != 0:
        _log("WARN", "sg.py failed during manager stage")
        if result.stdout:
            print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, flush=True)
    else:
        _log("INFO", "sg.py mapping generated successfully")

    if input_pack.is_dir():
        _copy_mapping_from_directory(input_pack)
    else:
        _copy_mapping_from_directory(input_pack.resolve().parent)
    _copy_mapping_from_directory(PACK_WORK_DIR)

    if script_json.exists() and not sprites_json.exists():
        shutil.copyfile(script_json, sprites_json)
    elif sprites_json.exists() and not script_json.exists():
        shutil.copyfile(sprites_json, script_json)


def _pack_index(limit: int = 20000) -> List[str]:
    files: List[str] = []
    if not PACK_WORK_DIR.exists():
        return files

    for path in PACK_WORK_DIR.rglob("*"):
        if not path.is_file():
            continue
        files.append(path.relative_to(PACK_WORK_DIR).as_posix())
        if len(files) >= limit:
            break
    return files


def _detect_plugins(file_index: List[str]) -> Dict[str, List[str]]:
    lower_index = [item.lower() for item in file_index]
    detected: Dict[str, List[str]] = {}

    for plugin_name, markers in PLUGIN_MARKERS.items():
        hits: List[str] = []
        for marker in markers:
            for rel_path in lower_index:
                if marker in rel_path:
                    hits.append(rel_path)
                    break
        if hits:
            detected[plugin_name] = sorted(set(hits))[:5]
    return detected


def _write_plugin_context() -> Set[str]:
    file_index = _pack_index()
    detected = _detect_plugins(file_index)
    lower_index = [path.lower() for path in file_index]

    payload = {
        "source_pack": str(_resolved_input_pack()),
        "pack_workspace": str(PACK_WORK_DIR),
        "detected_plugins": sorted(detected.keys()),
        "plugin_markers": detected,
        "features": {
            "has_fonts": any("/font/" in path and path.endswith((".json", ".yml", ".yaml")) for path in lower_index),
            "has_blockstates": any("/blockstates/" in path for path in lower_index),
            "has_sounds": any(path.endswith("sounds.json") for path in lower_index),
            "has_sound_files": any(path.startswith("assets/") and "/sounds/" in path and path.endswith((".ogg", ".wav", ".mp3", ".flac")) for path in lower_index),
            "has_modelengine": any("modelengine" in path for path in lower_index),
            "has_item_models": any("/models/item/" in path and path.endswith(".json") for path in lower_index),
            "has_item_definitions": any("/items/" in path and path.endswith(".json") for path in lower_index),
            "has_component_custom_models": any(
                "/items/" in path and path.endswith(".json")
                for path in lower_index
            ),
            "has_ranks": any(
                any(token in path for token in ("rank", "permission", "group", "luckperms", "lp_", "prefix"))
                for path in lower_index
            ),
            "has_gui": any(
                any(token in path for token in ("gui", "menu", "inventory", "hud", "slot", "deluxemenus", "trmenu", "shopgui", "chestcommands"))
                for path in lower_index
            ),
            "has_particles": any("/particles/" in path and path.endswith(".json") for path in lower_index),
            "has_entity_models": any("/models/entity/" in path and path.endswith(".json") for path in lower_index),
            "has_entity_geo": any("/geo/" in path and path.endswith(".json") for path in lower_index),
            "has_entity_animations": any(
                "/animations/" in path and path.endswith(".json") for path in lower_index
            ),
            "has_entity_definitions": any("/entity/" in path and path.endswith(".json") for path in lower_index),
            "has_attachables": any("/attachables/" in path and path.endswith(".json") for path in lower_index),
            "has_render_controllers": any(
                "/render_controllers/" in path and path.endswith(".json") for path in lower_index
            ),
            "has_animation_controllers": any(
                "/animation_controllers/" in path and path.endswith(".json") for path in lower_index
            ),
            "has_entity_textures": any(
                "/textures/entity/" in path and path.endswith((".png", ".tga")) for path in lower_index
            ),
        },
    }

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    with PLUGIN_CONTEXT_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    os.environ["CONVERTER_PLUGIN_CONTEXT"] = str(PLUGIN_CONTEXT_FILE)
    if detected:
        _log("INFO", f"Detected plugin ecosystems: {', '.join(sorted(detected.keys()))}")
    else:
        _log("INFO", "No known plugin ecosystem markers detected")
    return set(detected.keys())


def _run_hook(module_name: str) -> None:
    try:
        module = importlib.import_module(module_name)
        runner = getattr(module, "run", None)
        if callable(runner):
            runner()
        _log("OK", f"Hook executed: {module_name}")
    except Exception:
        _log("WARN", f"Hook failed: {module_name}")
        traceback.print_exc()


def _safe_load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int_from_payload(payload: Dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return 0
        try:
            return int(raw, 0)
        except Exception:
            try:
                return int(float(raw))
            except Exception:
                return 0
    return 0


def _collect_unresolved_samples(payload: Dict[str, Any], module_name: str, diagnostics_payload: Optional[Dict[str, Any]] = None, limit: int = 12) -> List[str]:
    samples: Set[str] = set()

    def _add(value: Any, prefix: str = "") -> None:
        if len(samples) >= limit * 3:
            return

        if isinstance(value, str):
            raw = value.strip()
            if raw:
                samples.add(f"{prefix}{raw}" if prefix else raw)
            return

        if isinstance(value, list):
            for item in value:
                _add(item, prefix)
            return

        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key).strip()
                nested_prefix = f"{prefix}{key_text}:" if key_text else prefix
                _add(nested, nested_prefix)
            return

    for key in (
        "unresolved",
        "unresolved_refs",
        "unresolved_ref",
        "missing_refs",
        "missing_texture_refs",
        "missing_icon_refs",
        "unresolved_icon_refs",
        "unresolved_model_refs",
        "unresolved_sources",
        "parse_failures",
    ):
        _add(payload.get(key))

    entries = payload.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in (
                "unresolved",
                "unresolved_refs",
                "missing_refs",
                "missing_texture_refs",
                "unresolved_icon_refs",
                "unresolved_model_refs",
                "unresolved_sources",
                "parse_failures",
            ):
                _add(entry.get(key))

    if module_name == "ranks":
        _add(payload.get("unresolved_permission_icon_refs"))

    if diagnostics_payload:
        _add(diagnostics_payload.get("unresolved"))
        _add(diagnostics_payload.get("missing"))
        _add(diagnostics_payload.get("parse_failures"))
        _add(diagnostics_payload.get("unresolved_sources"))

    return sorted(samples)[:limit]


def _write_coverage_report() -> None:
    module_files: Dict[str, Path] = {
        "font": STAGING_DIR / "target" / "font_map.json",
        "ranks": STAGING_DIR / "rank_map.json",
        "gui": STAGING_DIR / "gui_map.json",
        "particles": STAGING_DIR / "particle_map.json",
        "entity": STAGING_DIR / "entity_map.json",
        "blocks": STAGING_DIR / "block_map.json",
        "sounds": STAGING_DIR / "target" / "rp" / "sounds" / "sound_definitions.json",
    }

    unresolved_keys = {
        "font": ["missing_texture_ref_count", "missing_ref_count", "unresolved_ref_count", "parse_failure_count"],
        "ranks": ["unresolved_icon_ref_count", "unresolved_ref_count", "parse_failure_count"],
        "gui": ["unresolved_texture_ref_count", "unresolved_ref_count", "parse_failure_count"],
        "particles": [
            "unresolved_texture_ref_count",
            "unresolved_ref_count",
            "parse_failure_count",
            "unresolved_source_count",
        ],
        "entity": [
            "unresolved_ref_count",
            "missing_ref_count",
            "parse_failure_count",
            "unresolved_texture_ref_count",
        ],
        "blocks": ["unresolved_ref_count", "missing_ref_count", "parse_failure_count", "unresolved_source_count"],
        "sounds": ["unresolved_count", "unresolved_ref_count", "parse_failure_count"],
    }

    converted_keys = {
        "font": ["glyph_count"],
        "ranks": ["rank_entry_count", "icon_count"],
        "gui": ["gui_entry_count", "texture_count", "model_mapping_count"],
        "particles": ["particle_file_count", "texture_count"],
        "entity": ["model_count", "animation_count", "entity_definition_count", "texture_count"],
        "blocks": ["block_count", "converted_variant_count"],
        "sounds": ["sound_event_count", "converted_event_count"],
    }

    report_modules: Dict[str, Dict[str, Any]] = {}
    total_unresolved = 0
    total_converted_signals = 0

    for module_name, module_file in module_files.items():
        if not module_file.exists():
            continue

        payload = _safe_load_json(module_file)
        if not payload:
            continue

        unresolved = 0
        for key in unresolved_keys.get(module_name, []):
            unresolved = max(unresolved, _int_from_payload(payload, key))

        diagnostics_payload: Dict[str, Any] = {}
        if module_name == "sounds":
            diagnostics_payload = _safe_load_json(STAGING_DIR / "target" / "rp" / "sounds" / "sound_diagnostics.json")
            for key in unresolved_keys.get(module_name, []):
                unresolved = max(unresolved, _int_from_payload(diagnostics_payload, key))

        converted_signal = 0
        if module_name == "sounds":
            definitions = payload.get("sound_definitions")
            if isinstance(definitions, dict):
                converted_signal = len(definitions)
        if converted_signal == 0:
            for key in converted_keys.get(module_name, []):
                converted_signal += _int_from_payload(payload, key)

        report_modules[module_name] = {
            "source": module_file.relative_to(ROOT_DIR).as_posix(),
            "unresolved": unresolved,
            "converted_signal": converted_signal,
            "top_unresolved_refs": _collect_unresolved_samples(payload, module_name, diagnostics_payload),
        }

        total_unresolved += unresolved
        total_converted_signals += converted_signal

    report_payload = {
        "pack_workspace": str(PACK_WORK_DIR),
        "modules": report_modules,
        "totals": {
            "module_count": len(report_modules),
            "unresolved": total_unresolved,
            "converted_signal": total_converted_signals,
        },
    }

    report_path = STAGING_DIR / "coverage_report.json"
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report_payload, file, indent=2, ensure_ascii=False)
    _log("INFO", f"Coverage report written: {report_path}")


def main() -> None:
    os.chdir(ROOT_DIR)

    prepared = _prepare_pack_workspace()
    if not prepared:
        return

    _ensure_script_mapping()

    plugins = _write_plugin_context()

    hook_specs = [
        (
            "sound",
            "SOUNDS_CONVERSION",
            any(PACK_WORK_DIR.glob("assets/**/sounds.json"))
            or any(PACK_WORK_DIR.glob("assets/**/sounds/**/*.ogg"))
            or any(PACK_WORK_DIR.glob("assets/**/sounds/**/*.wav"))
            or any(PACK_WORK_DIR.glob("assets/**/sounds/**/*.mp3"))
            or any(PACK_WORK_DIR.glob("assets/**/sounds/**/*.flac")),
        ),
        ("meg3", "MEG3_FIX", "modelengine" in plugins),
        (
            "armor",
            "ARMOR_CONVERSION",
            (PACK_WORK_DIR / "assets/minecraft/optifine/cit/ia_generated_armors").exists(),
        ),
        (
            "font",
            "FONT_CONVERSION",
            any(PACK_WORK_DIR.glob("assets/**/font/default.json"))
            or any(PACK_WORK_DIR.glob("assets/**/font/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/font/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/font/*.yml"))
            or any(PACK_WORK_DIR.glob("assets/**/font/**/*.yml"))
            or any(PACK_WORK_DIR.glob("assets/**/font/*.yaml"))
            or any(PACK_WORK_DIR.glob("assets/**/font/**/*.yaml")),
        ),
        ("bow", "BOW_CONVERSION", any(PACK_WORK_DIR.glob("assets/**/models/item/bow.json"))),
        ("shield", "SHIELD_CONVERSION", any(PACK_WORK_DIR.glob("assets/**/models/item/shield.json"))),
        (
            "blocks",
            "BLOCK_CONVERSION",
            any(PACK_WORK_DIR.glob("assets/**/blockstates/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/blockstates/**/*.json")),
        ),
        (
            "ranks",
            "RANK_CONVERSION",
            any(PACK_WORK_DIR.glob("**/*rank*.json"))
            or any(PACK_WORK_DIR.glob("**/*rank*.yml"))
            or any(PACK_WORK_DIR.glob("**/*rank*.yaml"))
            or any(PACK_WORK_DIR.glob("**/*permission*.json"))
            or any(PACK_WORK_DIR.glob("**/*permission*.yml"))
            or any(PACK_WORK_DIR.glob("**/*permission*.yaml"))
            or any(PACK_WORK_DIR.glob("**/*group*.json"))
            or any(PACK_WORK_DIR.glob("**/*group*.yml"))
            or any(PACK_WORK_DIR.glob("**/*group*.yaml")),
        ),
        (
            "gui",
            "GUI_CONVERSION",
            any(PACK_WORK_DIR.glob("**/*gui*.json"))
            or any(PACK_WORK_DIR.glob("**/*gui*.yml"))
            or any(PACK_WORK_DIR.glob("**/*gui*.yaml"))
            or any(PACK_WORK_DIR.glob("**/*menu*.json"))
            or any(PACK_WORK_DIR.glob("**/*menu*.yml"))
            or any(PACK_WORK_DIR.glob("**/*menu*.yaml"))
            or any(PACK_WORK_DIR.glob("**/*inventory*.json"))
            or any(PACK_WORK_DIR.glob("**/*inventory*.yml"))
            or any(PACK_WORK_DIR.glob("**/*inventory*.yaml"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/**/*gui*.png"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/**/*gui*.tga"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/**/*menu*.png"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/**/*menu*.tga"))
            or any(PACK_WORK_DIR.glob("assets/**/models/item/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/models/item/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/items/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/items/**/*.json")),
        ),
        (
            "particles",
            "PARTICLE_CONVERSION",
            any(PACK_WORK_DIR.glob("assets/**/particles/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/particles/**/*.json")),
        ),
        (
            "entity",
            "ENTITY_CONVERSION",
            any(PACK_WORK_DIR.glob("assets/**/models/entity/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/models/entity/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/geo/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/geo/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/attachables/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/attachables/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/animations/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/animations/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/animation_controllers/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/animation_controllers/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/entity/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/entity/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/render_controllers/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/render_controllers/**/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/entity/*.png"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/entity/**/*.png"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/entity/*.tga"))
            or any(PACK_WORK_DIR.glob("assets/**/textures/entity/**/*.tga"))
            or any(PACK_WORK_DIR.glob("assets/**/materials/*.material"))
            or any(PACK_WORK_DIR.glob("assets/**/materials/**/*.material"))
            or any(PACK_WORK_DIR.glob("assets/**/materials/*.json"))
            or any(PACK_WORK_DIR.glob("assets/**/materials/**/*.json")),
        ),
    ]

    for module_name, env_name, auto_default in hook_specs:
        if _env_or_auto(env_name, auto_default):
            _run_hook(module_name)

    _write_coverage_report()


if __name__ == "__main__":
    main()
