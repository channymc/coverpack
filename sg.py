#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║   sprites_generator.py  v3.0.0                                             ║
# ║   Java2Bedrock · sprites.json Ultra Generator                              ║
# ║   Repo : https://github.com/AZPixel-Team/Java2Bedrock                     ║
# ║                                                                              ║
# ║   Reads a Java Edition Resource Pack (.zip or directory) and extracts      ║
# ║   item model overrides to produce sprites.json for converter.sh.           ║
# ║                                                                              ║
# ║   Python 3.8+  ·  Zero third-party dependencies.                          ║
# ║   Exit codes: 0 = success, 1 = error, 2 = warnings only                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
# USAGE:
#   python sprites_generator.py <pack.zip|dir> [options]
#
# OPTIONS:
#   -o / --output FILE          Write sprites.json (default: stdout)
#   -m / --merge FILE           Merge with existing sprites.json
#   -b / --bedrock-rp PATH      Validate sprite paths against Bedrock RP
#   --inject                    Embed sprites.json into a copy of the zip
#   --report FILE               Write detailed debug/validation report
#   --filter ITEM,...           Process only these item names
#   --namespace NS,...          Process only these namespaces
#   --sprite-prefix PREFIX      Prepend PREFIX to all generated sprite paths
#   --indent N                  JSON indentation spaces (default: 4, 0=compact)
#   --dry-run                   Parse and validate without writing any files
#   --self-test                 Run built-in unit tests and exit
#   --no-color                  Disable ANSI colour output
#   -v / --verbose              Verbose debug output
#   -q / --quiet                Only print errors (warnings go to stderr)
#   --version                   Show version and exit
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple, Union

# ══════════════════════════════════════════════════════════════════════════════
# §1  VERSION & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

VERSION           = "3.0.0"
MAX_PARENT_DEPTH  = 24   # max parent-chain depth before giving up
MAX_VAR_PASSES    = 12   # max passes when resolving #variable texture refs

# ── Pack-format → Minecraft version display ───────────────────────────────────
PACK_FORMAT_TO_MC: Dict[int, str] = {
    4: "1.13–1.14",  5: "1.15–1.16",    6: "1.16.2–1.16.5",
    7: "1.17",       8: "1.18",          9: "1.19",
    12: "1.19.4",   13: "1.20",         15: "1.20.2",
    18: "1.20.4",   22: "1.20.6",       32: "1.21",
    34: "1.21.1",   36: "1.21.2",       38: "1.21.3",
    40: "1.21.4",   41: "1.21.5",       46: "1.21.4+new-format",
}
# Pack formats that use the NEW 1.21.4+ item model system (items/ dir, no overrides)
NEW_FORMAT_THRESHOLD = 46

# ── Java item max-durability ───────────────────────────────────────────────────
# Source: https://minecraft.wiki/w/Item_durability
JAVA_MAX_DAMAGE: Dict[str, int] = {
    # Leather armor
    "leather_helmet": 55,       "leather_chestplate": 80,
    "leather_leggings": 75,     "leather_boots": 65,
    # Chainmail armor
    "chainmail_helmet": 165,    "chainmail_chestplate": 240,
    "chainmail_leggings": 225,  "chainmail_boots": 195,
    # Iron armor
    "iron_helmet": 165,         "iron_chestplate": 240,
    "iron_leggings": 225,       "iron_boots": 195,
    # Golden armor
    "golden_helmet": 77,        "golden_chestplate": 112,
    "golden_leggings": 105,     "golden_boots": 91,
    # Diamond armor
    "diamond_helmet": 363,      "diamond_chestplate": 528,
    "diamond_leggings": 495,    "diamond_boots": 429,
    # Netherite armor
    "netherite_helmet": 407,    "netherite_chestplate": 592,
    "netherite_leggings": 555,  "netherite_boots": 481,
    # Misc armor
    "turtle_helmet": 275,       "wolf_armor": 64,
    # Swords
    "wooden_sword": 59,    "stone_sword": 131,    "iron_sword": 250,
    "golden_sword": 32,    "diamond_sword": 1561, "netherite_sword": 2031,
    # Pickaxes
    "wooden_pickaxe": 59,  "stone_pickaxe": 131,  "iron_pickaxe": 250,
    "golden_pickaxe": 32,  "diamond_pickaxe": 1561, "netherite_pickaxe": 2031,
    # Axes
    "wooden_axe": 59,      "stone_axe": 131,      "iron_axe": 250,
    "golden_axe": 32,      "diamond_axe": 1561,   "netherite_axe": 2031,
    # Shovels
    "wooden_shovel": 59,   "stone_shovel": 131,   "iron_shovel": 250,
    "golden_shovel": 32,   "diamond_shovel": 1561,"netherite_shovel": 2031,
    # Hoes
    "wooden_hoe": 59,      "stone_hoe": 131,      "iron_hoe": 250,
    "golden_hoe": 32,      "diamond_hoe": 1561,   "netherite_hoe": 2031,
    # Ranged / tools
    "bow": 384,            "crossbow": 465,        "trident": 250,
    "shield": 336,         "fishing_rod": 64,      "flint_and_steel": 64,
    "carrot_on_a_stick": 25, "warped_fungus_on_a_stick": 25,
    "shears": 238,         "elytra": 432,          "mace": 500,
    # Not damageable (0 means "skip damage logic")
    "leather_horse_armor": 0, "iron_horse_armor": 0,
    "golden_horse_armor": 0,  "diamond_horse_armor": 0,
}

# ── Texture variable priority ─────────────────────────────────────────────────
# We look for these keys in order when picking the best texture from a model.
TEXTURE_KEY_PRIORITY: Tuple[str, ...] = (
    "layer0",   # 2-D item — most common for custom items
    "layer1",   # secondary (leather trim, dye overlay, etc.)
    "all",      # uniform block face
    "texture",
    "top",  "side", "front", "back", "bottom",
    "cross", "plant", "particle",
    "fan", "end", "edge",
    "north", "south", "east", "west",
    "pane", "stem",
)

# ── Java texture folder → Bedrock texture folder ──────────────────────────────
# Used in java_tex_to_bedrock_sprite(); defined at module level to avoid
# rebuilding the dict on every call (was a performance bug in v2).
_TEX_FOLDER_MAP: Dict[str, str] = {
    "item":         "textures/items",
    "items":        "textures/items",
    "block":        "textures/blocks",
    "blocks":       "textures/blocks",
    "entity":       "textures/entity",
    "entities":     "textures/entity",
    "gui":          "textures/gui",
    "painting":     "textures/painting",
    "paintings":    "textures/painting",
    "particle":     "textures/particle",
    "particles":    "textures/particle",
    "environment":  "textures/environment",
    "misc":         "textures/misc",
    "colormap":     "textures/colormap",
    "map":          "textures/map",
    "models":       "textures/models",
    "armor":        "textures/models/armor",
    "effect":       "textures/mob_effect",
    "mob_effect":   "textures/mob_effect",
}

# ── Built-in parent models that have no file in any pack ──────────────────────
_BUILTIN_PARENTS: Set[str] = {
    # Normalised (ns:path) forms
    "minecraft:builtin/generated",   "minecraft:builtin/entity",
    "minecraft:builtin/compass",     "minecraft:builtin/clock",
    "minecraft:item/generated",      "minecraft:item/handheld",
    "minecraft:item/handheld_rod",   "minecraft:item/handheld_crossbow",
    "minecraft:item/bow",            "minecraft:item/crossbow",
    "minecraft:item/trident",        "minecraft:item/template_spawn_egg",
    "minecraft:item/template_shulker_box",
    "minecraft:block/block",         "minecraft:block/cube",
    "minecraft:block/cube_all",      "minecraft:block/cube_column",
    "minecraft:block/cube_mirrored_all",
    "minecraft:block/leaves",        "minecraft:block/thin_block",
    "minecraft:block/cross",         "minecraft:block/tinted_cross",
    "minecraft:block/orientable",    "minecraft:block/orientable_vertical",
    "minecraft:block/crop",          "minecraft:block/template_glazed_terracotta",
    "minecraft:block/pressure_plate_up", "minecraft:block/pressure_plate_down",
    "minecraft:block/slab",          "minecraft:block/slab_top",
    "minecraft:block/stairs",        "minecraft:block/button",
    "minecraft:block/fence_post",    "minecraft:block/fence_side",
    "minecraft:block/fence_gate",    "minecraft:block/fence_gate_open",
    "minecraft:block/wall_post",     "minecraft:block/wall_side",
    "minecraft:block/template_torch","minecraft:block/template_trapdoor_bottom",
    "minecraft:block/template_trapdoor_top",
    "minecraft:block/door_bottom_left","minecraft:block/door_top_left",
}
# Also accept plain forms (without "minecraft:")
_BUILTIN_PARENTS |= {p.split(":", 1)[1] for p in _BUILTIN_PARENTS}

# ── Vanilla predicates NOT supported in sprites.json ─────────────────────────
_UNSUPPORTED_PREDICATES: Set[str] = {
    "pulling", "pull", "charged", "firework", "blocking", "broken",
    "cast", "lefthanded", "cooldown", "angle", "level", "time",
    "throwing", "tooting", "trim_type", "brushing", "using_item",
    "display_context", "selected",
}


# ══════════════════════════════════════════════════════════════════════════════
# §2  LOGGER
# ══════════════════════════════════════════════════════════════════════════════

