"""Migrate local JSON files to Firestore collections.

Usage examples:
  GCP_PROJECT_ID=your-project \
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json \
  python tools/migrate_to_firestore.py --files learning_progress.json session_storage.json summary_storage.json

Requirements:
  - Firestore API enabled on the target project
  - Billing enabled and the credentials/account have Firestore write permissions
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from storage.firestore_store import bulk_import, save_document


def load_json_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def migrate_file_to_collection(path: Path, collection: str, id_field: str | None = None, project: str | None = None, database: str | None = None):
    data = load_json_file(path)
    print(f"Loaded {len(data)} top-level items from {path}")
    # If data is a dict mapping ids -> objects, convert to list of dicts and use keys as ids
    if isinstance(data, dict):
        items = []
        # try to detect simple mapping patterns
        for key, value in data.items():
            if isinstance(value, dict):
                doc = dict(value)
                # preserve original id under `_id` if id_field not provided
                if id_field:
                    doc[id_field] = key
                else:
                    doc["_id"] = key
                items.append(doc)
            else:
                items.append({"_id": key, "value": value})
        if id_field:
            print(f"Importing into collection '{collection}' using id field '{id_field}'")
            bulk_import(collection, items, id_field=id_field, project=project, database=database)
        else:
            print(f"Importing into collection '{collection}' with generated IDs; original keys stored in '_id' field")
            bulk_import(collection, items, id_field=None, project=project, database=database)
    elif isinstance(data, list):
        bulk_import(collection, data, id_field=id_field, project=project, database=database)
    else:
        # single object
        save_document(collection, path.stem, data, project=project, database=database)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="+", help="Local JSON files to migrate (paths)")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID"), help="GCP project id")
    parser.add_argument("--database", default=os.environ.get("FIRESTORE_DATABASE"), help="Firestore database id (non-default)")
    parser.add_argument("--collection-prefix", default="sb_")
    parser.add_argument("--id-field", default=None, help="If provided, use this key from each item as doc id")
    args = parser.parse_args()

    if not args.files:
        print("No files provided; nothing to do")
        sys.exit(1)

    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"File not found: {p}")
            continue
        collection = f"{args.collection_prefix}{p.stem}"
        print(f"Migrating {p} -> collection {collection}")
        try:
            migrate_file_to_collection(p, collection, id_field=args.id_field, project=args.project, database=args.database)
            print(f"Finished migrating {p}")
        except Exception as e:
            print(f"Failed to migrate {p}: {e}")


if __name__ == "__main__":
    main()
