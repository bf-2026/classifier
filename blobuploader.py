import csv
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

load_dotenv(override=True)

# ==========================
# Configuration
# ==========================
ACCOUNT_URL = os.environ["BLOBSTORAGE_ACCOUNT_URL"]
CONTAINER_NAME = "content"
CSV_FILE = Path("./output/pdf_inventory.csv")
OVERWRITE_EXISTING = True


def upload_files() -> tuple[int, int]:
    """Upload inventory files and display progress for the complete batch."""
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))

    blob_service_client = BlobServiceClient(
        account_url=ACCOUNT_URL,
        credential=DefaultAzureCredential(),
    )
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    console = Console()
    uploaded = 0
    failed = 0

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        console=console,
    )

    with progress:
        task_id = progress.add_task("Uploading files", total=len(rows))

        for row in rows:
            local_path = Path(row["full_path"])
            blob_name = row["upload_name"]
            progress.update(task_id, description=f"Uploading {local_path.name}")

            try:
                if not local_path.is_file():
                    raise FileNotFoundError(f"Local file does not exist: {local_path}")

                blob_client = container_client.get_blob_client(blob_name)
                with local_path.open("rb") as data:
                    blob_client.upload_blob(data, overwrite=OVERWRITE_EXISTING)

                uploaded += 1
            except Exception as error:
                failed += 1
                console.print(f"[red]Failed:[/red] {local_path} — {error}")
            finally:
                progress.advance(task_id)

    console.print("\n[bold green]Done[/bold green]")
    console.print(f"Uploaded: {uploaded}")
    console.print(f"Failed:   {failed}")
    console.print(f"Storage account: {ACCOUNT_URL}")
    return uploaded, failed


if __name__ == "__main__":
    upload_files()