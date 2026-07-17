import hashlib
import os
import re
from datetime import datetime

from pathlib import Path


from dotenv import load_dotenv

from inventory_csv import PdfInventoryCsv


# Rich UI imports

from rich.console import Console

from rich.live import Live

from rich.panel import Panel

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskProgressColumn, TextColumn

from rich.table import Table



class PDFScanner:

    """Scan a directory for PDF files and export their metadata to CSV."""


    def __init__(self, dir: str, output: str = "./output/pdf_inventory.csv"):

        """Initialize the scanner.


        :param dir: The path to the folder you want to scan.

        :param output: The path where the CSV file should be saved.
        """

        self.dir = Path(dir)

        self.output = Path(output)

        self.console = Console()


    def scan_and_export(self):

        """Trigger the folder scan and write the results to the CSV."""

        if not self.dir.is_dir():

            self.console.print(f"[bold red]Error:[/bold red] The directory '{self.dir}' does not exist.")
            return


        # --- 1. Find and parse PDFs (Progress bar active here) ---

        pdf_files = self._find_pdfs()


        if not pdf_files:

            self.console.print(f"[yellow]No PDF files found in '{self.dir}'.[/yellow]")
            return


        # --- 2. Filter New Files ---

        inventory_store = PdfInventoryCsv(self.output)

        existing_paths = inventory_store.load_existing_relative_paths()
        

        new_files = [
            item
            for item in pdf_files

            if self._normalize_relative_path(item["relative_path"]) not in existing_paths

        ]


        total_files = len(pdf_files)

        added_files = len(new_files)

        skipped_files = total_files - added_files


        if added_files > 0:

            inventory_store.write_rows(new_files)


        # --- 3. Final Detailed Summary Dashboard ---

        summary_title = "[white]Scan & Export to csv Completed Successfully[/white]"

        summary_text = (

            f"├── Total PDFs Analyzed:  [bold cyan]{total_files}[/bold cyan]\n"

            f"├── New Records Saved:    [bold green]{added_files}[/bold green]\n"

            f"└── Existing (Skipped):   [dim]{skipped_files}[/dim]"
        )
        

        self.console.print()  # Spacer
        self.console.print(

            Panel(

                summary_text,

                title=summary_title,

                title_align="left",

                border_style="white",

                expand=False,
            )
        )


    def _normalize_relative_path(self, relative_path: str) -> str:

        """Normalize paths so Windows and POSIX separators compare equally."""

        return Path(relative_path).as_posix()


    def _find_pdfs(self) -> list:

        """Recursively search for PDFs, skip exact duplicates, and mark latest revisions."""
        

        # Pre-scan directory to get a total file count for the progress bar bounds

        all_paths = list(self.dir.rglob("*"))

        pdf_paths = [p for p in all_paths if p.is_file() and p.suffix.lower() == ".pdf"]

        total_files = len(pdf_paths)


        if total_files == 0:

            return []


        pdf_list = []

        grouped_files = {}

        seen_hashes = set()

        duplicates_count = 0  # To tracking skipped duplicates dynamically


        # Rich Progress Configuration

        progress = Progress(

            TextColumn("[cyan]Scanning PDFs[/cyan]"),

            BarColumn(bar_width=30, complete_style="cyan", finished_style="bright_green"),

            TaskProgressColumn(),

            MofNCompleteColumn(),
        )

        task_id = progress.add_task("scanning", total=total_files)


        # Live Layout Grid setup

        def make_layout(current_file: str, dup_count: int) -> Table:

            grid = Table.grid(expand=True)

            grid.add_column()

            grid.add_row(progress)
            

            # Show active duplicates warning in dim/yellow depending on matches

            dup_color = "yellow" if dup_count > 0 else "dim"

            grid.add_row(f" └── 📑 [dim]{current_file:<40}[/dim] [dim]({dup_count} dups skipped)[/dim]"
)
            return grid


        current_filename = "Starting scan..."


        # Live Context Context Manager

        with Live(make_layout(current_filename, duplicates_count), refresh_per_second=12, console=self.console) as live:

            for file_path in pdf_paths:

                current_filename = file_path.name

                live.update(make_layout(current_filename, duplicates_count))


                file_hash = self._md5_hash(file_path)


                # Skip exact duplicate files by content.

                if file_hash in seen_hashes:

                    duplicates_count += 1

                    progress.advance(task_id)

                    live.update(make_layout(current_filename, duplicates_count))
                    continue
                    
                seen_hashes.add(file_hash)


                relative_path = file_path.relative_to(self.dir.parent).as_posix()

                group_key, revision = self._parse_revision_key(file_path.stem)

                revision_number = self._parse_revision_number(revision)

                creation_timestamp = self._file_creation_time(file_path)

                file_size = file_path.stat().st_size


                item = {

                    "filename": file_path.name,

                    "full_path": str(file_path.resolve()),

                    "relative_path": str(relative_path),

                    "file_size": file_size,

                    "reduced": "True" if file_path.stem.lower().endswith("-reduced") else "False",

                    "group_key": group_key,

                    "revision": revision,

                    "revision_number": revision_number,

                    "creation_time": self._format_creation_time(creation_timestamp),

                    "creation_timestamp": creation_timestamp,

                    "md5_hash": file_hash,

                }


                grouped_files.setdefault(group_key, []).append(item)
                

                progress.advance(task_id)

                live.update(make_layout(current_filename, duplicates_count))


        # Finalize the revisions calculations

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



# --- Example Usage ---

if __name__ == "__main__":

    load_dotenv(override=True)  # Load environment variables from .env file
    pdf_folder = os.getenv("PDF_FOLDER")


    scanner = PDFScanner(dir=pdf_folder)

    scanner.scan_and_export()