"""
Command-line runner for processing requests without the Streamlit UI.

Examples:
  python -m src.cli --mode live --only R1
  python -m src.cli --mode replay
"""
import argparse
import json

from src import storage
from src.loader import load_all_requests, DATA_DIR
from src.processing import process_one_request


def main() -> None:
    parser = argparse.ArgumentParser(description="Process order requests.")
    parser.add_argument("--mode", choices=["live", "replay"], default="replay")
    parser.add_argument("--only", nargs="*", help="Request IDs to process (default: all)")
    parser.add_argument("--db", default=storage.DB_PATH, help="SQLite database path")
    args = parser.parse_args()

    storage.init_db(args.db)
    records = load_all_requests(DATA_DIR)
    if args.only:
        records = [r for r in records if r.request_id in set(args.only)]

    for record in records:
        result = process_one_request(record, mode=args.mode, db_path=args.db)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
