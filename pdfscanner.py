import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path




class PDFScanner:

    """Scan a directory for PDF files and export their metadata to CSV."""

    def __init__(self, dir: str, output: str):
        """Initialize the scanner.

        :param dir: The path to the folder you want to scan.
        :param output: The path where the CSV file should be saved.
        """
        self.dir = Path(dir)
        self.output = Path(output)

    def scan_and_export(self):
        """Trigger the folder scan and write the results to the CSV."""
        if not self.dir.is_dir():
            print(f"Error: The directory '{self.dir}' does not exist.")
            return

        pdf_files = self._find_pdfs()

        if not pdf_files:
            print(f"No PDF files found in '{self.dir}'.")
            return

        existing_paths = self._load_existing_relative_paths()
        new_files = [
            item
            for item in pdf_files
            if self._normalize_relative_path(item["relative_path"]) not in existing_paths
        ]

        if not new_files:
            print(f"No new PDF files to add to '{self.output}'.")
            return

        self._write_to_csv(new_files)
        print(
            f"Success! Found {len(new_files)} new PDFs and saved them to '{self.output}'."
        )

    def _load_existing_relative_paths(self) -> set[str]:
        """Load relative paths already present in the CSV output file."""
        if not self.output.exists() or self.output.stat().st_size == 0:
            return set()

        existing_paths = set()
        with open(self.output, newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                relative_path = row.get("relative_path")
                if relative_path:
                    existing_paths.add(self._normalize_relative_path(relative_path))
        return existing_paths

    def _normalize_relative_path(self, relative_path: str) -> str:
        """Normalize paths so Windows and POSIX separators compare equally."""
        return Path(relative_path).as_posix()

    def _find_pdfs(self) -> list:

        """Recursively search for PDFs, skip exact duplicates, and mark latest revisions."""

        pdf_list = []
        grouped_files = {}
        seen_hashes = set()

        # rglob("*") searches recursively through all subfolders.
        for file_path in self.dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() == ".pdf":
                file_hash = self._md5_hash(file_path)

                # Skip exact duplicate files by content.
                if file_hash in seen_hashes:
                    continue
                seen_hashes.add(file_hash)

                relative_path = file_path.relative_to(self.dir.parent).as_posix()
                group_key, revision = self._parse_revision_key(file_path.stem)
                revision_number = self._parse_revision_number(revision)
                creation_timestamp = self._file_creation_time(file_path)


                item = {
                    "filename": file_path.name,
                    "relative_path": str(relative_path),
                    "group_key": group_key,
                    "revision": revision,
                    "revision_number": revision_number,
                    "creation_time": self._format_creation_time(creation_timestamp),
                    "creation_timestamp": creation_timestamp,
                    "md5_hash": file_hash,
                }

                grouped_files.setdefault(group_key, []).append(item)

        for items in grouped_files.values():
            latest_item = max(items, key=self._revision_sort_key)

            for item in items:
                item["is_latest"] = item is latest_item
                pdf_list.append(item)

        return pdf_list

    def _parse_revision_key(self, stem: str) -> tuple[str, str]:
        """Split a filename stem into a group key and revision string."""

        match = re.search(r"[\s_]+(rev\d+[a-z]?)$", stem, flags=re.IGNORECASE)
        if match:
            revision = match.group(1)
            group_key = stem[: match.start()].strip(" _")
            return group_key, revision
        return stem, ""

    def _parse_revision_number(self, revision: str) -> int:
        """Extract the numeric revision portion used for sorting."""
        match = re.match(r"rev(\d+)", revision or "", flags=re.IGNORECASE)
        if not match:
            return -1
        return int(match.group(1))

    def _file_creation_time(self, file_path: Path) -> float:
        """Return the file creation time used as a tiebreaker."""
        return file_path.stat().st_ctime


    def _format_creation_time(self, timestamp: float) -> str:
        """Format a timestamp as a human-readable date and time string."""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _revision_sort_key(self, item: dict):
        """Return a key that sorts by revision number and then creation timestamp."""
        return (
            item.get("revision_number", -1),
            item.get("creation_timestamp", -1.0),
        )


    def _md5_hash(self, file_path: Path) -> str:

        """Return the MD5 hash for a file."""

        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _write_to_csv(self, data: list):
        """Append the extracted data to the CSV file if they are not already present."""
        fieldnames = [
            "filename",
            "relative_path",
            "group_key",
            "revision",
            "revision_number",
            "creation_time",
            "is_latest",
            "md5_hash",
        ]


        # Ensure the output directory exists before writing.

        self.output.parent.mkdir(parents=True, exist_ok=True)

        file_exists = self.output.exists()
        write_header = not file_exists or self.output.stat().st_size == 0

        rows = [{key: item.get(key, "") for key in fieldnames} for item in data]

        with open(self.output, mode="a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)





# --- Example Usage ---
if __name__ == "__main__":
    TARGET_FOLDER = r"C:\Users\DELL\Lucas\DPA\DeebKBB\Daten_fuer_KI"
    #TARGET_FOLDER = "./docs"
    CSV_OUTPUT = "./pdf_inventory.csv"

    scanner = PDFScanner(dir=TARGET_FOLDER, output=CSV_OUTPUT)
    scanner.scan_and_export()