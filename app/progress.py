"""Document processing progress tracking utilities."""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProcessingProgress:
    """Progress tracker for document processing.

    Attributes:
        document_id: ID of the document being processed
        current_page: Current page being processed (0-indexed)
        total_pages: Total number of pages in the document
        started_at: Processing start timestamp
        estimated_time_seconds: Estimated total processing time
        elapsed_seconds: Time elapsed so far
    """
    document_id: str
    current_page: int
    total_pages: int
    started_at: str
    estimated_time_seconds: Optional[float] = None
    elapsed_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert progress to dictionary for API response."""
        progress_percent = (self.current_page + 1) / self.total_pages * 100 if self.total_pages > 0 else 0

        remaining_time = None
        if self.elapsed_seconds and progress_percent > 0:
            # Calculate remaining time based on current rate
            remaining_pages = self.total_pages - self.current_page - 1
            if remaining_pages > 0 and progress_percent < 100:
                avg_processing_time = self.elapsed_seconds / max(1, self.current_page + 1)
                remaining_time = avg_processing_time * remaining_pages

        return {
            "document_id": self.document_id,
            "current_page": self.current_page + 1,  # 1-indexed for user display
            "total_pages": self.total_pages,
            "progress_percent": round(progress_percent, 2),
            "started_at": self.started_at,
            "elapsed_seconds": self.elapsed_seconds or 0,
            "estimated_remaining_seconds": remaining_time or 0
        }


class ProgressTracker:
    """Singleton progress tracker for document processing."""

    _instance = None
    _progress_data: dict[str, ProcessingProgress] = {}  # document_id -> progress

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def update_progress(cls, document_id: str, current_page: int, total_pages: int,
                        estimated_time: Optional[float] = None) -> None:
        """Update processing progress for a document.

        Args:
            document_id: ID of the document
            current_page: Current page number (0-indexed)
            total_pages: Total pages in document
            estimated_time: Estimated total processing time in seconds
        """
        started_at = cls._progress_data.get(document_id, {}).get('started_at', datetime.now(UTC).isoformat())

        cls._progress_data[document_id] = ProcessingProgress(
            document_id=document_id,
            current_page=current_page,
            total_pages=total_pages,
            started_at=started_at,
            estimated_time_seconds=estimated_time,
            elapsed_seconds=None  # Will be calculated when queried
        )

        logger.info(f"Progress updated: {document_id} at page {current_page+1}/{total_pages}")

    @classmethod
    def get_progress(cls, document_id: str) -> dict | None:
        """Get processing progress for a specific document.

        Args:
            document_id: ID of the document

        Returns:
            Progress dictionary or None if not found
        """
        progress = cls._progress_data.get(document_id)
        if not progress:
            return None

        # Calculate elapsed time
        from datetime import datetime
        started = datetime.fromisoformat(progress.started_at.replace('+00:00', ''))
        elapsed = (datetime.now(UTC) - started).total_seconds()
        progress.elapsed_seconds = elapsed

        return progress.to_dict()

    @classmethod
    def clear_progress(cls, document_id: str) -> None:
        """Clear progress tracking for completed or failed document."""
        cls._progress_data.pop(document_id, None)
        logger.info(f"Progress cleared for {document_id}")

    @classmethod
    def get_all_progress(cls) -> list[dict]:
        """Get all active processing progress.

        Returns:
            List of progress dictionaries
        """
        return [cls.get_progress(doc_id) for doc_id in cls._progress_data if cls.get_progress(doc_id)]