class Logger:
    """
    Console logger — ANSI colour, quiet, verbose.

    Severity routing:
      ok / info / section / debug  → stdout  (suppressed in --quiet)
      warn                          → stderr  (always visible, even in --quiet)
      err                           → stderr  (always visible)
    """

    RESET   = "\033[0m";   BOLD    = "\033[1m";   DIM     = "\033[2m"
    RED     = "\033[31m";  GREEN   = "\033[32m";  YELLOW  = "\033[33m"
    BLUE    = "\033[36m";  MAGENTA = "\033[35m";  GRAY    = "\033[37m"
    WHITE   = "\033[97m"

    def __init__(self, use_color: bool = True,
                 quiet: bool = False, verbose: bool = False) -> None:
        # Respect NO_COLOR env var (https://no-color.org/)
        env_no_color = os.environ.get("NO_COLOR", "") != ""
        self.use_color = use_color and not env_no_color and sys.stdout.isatty()
        self.quiet     = quiet
        self.verbose   = verbose
        self._warnings: List[str] = []
        self._errors:   List[str] = []

    def _c(self, *codes: str, text: str) -> str:
        return ("".join(codes) + text + self.RESET) if self.use_color else text

    def _so(self, msg: str) -> None:
        """Print to stdout unless quiet."""
        if not self.quiet:
            print(msg, flush=True)

    def _se(self, msg: str) -> None:
        """Print to stderr — always visible."""
        print(msg, file=sys.stderr, flush=True)

    def ok(self, msg: str) -> None:
        self._so(self._c(self.GREEN, text="[✔] ") + self._c(self.GRAY, text=msg))

    def info(self, msg: str) -> None:
        self._so(self._c(self.BLUE, text="[•] ") + self._c(self.GRAY, text=msg))

    def section(self, title: str) -> None:
        self._so(f"\n{self._c(self.BOLD, self.BLUE, text=f'══ {title} ══')}")

    def debug(self, msg: str) -> None:
        if self.verbose:
            self._so(self._c(self.DIM, self.GRAY, text=f"    [~] {msg}"))

    def warn(self, msg: str) -> None:
        """Warnings always go to stderr, even in --quiet mode."""
        self._warnings.append(msg)
        self._se(self._c(self.YELLOW, self.BOLD, text="[!] ") + self._c(self.GRAY, text=msg))

    def err(self, msg: str, fatal: bool = False) -> None:
        """Errors always go to stderr."""
        self._errors.append(msg)
        self._se(self._c(self.RED, self.BOLD, text="[✘] ") + self._c(self.GRAY, text=msg))
        if fatal:
            sys.exit(1)

    def banner(self) -> None:
        b = (
            f"\n  ╔══════════════════════════════════════════════════════════════╗\n"
            f"  ║  sprites_generator.py  v{VERSION:<6}                              ║\n"
            f"  ║  Java2Bedrock · sprites.json Ultra Generator  v3             ║\n"
            f"  ║  github.com/AZPixel-Team/Java2Bedrock                        ║\n"
            f"  ╚══════════════════════════════════════════════════════════════╝"
        )
        self._so(self._c(self.BOLD, self.BLUE, text=b))

    def progress(self, done: int, total: int, label: str = "") -> None:
        """Print an in-place progress bar (only when not quiet/verbose)."""
        if self.quiet or self.verbose or total <= 0:
            return
        pct  = done * 100 // total
        bar  = "█" * (pct // 5) + "░" * (20 - pct // 5)
        line = f"\r  [{bar}] {pct:3d}%  {label:<30}"
        print(line, end="", flush=True)
        if done >= total:
            print()  # newline at end

    @property
    def warning_count(self) -> int: return len(self._warnings)
    @property
    def error_count(self)   -> int: return len(self._errors)
    def all_warnings(self)  -> List[str]: return list(self._warnings)
    def all_errors(self)    -> List[str]: return list(self._errors)


# ══════════════════════════════════════════════════════════════════════════════
# §3  LENIENT JSON PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _scan_json_string(text: str, start: int) -> int:
    """
    Starting just after the opening '"' at text[start], find the index
    of the closing unescaped '"'.  Returns index of the closing quote.
    Handles all \\uXXXX and single-character escape sequences.
    """
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2  # skip escaped character (handles \", \\, \/, \b, \f, \n, \r, \t, \uXXXX)
            continue
        if c == '"':
            return i
        i += 1
    return i  # unterminated string — return end


def _strip_json_comments(text: str) -> str:
    """
    Remove // line-comments and /* block-comments */ from JSON-like text,
    while fully respecting quoted strings (does NOT corrupt strings that
    happen to contain '/' characters or comment-like sequences).

    FIX vs v2: v2 used `re.sub(r'/\\*.*?\\*/', ...)` which ran BEFORE the
    string-aware scanner and could corrupt e.g. {"path": "a/*/b"}.
    This version is fully string-aware for BOTH comment types.
    """
    result: List[str] = []
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        # ── Inside a string literal ───────────────────────────────────────────
        if c == '"':
            result.append(c)
            end = _scan_json_string(text, i + 1)
            result.append(text[i + 1 : end + 1])   # include closing quote
            i = end + 1
            continue

        # ── Possible start of a comment ───────────────────────────────────────
        if c == "/" and i + 1 < n:
            nc = text[i + 1]

            # // line comment → skip to end of line
            if nc == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue

            # /* block comment */ → skip to */
            if nc == "*":
                i += 2
                while i + 1 < n:
                    if text[i] == "*" and text[i + 1] == "/":
                        i += 2
                        break
                    i += 1
                else:
                    i = n   # unterminated block comment
                continue

        result.append(c)
        i += 1

    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """
    Remove trailing commas before ] or } that are NOT inside strings.
    Simple regex is sufficient here because we call this AFTER _strip_json_comments,
    so no comment-lookalike sequences remain, and the regex only touches
    ',<whitespace>}' or ',<whitespace>]' patterns which are syntactically
    unambiguous even without string-awareness.
    """
    return re.sub(r",(\s*[}\]])", r"\1", text)


def parse_json_lenient(raw: bytes) -> Any:
    """
    Parse JSON bytes using 4 escalating strategies:
      1. Direct parse after BOM-aware decode
      2. Strip comments (string-aware) + trailing commas, then parse
      3. Trailing-commas only
      4. Regex-extract outermost { } or [ ], then apply strategy 2

    Raises ValueError if ALL strategies fail.
    Supports: utf-8-sig (BOM), utf-8, latin-1, cp1252 encodings.
    """
    text = ""
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, AttributeError):
            pass
    else:
        raise ValueError("Cannot decode file with any known encoding")

    def try_parse(t: str) -> Optional[Any]:
        try:
            return json.loads(t)
        except (json.JSONDecodeError, ValueError):
            return None

    # Strategy 1: plain
    r = try_parse(text)
    if r is not None:
        return r

    # Strategy 2: strip comments + trailing commas
    cleaned = _strip_trailing_commas(_strip_json_comments(text))
    r = try_parse(cleaned)
    if r is not None:
        return r

    # Strategy 3: trailing commas only (in case comment-strip broke something)
    r = try_parse(_strip_trailing_commas(text))
    if r is not None:
        return r

    # Strategy 4: extract outermost JSON object/array, then clean + parse
    m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m:
        r = try_parse(_strip_trailing_commas(_strip_json_comments(m.group(1))))
        if r is not None:
            return r

    raise ValueError(
        f"Cannot parse JSON (first 160 chars): {text[:160]!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# §4  NAMESPACE / PATH UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def split_ns(ref: str) -> Tuple[str, str]:
    """
    'namespace:path'  →  ('namespace', 'path')
    'path'            →  ('minecraft', 'path')
    Strips leading/trailing whitespace from both halves.
    """
    if ":" in ref:
        ns, path = ref.split(":", 1)
        return ns.strip(), path.strip()
    return "minecraft", ref.strip()


def normalise_ns(ref: str) -> str:
    """Return 'ns:path' form (always has namespace prefix)."""
    ns, path = split_ns(ref)
    return f"{ns}:{path}"


def model_to_pack_path(model_ref: str) -> str:
    """
    'minecraft:item/foo'  →  'assets/minecraft/models/item/foo.json'
    'mypack:custom/bar'   →  'assets/mypack/models/custom/bar.json'
    """
    ns, path = split_ns(model_ref)
    return f"assets/{ns}/models/{path}.json"


def java_tex_to_bedrock_sprite(tex_ref: str, sprite_prefix: str = "") -> str:
    """
    Convert a Java texture reference to a Bedrock sprite path (no extension).

    Java source                         Bedrock target
    ──────────────────────────────────  ─────────────────────────────────────
    minecraft:item/custom/sword      →  textures/items/custom/sword
    minecraft:block/stone            →  textures/blocks/stone
    minecraft:entity/pig             →  textures/entity/pig
    minecraft:gui/icons              →  textures/gui/icons
    minecraft:gui/sprites/hud/a      →  textures/gui/hud/a   (1.20 atlas)
    minecraft:painting/aztec         →  textures/painting/aztec
    mypack:custom/icon               →  textures/custom/icon
    minecraft:item/foo.png           →  textures/items/foo   (strips .png)

    If sprite_prefix is set (e.g. "textures/custom_items"), all generated
    paths are replaced with  sprite_prefix/<model_stem>  instead.
    """
    _, path = split_ns(tex_ref)

    # Strip trailing .png/.tga some packs accidentally include
    for ext in (".png", ".tga", ".PNG", ".TGA"):
        if path.endswith(ext):
            path = path[: -len(ext)]
            break

    # Normalise any backslashes (Windows-style packs)
    path = path.replace("\\", "/")

    # If caller wants a custom prefix, just return prefix + leaf name
    if sprite_prefix:
        leaf = path.split("/")[-1]
        prefix = sprite_prefix.rstrip("/")
        return f"{prefix}/{leaf}"

    parts = path.split("/")
    if not parts or not parts[0]:
        return f"textures/{path}"

    first = parts[0]
    rest  = "/".join(parts[1:]) if len(parts) > 1 else ""

    if first in _TEX_FOLDER_MAP:
        base = _TEX_FOLDER_MAP[first]
        # 1.20+ GUI atlas: gui/sprites/hud/foo → textures/gui/hud/foo
        if first == "gui" and rest.startswith("sprites/"):
            rest = rest[len("sprites/"):]
        return f"{base}/{rest}" if rest else base

    return f"textures/{path}"


def sanitise_sprite_path(path: str) -> str:
    """
    Normalise a Bedrock sprite path:
      - Collapse double slashes
      - Strip leading/trailing slashes
      - Replace backslashes
      - Lowercase (Bedrock is case-sensitive on Android)
    """
    path = path.replace("\\", "/")
    # Collapse // → /
    while "//" in path:
        path = path.replace("//", "/")
    path = path.strip("/")
    return path.lower()


def compute_path_hash(item: str,
                      cmd:         Optional[int],
                      damage:      Optional[int],
                      unbreakable: Optional[bool]) -> str:
    """
    Reproduce the 7-char MD5 identifier used by the original Kas-tle converter.
    Formula: md5(item + str(cmd|'') + str(damage|'') + str(unbreakable|''))[:7]

    Note: The AZPixel fork (converter_pro.sh) uses sequential gmdl_N IDs
    instead of this hash, but we generate it for cross-reference in reports.
    """
    parts = [
        item,
        str(cmd)         if cmd         is not None else "",
        str(damage)      if damage      is not None else "",
        str(unbreakable) if unbreakable is not None else "",
    ]
    return hashlib.md5("".join(parts).encode()).hexdigest()[:7]


# ══════════════════════════════════════════════════════════════════════════════
# §5  JAVA RESOURCE PACK READER
# ══════════════════════════════════════════════════════════════════════════════

class JavaPackReader:
    """
    Unified reader for Java Resource Packs.

    Supported inputs:
      ① Flat .zip / .jar / .mcpack files
      ② Nested zips — content inside ONE subdirectory (GitHub "Download ZIP")
      ③ Zips nested up to 4 directory levels deep
      ④ Unpacked directory trees
    """

    def __init__(self, pack_path: str, log: Optional[Logger] = None) -> None:
        self.pack_path  = os.fspath(pack_path)
        self._log       = log or Logger(use_color=False, quiet=True)
        self._is_zip    = False
        self._zf:       Optional[zipfile.ZipFile] = None
        self._prefix    = ""                       # e.g. "MyPack-main/"
        self._name_set: Set[str] = set()           # cached namelist (always a set)

        if not os.path.exists(self.pack_path):
            self._log.err(f"Pack not found: {self.pack_path}", fatal=True)

        if os.path.isfile(self.pack_path):
            if not zipfile.is_zipfile(self.pack_path):
                self._log.err(
                    f"'{self.pack_path}' is not a valid ZIP/JAR archive.",
                    fatal=True,
                )
            self._is_zip = True
            try:
                self._zf = zipfile.ZipFile(self.pack_path, "r")
            except zipfile.BadZipFile as exc:
                self._log.err(f"Cannot open archive: {exc}", fatal=True)

            names          = self._zf.namelist()         # type: ignore[union-attr]
            self._name_set = set(names)
            self._prefix   = self._find_prefix(names)
            if self._prefix:
                self._log.debug(f"Nested zip prefix: '{self._prefix}'")

    def __enter__(self) -> "JavaPackReader":
        return self

    def __exit__(self, *_: Any) -> None:
        if self._zf:
            self._zf.close()

    # ── Prefix detection ──────────────────────────────────────────────────────

    @staticmethod
    def _find_prefix(names: List[str]) -> str:
        """
        Detect the subdirectory prefix for nested packs (searches 1–4 levels).
        Returns '' for flat packs, or e.g. 'MyPack-main/' for GitHub zips.

        FIX vs v2: simplified to a single clear pass without redundant inner loop.
        """
        if "pack.mcmeta" in names:
            return ""
        # Build a fast lookup set for O(1) checking
        name_set = set(names)
        for n in names:
            if not n.endswith("/pack.mcmeta"):
                continue
            segs  = n.split("/")
            depth = len(segs) - 1   # number of directory components before pack.mcmeta
            if 1 <= depth <= 4:
                return "/".join(segs[:-1]) + "/"
        return ""

    # ── Internal ──────────────────────────────────────────────────────────────

    def _full(self, rel_path: str) -> str:
        """Add the zip prefix to a relative path."""
        return self._prefix + rel_path

    # ── Public API ────────────────────────────────────────────────────────────

    def exists(self, rel_path: str) -> bool:
        full = self._full(rel_path)
        if self._is_zip:
            return full in self._name_set
        return os.path.isfile(os.path.join(self.pack_path, rel_path))

    def read_bytes(self, rel_path: str) -> Optional[bytes]:
        """Return raw bytes from a relative path, or None if unreadable."""
        full = self._full(rel_path)
        if self._is_zip:
            assert self._zf is not None
            try:
                with self._zf.open(full) as fh:
                    return fh.read()
            except (KeyError, zipfile.BadZipFile, Exception):
                return None
        try:
            with open(os.path.join(self.pack_path, rel_path), "rb") as fh:
                return fh.read()
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
            return None

    def read_json(self, rel_path: str) -> Optional[Any]:
        """Leniently parse a JSON file from the pack. Returns None on failure."""
        raw = self.read_bytes(rel_path)
        if raw is None:
            return None
        try:
            return parse_json_lenient(raw)
        except Exception as exc:
            self._log.debug(f"JSON error [{rel_path}]: {exc}")
            return None

    def list_files(self, prefix_path: str) -> Iterator[str]:
        """
        Yield relative paths of all FILES under prefix_path.
        Paths use forward slashes and do not start with '/'.

        FIX vs v2: uses explicit `is not None` check for `_name_set`
        instead of `_name_set or []` (which would trigger on empty set).
        """
        prefix_path = prefix_path.rstrip("/") + "/"
        if self._is_zip:
            full_pf  = self._full(prefix_path)
            pref_len = len(self._prefix)
            # _name_set is always a set (never None) in v3
            for n in self._name_set:
                if n.startswith(full_pf) and not n.endswith("/"):
                    yield n[pref_len:]
        else:
            base = os.path.join(self.pack_path, prefix_path)
            if not os.path.isdir(base):
                return
            for root, _dirs, files in os.walk(base):
                for fname in files:
                    abs_p = os.path.join(root, fname)
                    rel   = os.path.relpath(abs_p, self.pack_path)
                    yield rel.replace("\\", "/")

    def get_namespaces(self) -> List[str]:
        """Return all namespace names found under assets/."""
        seen: Set[str] = set()
        for f in self.list_files("assets/"):
            parts = f.split("/")
            if len(parts) >= 2 and parts[0] == "assets" and parts[1]:
                seen.add(parts[1])
        return sorted(seen)

    def get_pack_format(self) -> Optional[int]:
        data = self.read_json("pack.mcmeta")
        if isinstance(data, dict):
            pack = data.get("pack", {})
            if isinstance(pack, dict):
                fmt = pack.get("pack_format")
                if isinstance(fmt, int):
                    return fmt
        return None

    def has_new_item_format(self, ns: str = "minecraft") -> bool:
        """
        Detect 1.21.4+ new item model system.
        In pack_format >= 46, item models live in assets/<ns>/items/
        as component-based JSON, without 'overrides' arrays.
        """
        for _ in self.list_files(f"assets/{ns}/items/"):
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════════
# §6  MODEL + TEXTURE RESOLVER
# ══════════════════════════════════════════════════════════════════════════════

class ModelCache:
    """
    Caches raw model JSON and fully-resolved texture dicts.
    Shared across all items in a session to avoid re-reading parent chains.
    """

    def __init__(self, reader: JavaPackReader, log: Logger) -> None:
        self._reader    = reader
        self._log       = log
        self._raw:      Dict[str, Optional[Any]]  = {}
        self._textures: Dict[str, Dict[str, str]] = {}

    def get_raw(self, pack_path: str) -> Optional[Any]:
        if pack_path not in self._raw:
            self._raw[pack_path] = self._reader.read_json(pack_path)
        return self._raw[pack_path]

    def get_textures(self, model_ref: str) -> Dict[str, str]:
        """Walk the parent chain and return a fully-resolved texture dict."""
        pack_path = model_to_pack_path(model_ref)
        if pack_path not in self._textures:
            self._textures[pack_path] = self._resolve(model_ref, frozenset(), 0)
        return self._textures[pack_path]

    def _resolve(
        self,
        model_ref: str,
        visited:   frozenset,
        depth:     int,
    ) -> Dict[str, str]:
        """
        Recursive parent-chain walker.
        Uses frozenset for visited to avoid mutable-default-arg pitfalls.
        """
        if depth >= MAX_PARENT_DEPTH:
            self._log.debug(f"Max parent depth ({MAX_PARENT_DEPTH}) at '{model_ref}'")
            return {}

        norm = normalise_ns(model_ref)
        if norm in _BUILTIN_PARENTS or model_ref in _BUILTIN_PARENTS:
            return {}

        pack_path = model_to_pack_path(model_ref)
        if pack_path in visited:
            self._log.debug(f"Circular parent ref: '{model_ref}'")
            return {}

        visited = visited | {pack_path}

        data = self.get_raw(pack_path)
        if not isinstance(data, dict):
            self._log.debug(f"Model not found or invalid: '{pack_path}'")
            return {}

        textures: Dict[str, str] = {}

        # ① Parent (lower priority — child overrides)
        parent = data.get("parent")
        if parent and isinstance(parent, str):
            textures.update(self._resolve(parent, visited, depth + 1))

        # ② Own textures override parent
        own = data.get("textures")
        if isinstance(own, dict):
            for k, v in own.items():
                if isinstance(v, str):
                    textures[k] = v

        # ③ Resolve '#variable' references until stable
        for _pass in range(MAX_VAR_PASSES):
            changed = False
            for k in list(textures):
                v = textures[k]
                if not (isinstance(v, str) and v.startswith("#")):
                    continue
                ref = v[1:]
                target = textures.get(ref)
                if isinstance(target, str) and not target.startswith("#"):
                    textures[k] = target
                    changed = True
            if not changed:
                break

        return textures


def pick_best_texture(textures: Dict[str, str]) -> Optional[str]:
    """
    Choose the most representative texture reference from a resolved dict.
    Follows TEXTURE_KEY_PRIORITY, then falls back to the first non-variable value.
    Returns None if no usable texture found.
    """
    # Priority-ordered lookup
    for key in TEXTURE_KEY_PRIORITY:
        val = textures.get(key)
        if val and isinstance(val, str) and not val.startswith("#"):
            return val
    # Fallback: any non-variable value
    for val in textures.values():
        if isinstance(val, str) and not val.startswith("#"):
            return val
    return None


# ══════════════════════════════════════════════════════════════════════════════
# §7  PREDICATE PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_predicate_entry(
    item_name:     str,
    override:      Any,
    cache:         ModelCache,
    log:           Logger,
    sprite_prefix: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Parse one entry from an item model's 'overrides' array.

    Returns a dict with public keys (custom_model_data, damage_predicate,
    unbreakable, sprite) plus private metadata keys prefixed with '_':
      _path_hash, _model_ref, _tex_source, _fallback

    Returns None if this entry should be skipped.
    """
    if not isinstance(override, dict):
        return None

    predicate: Any = override.get("predicate")
    model_ref: Any = override.get("model")

    if not isinstance(predicate, dict):
        return None
    if not isinstance(model_ref, str) or not model_ref.strip():
        return None

    model_ref = model_ref.strip()

    has_cmd     = "custom_model_data" in predicate
    has_damage  = "damage"            in predicate
    has_damaged = "damaged"           in predicate

    if not (has_cmd or has_damage or has_damaged):
        if log.verbose:
            unsupported = set(predicate.keys()) & _UNSUPPORTED_PREDICATES
            unknown     = set(predicate.keys()) - _UNSUPPORTED_PREDICATES - {"custom_model_data", "damage", "damaged"}
            if unsupported:
                log.debug(f"[{item_name}] Skip: unsupported predicates {sorted(unsupported)} ({model_ref})")
            if unknown:
                log.debug(f"[{item_name}] Skip: unknown predicates {sorted(unknown)} ({model_ref})")
        return None

    # ── custom_model_data ─────────────────────────────────────────────────────
    cmd: Optional[int] = None
    if has_cmd:
        raw_cmd = predicate["custom_model_data"]
        try:
            cmd = int(raw_cmd)
        except (TypeError, ValueError):
            log.warn(f"[{item_name}] Invalid custom_model_data={raw_cmd!r} — entry skipped")
            return None
        if cmd < 0:
            log.warn(f"[{item_name}] Negative custom_model_data={cmd} — entry skipped")
            return None

    # ── damage ────────────────────────────────────────────────────────────────
    damage: Optional[int] = None
    if has_damage:
        try:
            raw_f = float(predicate["damage"])
        except (TypeError, ValueError):
            log.warn(f"[{item_name}] Invalid damage value {predicate['damage']!r} — treating as 0.0")
            raw_f = 0.0

        if not (0.0 <= raw_f <= 1.0):
            log.warn(f"[{item_name}] damage={raw_f:.4f} out of range [0, 1] — clamped")
            raw_f = max(0.0, min(1.0, raw_f))

        max_dur = JAVA_MAX_DAMAGE.get(item_name)
        if max_dur is None:
            # Unknown item — warn once (check via a flag on the log)
            log.debug(f"[{item_name}] Not in durability table; defaulting max_dur=1")
            max_dur = 1
        if max_dur > 0:
            damage = math.ceil(raw_f * max_dur)
        else:
            log.debug(f"[{item_name}] max_dur=0 (non-damageable) — damage predicate skipped")

    # ── damaged → unbreakable ─────────────────────────────────────────────────
    # Java semantics: damaged=0 → item is UNBREAKABLE (Bedrock: Unbreakable:true)
    #                 damaged=1 → item CAN be damaged (default, no flag needed)
    unbreakable: Optional[bool] = None
    if has_damaged:
        dv = predicate.get("damaged")
        # Accept: 0, False, "0", "false"
        if dv == 0 or dv is False or str(dv).lower() in ("0", "false"):
            unbreakable = True

    # ── Resolve sprite texture ────────────────────────────────────────────────
    textures  = cache.get_textures(model_ref)
    best_tex  = pick_best_texture(textures)
    fallback  = False

    if best_tex:
        sprite = java_tex_to_bedrock_sprite(best_tex, sprite_prefix)
    else:
        # Derive sprite from the model reference path as last resort
        _, mp = split_ns(model_ref)
        mp    = mp.replace("\\", "/")
        segs  = mp.split("/")
        if sprite_prefix:
            sprite = f"{sprite_prefix.rstrip('/')}/{segs[-1]}"
        elif segs and segs[0] == "item":
            sprite = "textures/items/" + "/".join(segs[1:])
        elif segs and segs[0] == "block":
            sprite = "textures/blocks/" + "/".join(segs[1:])
        else:
            sprite = "textures/items/" + "/".join(segs)
        log.debug(f"[{item_name}] No texture for '{model_ref}' — fallback: {sprite}")
        fallback = True

    # Sanitise the sprite path
    sprite = sanitise_sprite_path(sprite)

    # ── Build entry ───────────────────────────────────────────────────────────
    entry: Dict[str, Any] = {}
    if cmd         is not None: entry["custom_model_data"] = cmd
    if damage      is not None: entry["damage_predicate"]  = damage
    if unbreakable is True:     entry["unbreakable"]       = True
    entry["sprite"] = sprite

    # Internal metadata (stripped before JSON output; kept for report/debug)
    entry["_path_hash"]  = compute_path_hash(item_name, cmd, damage, unbreakable)
    entry["_model_ref"]  = model_ref
    entry["_tex_source"] = best_tex or "<fallback>"
    entry["_fallback"]   = fallback

    return entry


# ══════════════════════════════════════════════════════════════════════════════
# §8  VALIDATORS & CHECKERS
# ══════════════════════════════════════════════════════════════════════════════

class BedrockRPValidator:
    """
    Verify that sprite paths in sprites.json exist inside a Bedrock Resource Pack
    (.mcpack, .zip, or unpacked directory).
    """

    def __init__(self, path: str, log: Logger) -> None:
        self._log   = log
        self._files: Set[str] = set()
        self._load(path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            self._log.warn(f"Bedrock RP not found: {path}")
            return
        count = 0
        if os.path.isfile(path) and zipfile.is_zipfile(path):
            with JavaPackReader(path, self._log) as rdr:
                for f in rdr.list_files("textures/"):
                    self._files.add(f.lower())
                    count += 1
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                for fname in files:
                    rel = os.path.relpath(
                        os.path.join(root, fname), path
                    ).replace("\\", "/").lower()
                    self._files.add(rel)
                    count += 1
        self._log.debug(f"Bedrock RP indexed {count} texture files")

    def check(self, sprite_path: str) -> bool:
        """True if sprite_path exists (with or without common extensions)."""
        sp = sprite_path.lower().lstrip("/")
        return any(
            f"{sp}{ext}" in self._files or sp in self._files
            for ext in ("", ".png", ".tga", ".jpg", ".jpeg")
        )

    def validate_all(
        self, raw: Dict[str, List[Dict[str, Any]]]
    ) -> Tuple[int, int]:
        """Returns (ok_count, missing_count)."""
        ok = missing = 0
        for item, entries in raw.items():
            for e in entries:
                sp = e.get("sprite", "")
                if self.check(sp):
                    ok += 1
                else:
                    missing += 1
                    self._log.warn(f"Bedrock RP missing: '{sp}'  [{item}]")
        return ok, missing


def check_duplicates(
    result: Dict[str, List[Dict[str, Any]]], log: Logger
) -> int:
    """
    Detect duplicate (cmd, dmg, unbreakable) combos within the same item.
    Returns total duplicate count.
    """
    total = 0
    for item, entries in result.items():
        seen: Dict[Tuple[Any, ...], int] = {}
        for idx, e in enumerate(entries):
            key: Tuple[Any, ...] = (
                e.get("custom_model_data"),
                e.get("damage_predicate"),
                e.get("unbreakable"),
            )
            if key in seen:
                log.warn(
                    f"[{item}] Duplicate predicate {key} "
                    f"at positions #{seen[key]+1} and #{idx+1} — "
                    "second entry ignored by converter"
                )
                total += 1
            else:
                seen[key] = idx
    return total


def check_duplicate_sprites(
    result: Dict[str, List[Dict[str, Any]]], log: Logger
) -> int:
    """
    Warn when the SAME sprite path is used for two or more different
    (item, predicate) combos — likely a copy-paste error.
    Returns number of collisions found.
    """
    sprite_map: Dict[str, List[str]] = defaultdict(list)
    for item, entries in result.items():
        for e in entries:
            sp = e.get("sprite", "")
            cmd = e.get("custom_model_data")
            label = f"{item}" + (f"[cmd={cmd}]" if cmd is not None else "")
            sprite_map[sp].append(label)

    collisions = 0
    for sprite, owners in sprite_map.items():
        if len(owners) > 1:
            log.warn(
                f"Sprite '{sprite}' shared by {len(owners)} entries: "
                + ", ".join(owners[:5])
                + (" …" if len(owners) > 5 else "")
            )
            collisions += 1
    return collisions


# ══════════════════════════════════════════════════════════════════════════════
# §9  MERGE HELPER
# ══════════════════════════════════════════════════════════════════════════════

def load_existing_sprites(path: str, log: Logger) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load an existing sprites.json file.
    Returns an empty dict on any failure.
    """
    if not os.path.isfile(path):
        log.warn(f"--merge target not found: {path} — starting fresh")
        return {}
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = parse_json_lenient(raw)
        if not isinstance(data, dict):
            log.warn(f"--merge target is not a JSON object: {path}")
            return {}
        log.info(f"Merging with existing sprites.json: {path} ({len(data)} items)")
        return data  # type: ignore[return-value]
    except Exception as exc:
        log.warn(f"Cannot load --merge target {path}: {exc}")
        return {}


def merge_sprites(
    base:     Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    log:      Logger,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Merge `incoming` into `base`.
    Strategy: new entries are APPENDED; existing (item, predicate-key) combos
    are OVERWRITTEN by the incoming version.
    """
    result: Dict[str, List[Dict[str, Any]]] = {
        k: list(v) for k, v in base.items()
    }

    for item, new_entries in incoming.items():
        if item not in result:
            result[item] = list(new_entries)
            continue

        existing = result[item]
        for ne in new_entries:
            ne_key = (ne.get("custom_model_data"),
                      ne.get("damage_predicate"),
                      ne.get("unbreakable"))
            # Replace existing entry with matching key, or append
            replaced = False
            for i, ee in enumerate(existing):
                ee_key = (ee.get("custom_model_data"),
                          ee.get("damage_predicate"),
                          ee.get("unbreakable"))
                if ee_key == ne_key:
                    existing[i] = ne
                    replaced = True
                    break
            if not replaced:
                existing.append(ne)

        result[item] = existing

    merged_items = len(result) - len(base)
    if merged_items:
        log.info(f"Merge added {merged_items} new item(s)")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# §10  ZIP INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def inject_into_zip(
    pack_path: str, json_str: str, prefix: str, log: Logger
) -> Optional[str]:
    """
    Create a NEW zip that is a copy of pack_path with sprites.json at its root.

    Uses streaming copy to handle large packs without loading everything into RAM.
    Returns the output path, or None on failure.
    """
    base, ext  = os.path.splitext(pack_path)
    out_path   = base + "_with_sprites" + ext
    inner_name = prefix + "sprites.json"

    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        with zipfile.ZipFile(pack_path, "r") as zin:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zout:
                for info in zin.infolist():
                    if info.filename == inner_name:
                        log.debug("Replacing existing sprites.json in zip")
                        continue
                    # Stream-copy to avoid loading the whole entry into memory
                    with zin.open(info) as src:
                        with zout.open(info, "w") as dst:
                            shutil.copyfileobj(src, dst)

                # Write sprites.json
                zout.writestr(
                    zipfile.ZipInfo(inner_name),
                    json_str.encode("utf-8"),
                )
        # Atomic replace
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.move(tmp, out_path)
        return out_path
    except Exception as exc:
        log.err(f"Inject failed: {exc}")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


# ══════════════════════════════════════════════════════════════════════════════
# §11  REPORT WRITER
# ══════════════════════════════════════════════════════════════════════════════

def write_report(
    report_path:  str,
    pack_path:    str,
    pack_format:  Optional[int],
    namespaces:   List[str],
    sorted_raw:   Dict[str, List[Dict[str, Any]]],
    final:        Dict[str, List[Dict[str, Any]]],
    stats:        Dict[str, Any],
    warnings:     List[str],
    errors:       List[str],
    elapsed_s:    float = 0.0,
) -> None:
    """Write a detailed plain-text report for debugging / auditing."""
    SEP  = "=" * 80
    SEP2 = "-" * 40
    lines: List[str] = []
    w = lines.append

    mc_ver = PACK_FORMAT_TO_MC.get(pack_format or 0, "unknown")

    w(SEP)
    w("  sprites_generator.py  DETAILED REPORT")
    w(f"  Version      : {VERSION}")
    w(f"  Pack         : {pack_path}")
    w(f"  Pack format  : {pack_format or 'unknown'}  (≈ Minecraft {mc_ver})")
    w(f"  Namespaces   : {', '.join(namespaces)}")
    w(f"  Elapsed      : {elapsed_s:.2f}s")
    w(SEP); w("")

    w("STATISTICS"); w(SEP2)
    stat_labels: List[Tuple[str, str]] = [
        ("items_found",    "Items with sprite entries"),
        ("models",         "Item models with overrides"),
        ("overrides",      "Total sprite entries"),
        ("no_override",    "Models without overrides"),
        ("skipped_json",   "Unparseable JSON files"),
        ("fallback_tex",   "Entries using fallback texture"),
        ("dupes",          "Duplicate predicate entries"),
        ("sprite_collide", "Shared sprite path collisions"),
        ("bdrc_ok",        "Sprite paths OK (Bedrock RP)"),
        ("bdrc_missing",   "Sprite paths MISSING (Bedrock RP)"),
    ]
    for key, label in stat_labels:
        v = stats.get(key)
        if v is not None:
            w(f"  {label:<40}: {v}")
    w("")

    if errors:
        w("ERRORS"); w(SEP2)
        for e in errors:
            w(f"  [✘] {e}")
        w("")

    if warnings:
        w("WARNINGS"); w(SEP2)
        for ww in warnings:
            w(f"  [!] {ww}")
        w("")

    w("SPRITE ENTRIES (with debug metadata)"); w(SEP2)
    for item, entries in sorted(sorted_raw.items()):
        fallbacks = sum(1 for e in entries if e.get("_fallback"))
        w(f"\n  [{item}]  {len(entries)} entries"
          + (f"  ({fallbacks} fallback)" if fallbacks else ""))
        for i, e in enumerate(entries, 1):
            predicates = []
            if "custom_model_data" in e: predicates.append(f"cmd={e['custom_model_data']}")
            if "damage_predicate"  in e: predicates.append(f"dmg={e['damage_predicate']}")
            if e.get("unbreakable"):     predicates.append("unbreakable")
            pred_str = ", ".join(predicates) or "(none)"
            w(f"    #{i:3d}  predicates : {pred_str}")
            w(f"          sprite     : {e.get('sprite', '?')}")
            w(f"          model      : {e.get('_model_ref', '?')}")
            w(f"          tex_src    : {e.get('_tex_source', '?')}")
            w(f"          path_hash  : {e.get('_path_hash', '?')}"
              + (" [FALLBACK]" if e.get("_fallback") else ""))

    w(""); w(SEP); w("END OF REPORT")

    os.makedirs(
        os.path.dirname(os.path.abspath(report_path)) or ".",
        exist_ok=True,
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# §12  MAIN GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def _strip_private(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip all internal metadata keys (prefixed '_') from entries."""
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]


def _sort_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort sprite entries stably:
      primary   : custom_model_data ascending (None last)
      secondary : damage_predicate ascending  (None last)
      tertiary  : unbreakable=False before True
    """
    MAX_INT = 10 ** 9
    return sorted(
        entries,
        key=lambda e: (
            e.get("custom_model_data") if e.get("custom_model_data") is not None else MAX_INT,
            e.get("damage_predicate")  if e.get("damage_predicate")  is not None else MAX_INT,
            1 if e.get("unbreakable") else 0,
        ),
    )


def generate_sprites_json(
    pack_path:       str,
    output_path:     Optional[str]       = None,
    merge_path:      Optional[str]       = None,
    bedrock_rp_path: Optional[str]       = None,
    inject:          bool                = False,
    report_path:     Optional[str]       = None,
    item_filter:     Optional[Set[str]]  = None,
    ns_filter:       Optional[Set[str]]  = None,
    sprite_prefix:   str                 = "",
    json_indent:     int                 = 4,
    dry_run:         bool                = False,
    use_color:       bool                = True,
    quiet:           bool                = False,
    verbose:         bool                = False,
) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
    """
    Core generator.

    Parameters
    ----------
    pack_path       : Java RP (.zip or directory).
    output_path     : Write sprites.json here; None → stdout.
    merge_path      : Merge with existing sprites.json at this path.
    bedrock_rp_path : Bedrock RP for sprite-path validation.
    inject          : Embed sprites.json into a copy of the input zip.
    report_path     : Write debug report here.
    item_filter     : Only process these item names.
    ns_filter       : Only process these namespaces.
    sprite_prefix   : Prepend this prefix to all sprite paths.
    json_indent     : JSON indentation (0 = compact single-line).
    dry_run         : Parse/validate but write nothing.
    use_color / quiet / verbose : Logger settings.

    Returns
    -------
    (final_dict, exit_code)  where exit_code is 0/1/2.
    """
    t_start = time.monotonic()
    log = Logger(use_color=use_color, quiet=quiet, verbose=verbose)

    if not quiet:
        log.banner()

    exit_code = 0  # will be set to 1 on error, 2 on warnings-only

    # ── Open pack ─────────────────────────────────────────────────────────────
    log.section("Opening Pack")

    with JavaPackReader(pack_path, log) as reader:
        namespaces  = reader.get_namespaces()
        pack_format = reader.get_pack_format()

        if not namespaces:
            log.err(
                "No 'assets/' directory found. "
                "Is this a valid Java Edition Resource Pack?",
                fatal=True,
            )

        mc_ver = PACK_FORMAT_TO_MC.get(pack_format or 0, "unknown")
        log.info(f"Pack         : {pack_path}")
        log.info(f"Pack format  : {pack_format or 'unknown'}  (≈ MC {mc_ver})")
        log.info(f"Namespaces   : {', '.join(namespaces)}")

        # Warn about new 1.21.4+ item format
        if pack_format and pack_format >= NEW_FORMAT_THRESHOLD:
            if reader.has_new_item_format():
                log.warn(
                    f"Pack format {pack_format} (≥{NEW_FORMAT_THRESHOLD}) uses the "
                    "new 1.21.4 item model system (assets/<ns>/items/) which does NOT "
                    "use 'overrides'. sprites.json cannot be auto-generated from this "
                    "format. Results may be empty or incomplete."
                )

        if item_filter:
            log.info(f"Item filter  : {', '.join(sorted(item_filter))}")
        if ns_filter:
            log.info(f"NS filter    : {', '.join(sorted(ns_filter))}")
        if sprite_prefix:
            log.info(f"Sprite prefix: {sprite_prefix}")
        if dry_run:
            log.info("Dry-run mode: no files will be written")

        cache = ModelCache(reader, log)

        # ── Scan item models ───────────────────────────────────────────────────
        log.section("Scanning Item Models")

        result_raw: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        n_models = n_overrides = n_skip_json = n_no_override = n_fallback = 0

        effective_ns = [
            ns for ns in namespaces
            if ns_filter is None or ns in ns_filter
        ]

        all_model_files: List[Tuple[str, str]] = []  # (namespace, file_path)
        for ns in effective_ns:
            for f in reader.list_files(f"assets/{ns}/models/item/"):
                if f.endswith(".json"):
                    all_model_files.append((ns, f))
        all_model_files.sort(key=lambda x: x[1])

        total_files = len(all_model_files)
        log.info(f"Total item model files: {total_files}")

        for file_idx, (ns, mf) in enumerate(all_model_files):
            log.progress(file_idx + 1, total_files, PurePosixPath(mf).stem)

            item_name = PurePosixPath(mf).stem
            if item_filter and item_name not in item_filter:
                continue

            data = reader.read_json(mf)
            if not isinstance(data, dict):
                n_skip_json += 1
                log.debug(f"Cannot parse: {mf}")
                continue

            overrides = data.get("overrides")
            if not overrides or not isinstance(overrides, list):
                n_no_override += 1
                continue

            n_models += 1
            for ov in overrides:
                entry = parse_predicate_entry(
                    item_name, ov, cache, log, sprite_prefix
                )
                if entry is not None:
                    result_raw[item_name].append(entry)
                    n_overrides += 1
                    if entry.get("_fallback"):
                        n_fallback += 1

        # ── Sort ───────────────────────────────────────────────────────────────
        log.section("Processing & Validating")

        sorted_raw: Dict[str, List[Dict[str, Any]]] = {
            item: _sort_entries(entries)
            for item, entries in sorted(result_raw.items())
        }

        # ── Checks ────────────────────────────────────────────────────────────
        n_dupes    = check_duplicates(sorted_raw, log)
        n_collide  = check_duplicate_sprites(sorted_raw, log)

        # ── Bedrock RP validation ──────────────────────────────────────────────
        bdrc_ok = bdrc_missing = 0
        if bedrock_rp_path:
            log.section("Validating Bedrock RP Sprites")
            val = BedrockRPValidator(bedrock_rp_path, log)
            bdrc_ok, bdrc_missing = val.validate_all(sorted_raw)

        # ── Merge with existing sprites.json ──────────────────────────────────
        if merge_path:
            existing = load_existing_sprites(merge_path, log)
            sorted_raw = merge_sprites(existing, sorted_raw, log)
            # Re-sort after merge
            sorted_raw = {
                item: _sort_entries(entries)
                for item, entries in sorted(sorted_raw.items())
            }

        # ── Clean output (strip _private keys) ────────────────────────────────
        final: Dict[str, List[Dict[str, Any]]] = {
            k: _strip_private(v) for k, v in sorted_raw.items()
        }

        # ── Summary ────────────────────────────────────────────────────────────
        log.section("Summary")
        log.ok(f"Items with overrides   : {len(final)}")
        log.ok(f"Total sprite entries   : {n_overrides}")
        log.info(f"Item models scanned    : {n_models}")
        log.info(f"Models w/o overrides   : {n_no_override}")
        if n_fallback:    log.warn(f"Fallback textures used : {n_fallback}")
        if n_skip_json:   log.warn(f"Unparseable JSON files : {n_skip_json}")
        if n_dupes:       log.warn(f"Duplicate predicates   : {n_dupes}")
        if n_collide:     log.warn(f"Shared sprite paths    : {n_collide}")
        if bedrock_rp_path:
            log.ok(f"Sprite paths OK        : {bdrc_ok}")
            if bdrc_missing: log.warn(f"Sprite paths MISSING   : {bdrc_missing}")
        if not final:
            log.warn(
                "No sprite entries found. "
                "Check that your pack has item models with 'overrides' using "
                "custom_model_data, damage, or damaged predicates."
            )

        # ── JSON output ────────────────────────────────────────────────────────
        indent = json_indent if json_indent > 0 else None
        json_str = json.dumps(final, indent=indent, ensure_ascii=False)

        if not dry_run:
            if output_path:
                out_dir = os.path.dirname(os.path.abspath(output_path))
                os.makedirs(out_dir, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                    if indent:
                        f.write("\n")
                log.ok(f"sprites.json saved     → {output_path}")
            else:
                if not quiet:
                    log.section("sprites.json")
                print(json_str)
        else:
            log.ok("Dry-run: sprites.json NOT written (--dry-run)")

        # ── Inject ────────────────────────────────────────────────────────────
        if inject and not dry_run:
            if not (os.path.isfile(pack_path) and zipfile.is_zipfile(pack_path)):
                log.warn("--inject requires a .zip input file; skipping")
            else:
                injected = inject_into_zip(
                    pack_path, json_str, reader._prefix, log
                )
                if injected:
                    log.ok(f"Injected zip saved     → {injected}")

        # ── Report ────────────────────────────────────────────────────────────
        elapsed = time.monotonic() - t_start
        if report_path and not dry_run:
            write_report(
                report_path  = report_path,
                pack_path    = pack_path,
                pack_format  = pack_format,
                namespaces   = namespaces,
                sorted_raw   = sorted_raw,
                final        = final,
                stats        = {
                    "items_found":    len(final),
                    "models":         n_models,
                    "overrides":      n_overrides,
                    "no_override":    n_no_override,
                    "skipped_json":   n_skip_json,
                    "fallback_tex":   n_fallback,
                    "dupes":          n_dupes,
                    "sprite_collide": n_collide,
                    "bdrc_ok":        bdrc_ok,
                    "bdrc_missing":   bdrc_missing,
                },
                warnings  = log.all_warnings(),
                errors    = log.all_errors(),
                elapsed_s = elapsed,
            )
            log.ok(f"Report saved           → {report_path}")

        log.info(f"Done in {elapsed:.2f}s")

        # ── Exit code ─────────────────────────────────────────────────────────
        if log.error_count:
            exit_code = 1
        elif log.warning_count:
            exit_code = 2

    return final, exit_code


# ══════════════════════════════════════════════════════════════════════════════
# §13  BUILT-IN SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

def run_self_tests() -> int:
    """
    Run built-in unit + integration tests.
    Returns 0 on success, 1 on failure.
    """
    import io, traceback

    RED   = "\033[31m"
    GREEN = "\033[32m"
    RESET = "\033[0m"
    passed = failed = 0

    def ok(name: str) -> None:
        nonlocal passed
        passed += 1
        print(f"  {GREEN}PASS{RESET}  {name}")

    def fail(name: str, reason: str) -> None:
        nonlocal failed
        failed += 1
        print(f"  {RED}FAIL{RESET}  {name}: {reason}")

    def check(name: str, condition: bool, msg: str = "") -> None:
        if condition:
            ok(name)
        else:
            fail(name, msg or "assertion failed")

    print("\n── §A  JSON Parser ────────────────────────────────────────────")

    for desc, raw in [
        ("plain",               b'{"a":1}'),
        ("BOM",                 b'\xef\xbb\xbf{"a":1}'),
        ("trailing comma",      b'{"a":1,}'),
        ("line comment",        b'// hi\n{"a":1}'),
        ("block comment",       b'{"a":/* x */1}'),
        ("string with /**/",    b'{"url":"http://a/*b*/c"}'),
        ("nested comment",      b'{"k":"v"} // comment'),
        ("latin-1",             "{'a':1}".encode("latin-1").replace(b"'", b'"')),
    ]:
        try:
            r = parse_json_lenient(raw)
            check(f"json/{desc}", isinstance(r, dict))
        except Exception as e:
            fail(f"json/{desc}", str(e))

    # Ensure block comment inside string is NOT stripped
    r2 = parse_json_lenient(b'{"k": "path/*/to"}')
    check("json/string_asterisk_preserved",
          isinstance(r2, dict) and r2.get("k") == "path/*/to",
          f"got {r2!r}")

    print("\n── §B  Path Utilities ─────────────────────────────────────────")

    cases_ns = [
        ("minecraft:item/foo",  ("minecraft", "item/foo")),
        ("item/foo",            ("minecraft", "item/foo")),
        ("mypack:custom/bar",   ("mypack",    "custom/bar")),
        (" ns : path ",         ("ns",        "path")),
    ]
    for ref, expected in cases_ns:
        check(f"split_ns({ref!r})", split_ns(ref) == expected, f"got {split_ns(ref)}")

    cases_tex = [
        ("minecraft:item/custom/sword",   "textures/items/custom/sword"),
        ("minecraft:block/stone",          "textures/blocks/stone"),
        ("minecraft:entity/pig",           "textures/entity/pig"),
        ("minecraft:gui/sprites/hud/a",    "textures/gui/hud/a"),
        ("minecraft:painting/aztec",       "textures/painting/aztec"),
        ("minecraft:item/foo.png",         "textures/items/foo"),
        ("minecraft:item/foo.TGA",         "textures/items/foo"),
        ("mypack:custom/icon",             "textures/custom/icon"),
        ("minecraft:item/foo//bar",        "textures/items/foo/bar"),  # sanitise
    ]
    for inp, expected in cases_tex:
        got = sanitise_sprite_path(java_tex_to_bedrock_sprite(inp))
        check(f"tex2sprite({inp!r})", got == expected, f"expected {expected!r}, got {got!r}")

    # sprite_prefix override
    got_pf = sanitise_sprite_path(
        java_tex_to_bedrock_sprite("minecraft:item/sword", "textures/custom_items")
    )
    check("sprite_prefix", got_pf == "textures/custom_items/sword",
          f"got {got_pf!r}")

    print("\n── §C  path_hash ──────────────────────────────────────────────")

    h1 = compute_path_hash("leather", 1, None, None)
    h2 = compute_path_hash("leather", 1, None, None)
    h3 = compute_path_hash("leather", 2, None, None)
    check("hash_length",        len(h1) == 7)
    check("hash_hex",           all(c in "0123456789abcdef" for c in h1))
    check("hash_deterministic", h1 == h2)
    check("hash_distinct",      h1 != h3)

    print("\n── §D  Durability math ────────────────────────────────────────")

    # leather_helmet max=55, damage=0.5 → ceil(27.5)=28
    check("durability_ceil",
          math.ceil(0.5 * JAVA_MAX_DAMAGE["leather_helmet"]) == 28)
    # diamond_axe max=1561, damage=0.5 → ceil(780.5)=781
    check("durability_diamond_axe",
          math.ceil(0.5 * JAVA_MAX_DAMAGE["diamond_axe"]) == 781)

    print("\n── §E  Integration (fake pack) ────────────────────────────────")

    def make_zip(files: Dict[str, str]) -> str:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(buf.read())
        return tmp

    # ── E1: basic custom_model_data ──────────────────────────────────────────
    tmp1 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/leather.json": json.dumps({
            "parent": "item/generated",
            "textures": {"layer0": "minecraft:item/leather"},
            "overrides": [
                {"predicate": {"custom_model_data": 1}, "model": "test:item/sword"},
                {"predicate": {"custom_model_data": 2}, "model": "test:item/shield"},
                {"predicate": {"pulling": 1},           "model": "item/bow_pulling_0"},
            ],
        }),
        "assets/test/models/item/sword.json": json.dumps({
            "textures": {"layer0": "test:item/sword_tex"},
        }),
        "assets/test/models/item/shield.json": json.dumps({
            "textures": {"layer0": "test:item/shield_tex"},
        }),
    })
    result1, ec1 = generate_sprites_json(tmp1, quiet=True)
    os.unlink(tmp1)
    check("E1/leather_found",    "leather" in result1)
    check("E1/two_entries",      len(result1.get("leather", [])) == 2)
    check("E1/bow_excluded",     "bow" not in result1)
    check("E1/cmd_1",            any(e.get("custom_model_data") == 1 for e in result1.get("leather", [])))
    check("E1/sprite_items",     all(e["sprite"].startswith("textures/items/") for e in result1.get("leather", [])))
    check("E1/exitcode",         ec1 == 0, f"got exit_code={ec1}")

    # ── E2: nested zip (GitHub-style) ────────────────────────────────────────
    tmp2 = make_zip({
        "MyPack-main/pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "MyPack-main/assets/minecraft/models/item/iron_sword.json": json.dumps({
            "overrides": [
                {"predicate": {"custom_model_data": 5}, "model": "test:item/mysword"},
            ],
        }),
        "MyPack-main/assets/test/models/item/mysword.json": json.dumps({
            "textures": {"layer0": "test:item/mysword_tex"},
        }),
    })
    result2, _ = generate_sprites_json(tmp2, quiet=True)
    os.unlink(tmp2)
    check("E2/nested_zip",    "iron_sword" in result2)
    check("E2/cmd_5",         result2.get("iron_sword", [{}])[0].get("custom_model_data") == 5)
    check("E2/sprite_ok",     result2.get("iron_sword", [{}])[0].get("sprite") == "textures/items/mysword_tex")

    # ── E3: damage + unbreakable ──────────────────────────────────────────────
    tmp3 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/diamond_axe.json": json.dumps({
            "overrides": [
                {"predicate": {"damage": 0.5, "damaged": 0}, "model": "test:item/axe_dmg"},
                {"predicate": {"damage": 0.9},               "model": "test:item/axe_broken"},
            ],
        }),
        "assets/test/models/item/axe_dmg.json":    json.dumps({"textures": {"layer0": "test:item/axe_dmg_tex"}}),
        "assets/test/models/item/axe_broken.json": json.dumps({"textures": {"layer0": "test:item/axe_broken_tex"}}),
    })
    result3, _ = generate_sprites_json(tmp3, quiet=True)
    os.unlink(tmp3)
    axe = result3.get("diamond_axe", [])
    check("E3/two_axe_entries",   len(axe) == 2)
    unbreak = [e for e in axe if e.get("unbreakable")]
    check("E3/unbreakable_set",   len(unbreak) == 1)
    check("E3/dmg_math",          unbreak[0].get("damage_predicate") == 781,
          f"got {unbreak[0].get('damage_predicate')!r}")

    # ── E4: #variable texture chain resolution ────────────────────────────────
    tmp4 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/shears.json": json.dumps({
            "overrides": [
                {"predicate": {"custom_model_data": 7}, "model": "test:item/chain"},
            ],
        }),
        "assets/test/models/item/chain.json": json.dumps({
            "parent": "test:item/chain_parent",
            "textures": {"layer0": "#base"},
        }),
        "assets/test/models/item/chain_parent.json": json.dumps({
            "parent": "item/handheld",
            "textures": {"base": "test:item/resolved_tex"},
        }),
    })
    result4, _ = generate_sprites_json(tmp4, quiet=True)
    os.unlink(tmp4)
    check("E4/var_resolved",
          result4.get("shears", [{}])[0].get("sprite") == "textures/items/resolved_tex",
          f"got {result4.get('shears', [{}])[0].get('sprite')!r}")

    # ── E5: BOM + trailing comma ──────────────────────────────────────────────
    bom_mcmeta = b"\xef\xbb\xbf" + json.dumps({"pack": {"pack_format": 15,}}).encode()
    tmp5 = make_zip({
        "pack.mcmeta": bom_mcmeta.decode("latin-1"),
        "assets/minecraft/models/item/golden_sword.json": json.dumps({
            "overrides": [
                {"predicate": {"custom_model_data": 99}, "model": "test:item/g99"},
            ],
        }),
        "assets/test/models/item/g99.json": json.dumps(
            {"textures": {"layer0": "test:item/g99_tex"}}
        ),
    })
    # Write the BOM bytes properly
    import io as _io
    buf5 = _io.BytesIO()
    with zipfile.ZipFile(buf5, "w") as zf5:
        zf5.writestr("pack.mcmeta", bom_mcmeta)
        zf5.writestr("assets/minecraft/models/item/golden_sword.json",
                     json.dumps({"overrides": [
                         {"predicate": {"custom_model_data": 99}, "model": "test:item/g99"},
                     ]}).encode())
        zf5.writestr("assets/test/models/item/g99.json",
                     json.dumps({"textures": {"layer0": "test:item/g99_tex"}}).encode())
    buf5.seek(0)
    fd5, tmp5b = tempfile.mkstemp(suffix=".zip")
    os.close(fd5)
    with open(tmp5b, "wb") as f5:
        f5.write(buf5.read())
    result5, _ = generate_sprites_json(tmp5b, quiet=True)
    os.unlink(tmp5b)
    check("E5/BOM_pack",
          "golden_sword" in result5 and result5["golden_sword"][0]["custom_model_data"] == 99)

    # ── E6: --inject ──────────────────────────────────────────────────────────
    tmp6 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/bow.json": json.dumps({
            "overrides": [{"predicate": {"custom_model_data": 3}, "model": "test:item/t3"}],
        }),
        "assets/test/models/item/t3.json": json.dumps(
            {"textures": {"layer0": "test:item/t3_tex"}}
        ),
    })
    fd6o, out6 = tempfile.mkstemp(suffix=".json")
    os.close(fd6o)
    _, _ = generate_sprites_json(tmp6, output_path=out6, inject=True, quiet=True)
    base6, ext6 = os.path.splitext(tmp6)
    injected6 = base6 + "_with_sprites" + ext6
    check("E6/inject_exists", os.path.exists(injected6))
    if os.path.exists(injected6):
        with zipfile.ZipFile(injected6) as zf6:
            check("E6/sprites_in_zip", "sprites.json" in zf6.namelist())
            if "sprites.json" in zf6.namelist():
                content6 = json.loads(zf6.read("sprites.json"))
                check("E6/bow_in_sprites", "bow" in content6)
        os.unlink(injected6)
    os.unlink(tmp6)
    os.unlink(out6)

    # ── E7: merge ─────────────────────────────────────────────────────────────
    existing_sprites = {"leather": [{"custom_model_data": 9, "sprite": "textures/items/old"}]}
    fd7, merge7 = tempfile.mkstemp(suffix=".json")
    os.close(fd7)
    with open(merge7, "w") as fm:
        json.dump(existing_sprites, fm)
    tmp7 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/leather.json": json.dumps({
            "overrides": [{"predicate": {"custom_model_data": 1}, "model": "test:item/new"}],
        }),
        "assets/test/models/item/new.json": json.dumps(
            {"textures": {"layer0": "test:item/new_tex"}}
        ),
    })
    result7, _ = generate_sprites_json(tmp7, merge_path=merge7, quiet=True)
    os.unlink(tmp7); os.unlink(merge7)
    leather7 = result7.get("leather", [])
    cmds7 = {e.get("custom_model_data") for e in leather7}
    check("E7/merge_keeps_old", 9 in cmds7, f"cmds={cmds7}")
    check("E7/merge_adds_new",  1 in cmds7, f"cmds={cmds7}")

    # ── E8: --filter ──────────────────────────────────────────────────────────
    tmp8 = make_zip({
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 15}}),
        "assets/minecraft/models/item/iron_axe.json": json.dumps({
            "overrides": [{"predicate": {"custom_model_data": 1}, "model": "test:item/a"}],
        }),
        "assets/minecraft/models/item/iron_sword.json": json.dumps({
            "overrides": [{"predicate": {"custom_model_data": 2}, "model": "test:item/b"}],
        }),
        "assets/test/models/item/a.json": json.dumps({"textures": {"layer0": "test:item/a"}}),
        "assets/test/models/item/b.json": json.dumps({"textures": {"layer0": "test:item/b"}}),
    })
    result8, _ = generate_sprites_json(tmp8, item_filter={"iron_axe"}, quiet=True)
    os.unlink(tmp8)
    check("E8/filter_includes", "iron_axe"   in result8)
    check("E8/filter_excludes", "iron_sword" not in result8)

    # ── E9: sort order ────────────────────────────────────────────────────────
    entries_unsorted = [
        {"custom_model_data": 3, "sprite": "x"},
        {"custom_model_data": 1, "sprite": "x"},
        {"custom_model_data": 2, "sprite": "x"},
    ]
    sorted_e = _sort_entries(entries_unsorted)
    cmds_sorted = [e["custom_model_data"] for e in sorted_e]
    check("E9/sort_order", cmds_sorted == [1, 2, 3], f"got {cmds_sorted}")

    # ── E10: sanitise_sprite_path ─────────────────────────────────────────────
    cases_san = [
        ("textures//items//foo",   "textures/items/foo"),
        ("/textures/items/bar/",   "textures/items/bar"),
        ("Textures\\Items\\Baz",   "textures/items/baz"),
        ("TEXTURES/ITEMS/UPPER",   "textures/items/upper"),
    ]
    for inp, expected in cases_san:
        got = sanitise_sprite_path(inp)
        check(f"sanitise({inp!r})", got == expected, f"got {got!r}")

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n  ── Results: {passed} passed, {failed} failed ──")
    return 0 if failed == 0 else 1


# ══════════════════════════════════════════════════════════════════════════════
# §14  CLI
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sprites_generator.py",
        description=(
            f"sprites_generator.py v{VERSION}\n"
            "Generate sprites.json for AZPixel-Team/Java2Bedrock converter.\n"
            "Supports: custom_model_data · damage · damaged/unbreakable"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
SPRITES.JSON FORMAT  (place at the ROOT of your Java RP .zip)
─────────────────────────────────────────────────────────────
{
    "leather": [
        {"custom_model_data": 1, "sprite": "textures/items/custom/tex1"},
        {"custom_model_data": 2, "sprite": "textures/items/custom/tex2"}
    ],
    "diamond_axe": [
        {"damage_predicate": 2, "unbreakable": true,
         "sprite": "textures/items/custom/axe_skin"}
    ]
}

KEY NOTES
─────────
• sprite paths reference textures INSIDE the Bedrock RP (no file extension).
• Review and adjust sprite paths to match your Bedrock RP layout before use.
• Place sprites.json at the ROOT of your Java RP zip before passing to converter.sh.

EXAMPLES
────────
  # Quick preview to stdout
  python sprites_generator.py MyPack.zip

  # Save to file with verbose debug
  python sprites_generator.py MyPack.zip -o sprites.json -v

  # Save + report + validate against Bedrock RP
  python sprites_generator.py MyPack.zip -o sprites.json \
      --report debug.txt -b MyBedrock.mcpack

  # Inject directly into a copy of the zip (ready for converter.sh)
  python sprites_generator.py MyPack.zip -o sprites.json --inject

  # Merge with existing sprites.json
  python sprites_generator.py MyPack.zip -o sprites.json -m old_sprites.json

  # Filter to specific items / namespaces
  python sprites_generator.py MyPack.zip -o sprites.json \
      --filter leather,diamond_axe --namespace minecraft,mypack

  # Custom sprite prefix (all sprites will be textures/custom/<leaf>)
  python sprites_generator.py MyPack.zip -o sprites.json \
      --sprite-prefix textures/custom

  # Dry-run (parse and validate without writing anything)
  python sprites_generator.py MyPack.zip --dry-run -v

  # Run built-in unit tests
  python sprites_generator.py --self-test

  # Unpacked directory
  python sprites_generator.py path/to/my_pack/ -o sprites.json
""",
    )

    p.add_argument(
        "pack",
        metavar="PACK",
        nargs="?",
        help="Java RP (.zip / .jar / unpacked directory)",
    )
    p.add_argument("-o", "--output",     metavar="FILE",   default=None,
                   help="Write sprites.json to FILE (default: stdout)")
    p.add_argument("-m", "--merge",      metavar="FILE",   default=None,
                   help="Merge with existing sprites.json at FILE")
    p.add_argument("-b", "--bedrock-rp", metavar="PATH",   default=None, dest="bedrock_rp",
                   help="Bedrock RP to validate sprite paths against")
    p.add_argument("--inject",           action="store_true", default=False,
                   help="Embed sprites.json into a copy of the input zip")
    p.add_argument("--report",           metavar="FILE",   default=None,
                   help="Write detailed debug/validation report")
    p.add_argument("--filter",           metavar="ITEMS",  default=None,
                   help="Comma-separated item names to process")
    p.add_argument("--namespace",        metavar="NS",     default=None,
                   help="Comma-separated namespaces to process (default: all)")
    p.add_argument("--sprite-prefix",    metavar="PREFIX", default="", dest="sprite_prefix",
                   help="Prepend PREFIX to all generated sprite paths")
    p.add_argument("--indent",           metavar="N",      default=4, type=int,
                   help="JSON indentation (0=compact, default: 4)")
    p.add_argument("--dry-run",          action="store_true", default=False, dest="dry_run",
                   help="Parse and validate but write no files")
    p.add_argument("--self-test",        action="store_true", default=False, dest="self_test",
                   help="Run built-in unit tests and exit")
    p.add_argument("--no-color",         action="store_true", default=False, dest="no_color",
                   help="Disable ANSI colour output")
    p.add_argument("-v", "--verbose",    action="store_true", default=False,
                   help="Verbose debug output")
    p.add_argument("-q", "--quiet",      action="store_true", default=False,
                   help="Suppress info/ok output (warnings still go to stderr)")
    p.add_argument("--version",          action="version",
                   version=f"%(prog)s v{VERSION}")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.self_test:
        sys.exit(run_self_tests())

    if not args.pack:
        parser.print_help()
        sys.exit(1)

    item_filter: Optional[Set[str]] = None
    if args.filter:
        item_filter = {s.strip() for s in args.filter.split(",") if s.strip()}

    ns_filter: Optional[Set[str]] = None
    if args.namespace:
        ns_filter = {s.strip() for s in args.namespace.split(",") if s.strip()}

    _, exit_code = generate_sprites_json(
        pack_path       = args.pack,
        output_path     = args.output,
        merge_path      = args.merge,
        bedrock_rp_path = args.bedrock_rp,
        inject          = args.inject,
        report_path     = args.report,
        item_filter     = item_filter,
        ns_filter       = ns_filter,
        sprite_prefix   = args.sprite_prefix,
        json_indent     = args.indent,
        dry_run         = args.dry_run,
        use_color       = not args.no_color,
        quiet           = args.quiet,
        verbose         = args.verbose,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
