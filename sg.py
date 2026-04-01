from __future__ import annotations

import hashlib, json, math, os, re, shutil, sys, tempfile, time, zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

VERSION = "8.1.0"

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

OUTPUT_ENV_DIR = "SG_OUTPUT_DIR"
PLUGIN_HINTS: Tuple[str, ...] = (
    "itemadder", "ia_generated", "items_packs",
    "oraxen", "nexo", "craftengine",
    "modelengine", "mmoitems", "mmocore", "mythicmobs", "mythic",
)

# ─── pack_format → MC version string (display only) ──────────────────────────
PACK_FORMAT_TO_MC: Dict[int, str] = {
    4:"1.13-1.14", 5:"1.15-1.16", 6:"1.16.2-1.16.5",
    7:"1.17", 8:"1.18", 9:"1.19", 12:"1.19.4",
    13:"1.20", 15:"1.20.2", 18:"1.20.4", 22:"1.20.6",
    32:"1.21", 34:"1.21.1", 36:"1.21.2", 38:"1.21.3",
    40:"1.21.4", 41:"1.21.5",
}

# ─── max durability per item (damage fraction × max_dur = absolute damage) ───
JAVA_MAX_DAMAGE: Dict[str, int] = {
    "leather_helmet":55,"leather_chestplate":80,"leather_leggings":75,"leather_boots":65,
    "chainmail_helmet":165,"chainmail_chestplate":240,"chainmail_leggings":225,"chainmail_boots":195,
    "iron_helmet":165,"iron_chestplate":240,"iron_leggings":225,"iron_boots":195,
    "golden_helmet":77,"golden_chestplate":112,"golden_leggings":105,"golden_boots":91,
    "diamond_helmet":363,"diamond_chestplate":528,"diamond_leggings":495,"diamond_boots":429,
    "netherite_helmet":407,"netherite_chestplate":592,"netherite_leggings":555,"netherite_boots":481,
    "turtle_helmet":275,"wolf_armor":64,
    "wooden_sword":59,"stone_sword":131,"iron_sword":250,"golden_sword":32,
    "diamond_sword":1561,"netherite_sword":2031,
    "wooden_pickaxe":59,"stone_pickaxe":131,"iron_pickaxe":250,"golden_pickaxe":32,
    "diamond_pickaxe":1561,"netherite_pickaxe":2031,
    "wooden_axe":59,"stone_axe":131,"iron_axe":250,"golden_axe":32,
    "diamond_axe":1561,"netherite_axe":2031,
    "wooden_shovel":59,"stone_shovel":131,"iron_shovel":250,"golden_shovel":32,
    "diamond_shovel":1561,"netherite_shovel":2031,
    "wooden_hoe":59,"stone_hoe":131,"iron_hoe":250,"golden_hoe":32,
    "diamond_hoe":1561,"netherite_hoe":2031,
    "bow":384,"crossbow":465,"trident":250,"shield":336,"fishing_rod":64,
    "flint_and_steel":64,"carrot_on_a_stick":25,"warped_fungus_on_a_stick":25,
    "shears":238,"elytra":432,"mace":500,
    "leather_horse_armor":0,"iron_horse_armor":0,"golden_horse_armor":0,"diamond_horse_armor":0,
}

# ─── priority when picking the "best" texture from a model's texture map ─────
TEXTURE_PICK_ORDER: Tuple[str,...] = (
    "layer0","layer1","all","texture","top","side","front","back","bottom",
    "cross","plant","particle","fan","end","edge","north","south","east","west","pane","stem",
)

# ─── Java texture subfolder → Bedrock destination folder ─────────────────────
# (used ONLY for the folder prefix; basename is always flat — see below)
JAVA_TEX_FOLDER_MAP: Dict[str, str] = {
    "item":"textures/items","items":"textures/items",
    "block":"textures/blocks","blocks":"textures/blocks",
    "entity":"textures/entity","entities":"textures/entity",
    "gui":"textures/gui",
    "painting":"textures/painting","paintings":"textures/painting",
    "particle":"textures/particle","particles":"textures/particle",
    "environment":"textures/environment","misc":"textures/misc",
    "colormap":"textures/colormap","map":"textures/map",
    "models":"textures/models","armor":"textures/models/armor",
    "effect":"textures/mob_effect","mob_effect":"textures/mob_effect",
}

# ─── built-in parent models that carry no textures ───────────────────────────
BUILTIN_PARENTS: Set[str] = {
    "minecraft:builtin/generated","builtin/generated",
    "minecraft:builtin/entity","builtin/entity",
    "minecraft:item/generated","item/generated",
    "minecraft:item/handheld","item/handheld",
    "minecraft:item/handheld_rod","item/handheld_rod",
    "minecraft:item/handheld_crossbow",
    "minecraft:item/bow","item/bow",
    "minecraft:item/crossbow","item/crossbow",
    "minecraft:item/trident","item/trident",
    "minecraft:item/template_spawn_egg",
    "minecraft:item/template_shulker_box",
    "minecraft:block/block","block/block",
    "minecraft:block/cube","block/cube",
    "minecraft:block/cube_all","block/cube_all",
    "minecraft:block/cube_column","block/cube_column",
    "minecraft:block/leaves","block/leaves",
    "minecraft:block/cross","block/cross",
    "minecraft:block/orientable","block/orientable",
    "minecraft:block/crop","block/crop",
}

# 1.21.4+ component types
_T_MODEL     = "minecraft:model"
_T_DISPATCH  = "minecraft:range_dispatch"
_T_CONDITION = "minecraft:condition"
_T_SELECT    = "minecraft:select"
_T_COMPOSITE = "minecraft:composite"
_P_CMD       = "minecraft:custom_model_data"
_P_DAMAGE    = "minecraft:damage"
_P_DAMAGED   = "minecraft:damaged"

OVERLAY_MIN_FORMAT = 18  # overlays added in 1.20.4


# ═════════════════════════════════════════════════════════════════════════════
# CONSOLE HELPERS
# ═════════════════════════════════════════════════════════════════════════════
_ANSI = sys.stdout.isatty()
def _c(code, txt): return f"\033[{code}m{txt}\033[0m" if _ANSI else txt

def _ok(m):      print(_c("32","[+] ") + m, flush=True)
def _info(m):    print(_c("36","[.] ") + m, flush=True)
def _warn(m):    print(_c("33","[!] ") + m, file=sys.stderr, flush=True)
def _err(m):     print(_c("31","[X] ") + m, file=sys.stderr, flush=True)
def _sec(m):     print(f"\n{_c('1;36', f'== {m} ==')}", flush=True)


# ═════════════════════════════════════════════════════════════════════════════
# LENIENT JSON PARSER  (BOM · // comments · /* */ comments · trailing commas)
# ═════════════════════════════════════════════════════════════════════════════
def _str_end(s, i):
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\": i += 2; continue
        if c == '"':  return i
        i += 1
    return i

