"""Read-only previews of tabular files and SQLite schemas in the project."""
import csv
from itertools import islice
import sqlite3
import time


def inspect_project_data(path):
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ValueError('Choose an existing project dataset.')
    if suffix in {'.csv', '.tsv'}:
        return preview_delimited(path, '\t' if suffix == '.tsv' else ',')
    if suffix in {'.db', '.sqlite', '.sqlite3'}:
        return inspect_sqlite(path)
    raise ValueError('Project Data accepts CSV, TSV and SQLite files.')


def preview_delimited(path, delimiter):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        def lines():
            for _ in range(100):
                line = handle.readline(65537)
                if not line:
                    return
                if len(line) > 65536:
                    raise ValueError('A dataset line exceeds 65,536 characters.')
                yield line
        reader = csv.reader(lines(), delimiter=delimiter, strict=True)
        rows = []
        for row in islice(reader, 16):
            rows.append([cell[:240] for cell in row[:20]])
    return {'type': 'delimited', 'preview': rows, 'bytes': path.stat().st_size,
            'preview_limits': 'First 16 records and 20 columns, up to 240 characters per cell.'}


def inspect_sqlite(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        deadline = time.monotonic() + 2
        connection.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        connection.execute('PRAGMA trusted_schema=OFF')
        rows = connection.execute(
            "SELECT name, type, substr(sql, 1, 400) FROM sqlite_schema "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 22"
        ).fetchall()
        return {'type': 'sqlite', 'objects': [{'name': name[:180], 'kind': kind, 'sql': sql}
                                             for name, kind, sql in rows[:20]],
                'truncated': len(rows) > 20, 'bytes': path.stat().st_size}
    finally:
        connection.close()
