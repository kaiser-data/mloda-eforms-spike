"""Rebuild data/ from public sources: the pinned eForms SDK fields.json and the TED notices in data/ids.txt.

The notices are not in git because they contain procurement officers' contact details. TED publishes
them, but this repo doesn't need to keep copies.
Usage: python3 -I src/fetch_data.py data
"""

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SDK = "https://raw.githubusercontent.com/OP-TED/eForms-SDK/1.13.0/fields/fields.json"
NOTICE = "https://ted.europa.eu/en/notice/{}/xml"


def get(url: str) -> bytes:
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:  # nosec B310 - fixed https URLs
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(5 * (attempt + 1))  # TED rate-limits bursts
    raise RuntimeError(f"still rate-limited: {url}")


def main() -> None:
    data = Path(sys.argv[1])
    (data / "xml").mkdir(parents=True, exist_ok=True)
    if not (data / "fields.json").exists():
        (data / "fields.json").write_bytes(get(SDK))
    ids = [i.strip() for i in (data / "ids.txt").read_text().split() if i.strip()]
    for n, pub in enumerate(ids, 1):
        out = data / "xml" / f"{pub}.xml"
        if not out.exists():
            out.write_bytes(get(NOTICE.format(pub)))
            time.sleep(1)
        print(f"\r{n}/{len(ids)}", end="", flush=True)
    print(f"\nfetched {len(ids)} notices into {data / 'xml'}")


if __name__ == "__main__":
    main()