def _no_comments(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            out.append(c); e = _str_end(s, i+1)
            out.append(s[i+1:e+1]); i = e+1; continue
        if c == '/' and i+1 < n:
            nc = s[i+1]
            if nc == '/':
                while i < n and s[i] != '\n': i += 1; continue
            if nc == '*':
                i += 2
                while i+1 < n:
                    if s[i] == '*' and s[i+1] == '/': i += 2; break
                    i += 1
                else: i = n
                continue
        out.append(c); i += 1
    return ''.join(out)

def _no_trail(s): return re.sub(r',(\s*[}\]])', r'\1', s)

def parse_json(raw: bytes) -> Any:
    text = ''
    for enc in ('utf-8-sig','utf-8','latin-1','cp1252'):
        try: text = raw.decode(enc); break
        except: pass
    else: raise ValueError("undecodable bytes")
    def try_(t):
        try: return json.loads(t)
        except: return None
    for t in (text, _no_trail(_no_comments(text)), _no_trail(text)):
        r = try_(t)
        if r is not None: return r
    m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if m:
        r = try_(_no_trail(_no_comments(m.group(1))))
        if r is not None: return r
    raise ValueError(f"cannot parse: {text[:100]!r}")


def parse_yaml_fallback(raw: bytes) -> Optional[Any]:
    text = ''
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if not text:
        return None

    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    key_allow = {
        'material', 'item', 'base_item', 'minecraft_item', 'vanilla_item', 'parent_item',
        'display_material', 'model', 'model_path', 'model_id', 'model_name',
        'texture', 'texture_path', 'sprite', 'icon', 'custom_model_data', 'cmd',
        'damage', 'damage_predicate', 'damaged', 'unbreakable'
    }

    for raw_line in text.splitlines():
        line = raw_line.split('#', 1)[0].rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith('- '):
            stripped = stripped[2:].strip()

        if stripped.endswith(':') and stripped.count(':') == 1:
            if current:
                entries.append(current)
                current = {}
            continue

        if ':' not in stripped:
            continue

        key, value = stripped.split(':', 1)
        key = key.strip().lower()
        if key not in key_allow:
            continue

        value = value.strip().strip("\"'")
        if not value:
            continue

        if key in {'unbreakable'}:
            current[key] = value.lower() in {'true', 'yes', '1'}
        elif key in {'damaged'}:
            if value.lower() in {'false', '0'}:
                current[key] = 0
            elif value.lower() in {'true', '1'}:
                current[key] = 1
            else:
                current[key] = value
        else:
            current[key] = value

    if current:
        entries.append(current)

    return entries or None


# ═════════════════════════════════════════════════════════════════════════════
# PATH / NAMESPACE UTILITIES
# ═════════════════════════════════════════════════════════════════════════════
def split_ns(ref: str) -> Tuple[str, str]:
    if ':' in ref:
        ns, p = ref.split(':', 1); return ns.strip(), p.strip()
    return 'minecraft', ref.strip()

def norm_model(ref: str) -> str:
    ref = ref.strip(); has_ns = ':' in ref
    ns, p = split_ns(ref)
    if p.endswith('.json'): p = p[:-5]
    p = p.replace('\\', '/').strip('/')
    while '//' in p: p = p.replace('//', '/')
    return f'{ns}:{p}' if has_ns else p

def model_file(ns: str, path: str) -> str:
    return f'assets/{ns}/models/{path}.json'

def dmg_abs(frac: float, item: str) -> int:
    frac = max(0.0, min(1.0, frac))
    md = JAVA_MAX_DAMAGE.get(item)
    if md is None:  return math.ceil(frac * 1000)
    if md == 0:     return 0
    return math.ceil(frac * md)

def entry_hash(item, cmd, dmg, unbreak) -> str:
    parts = [item, str(cmd) if cmd is not None else '',
             str(dmg) if dmg is not None else '',
             str(unbreak) if unbreak is not None else '']
    return hashlib.md5(''.join(parts).encode()).hexdigest()[:7]

def java_tex_to_sprite(tex_ref: str) -> str:
    """
    Convert a Java texture reference to a Bedrock sprite path.

    Uses BASENAME ONLY (flat) to match converter.sh's behaviour:
      local_name="$(basename "${tex%.*}")"
      dest="${RP}/textures/items/${local_name}.png"

    Examples:
      minecraft:item/custom/sub/sword  →  textures/items/sword
      minecraft:block/stone            →  textures/blocks/stone
      mypack:custom/deep/icon          →  textures/icon   (unknown folder)
    """
    _, path = split_ns(tex_ref)
    for ext in ('.png', '.tga', '.PNG', '.TGA'):
        if path.endswith(ext): path = path[:-len(ext)]; break
    path = path.replace('\\', '/').strip('/')
    while '//' in path: path = path.replace('//', '/')
    if not path: return 'textures/unknown'

    parts  = path.split('/')
    folder = parts[0]
    base   = parts[-1]          # <── ALWAYS basename (flat, matches converter.sh)

    bedrock = JAVA_TEX_FOLDER_MAP.get(folder)
    if bedrock:
        # gui/sprites/ prefix cleanup
        if folder == 'gui' and len(parts) > 1 and parts[1] == 'sprites':
            base = parts[-1]
        return f'{bedrock}/{base}'
    # Unknown folder — best effort
    return f'textures/{base}'

def san(path: str) -> str:
    """Sanitise a sprite path for Bedrock."""
    path = path.replace('\\', '/').strip('/')
    while '//' in path: path = path.replace('//', '/')
    path = path.lower()
    path = re.sub(r'[ \t]+', '_', path)
    path = re.sub(r'[^a-z0-9_.\-/]', '', path)
    return path


# ═════════════════════════════════════════════════════════════════════════════
# RESOURCE PACK READER
# Supports: zip / directory · nested zip folders · pack overlays (fmt ≥ 18)
# ═════════════════════════════════════════════════════════════════════════════
class PackReader:
    def __init__(self, pack_path: str):
        self.path      = os.fspath(pack_path)
        self._is_zip   = False
        self._zf: Optional[zipfile.ZipFile] = None
        self._prefix   = ''              # e.g. "MyPack/" if nested zip
        self._all: Set[str] = set()
        self._overlays: List[str] = []  # overlay directory prefixes

        # Fast lookup indices
        self._model_idx:   Set[str] = set()   # assets/*/models/item/*.json
        self._items_idx:   Set[str] = set()   # assets/*/items/*.json (new fmt)
        self._tex_idx:     Set[str] = set()   # stems of texture files (lower)

        if not os.path.exists(self.path):
            raise FileNotFoundError(f'Not found: {self.path}')
        if os.path.isfile(self.path):
            if not zipfile.is_zipfile(self.path):
                raise ValueError(f'Not a valid zip: {self.path}')
            self._is_zip = True
            self._zf     = zipfile.ZipFile(self.path, 'r')
            self._all    = set(self._zf.namelist())
            self._prefix = self._find_prefix()

        self._build_indices()
        self._find_overlays()

    def __enter__(self): return self
    def __exit__(self, *_):
        if self._zf: self._zf.close()

    def _find_prefix(self) -> str:
        """Detect nested-folder prefix (pack.mcmeta not at zip root)."""
        if 'pack.mcmeta' in self._all: return ''
        for n in self._all:
            if n.endswith('/pack.mcmeta'):
                segs = n.split('/')
                if 1 <= len(segs)-1 <= 4:
                    return '/'.join(segs[:-1]) + '/'
        return ''

    def _index_file(self, f: str):
        fl = f.lower()
        if '/models/item/' in fl and fl.endswith('.json'): self._model_idx.add(f)
        if '/items/'       in fl and fl.endswith('.json'): self._items_idx.add(f)
        if '/textures/'    in fl:
            stem, ext = os.path.splitext(fl)
            if ext in {'.png', '.tga', '.jpg', '.jpeg'}: self._tex_idx.add(stem)

    def _build_indices(self):
        for f in self._iter('assets/'): self._index_file(f)

    def _find_overlays(self):
        data = self.read_json('pack.mcmeta')
        if not isinstance(data, dict): return
        fmt = data.get('pack', {}).get('pack_format', 0)
        if not isinstance(fmt, int) or fmt < OVERLAY_MIN_FORMAT: return
        for entry in data.get('overlays', {}).get('entries', []):
            if not isinstance(entry, dict): continue
            d = entry.get('directory', '')
            if not isinstance(d, str) or not d: continue
            ov = d.rstrip('/') + '/'
            found = False
            for _ in self._iter(ov + 'assets/'): found = True; break
            if found:
                self._overlays.append(ov)
                for f in self._iter(ov + 'assets/'): self._index_file(f)

    def _iter(self, prefix: str) -> Iterator[str]:
        prefix = prefix.rstrip('/') + '/'
        if self._is_zip:
            full = self._prefix + prefix; pl = len(self._prefix)
            for n in self._all:
                if n.startswith(full) and not n.endswith('/'): yield n[pl:]
        else:
            base = os.path.join(self.path, prefix)
            if not os.path.isdir(base): return
            for root, _, files in os.walk(base):
                for fn in files:
                    rel = os.path.relpath(os.path.join(root, fn), self.path)
                    yield rel.replace('\\', '/')

    def _read_raw(self, rel: str) -> Optional[bytes]:
        full = self._prefix + rel
        if self._is_zip:
            try:
                with self._zf.open(full) as fh: return fh.read()
            except KeyError: return None
        try:
            with open(os.path.join(self.path, rel), 'rb') as fh: return fh.read()
        except OSError: return None

    def read_bytes(self, rel: str) -> Optional[bytes]:
        """Overlay-aware read: latest overlay wins."""
        for ov in reversed(self._overlays):
            d = self._read_raw(ov + rel)
            if d is not None: return d
        return self._read_raw(rel)

    def read_json(self, rel: str) -> Optional[Any]:
        raw = self.read_bytes(rel)
        if raw is None: return None
        try: return parse_json(raw)
        except Exception: return None

    def read_yaml(self, rel: str) -> Optional[Any]:
        raw = self.read_bytes(rel)
        if raw is None:
            return None

        if yaml is None:
            return parse_yaml_fallback(raw)

        text = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            return parse_yaml_fallback(raw)
        try:
            return yaml.safe_load(text)
        except Exception:
            return parse_yaml_fallback(raw)

    def list_files(self, prefix: str) -> Iterator[str]:
        yield from self._iter(prefix)

    def all_files(self) -> List[str]:
        if self._is_zip:
            pl = len(self._prefix)
            return sorted([
                n[pl:]
                for n in self._all
                if not n.endswith('/') and n.startswith(self._prefix)
            ])

        out: List[str] = []
        for root, _, files in os.walk(self.path):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), self.path)
                out.append(rel.replace('\\', '/'))
        return sorted(out)

    # ── convenience ───────────────────────────────────────────────────────────
    def get_namespaces(self) -> List[str]:
        ns = set()
        for f in self._iter('assets/'):
            p = f.split('/');
            if len(p) >= 2 and p[0]=='assets' and p[1]: ns.add(p[1])
        return sorted(ns)

    def get_pack_format(self) -> Optional[int]:
        d = self.read_json('pack.mcmeta')
        if isinstance(d, dict):
            f = d.get('pack', {}).get('pack_format')
            if isinstance(f, int): return f
        return None

    def get_description(self) -> str:
        d = self.read_json('pack.mcmeta')
        if not isinstance(d, dict): return ''
        desc = d.get('pack', {}).get('description', '')
        if isinstance(desc, list):
            desc = ' '.join(str(x.get('text', x.get('translate','')) if isinstance(x, dict) else x) for x in desc)
        elif isinstance(desc, dict):
            desc = str(desc.get('text', desc.get('translate','')))
        return re.sub(r'[§\u00a7][0-9a-fk-orx]', '', str(desc)).strip()

    def model_exists(self, p: str) -> bool: return p in self._model_idx

    def tex_exists(self, sprite: str) -> bool:
        return sprite.lower() in self._tex_idx

    def find_model_ns(self, model_path: str) -> List[str]:
        """Which namespaces contain a model file at models/<model_path>.json?"""
        suffix = f'models/{model_path}.json'
        result = []
        for f in self._model_idx:
            if f.endswith('/' + suffix) or f == 'assets/' + suffix:
                parts = f.split('/')
                if len(parts) >= 2 and parts[0] == 'assets': result.append(parts[1])
        return result

    def item_model_files(self, ns: str) -> List[str]:
        return [f for f in self._model_idx if f.startswith(f'assets/{ns}/models/item/')]

    def items_dir_files(self, ns: str) -> List[str]:
        return [f for f in self._items_idx if f.startswith(f'assets/{ns}/items/')]


