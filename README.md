# PDF Inventory, Conversion, Classification, and Upload Pipeline

This project processes document files through a local PDF inventory and an AI classification workflow. It can:

1. Convert Outlook `.msg` email files to lightweight PDFs.
2. Scan PDF folders recursively and create an inventory CSV.
3. Detect duplicate PDFs by MD5 hash.
4. Identify the latest revision in each document group.
5. Classify latest PDFs as text- or image-oriented documents using an Azure OpenAI vision-capable deployment.
6. Upload inventoried files to Azure Blob Storage.

## Project structure

| File                | Purpose                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------- |
| `convertmsgpdf.py`  | Converts `.msg` files to PDFs, preserving the input folder structure and updating the inventory CSV.      |
| `pdfscanner.py`     | Recursively scans PDFs, removes exact duplicates, extracts revision metadata, and marks latest revisions. |
| `inventory_csv.py`  | Reads and writes the shared inventory CSV and generates portable upload names.                            |
| `llm_classifier.py` | Renders the first PDF pages and classifies latest documents with Azure OpenAI.                            |
| `blobuploader.py`   | Uploads files listed in the inventory CSV to Azure Blob Storage.                                          |
| `llm.py`            | Small Azure OpenAI connectivity/example script.                                                           |
| `tests/`            | Automated tests for conversion, inventory handling, scanning, and classification.                         |

## Requirements

- Windows with Python 3.10 or newer recommended.
- Python packages:
  - `extract-msg`
  - `PyMuPDF` (`fitz`)
  - `openai`
  - `python-dotenv`
  - `reportlab`
  - `rich`
  - `azure-identity`
  - `azure-storage-blob`
  - `pytest` for tests

Install the dependencies in the project virtual environment with your preferred package manager. If a dependency file is added later, use that file as the source of truth.

## Configuration

Create or update `.env` in the project root. Do not commit secrets.

### Azure OpenAI

Required by `llm_classifier.py` and `llm.py`:

```text
AZURE_OPENAI_ENDPOINT=https://<your-endpoint>/openai/v1/
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
AZURE_OPENAI_API_KEY=<api-key>
```

The endpoint and deployment must support the OpenAI Responses API used by the project. The classifier sends rendered images of up to the first two pages of each PDF.

### Azure Blob Storage

Required by `blobuploader.py`:

```text
BLOBSTORAGE_ACCOUNT_URL=https://<storage-account>.blob.core.windows.net
```

Blob authentication uses `DefaultAzureCredential`, so the signed-in Azure identity must have permission to upload blobs to the `content` container. The uploader currently overwrites existing blobs.

## Typical workflow

### 1. Convert Outlook messages to PDFs

The default input folder is `data`, and PDFs are written below `output/emails`:

```text
python convertmsgpdf.py
```

Custom paths can be supplied:

```text
python convertmsgpdf.py --input-dir data --output-dir output/emails --inventory-csv output/pdf_inventory.csv
```

The converter extracts sender, recipients, date, subject, and message body. It supports plain-text and HTML message bodies and appends each converted PDF to the inventory unless the relative path is already present.

### 2. Scan PDFs and update the inventory

```text
python pdfscanner.py
```

The default scan target is `output/emails`, with output written to `output/pdf_inventory.csv`.

The scanner:

- Searches recursively for `.pdf` files.
- Skips exact content duplicates.
- Groups revisions using filenames ending in patterns such as `rev01`, `rev02a`, or `rev03`.
- Marks the highest revision as latest; file creation time is used as a tie-breaker.
- Appends only new relative paths to the inventory.

### 3. Classify latest PDFs

```text
python run_llm_classifier.py
```

Only rows whose `is_latest` value is true-like (`True`, `1`, `yes`, or `y`) are sent for classification. Existing rows with an `llm_document_type` are skipped, making repeated runs incremental.

The default inventory is `./output/pdf_inventory.csv`. A custom inventory CSV may be supplied as the only optional argument:

```text
python run_llm_classifier.py path/to/pdf_inventory.csv
```

PDF paths are resolved from each inventory row and relative to the CSV directory when needed. Results are written back to that same CSV, and a Rich progress bar is displayed while latest documents are processed.

The classifier adds these columns to the inventory CSV:

- `llm_document_type`
- `llm_confidence`
- `llm_reason`
- `llm_raw_json`

The supported normalized document types are `text` and `image`; unrecognized model output is recorded as `error`.

### 4. Upload files to Azure Blob Storage

After checking the inventory paths and Azure credentials:

```text
python blobuploader.py
```

The script reads `output/pdf_inventory.csv`, uploads each file using its `upload_name`, and prints uploaded and failed counts. It expects the local paths in the CSV to remain valid.

## Testing

Run the test suite from the repository root:

```text
pytest
```

The tests use temporary directories and mocks for external services, so they do not require access to Azure OpenAI or Azure Blob Storage.

## Data and generated files

- Input `.msg` files are kept under `data/` by default.
- Generated PDFs and inventory files are stored under `output/`.
- `.env` contains local credentials and must remain private.
- Large document collections and generated outputs should generally not be committed to source control.

## Operational notes

- Run conversion before scanning when new `.msg` files have been added.
- Run the scanner again after adding or replacing PDFs so revision flags are current for newly discovered records.
- The classifier writes results back to the CSV and uses existing classifications as a cache.
- Review the inventory CSV before uploading, especially `full_path`, `upload_name`, and `is_latest`.
- The project uses MD5 for duplicate detection and inventory metadata; it is used for content matching, not security.
