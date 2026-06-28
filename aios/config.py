from __future__ import annotations

import os
from pathlib import Path


def load_env(dotenv_path: Path | None = None) -> None:
    """Load .env file from the given path or search upward from cwd."""
    path = dotenv_path or _find_dotenv()
    if path and path.exists():
        _parse_dotenv(path)


def _find_dotenv() -> Path | None:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


def _parse_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_key(env_var: str, hint: str = "") -> str:
    """
    Return an env var value or raise a clear error with setup instructions.
    """
    value = os.environ.get(env_var)
    if not value:
        lines = [
            f"\n  Missing environment variable: {env_var}",
            "",
            "  Add it to your .env file:",
            f"    {env_var}=your-key-here",
        ]
        if hint:
            lines += ["", f"  {hint}"]
        lines += ["", "  Or export it in your shell:", f"    export {env_var}=your-key-here", ""]
        raise OSError("\n".join(lines))
    return value


def get_model_key(model: str) -> str | None:
    """Return the relevant API key env var for a given model string."""
    model = model.lower()
    if model.startswith("claude") or "anthropic" in model:
        return os.environ.get("ANTHROPIC_API_KEY")
    if model.startswith("gpt") or "openai" in model:
        return os.environ.get("OPENAI_API_KEY")
    if model.startswith("gemini") or "google" in model:
        return os.environ.get("GOOGLE_API_KEY")
    if model.startswith("ollama") or model.startswith("ollama/"):
        return "local"  # no key needed
    return None


def validate_model_key(model: str) -> None:
    """Raise a clear error if the API key for the given model is missing."""
    model_lower = model.lower()
    if model_lower.startswith("ollama"):
        return  # local, no key

    env_map = {
        "claude": ("ANTHROPIC_API_KEY", "Get yours at https://console.anthropic.com"),
        "gpt": ("OPENAI_API_KEY", "Get yours at https://platform.openai.com/api-keys"),
        "gemini": ("GOOGLE_API_KEY", "Get yours at https://aistudio.google.com/app/apikey"),
        "mistral": ("MISTRAL_API_KEY", "Get yours at https://console.mistral.ai"),
    }
    for prefix, (env_var, hint) in env_map.items():
        if model_lower.startswith(prefix):
            require_key(env_var, hint)
            return