# ═════════════════════════════════════════════════════════════════════════════
# MODEL TEXTURE RESOLVER
# Walks parent chains and merges textures{} blocks exactly as Minecraft does.
# ═════════════════════════════════════════════════════════════════════════════
class ModelResolver:
    def __init__(self, reader: PackReader):
        self._r   = reader
        self._raw: Dict[str, Optional[Any]] = {}
        self._tex: Dict[str, Dict[str, str]] = {}

    def _get(self, pp: str) -> Optional[Any]:
        if pp not in self._raw: self._raw[pp] = self._r.read_json(pp)
        return self._raw[pp]

    def resolve(self, model_ref: str, ctx_ns: Optional[str]=None) -> Dict[str, str]:
        clean = norm_model(model_ref)
        ns, path = split_ns(clean)
        key = f'{ns}:{path}'
        if key not in self._tex:
            self._tex[key] = self._walk(ns, path, ':' in clean, ctx_ns, frozenset(), 0)
        return self._tex[key]

    def _walk(self, ns, path, has_ns, ctx_ns, visited, depth) -> Dict[str, str]:
        if depth >= 64: return {}
        norm = f'{ns}:{path}'
        if norm in BUILTIN_PARENTS or path in BUILTIN_PARENTS: return {}
        pp = model_file(ns, path)
        if pp in visited: return {}
        visited = visited | {pp}

        data = self._get(pp); eff = ns
        # Namespace fallback
        if data is None and not has_ns and ctx_ns and ctx_ns != ns:
            fb = model_file(ctx_ns, path); fd = self._get(fb)
            if fd is not None: data = fd; eff = ctx_ns; visited = visited | {fb}

        if not isinstance(data, dict): return {}
        tex: Dict[str, str] = {}

        # Parent chain
        par = data.get('parent')
        if isinstance(par, str) and par.strip():
            pc = norm_model(par); pns, pp2 = split_ns(pc)
            tex.update(self._walk(pns, pp2, ':' in pc, eff, visited, depth+1))

        # Own textures{}
        own = data.get('textures')
        if isinstance(own, dict):
            for k, v in own.items():
                if isinstance(v, str): tex[k] = v

        # layers[] (alternative to textures{})
        layers = data.get('layers')
        if isinstance(layers, list):
            for i, ly in enumerate(layers):
                if isinstance(ly, dict) and isinstance(ly.get('texture'), str):
                    k = f'layer{i}'
                    if k not in tex: tex[k] = ly['texture']

        # Resolve #variable references
        for _ in range(32):
            changed = False
            for k in list(tex):
                v = tex[k]
                if isinstance(v, str) and v.startswith('#'):
                    t = tex.get(v[1:])
                    if isinstance(t, str) and not t.startswith('#'):
                        tex[k] = t; changed = True
            if not changed: break
        return tex


def best_tex(tex_map: Dict[str, str]) -> Optional[str]:
    for k in TEXTURE_PICK_ORDER:
        v = tex_map.get(k)
        if v and isinstance(v, str) and not v.startswith('#'): return v
    for v in tex_map.values():
        if isinstance(v, str) and not v.startswith('#'): return v
    return None


