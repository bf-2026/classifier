import os
import csv
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

load_dotenv()
# ==========================
# Configuration
# ==========================
ACCOUNT_URL = os.environ["BLOBSTORAGE_ACCOUNT_URL"]
CONTAINER_NAME = "content"
CSV_FILE = "./output/pdf_inventory.csv"

credential = DefaultAzureCredential()

OVERWRITE_EXISTING = True


# ==========================
# Azure Client
# ==========================
blob_service_client = BlobServiceClient(account_url=ACCOUNT_URL, credential=credential)

container_client = blob_service_client.get_container_client(CONTAINER_NAME)

# ==========================
# Upload Files
# ==========================
uploaded = 0
failed = 0

with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)

    for row in reader:
        local_path = row["full_path"]
        blob_name = row["relative_path"].replace("\\", "/").replace("/", "--")


        if not os.path.exists(local_path):
            print(f"[MISSING] {local_path}")
            failed += 1
            continue

        try:
            blob_client = container_client.get_blob_client(blob_name)

            with open(local_path, "rb") as data:
                blob_client.upload_blob(
                    data,
                    overwrite=OVERWRITE_EXISTING
                )

            print(f"[UPLOADED] {local_path} -> {blob_name}")
            uploaded += 1

        except Exception as e:
            print(f"[FAILED] {local_path}")
            print(f"         {e}")
            failed += 1

print("\nDone")
print(f"Uploaded: {uploaded}")
print(f"Failed:   {failed}")