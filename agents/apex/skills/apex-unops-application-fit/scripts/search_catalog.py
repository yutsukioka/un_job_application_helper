#!/usr/bin/env python3
"""Search a supplied CSV locally and return bounded complete catalog records."""
from __future__ import annotations
import argparse
import json
from validate_unops_fit import load_catalog
from pathlib import Path


def search(catalog, query, limit=10):
    terms = query.casefold().split()
    if not terms or not 1 <= limit <= 50:
        raise ValueError('Supply a nonempty query and a limit from 1 to 50.')
    hits = []
    for name, record in catalog.records.items():
        text = (name + ' ' + record['description']).casefold()
        if all(term in text for term in terms):
            hits.append({'name': name, **record})
    hits.sort(key=lambda item: (item['name'].casefold() != query.casefold(),
                               not all(t in item['name'].casefold() for t in terms), item['name']))
    return {'catalog_sha256': catalog.sha256, 'matching_records': len(hits),
            'returned_records': min(limit, len(hits)), 'skills': hits[:limit]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--query', required=True)
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    try:
        print(json.dumps(search(load_catalog(args.catalog), args.query, args.limit), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
