from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent
STAGING_DIR = ROOT_DIR / "staging"
ARCHIVE_SUFFIXES = (".zip", ".jar", ".mcpack")
ENV_URL_KEYS = (
    "PACK_URL",
    "RESOURCE_PACK_URL",
    "INPUT_PACK_URL",
    "GITHUB_PACK_URL",
)


def _log(level: str, message: str) -> None:
    print(f"[CHATBOT:{level}] {message}", flush=True)


def _is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _normalize_github_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"github.com", "www.github.com"}:
        return url

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return url

    owner = parts[0]
    repo = parts[1].removesuffix(".git")

    if "archive" in parts or "releases" in parts:
        return url

    if parsed.path.lower().endswith(ARCHIVE_SUFFIXES):
        return url

    branch = "main"
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]

    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"


def _download(url: str, target_file: Path) -> Path:
    normalized_url = _normalize_github_url(url)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    _log("INFO", f"Downloading pack from {normalized_url}")

    request = urllib.request.Request(
        normalized_url,
        headers={"User-Agent": "Java2Bedrock-Chatbot/1.0"},
    )

    with urllib.request.urlopen(request, timeout=120) as response, target_file.open("wb") as output:
        shutil.copyfileobj(response, output)

    return target_file


def _archive_has_pack_meta(path: Path) -> bool:
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False

    try:
        with zipfile.ZipFile(path, "r") as zip_file:
            names = set(zip_file.namelist())
    except (OSError, zipfile.BadZipFile):
        return False

    if "pack.mcmeta" in names:
        return True

    return any(
        name.endswith("/pack.mcmeta") and name.count("/") < 4
        for name in names
    )


def _discover_local_pack() -> Optional[Path]:
    candidates = sorted(
        path
        for path in ROOT_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in ARCHIVE_SUFFIXES
    )
    if not candidates:
        return None

    preferred = [candidate for candidate in candidates if _archive_has_pack_meta(candidate)]
    if preferred:
        if len(preferred) > 1:
            _log("WARN", f"Multiple packs detected; using {preferred[0].name}")
        return preferred[0]

    _log("WARN", f"No pack.mcmeta detected in archives; using {candidates[0].name}")
    return candidates[0]


def _discover_source() -> Optional[str]:
    for env_key in ENV_URL_KEYS:
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            _log("INFO", f"Using URL from ${env_key}")
            return env_value

    url_file = ROOT_DIR / "pack.url"
    if url_file.exists():
        url_value = url_file.read_text(encoding="utf-8", errors="ignore").strip()
        if url_value:
            _log("INFO", "Using URL from pack.url")
            return url_value

    local_pack = _discover_local_pack()
    if local_pack is not None:
        _log("INFO", f"Using local pack {local_pack.name}")
        return str(local_pack)

    return None


def _stage_pack(source: str) -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    if _is_url(source):
        suffix = ".zip"
        parsed_path = urllib.parse.urlparse(source).path.lower()
        for extension in ARCHIVE_SUFFIXES:
            if parsed_path.endswith(extension):
                suffix = extension
                break
        staged = STAGING_DIR / f"input_pack{suffix}"
        return _download(source, staged)

    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = (ROOT_DIR / source_path).resolve()

    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Local source does not exist: {source_path}")

    staged = STAGING_DIR / f"input_pack{source_path.suffix.lower() or '.zip'}"
    shutil.copyfile(source_path, staged)
    return staged


def _run_step(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    _log("RUN", " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env)
    return result.returncode


def _run_sg(staged_pack: Path, env: dict[str, str]) -> None:
    sg_script = ROOT_DIR / "sg.py"
    if not sg_script.exists():
        _log("WARN", "sg.py not found; skipping script mapping generation")
        return

    sg_env = env.copy()
    sg_env["SG_OUTPUT_DIR"] = str(STAGING_DIR)

    code = _run_step([sys.executable, str(sg_script), str(staged_pack)], ROOT_DIR, sg_env)
    if code != 0:
        _log("WARN", "sg.py failed; converter will continue")


def _run_converter(staged_pack: Path, env: dict[str, str]) -> int:
    converter_script = ROOT_DIR / "converter.sh"
    if not converter_script.exists():
        _log("ERROR", "converter.sh not found")
        return 1

    bash = shutil.which("bash")
    if not bash:
        _log("ERROR", "bash is required to run converter.sh")
        return 1

    converter_env = env.copy()
    converter_env.setdefault("AUTO_MODE", "true")
    converter_env.setdefault("WARN", "false")

    return _run_step([bash, str(converter_script), staged_pack.name], STAGING_DIR, converter_env)


def _print_outputs() -> None:
    target_dir = STAGING_DIR / "target"
    packaged_dir = target_dir / "packaged"

    if not target_dir.exists():
        _log("WARN", "No target directory generated")
        return

    _log("OK", f"Target directory: {target_dir}")

    if packaged_dir.exists():
        artifacts = sorted(path.name for path in packaged_dir.iterdir() if path.is_file())
        if artifacts:
            _log("OK", "Packaged artifacts:")
            for artifact in artifacts:
                print(f"  - {artifact}", flush=True)


def main() -> int:
    explicit_source = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    source = explicit_source or _discover_source()

    if not source:
        _log(
            "ERROR",
            "No input pack found. Drop a .zip/.jar/.mcpack near chatbot.py or set PACK_URL.",
        )
        return 1

    try:
        staged_pack = _stage_pack(source)
    except Exception as exc:
        _log("ERROR", f"Failed to prepare input pack: {exc}")
        return 1

    base_env = os.environ.copy()
    _run_sg(staged_pack, base_env)
    result = _run_converter(staged_pack, base_env)

    if result != 0:
        _log("ERROR", "Conversion failed")
        return result

    _print_outputs()
    _log("DONE", "Conversion pipeline completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