# ═════════════════════════════════════════════════════════════════════════════
# SPRITE RESOLUTION HELPER
# ═════════════════════════════════════════════════════════════════════════════
def resolve_sprite(model_ref: str, item: str,
                   res: ModelResolver, reader: PackReader,
                   ctx_ns: str) -> Tuple[str, bool]:
    """
    Return (sprite_path, is_fallback).
    is_fallback=True when no texture could be found — model name was used.
    """
    clean  = norm_model(model_ref)
    m_ns, m_path = split_ns(clean)

    model_path = model_file(m_ns, m_path)
    if not reader.model_exists(model_path):
        found = reader.find_model_ns(m_path)
        if found:
            preferred = ctx_ns if isinstance(ctx_ns, str) and ctx_ns in found else found[0]
            if preferred and preferred != m_ns:
                m_ns = preferred
                clean = f'{m_ns}:{m_path}'

    tex_map = res.resolve(clean, ctx_ns=ctx_ns or m_ns)
    bt = best_tex(tex_map)

    if bt:
        return san(java_tex_to_sprite(bt)), False

    # Fallback: derive from model basename
    segs = m_path.replace('\\', '/').split('/')
    base = segs[-1]
    if segs and segs[0] in ('item','items'):
        sprite = f'textures/items/{base}'
    elif segs and segs[0] in ('block','blocks'):
        sprite = f'textures/blocks/{base}'
    else:
        sprite = f'textures/items/{base}'
    return san(sprite), True


# ═════════════════════════════════════════════════════════════════════════════
# CLASSIC FORMAT  (models/item/*.json  overrides[])
# Mirrors what converter.sh's jq pipeline reads from each override entry.
# ═════════════════════════════════════════════════════════════════════════════
def parse_override(ns: str, item: str, ov: Any,
                   res: ModelResolver, reader: PackReader
                   ) -> Optional[Dict[str, Any]]:
    if not isinstance(ov, dict): return None
    pred = ov.get('predicate'); mref = ov.get('model')
    if not isinstance(pred, dict): return None
    if not isinstance(mref, str) or not mref.strip(): return None

    has_cmd = 'custom_model_data' in pred
    has_dmg = 'damage'            in pred
    has_dmd = 'damaged'           in pred
    if not (has_cmd or has_dmg or has_dmd): return None

    disp = f'{ns}:{item}'

    # custom_model_data
    cmd: Optional[int] = None
    if has_cmd:
        raw = pred['custom_model_data']
        try:
            f = float(raw); cmd = int(f)
            if f != cmd: _warn(f'[{disp}] cmd={raw!r} not integer → {cmd}')
        except:
            _warn(f'[{disp}] invalid custom_model_data={raw!r}, skipping'); return None
        if cmd < 0: _warn(f'[{disp}] negative cmd={cmd}, skipping'); return None

    # damage  (fraction 0-1 → absolute)
    dmg: Optional[int] = None
    if has_dmg:
        try: frac = float(pred['damage'])
        except: _warn(f'[{disp}] invalid damage, using 0.0'); frac = 0.0
        dmg = dmg_abs(frac, item)  # dmg=0 is valid, must not be omitted

    # damaged  (0 → unbreakable)
    unbreak: Optional[bool] = None
    if has_dmd:
        dv = pred.get('damaged')
        if dv == 0 or dv is False or str(dv).lower() in ('0','false'):
            unbreak = True

    # validate + find model
    clean = norm_model(mref); m_ns, m_path = split_ns(clean)
    mf = model_file(m_ns, m_path)
    if not reader.model_exists(mf):
        found = reader.find_model_ns(m_path)
        if found:
            _warn(f'[{disp}] model {clean!r} not in {m_ns!r}, found in {found[0]!r}')
            clean = f'{found[0]}:{m_path}'; m_ns = found[0]

    sprite, fallback = resolve_sprite(clean, item, res, reader, m_ns)
    if not sprite:
        _warn(f'[{disp}] empty sprite for {clean!r}, skipping'); return None

    e: Dict[str, Any] = {}
    if cmd     is not None: e['custom_model_data'] = cmd
    if dmg     is not None: e['damage_predicate']  = dmg
    if unbreak is True:     e['unbreakable']        = True
    e['sprite'] = sprite
    e['_fb']    = fallback
    e['_hash']  = entry_hash(item, cmd, dmg, unbreak)
    return e


# ═════════════════════════════════════════════════════════════════════════════
# NEW FORMAT  (items/*.json  MC 1.21.4+  component tree walker)
# ═════════════════════════════════════════════════════════════════════════════
class _Ctx:
    __slots__ = ('cmd','dmg_frac','unbreak')
    def __init__(self, cmd=None, dmg_frac=None, unbreak=None):
        self.cmd=cmd; self.dmg_frac=dmg_frac; self.unbreak=unbreak
    def copy(self): return _Ctx(self.cmd, self.dmg_frac, self.unbreak)
    def has(self): return any(x is not None for x in (self.cmd,self.dmg_frac,self.unbreak))


def _to_component_node(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get('type'), str):
            return value

        nested_model = value.get('model')
        if isinstance(nested_model, dict) and (
            isinstance(nested_model.get('type'), str)
            or any(key in nested_model for key in ('entries', 'cases', 'on_true', 'on_false', 'models', 'fallback'))
        ):
            return nested_model

        if 'model' in value:
            return {'type': _T_MODEL, 'model': value.get('model')}
        if any(key in value for key in ('on_true', 'on_false')):
            enriched = dict(value)
            enriched.setdefault('type', _T_CONDITION)
            return enriched
        if 'cases' in value:
            enriched = dict(value)
            enriched.setdefault('type', _T_SELECT)
            return enriched
        if 'entries' in value:
            enriched = dict(value)
            enriched.setdefault('type', _T_DISPATCH)
            return enriched
        if 'models' in value:
            enriched = dict(value)
            enriched.setdefault('type', _T_COMPOSITE)
            return enriched
        return value

    if isinstance(value, str):
        raw = value.strip()
        if raw:
            return {'type': _T_MODEL, 'model': raw}
        return None

    if isinstance(value, list):
        return {'type': _T_COMPOSITE, 'models': value}
    return None


