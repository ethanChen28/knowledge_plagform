"""Document preprocessing utilities for handling large files."""
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Tuple

import pypdf

logger = logging.getLogger(__name__)

# Default threshold for splitting large documents
DEFAULT_MAX_PAGES = 200  # Documents with more than 200 pages will be split


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get total page count of a PDF file.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Number of pages in the PDF
    """
    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            return len(reader.pages)
    except Exception as e:
        logger.error(f"Failed to get page count: {e}")
        # If cannot determine pages, return 0 as fallback
        return 0


def split_pdf_into_chunks(
    pdf_path: Path,
    output_dir: Path,
    chunk_size: int = 50
) -> List[Tuple[Path, int, int]]:
    """Split a large PDF into multiple smaller chunks.

    Args:
        pdf_path: Path to original PDF
        output_dir: Directory to store split PDFs
        chunk_size: Number of pages per chunk (default 50)

    Returns:
        List of tuples: (chunk_path, start_page, end_page)
    """
    chunks = []

    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            total_pages = len(reader.pages)

            if total_pages == 0:
                logger.warning(f"PDF has no pages, skip splitting")
                return []

            # Split into chunks of chunk_size pages
            for start_idx in range(0, total_pages, chunk_size):
                # Extract a chunk of pages
                end_idx = min(start_idx + chunk_size, total_pages)
                writer = pypdf.PdfWriter()

                for page_num in range(start_idx, end_idx):
                    writer.add_page(reader.pages[page_num])

                # Save chunk with suffix indicating page range
                chunk_name = f"{pdf_path.stem}_pages_{start_idx+1}_to_{end_idx}.pdf"
                chunk_path = output_dir / chunk_name

                with open(chunk_path, 'wb') as chunk_file:
                    writer.write(chunk_file)

                chunks.append((chunk_path, start_idx + 1, end_idx))
                logger.info(f"Split chunk: pages {start_idx+1}-{end_idx}, saved to {chunk_path}")

        logger.info(f"Successfully split {total_pages}-page PDF into {len(chunks)} chunks")

    except Exception as e:
        logger.error(f"Failed to split PDF: {e}")
        raise

    return chunks


def preprocess_large_document(
    file_path: Path,
    output_dir: Path,
    max_pages: int = DEFAULT_MAX_PAGES
) -> Tuple[bool, List[Path]]:
    """Check if document needs preprocessing and split if necessary.

    Args:
        file_path: Path to uploaded file
        output_dir: Directory to store processed files
        max_pages: Maximum allowed pages before splitting

    Returns:
        (needs_split, file_paths) - Whether splitting was needed, and paths to use
    """
    # Only process PDF files
    if not str(file_path).endswith('.pdf'):
        return False, [file_path]

    # Get page count
    try:
        page_count = get_pdf_page_count(file_path)
    except Exception as e:
        logger.warning(f"Cannot determine page count, skip preprocessing: {e}")
        return False, [file_path]

    # If small enough, no need to split
    if page_count <= max_pages:
        logger.info(f"PDF has {page_count} pages, within limit (max {max_pages}), no split needed")
        return False, [file_path]

    # Split into chunks of 50 pages
    logger.info(f"Large PDF detected: {page_count} pages > {max_pages} threshold, will split")
    chunks = split_pdf_into_chunks(file_path, output_dir, chunk_size=50)

    # Return all chunk paths
    return True, [chunk_path for chunk_path, _, _ in chunks]
