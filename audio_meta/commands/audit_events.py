from __future__ import annotations

import json
from typing import Any, Optional

from ..cache import MetadataCache


def run(
    cache: MetadataCache,
    *,
    limit: int = 50,
    event: Optional[str] = None,
    since: Optional[str] = None,
    json_output: bool = False,
) -> None:
    since_value = (since or "").strip() or None
    since_id: int | None = None
    since_ts: str | None = None
    if since_value:
        if since_value.isdigit():
            since_id = int(since_value)
        else:
            since_ts = since_value
    events = cache.list_audit_events(limit=limit, event=event, since_id=since_id, since=since_ts)
    if not events:
        print("No audit events found.")
        return
    if json_output:
        print(json.dumps(events, indent=2, sort_keys=True))
        return
    for record in events:
        print(f"[{record['id']}] {record['created_at']} {record['event']}")
        payload = record.get("payload") or {}
        if isinstance(payload, dict) and payload:
            for key in sorted(payload):
                print(f"  {key}: {payload[key]}")
