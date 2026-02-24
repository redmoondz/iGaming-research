"""File utilities for atomic writes, sanitization, and JSON handling."""

import csv
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def sanitize_filename(name: str) -> str:
    """
    Sanitize company name for use as filename.

    Args:
        name: Company name to sanitize.

    Returns:
        Safe filename string.
    """
    # Replace problematic characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Replace multiple spaces/underscores with single underscore
    sanitized = re.sub(r'[\s_]+', '_', sanitized)
    # Remove leading/trailing underscores and dots
    sanitized = sanitized.strip('_.')
    # Limit length
    sanitized = sanitized[:100]
    return sanitized or "unnamed"


def generate_company_filename(name: str, website: Optional[str] = None) -> str:
    """
    Generate unique filename for a company.

    Includes hash to handle duplicates with same name but different websites.

    Args:
        name: Company name.
        website: Optional website for uniqueness.

    Returns:
        Filename without extension.
    """
    sanitized = sanitize_filename(name)
    # Create hash from name + website for uniqueness
    unique_str = f"{name}_{website or ''}"
    hash_suffix = hashlib.md5(unique_str.encode()).hexdigest()[:8]
    return f"{sanitized}_{hash_suffix}"


def atomic_write_json(filepath: Path, data: Any, indent: int = 2) -> None:
    """
    Atomically write JSON data to file.

    Writes to temp file first, then renames for crash safety.

    Args:
        filepath: Target file path.
        data: Data to serialize to JSON.
        indent: JSON indentation level.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (for atomic rename)
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=filepath.parent,
        suffix='.tmp',
        delete=False
    ) as tmp_file:
        json.dump(data, tmp_file, indent=indent, ensure_ascii=False)
        tmp_path = Path(tmp_file.name)

    # Atomic rename
    tmp_path.rename(filepath)


def load_json(filepath: Path) -> Optional[Any]:
    """
    Load JSON from file.

    Args:
        filepath: File to load.

    Returns:
        Parsed JSON data or None if file doesn't exist.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return None

    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_csv(filepath: Path, rows: List[Dict[str, Any]]) -> None:
    """
    Save list of dicts to CSV file.

    Args:
        filepath: Target CSV file.
        rows: List of dictionaries with consistent keys.
    """
    if not rows:
        return

    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(rows[0].keys())

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_csv(filepath: Path) -> List[Dict[str, str]]:
    """
    Load CSV file to list of dicts.

    Args:
        filepath: CSV file to load.

    Returns:
        List of row dictionaries.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return []

    with open(filepath, 'r', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t|;')
        except csv.Error:
            dialect = csv.excel  # fallback to comma
        reader = csv.DictReader(f, dialect=dialect)
        return list(reader)


class IndexManager:
    """Manages the _index.json file for tracking processed companies."""

    def __init__(self, raw_output_dir: Path):
        self.index_file = raw_output_dir / "_index.json"
        self._index: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load index from file."""
        data = load_json(self.index_file)
        self._index = data if isinstance(data, dict) else {}

    def save(self) -> None:
        """Save index to file."""
        atomic_write_json(self.index_file, self._index)

    def is_processed(self, company_name: str) -> bool:
        """Check if company has been processed."""
        return company_name in self._index

    def add(self, company_name: str, filename: str) -> None:
        """Add company to index."""
        self._index[company_name] = filename
        self.save()

    def get_filename(self, company_name: str) -> Optional[str]:
        """Get filename for a company."""
        return self._index.get(company_name)

    def get_all_processed(self) -> set:
        """Get set of all processed company names."""
        return set(self._index.keys())

    def count(self) -> int:
        """Get count of processed companies."""
        return len(self._index)


class ErrorLogger:
    """Manages the _errors.json file for tracking processing errors."""

    def __init__(self, raw_output_dir: Path):
        self.error_file = raw_output_dir / "_errors.json"
        self._errors: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Load errors from file."""
        data = load_json(self.error_file)
        self._errors = data if isinstance(data, list) else []

    def save(self) -> None:
        """Save errors to file."""
        atomic_write_json(self.error_file, self._errors)

    def add(
        self,
        company_name: str,
        error_type: str,
        error_message: str,
        timestamp: str,
        details: Optional[Dict] = None
    ) -> None:
        """Add error entry."""
        self._errors.append({
            "company_name": company_name,
            "error_type": error_type,
            "error_message": error_message,
            "timestamp": timestamp,
            "details": details or {}
        })
        self.save()

    def get_failed_companies(self) -> List[str]:
        """Get list of companies that failed processing."""
        return [e["company_name"] for e in self._errors]

    def count(self) -> int:
        """Get count of errors."""
        return len(self._errors)
