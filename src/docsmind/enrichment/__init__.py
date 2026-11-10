"""On-demand enrichment: embeddings (semantic search) and image captions.

Neither runs at ingest time. Both cost money, so the UI/CLI show what is
missing and an up-front estimate, and only run after the user confirms.
"""

from docsmind.enrichment.service import ENRICHMENT_KINDS, EnrichmentService

__all__ = ["ENRICHMENT_KINDS", "EnrichmentService"]
