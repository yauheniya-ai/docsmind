"""docsmind: chat with your documents, track what changed between versions.

Public surface is intentionally small. Everything else is an implementation
detail and may change without notice while the package is 0.x.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from docsmind.client import Docsmind
from docsmind.config import DocsmindConfig
from docsmind.domain.changes import ChangeAnalysis, ChangeResult, ChangeRow
from docsmind.domain.citations import Citation
from docsmind.domain.documents import Chunk, Document, DocumentVersion, Section
from docsmind.domain.tables import AgentTable


def _get_version() -> str:
    """Single source of truth: pyproject.toml's `[project] version`, read back from the installed
    package's metadata. Never hardcode the number anywhere else (FastAPI, the CLI and the UI all read
    it from here), so bumping pyproject.toml is the only change a release needs."""
    try:
        return _pkg_version("docsmind")
    except PackageNotFoundError:  # a source tree that was never installed
        return "unknown"


__version__ = _get_version()

__all__ = [
    "AgentTable",
    "ChangeAnalysis",
    "ChangeResult",
    "ChangeRow",
    "Chunk",
    "Citation",
    "Docsmind",
    "DocsmindConfig",
    "Document",
    "DocumentVersion",
    "Section",
    "__version__",
]