import os
import sys
from functools import lru_cache
from pathlib import Path

from app.config import settings

# STEP 1: set env BEFORE importing pageindex_src
os.environ["OPENAI_API_BASE"] = settings.LLM_BASE_URL
os.environ["OPENAI_API_KEY"] = settings.LLM_API_KEY or "empty"

# STEP 2: make pageindex_src importable as a package (it uses relative imports)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pageindex_src import PageIndexClient  # noqa: E402


def _fmt_model(model: str) -> str:
    """Prefix with openai/ so litellm routes to the OpenAI-compatible backend."""
    if model.startswith("openai/"):
        return model
    return f"openai/{model}"


@lru_cache(maxsize=1)
def get_pi_client() -> PageIndexClient:
    """Singleton PageIndexClient."""
    model = settings.LLM_MODEL
    retrieve_model = settings.LLM_RETRIEVE_MODEL or model
    workspace = Path(settings.FILES_DIR) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    return PageIndexClient(
        model=_fmt_model(model),
        retrieve_model=_fmt_model(retrieve_model),
        workspace=str(workspace),
    )
