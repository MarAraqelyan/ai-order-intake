"""
Load order requests from the data directory.

The manifest.json file maps request IDs to order references and text filenames.
Each .txt file contains the raw customer message for one request.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass(frozen=True)
class RequestRecord:
    request_id: str   # e.g. "R1"
    order_ref: str    # e.g. "O1"
    text: str         # full text of the customer message
    source_file: str  # e.g. "R1.txt"


def load_manifest(data_dir: Path = DATA_DIR) -> list:
    """
    Read data/manifest.json.
    Returns the raw list of dicts: [{"request_id": "R1", "order_ref": "O1", "file": "R1.txt"}, ...]
    """
    manifest_path = Path(data_dir) / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["requests"]


def load_request_text(filename: str, data_dir: Path = DATA_DIR) -> str:
    """
    Read data/requests/<filename> and return the stripped text content.
    Raises FileNotFoundError if the file does not exist.
    """
    file_path = Path(data_dir) / "requests" / filename
    if not file_path.exists():
        raise FileNotFoundError(f"Request file not found: {file_path}")
    return file_path.read_text(encoding="utf-8").strip()


def load_requests_tolerant(data_dir: Path = DATA_DIR) -> tuple:
    """
    Like load_all_requests, but one bad file does not stop the batch.
    Returns (records, errors). Each error is a dict:
      {"request_id", "order_ref", "source_file", "error"}
    A file is malformed if it is missing, not UTF-8 text, or empty.
    """
    records, errors = [], []
    for entry in load_manifest(data_dir):
        try:
            text = load_request_text(entry["file"], data_dir)
            if not text:
                raise ValueError("file is empty")
            records.append(RequestRecord(entry["request_id"], entry["order_ref"],
                                         text, entry["file"]))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            errors.append({"request_id": entry["request_id"], "order_ref": entry["order_ref"],
                           "source_file": entry["file"], "error": f"[malformed-file] {exc}"})
    return records, errors


def load_all_requests(data_dir: Path = DATA_DIR) -> list:
    """
    Load all requests listed in manifest.json.
    Returns a list of RequestRecord objects.
    Raises FileNotFoundError if any file listed in the manifest is missing.
    """
    entries = load_manifest(data_dir)
    records = []
    for entry in entries:
        text = load_request_text(entry["file"], data_dir)
        records.append(RequestRecord(
            request_id=entry["request_id"],
            order_ref=entry["order_ref"],
            text=text,
            source_file=entry["file"],
        ))
    return records
