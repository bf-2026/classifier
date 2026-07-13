import csv
import re
from pathlib import Path


class PdfInventoryCsv:
    """Read and write the shared PDF inventory CSV file."""

    FIELDNAMES = [
        "filename",
        "full_path",
        "relative_path",
        "upload_name",
        "group_key",
        "revision",
        "revision_number",
        "creation_time",
        "is_latest",
        "md5_hash",
    ]

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)

    def ensure_parent_dir(self) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

    def exists_and_not_empty(self) -> bool:
        return self.csv_path.exists() and self.csv_path.stat().st_size > 0

    def load_rows(self) -> list[dict[str, str]]:
        if not self.exists_and_not_empty():
            return []

        with open(self.csv_path, newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            return list(reader)

    def load_existing_relative_paths(self) -> set[str]:
        existing_paths = set()
        for row in self.load_rows():
            relative_path = row.get("relative_path")
            if relative_path:
                existing_paths.add(self._normalize_relative_path(relative_path))
        return existing_paths

    def append_row_if_missing(self, row: dict[str, str]) -> bool:
        self.ensure_parent_dir()

        relative_path = self._normalize_relative_path(str(row.get("relative_path", "")))
        if relative_path in self.load_existing_relative_paths():
            return False

        write_header = not self.exists_and_not_empty()
        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=self.FIELDNAMES)
            if write_header:
                writer.writeheader()
            writer.writerow(self._row_for_write(row))
        return True

    def write_rows(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return

        self.ensure_parent_dir()
        write_header = not self.exists_and_not_empty()

        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=self.FIELDNAMES)
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(self._row_for_write(row))

    def _row_for_write(self, row: dict[str, str]) -> dict[str, str]:
        normalized_relative_path = self._normalize_relative_path(str(row.get("relative_path", "")))
        upload_name = row.get("upload_name")
        if upload_name:
            sanitized_upload_name = self._sanitize_upload_name(upload_name)
        else:
            generated_name = normalized_relative_path.replace("/", "--")
            sanitized_upload_name = self._sanitize_generated_upload_name(generated_name)

        return {
            field: (
                sanitized_upload_name
                if field == "upload_name"
                else row.get(field, "")
            )
            for field in self.FIELDNAMES
        }

    def _sanitize_upload_name(self, upload_name: str) -> str:
        """Keep explicitly provided upload names portable and compact."""
        sanitized = self._transliterate_german_characters(str(upload_name))
        sanitized = re.sub(r"[#?]", "", sanitized)
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", sanitized)
        return re.sub(r"-+(?=\.[A-Za-z0-9]+$)", "", sanitized).strip("-")

    def _sanitize_generated_upload_name(self, upload_name: str) -> str:
        """Replace each unsupported generated-name character exactly once."""
        upload_name = self._transliterate_german_characters(upload_name)
        return re.sub(r"[^A-Za-z0-9._-]", "-", upload_name)

    def _transliterate_german_characters(self, value: str) -> str:
        """Convert German umlauts and sharp S to ASCII equivalents."""
        replacements = str.maketrans({
            "ä": "ae",
            "ö": "oe",
            "ü": "ue",
            "Ä": "Ae",
            "Ö": "Oe",
            "Ü": "Ue",
            "ß": "ss",
        })
        return value.translate(replacements)

    def _normalize_relative_path(self, relative_path: str) -> str:
        return relative_path.replace("\\", "/")
