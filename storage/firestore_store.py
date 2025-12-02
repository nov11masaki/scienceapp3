import os
import json
from typing import Any, Dict, Iterable

try:
    from google.cloud import firestore
except Exception:  # pragma: no cover - import error will surface at runtime if deps missing
    firestore = None


def get_client(project: str | None = None, database: str | None = None):
    """Return a Firestore client. Uses ADC or GOOGLE_APPLICATION_CREDENTIALS.

    If `project` is None, client will use default project from environment/config.
    If `database` is provided, it will be passed to `firestore.Client(..., database=...)`.
    """
    if firestore is None:
        raise RuntimeError("google-cloud-firestore is not installed")
    kwargs = {}
    if project:
        kwargs['project'] = project
    if database:
        # google-cloud-firestore Client accepts a `database` kwarg for non-default DBs
        kwargs['database'] = database
    if kwargs:
        return firestore.Client(**kwargs)
    return firestore.Client()


def save_document(collection: str, doc_id: str, data: Dict[str, Any], project: str | None = None, database: str | None = None):
    client = get_client(project, database=database)
    doc_ref = client.collection(collection).document(doc_id)
    doc_ref.set(data)


def bulk_import(collection: str, items: Iterable[Dict[str, Any]], id_field: str | None = None, project: str | None = None, database: str | None = None):
    """Import iterable of dicts into `collection`.

    If `id_field` is provided, uses that key from each item as the document id.
    Otherwise Firestore will generate document ids.
    """
    client = get_client(project, database=database)
    batch = client.batch()
    count = 0
    for i, item in enumerate(items):
        if id_field and id_field in item:
            doc_ref = client.collection(collection).document(str(item[id_field]))
        else:
            doc_ref = client.collection(collection).document()
        batch.set(doc_ref, item)
        count += 1
        # Firestore batch limit is 500
        if count >= 500:
            batch.commit()
            batch = client.batch()
            count = 0
    if count > 0:
        batch.commit()
