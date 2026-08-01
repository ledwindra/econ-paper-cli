"""Application services used by user-facing adapters."""

from econ_paper_cli.services.ingestion import run_ingestion_preflight

__all__ = [
    "run_ingestion_preflight",
]