def walk_tree(node: Any, item: str, res: ModelResolver, reader: PackReader,
              ctx: _Ctx, depth: int, out: List[Dict], item_ns: str = 'minecraft') -> None:
    if depth > 32:
        return
    node = _to_component_node(node)
    if not isinstance(node, dict):
        return

    t = _normalized_token(node.get('type', ''))
    prop = _normalized_token(node.get('property', ''))

    if t in {_normalized_token(_T_MODEL), 'model'}:
        mref = _extract_model_node(node.get('model'))
        if isinstance(mref, str) and ctx.has():
            _emit(item, mref, res, reader, ctx, out, item_ns=item_ns)
        return

    if t in {_normalized_token(_T_DISPATCH), 'range_dispatch', 'dispatch'}:
        entries_node = node.get('entries')
        entries: List[Dict[str, Any]] = []
        if isinstance(entries_node, list):
            for entry in entries_node:
                if isinstance(entry, dict):
                    entries.append(entry)
        elif isinstance(entries_node, dict):
            for raw_key, raw_value in entries_node.items():
                if isinstance(raw_value, dict):
                    candidate = dict(raw_value)
                else:
                    candidate = {'model': raw_value}
                if 'threshold' not in candidate:
                    candidate['threshold'] = raw_key
                if 'when' not in candidate:
                    candidate['when'] = raw_key
                entries.append(candidate)

        if prop in {_normalized_token(_P_CMD), 'custom_model_data', 'cmd'}:
            for i, entry in enumerate(entries):
                model_node = _extract_model_node(entry.get('model')) or _extract_model_node(entry.get('value'))
                threshold_value = entry.get('threshold')
                cmd_values = _extract_int_candidates(threshold_value)
                if not cmd_values:
                    cmd_values = _extract_int_candidates(entry.get('when'))
                if not cmd_values:
                    cmd_values = [i + 1]

                for cmd_value in sorted(set(cmd_values)):
                    sc = ctx.copy()
                    sc.cmd = cmd_value
                    walk_tree(model_node, item, res, reader, sc, depth + 1, out, item_ns=item_ns)

        elif prop in {_normalized_token(_P_DAMAGE), 'damage'}:
            for entry in entries:
                model_node = _extract_model_node(entry.get('model')) or _extract_model_node(entry.get('value'))
                threshold_value = entry.get('threshold')
                damage_value = _as_float(threshold_value)
                if damage_value is None:
                    damage_value = _as_float(entry.get('when'))
                if damage_value is None:
                    continue

                sc = ctx.copy()
                sc.dmg_frac = float(damage_value)
                walk_tree(model_node, item, res, reader, sc, depth + 1, out, item_ns=item_ns)

        else:
            for entry in entries:
                model_node = _extract_model_node(entry.get('model')) or _extract_model_node(entry.get('value'))
                walk_tree(model_node, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)

        fb = node.get('fallback')
        if fb is not None:
            walk_tree(fb, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        return

    if t in {_normalized_token(_T_CONDITION), 'condition'}:
        ot = node.get('on_true'); of_ = node.get('on_false')
        if prop in {_normalized_token(_P_DAMAGED), 'damaged'}:
            if of_ is not None:
                sc = ctx.copy(); sc.unbreak = True
                walk_tree(of_, item, res, reader, sc, depth + 1, out, item_ns=item_ns)
            if ot is not None:
                walk_tree(ot, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        elif prop in {_normalized_token(_P_CMD), 'custom_model_data', 'cmd'}:
            cmd_values = _extract_int_candidates(node.get('value'))
            if not cmd_values:
                cmd_values = _extract_int_candidates(node.get('when'))

            if cmd_values and ot is not None:
                for cmd_value in sorted(set(cmd_values)):
                    sc = ctx.copy()
                    sc.cmd = cmd_value
                    walk_tree(ot, item, res, reader, sc, depth + 1, out, item_ns=item_ns)
            elif ot is not None:
                walk_tree(ot, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)

            if of_ is not None:
                walk_tree(of_, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        elif prop in {_normalized_token(_P_DAMAGE), 'damage'}:
            damage_value = _as_float(node.get('value'))
            if damage_value is None:
                damage_value = _as_float(node.get('when'))

            if damage_value is not None and ot is not None:
                sc = ctx.copy()
                sc.dmg_frac = float(damage_value)
                walk_tree(ot, item, res, reader, sc, depth + 1, out, item_ns=item_ns)
            elif ot is not None:
                walk_tree(ot, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)

            if of_ is not None:
                walk_tree(of_, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        else:
            for br in (ot, of_):
                if br is not None:
                    walk_tree(br, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        return

    if t in {_normalized_token(_T_SELECT), 'select'}:
        for case in (node.get('cases') or []):
            if not isinstance(case, dict):
                continue

            model_node = _extract_model_node(case.get('model')) or _extract_model_node(case.get('value'))
            if not model_node:
                continue

            when = case.get('when')
            sc = ctx.copy()

            if prop in {_normalized_token(_P_CMD), 'custom_model_data', 'cmd'}:
                cmd_values = _extract_int_candidates(when)
                if not cmd_values:
                    cmd_values = _extract_int_candidates(case)

                if len(cmd_values) > 1:
                    for cmd_value in sorted(set(cmd_values)):
                        sc2 = ctx.copy()
                        sc2.cmd = cmd_value
                        walk_tree(model_node, item, res, reader, sc2, depth + 1, out, item_ns=item_ns)
                    continue

                if len(cmd_values) == 1:
                    sc.cmd = cmd_values[0]

            walk_tree(model_node, item, res, reader, sc, depth + 1, out, item_ns=item_ns)

        fb = node.get('fallback')
        if fb is not None:
            walk_tree(fb, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        return

    if t in {_normalized_token(_T_COMPOSITE), 'composite'}:
        for m in (node.get('models') or []):
            walk_tree(m, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)
        return

    for k in ('model','on_true','on_false','fallback','value'):
        sub = node.get(k)
        if sub is not None:
            walk_tree(sub, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)

    for value in node.values():
        if isinstance(value, list):
            for item_node in value:
                walk_tree(item_node, item, res, reader, ctx.copy(), depth + 1, out, item_ns=item_ns)


def _emit(item: str, mref: str, res: ModelResolver, reader: PackReader,
          ctx: _Ctx, out: List[Dict], item_ns: str = 'minecraft') -> None:
    clean = norm_model(mref); m_ns, _ = split_ns(clean)
    sprite, fb = resolve_sprite(clean, item, res, reader, item_ns or m_ns)
    if not sprite: return
    dmg = dmg_abs(ctx.dmg_frac, item) if ctx.dmg_frac is not None else None
    e: Dict[str,Any] = {}
    if ctx.cmd  is not None: e['custom_model_data'] = ctx.cmd
    if dmg      is not None: e['damage_predicate']  = dmg
    if ctx.unbreak is True:  e['unbreakable']        = True
    e['sprite'] = sprite; e['_fb'] = fb
    e['_hash']  = entry_hash(item, ctx.cmd, dmg, ctx.unbreak)
    out.append(e)


# ═════════════════════════════════════════════════════════════════════════════
# SORTING / DEDUPLICATION / STRIPPING
# ═════════════════════════════════════════════════════════════════════════════
def sort_entries(lst: List[Dict]) -> List[Dict]:
    M = 10**9
    return sorted(lst, key=lambda e: (
        e.get('custom_model_data') if e.get('custom_model_data') is not None else M,
        e.get('damage_predicate')  if e.get('damage_predicate')  is not None else M,
        1 if e.get('unbreakable') else 0,
    ))

def dedup(lst: List[Dict]) -> List[Dict]:
    seen: Dict = {}; out = []
    for e in lst:
        k = (e.get('custom_model_data'), e.get('damage_predicate'), e.get('unbreakable'))
        if k not in seen: seen[k]=True; out.append(e)
        else: _warn(f'  Duplicate predicate {k} — keeping first')
    return out

def clean_entry(e: Dict) -> Dict:
    return {k:v for k,v in e.items() if not k.startswith('_')}


def _iter_nodes(data: Any) -> Iterator[Dict[str, Any]]:
    stack: List[Any] = [data]
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


def _normalized_item_key(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    item = raw.strip().lower()
    if not item:
        return None
    item = item.replace(' ', '_').replace('-', '_')
    item = re.sub(r'[^a-z0-9_:/.]', '', item)
    if not item:
        return None

    if ':' in item:
        ns, key = item.split(':', 1)
        key = key.split('/')[-1].strip('_')
        if not key:
            return None
        ns = ns or 'minecraft'
        return key if ns == 'minecraft' else f'{ns}:{key}'

    key = item.split('/')[-1].strip('_')
    return key or None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None

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
                match = re.search(r'-?(?:0x[0-9a-fA-F]+|\d+)', raw)
                if match:
                    try:
                        return int(match.group(0), 0)
                    except Exception:
                        return None
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            match = re.search(r'-?(?:\d+(?:\.\d+)?|\.\d+)', raw)
            if match:
                try:
                    return float(match.group(0))
                except Exception:
                    return None
    return None


def _extract_int_candidates(value: Any) -> List[int]:
    out: Set[int] = set()
    stack: List[Any] = [value]

    while stack:
        current = stack.pop()
        parsed = _as_int(current)
        if parsed is not None:
            out.add(parsed)

        if isinstance(current, str):
            for token in re.findall(r'-?(?:0x[0-9a-fA-F]+|\d+)', current):
                try:
                    out.add(int(token, 0))
                except Exception:
                    continue
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, dict):
            stack.extend(current.values())

    return sorted(out)


def _normalized_token(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    token = value.strip().lower()
    if not token:
        return ''
    return token.split(':', 1)[-1]


def _extract_model_node(value: Any) -> Any:
    if isinstance(value, str):
        raw = value.strip()
        return raw if raw else None

    if isinstance(value, dict):
        if isinstance(value.get('type'), str) or any(key in value for key in ('entries', 'cases', 'on_true', 'on_false', 'models', 'fallback')):
            return value

        for key in ('model', 'path', 'id', 'name', 'value'):
            if key in value:
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
                if isinstance(nested, (dict, list)):
                    candidate = _extract_model_node(nested)
                    if candidate is not None:
                        return candidate

    if isinstance(value, list):
        for item in value:
            candidate = _extract_model_node(item)
            if candidate is not None:
                return candidate
    return None


def _sprite_from_literal(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ''
    if raw.startswith('textures/'):
        for ext in ('.png', '.tga', '.jpg', '.jpeg'):
            if raw.lower().endswith(ext):
                raw = raw[:-len(ext)]
                break
        return san(raw)
    return san(java_tex_to_sprite(raw))


def _coerce_unbreakable(node: Dict[str, Any]) -> Optional[bool]:
    lower = {_normalized_token(key): key for key in node.keys()}

    unbreak_key = lower.get('unbreakable')
    if unbreak_key is not None:
        unbreak_value = node.get(unbreak_key)
        if unbreak_value is True:
            return True
        if isinstance(unbreak_value, str) and unbreak_value.strip().lower() in {'true', '1', 'yes', 'on'}:
            return True

    damaged_key = lower.get('damaged')
    if damaged_key is None:
        return None

    damaged = node.get(damaged_key)
    if damaged in (0, False):
        return True
    if str(damaged).strip().lower() in {'0', 'false', 'off', 'no'}:
        return True
    return None


def _coerce_damage(value: Any, item_name: str, from_fraction: bool) -> Optional[int]:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None

    if from_fraction or (0.0 <= number <= 1.0):
        return dmg_abs(number, item_name)
    return int(number)


def _resolve_sprite_from_node(node: Dict[str, Any], item_name: str,
                              res: ModelResolver, reader: PackReader,
                              item_ns: str) -> str:
    def _pick(mapping: Dict[str, Any], *keys: str) -> Any:
        lower = {_normalized_token(key): key for key in mapping.keys()}
        for key in keys:
            original = lower.get(_normalized_token(key))
            if original is not None:
                return mapping.get(original)
        return None

    def _first_sprite_candidate(value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return _sprite_from_literal(value)
        if isinstance(value, dict):
            for nested_key in ('texture', 'sprite', 'icon', 'path', 'file', 'value', 'texture_path', 'image', 'background'):
                nested_value = _pick(value, nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return _sprite_from_literal(nested_value)
        return ''

    for key in ('sprite', 'icon', 'texture', 'texture_path', 'tex', 'image', 'file', 'path', 'background'):
        value = _pick(node, key)
        candidate = _first_sprite_candidate(value)
        if candidate:
            return candidate

    textures = _pick(node, 'textures')
    if isinstance(textures, dict):
        textured = {k: v for k, v in textures.items() if isinstance(v, str)}
        bt = best_tex(textured)
        if bt:
            return _sprite_from_literal(bt)
    elif isinstance(textures, list):
        for entry in textures:
            candidate = _first_sprite_candidate(entry)
            if candidate:
                return candidate

    for key in ('model', 'model_path', 'model_id', 'model_name', 'value'):
        value = _pick(node, key)
        model_ref = _extract_model_node(value)
        if isinstance(model_ref, str) and model_ref.strip():
            sprite, _ = resolve_sprite(model_ref, item_name, res, reader, item_ns)
            if sprite:
                return sprite

    return ''


def _extract_mapping_entry(item_key: str, node: Dict[str, Any],
                           res: ModelResolver, reader: PackReader) -> Optional[Dict[str, Any]]:
    item_name = item_key.split(':')[-1]
    item_ns = item_key.split(':', 1)[0] if ':' in item_key else 'minecraft'

    predicate = node.get('predicate')
    if not isinstance(predicate, dict):
        predicate = {}

    node_key_map = {_normalized_token(key): key for key in node.keys()}
    predicate_key_map = {_normalized_token(key): key for key in predicate.keys()}

    def _pick_value(*keys: str) -> Any:
        for key in keys:
            normalized = _normalized_token(key)
            node_original = node_key_map.get(normalized)
            if node_original is not None:
                return node.get(node_original)
            predicate_original = predicate_key_map.get(normalized)
            if predicate_original is not None:
                return predicate.get(predicate_original)
        return None

    cmd = None
    for key in (
        'custom_model_data',
        'customModelData',
        'custom-model-data',
        'minecraft:custom_model_data',
        'cmd',
        'model_id',
        'model_data',
        'threshold',
        'when',
    ):
        cmd_values = _extract_int_candidates(_pick_value(key))
        if cmd_values:
            cmd = cmd_values[0]
            break

    damage_predicate = None
    dmg_predicate_value = _pick_value('damage_predicate', 'damagepredicate')
    if dmg_predicate_value is not None:
        damage_predicate = _coerce_damage(dmg_predicate_value, item_name, False)
    else:
        damage_value = _pick_value('damage', 'durability', 'damage_value')
        if damage_value is not None:
            damage_predicate = _coerce_damage(damage_value, item_name, True)

    unbreakable = _coerce_unbreakable(node)
    if unbreakable is None and predicate:
        unbreakable = _coerce_unbreakable(predicate)
    if cmd is None and damage_predicate is None and unbreakable is None:
        return None

    sprite = _resolve_sprite_from_node(node, item_name, res, reader, item_ns)
    if not sprite:
        return None

    entry: Dict[str, Any] = {'sprite': sprite, '_fb': False}
    if cmd is not None:
        entry['custom_model_data'] = cmd
    if damage_predicate is not None:
        entry['damage_predicate'] = damage_predicate
    if unbreakable is True:
        entry['unbreakable'] = True

    entry['_hash'] = entry_hash(item_name, entry.get('custom_model_data'), entry.get('damage_predicate'), entry.get('unbreakable'))
    return entry


def _extract_mapping_dict(data: Any, res: ModelResolver, reader: PackReader) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if isinstance(data, list):
        for item in data:
            extracted = _extract_mapping_dict(item, res, reader)
            for item_key, entries in extracted.items():
                out[item_key].extend(entries)
        return out

    if not isinstance(data, dict):
        return out

    container_tokens = {'items', 'mappings', 'entries', 'values', 'models', 'overrides', 'script', 'sprites'}

    def _iter_candidates(value: Any) -> Iterator[Dict[str, Any]]:
        if isinstance(value, dict):
            yield value

            # Dict-style mapping: {"1001": "namespace:item/model"}
            for raw_key, nested_value in value.items():
                cmd_key = _as_int(raw_key)
                if cmd_key is None:
                    continue

                if isinstance(nested_value, str) and nested_value.strip():
                    yield {
                        'custom_model_data': cmd_key,
                        'model': nested_value,
                    }
                    continue

                if isinstance(nested_value, dict):
                    enriched = dict(nested_value)
                    if 'custom_model_data' not in enriched and 'customModelData' not in enriched:
                        enriched['custom_model_data'] = cmd_key
                    yield enriched

            for key in ('entries', 'mappings', 'overrides', 'variants', 'models', 'values', 'items'):
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict):
                            yield item
                elif isinstance(nested, dict):
                    for nested_key, nested_value in nested.items():
                        cmd_key = _as_int(nested_key)
                        if isinstance(nested_value, str) and nested_value.strip():
                            candidate: Dict[str, Any] = {'model': nested_value}
                            if cmd_key is not None:
                                candidate['custom_model_data'] = cmd_key
                            yield candidate
                        elif isinstance(nested_value, dict):
                            enriched = dict(nested_value)
                            if cmd_key is not None and 'custom_model_data' not in enriched and 'customModelData' not in enriched:
                                enriched['custom_model_data'] = cmd_key
                            yield enriched
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item

    for raw_item, values in data.items():
        raw_item_token = _normalized_token(raw_item)
        if raw_item_token in container_tokens and isinstance(values, (dict, list)):
            extracted = _extract_mapping_dict(values, res, reader)
            for item_key, entries in extracted.items():
                out[item_key].extend(entries)
            continue

        item_key = _normalized_item_key(raw_item)
        if not item_key:
            continue

        for value in _iter_candidates(values):
            entry = _extract_mapping_entry(item_key, value, res, reader)
            if entry is not None:
                out[item_key].append(entry)

    return out


def _extract_embedded_mapping(reader: PackReader, res: ModelResolver) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rel in ('script.json', 'sprites.json'):
        data = reader.read_json(rel)
        extracted = _extract_mapping_dict(data, res, reader)
        for item_key, entries in extracted.items():
            out[item_key].extend(entries)
    return out


def _extract_plugin_mapping(reader: PackReader, res: ModelResolver) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    files = reader.all_files()

    def _extract_item_values(node: Dict[str, Any], key: str) -> List[str]:
        value = node.get(key)
        out_values: List[str] = []

        if isinstance(value, str) and value.strip():
            out_values.append(value)

        if isinstance(value, dict):
            for nested_key in ('item', 'material', 'id', 'value', 'name'):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    out_values.append(nested)

        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    out_values.append(item)
                if isinstance(item, dict):
                    for nested_key in ('item', 'material', 'id', 'value'):
                        nested = item.get(nested_key)
                        if isinstance(nested, str) and nested.strip():
                            out_values.append(nested)

        dedup: Set[str] = set()
        for raw in out_values:
            text = raw.strip()
            if text:
                dedup.add(text)
        return sorted(dedup)

    for rel in files:
        low = rel.lower()
        if low in {'script.json', 'sprites.json'}:
            continue
        if low.startswith('assets/'):
            continue
        if not low.endswith(('.json', '.yml', '.yaml')):
            continue
        if not any(hint in low for hint in PLUGIN_HINTS):
            continue

        data = reader.read_json(rel) if low.endswith('.json') else reader.read_yaml(rel)
        if data is None:
            continue

        fallback_item = _normalized_item_key(PurePosixPath(rel).stem) or 'paper'
        for node in _iter_nodes(data):
            item_raw_values: List[str] = []
            for item_key in (
                'material',
                'materials',
                'item',
                'items',
                'base_item',
                'base_items',
                'minecraft_item',
                'minecraft_items',
                'vanilla_item',
                'vanilla_items',
                'parent_item',
                'display_material',
            ):
                item_raw_values.extend(_extract_item_values(node, item_key))

            item_keys: List[str] = []
            for item_raw in item_raw_values:
                normalized = _normalized_item_key(item_raw)
                if normalized:
                    item_keys.append(normalized)

            if not item_keys:
                item_keys = [fallback_item]

            for item_key in sorted(set(item_keys)):
                entry = _extract_mapping_entry(item_key, node, res, reader)
                if entry is not None:
                    out[item_key].append(entry)

    return out


# ═════════════════════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═════════════════════════════════════════════════════════════════════════════
def generate(pack_path: str) -> Dict[str, List[Dict]]:
    _sec('Reading Pack')

    with PackReader(pack_path) as reader:
        ns_list = reader.get_namespaces()
        fmt     = reader.get_pack_format()
        desc    = reader.get_description()

        if not ns_list:
            _err('No assets/ found — not a valid Java resource pack.'); sys.exit(1)

        mc = PACK_FORMAT_TO_MC.get(fmt or 0, 'unknown')
        _info(f'File        : {pack_path}')
        _info(f'Pack format : {fmt or "?"} (≈ MC {mc})')
        if desc: _info(f'Description : {desc[:80]}')
        _info(f'Namespaces  : {", ".join(ns_list)}')
        if reader._overlays: _info(f'Overlays    : {reader._overlays}')

        res     = ModelResolver(reader)
        raw: Dict[str, List[Dict]] = defaultdict(list)

        # ── Classic: models/item/*.json ───────────────────────────────────────
        _sec('Classic item models  (models/item/)')
        classic: List[Tuple[str,str]] = []
        for ns in ns_list:
            for f in reader.item_model_files(ns): classic.append((ns, f))
        classic.sort(key=lambda x: (x[0]!='minecraft', x[1]))

        ov_total = ov_used = 0
        for ns, fpath in classic:
            stem = PurePosixPath(fpath).stem
            key  = stem if ns=='minecraft' else f'{ns}:{stem}'
            data = reader.read_json(fpath)
            if not isinstance(data, dict): continue
            ovs = data.get('overrides')
            if not isinstance(ovs, list) or not ovs: continue
            for ov in ovs:
                ov_total += 1
                e = parse_override(ns, stem, ov, res, reader)
                if e is not None: raw[key].append(e); ov_used += 1

        _info(f'Overrides scanned: {ov_total}  used: {ov_used}')

        # ── New format: items/*.json (MC 1.21.4+) ─────────────────────────────
        new_files: List[Tuple[str,str]] = []
        for ns in ns_list:
            for f in reader.items_dir_files(ns): new_files.append((ns, f))

        if new_files:
            _sec('New item definitions  (items/  MC 1.21.4+)')
            new_files.sort(key=lambda x: (x[0]!='minecraft', x[1]))
            new_count = 0
            for ns, fpath in new_files:
                stem = PurePosixPath(fpath).stem
                key  = stem if ns=='minecraft' else f'{ns}:{stem}'
                data = reader.read_json(fpath)
                if not isinstance(data, dict): continue
                mn = data.get('model')
                if isinstance(mn, str) and mn.strip(): mn = {'type':_T_MODEL,'model':mn.strip()}
                if not isinstance(mn, dict): continue
                before = len(raw[key])
                walk_tree(mn, stem, res, reader, _Ctx(), 0, raw[key], item_ns=ns)
                new_count += len(raw[key]) - before
            _info(f'New-format entries: {new_count}')

        embedded = _extract_embedded_mapping(reader, res)
        embedded_count = 0
        if embedded:
            _sec('Embedded mappings  (script.json / sprites.json)')
            for item_key, entries in embedded.items():
                raw[item_key].extend(entries)
                embedded_count += len(entries)
            _info(f'Embedded entries: {embedded_count}')

        plugin_entries = _extract_plugin_mapping(reader, res)
        plugin_count = 0
        if plugin_entries:
            _sec('Plugin mappings  (JSON/YAML hints)')
            for item_key, entries in plugin_entries.items():
                raw[item_key].extend(entries)
                plugin_count += len(entries)
            _info(f'Plugin-derived entries: {plugin_count}')

        # ── Finalise ──────────────────────────────────────────────────────────
        _sec('Finalising')
        final: Dict[str, List[Dict]] = {}
        total = 0; fallbacks = 0

        for key in sorted(raw):
            entries = dedup(sort_entries(raw[key]))
            fallbacks += sum(1 for e in entries if e.get('_fb'))
            clean = [clean_entry(e) for e in entries]
            if clean: final[key] = clean; total += len(clean)

        if fallbacks:
            _warn(f'{fallbacks} sprite(s) used fallback paths — '
                  f'verify these textures exist in your Bedrock RP')

        non_mc = [k for k in final if ':' in k]
        if non_mc:
            _warn(f'Non-minecraft keys: {non_mc[:5]}'
                  + ('...' if len(non_mc)>5 else '')
                  + ' — converter.sh now attempts to merge namespaced script mappings')

        _ok(f'Items   : {len(final)}')
        _ok(f'Entries : {total}')
        return final


# ═════════════════════════════════════════════════════════════════════════════
# ZIP INJECTION
# ═════════════════════════════════════════════════════════════════════════════
def inject_zip(pack_path: str, json_str: str, prefix: str) -> Optional[str]:
    base, ext = os.path.splitext(pack_path)
    out = base + '_with_sprites' + ext
    inner_sprites = prefix + 'sprites.json'
    inner_script = prefix + 'script.json'
    fd, tmp = tempfile.mkstemp(suffix=ext); os.close(fd)
    try:
        with zipfile.ZipFile(pack_path,'r') as src:
            with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as dst:
                for info in src.infolist():
                    if info.filename in (inner_sprites, inner_script): continue
                    with src.open(info) as fi, dst.open(info,'w') as fo:
                        shutil.copyfileobj(fi, fo)
                dst.writestr(zipfile.ZipInfo(inner_sprites), json_str.encode('utf-8'))
                dst.writestr(zipfile.ZipInfo(inner_script), json_str.encode('utf-8'))
        if os.path.exists(out): os.remove(out)
        shutil.move(tmp, out); return out
    except Exception as e:
        _err(f'ZIP injection failed: {e}')
        try: os.unlink(tmp)
        except: pass
        return None


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════
def find_pack(directory: str) -> Optional[str]:
    good: List[str] = []; any_zip: List[str] = []; dirs: List[str] = []
    try: entries = sorted(os.listdir(directory))
    except: return None
    for entry in entries:
        full = os.path.join(directory, entry)
        if '_with_sprites' in entry: continue  # skip our own output
        if os.path.isfile(full) and entry.lower().endswith(('.zip','.jar','.mcpack')):
            if not zipfile.is_zipfile(full): continue
            try:
                with zipfile.ZipFile(full,'r') as zf:
                    names = set(zf.namelist())
                    has_meta = ('pack.mcmeta' in names or
                                any(n.endswith('/pack.mcmeta') and n.count('/')<4 for n in names))
                (good if has_meta else any_zip).append(full)
            except: pass
        elif os.path.isdir(full) and os.path.isfile(os.path.join(full,'pack.mcmeta')):
            dirs.append(full)
    if good:
        if len(good) > 1: _warn(f'Multiple packs found — using {os.path.basename(good[0])}')
        return good[0]
    if dirs: return dirs[0]
    if any_zip:
        _warn(f'No pack.mcmeta in {os.path.basename(any_zip[0])!r}, trying anyway')
        return any_zip[0]
    return None


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
def main() -> None:
    # Accept at most one positional argument (the pack path)
    pack_path: Optional[str] = None
    for arg in sys.argv[1:]:
        if arg in ('-h','--help'): print(__doc__); sys.exit(0)
        if arg in ('-v','--version'): print(f'sg.py v{VERSION}'); sys.exit(0)
        if pack_path is None: pack_path = arg
        else:
            _err('Too many arguments. Usage: python sg.py [pack.zip]')
            sys.exit(1)

    print(f"""
  ╔══════════════════════════════════════════════════════════╗
  ║  sg.py v{VERSION}  sprites.json Generator for J2B          ║
  ║  https://github.com/AZPixel-Team/Java2Bedrock             ║
  ║  Zero-flag: drop beside .zip → python sg.py              ║
  ╚══════════════════════════════════════════════════════════╝
""")

    t0 = time.monotonic()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if pack_path is None:
        pack_path = find_pack(script_dir)
        if pack_path is None:
            _err(f'No Java resource pack found in: {script_dir}')
            _err('Fix: python sg.py MyPack.zip')
            sys.exit(1)
        _ok(f'Auto-discovered: {os.path.basename(pack_path)}')

    if not os.path.exists(pack_path):
        _err(f'File not found: {pack_path}'); sys.exit(1)

    # Generate sprites.json data
    final = generate(pack_path)
    json_str = json.dumps(final, indent=4, ensure_ascii=False) + '\n'

    # Write canonical script.json and compatibility sprites.json
    _sec('Writing Output')
    pack_dir = Path(os.path.dirname(os.path.abspath(pack_path)))
    output_dir_raw = os.getenv(OUTPUT_ENV_DIR, '').strip()
    output_dir = Path(output_dir_raw).resolve() if output_dir_raw else pack_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    script_path = output_dir / 'script.json'
    sprites_path = output_dir / 'sprites.json'
    with open(script_path, 'w', encoding='utf-8') as f: f.write(json_str)
    with open(sprites_path, 'w', encoding='utf-8') as f: f.write(json_str)
    _ok(f'script.json   → {script_path}')
    _ok(f'sprites.json  → {sprites_path}')

    # Inject into zip
    if os.path.isfile(pack_path) and zipfile.is_zipfile(pack_path):
        _sec('ZIP Injection')
        # Detect nested prefix
        prefix = ''
        with zipfile.ZipFile(pack_path,'r') as zf:
            names = set(zf.namelist())
        if 'pack.mcmeta' not in names:
            for n in names:
                if n.endswith('/pack.mcmeta'):
                    segs = n.split('/')
                    if 1 <= len(segs)-1 <= 4: prefix = '/'.join(segs[:-1])+'/'; break
        new_zip = inject_zip(pack_path, json_str, prefix)
        if new_zip:
            _ok(f'New zip       → {new_zip}')
            _info('Injected      : sprites.json + script.json')
            _info(f'Run: ./converter.sh {os.path.basename(new_zip)}')
        else:
            _warn('ZIP injection failed.')
            _info('Manually copy script.json/sprites.json into your pack root before converter.sh')

    # Final summary
    elapsed = time.monotonic() - t0
    _sec('Summary')
    total = sum(len(v) for v in final.values())
    _ok(f'Items         : {len(final)}')
    _ok(f'Sprite entries: {total}')
    _info(f'Time          : {elapsed:.2f}s')

    if not final:
        print()
        _warn('sprites.json is EMPTY.')
        _warn('The pack has no item overrides with custom_model_data / damage / damaged.')
        _warn('Ensure your pack uses the correct Java RP structure:')
        _warn('  assets/minecraft/models/item/<item>.json  with an "overrides" array')
        print()
    else:
        print()
        _info('Next step: ./converter.sh <packname>_with_sprites.zip')
        print()


if __name__ == '__main__':
    main()
