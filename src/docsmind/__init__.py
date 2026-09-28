"""docsmind: chat with your documents, track what changed between versions.

Public surface is intentionally small. Everything else is an implementation
detail and may change without notice while the package is 0.x.
"""

from docsmind.client import Docsmind
from docsmind.config import DocsmindConfig
from docsmind.domain.changes import Change, ChangeSet, ImpactAssessment
from docsmind.domain.citations import Citation
from docsmind.domain.documents import Chunk, Document, DocumentVersion, Section
from docsmind.domain.tables import AgentTable

__version__ = "0.2.0"

__all__ = [
    "AgentTable",
    "Change",
    "ChangeSet",
    "Chunk",
    "Citation",
    "Docsmind",
    "DocsmindConfig",
    "Document",
    "DocumentVersion",
    "ImpactAssessment",
    "Section",
    "__version__",
]