import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

SCHEMA_VERSION = 12

MIGRATION_5 = (
    (
        "decks",
        "CREATE INDEX IF NOT EXISTS idx_decks_org_year_imported ON decks(organisation_id, year, imported_at DESC)",
    ),
    (
        "decks",
        "CREATE INDEX IF NOT EXISTS idx_decks_year_imported ON decks(year, imported_at DESC)",
    ),
    ("decks", "CREATE INDEX IF NOT EXISTS idx_decks_name ON decks(name)"),
    ("slides", "CREATE INDEX IF NOT EXISTS idx_slides_title ON slides(title)"),
)

MIGRATION_2 = (
    """
    CREATE TABLE organisations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE decks (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        storage_mode TEXT NOT NULL CHECK (storage_mode IN ('managed_copy', 'reference')),
        file_size INTEGER NOT NULL,
        modified_at TEXT,
        imported_at TEXT NOT NULL,
        slide_count INTEGER NOT NULL DEFAULT 0,
        organisation_id TEXT REFERENCES organisations(id) ON DELETE SET NULL,
        year INTEGER,
        description TEXT,
        description_status TEXT NOT NULL DEFAULT 'Not Reviewed',
        CHECK (slide_count >= 0),
        CHECK (file_size >= 0)
    )
    """,
    """
    CREATE TABLE slides (
        id TEXT PRIMARY KEY,
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        slide_number INTEGER NOT NULL CHECK (slide_number > 0),
        internal_slide_id TEXT NOT NULL,
        title TEXT,
        text_content TEXT NOT NULL DEFAULT '',
        speaker_notes TEXT NOT NULL DEFAULT '',
        description TEXT,
        description_status TEXT NOT NULL DEFAULT 'Not Reviewed',
        CHECK (slide_number > 0),
        UNIQUE (deck_id, internal_slide_id),
        UNIQUE (deck_id, slide_number)
    )
    """,
    """
    CREATE TABLE source_locations (
        id TEXT PRIMARY KEY,
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        source_path TEXT,
        logical_path TEXT NOT NULL,
        source_type TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        UNIQUE (deck_id, source_type, logical_path)
    )
    """,
    """
    CREATE TABLE file_checksums (
        sha256 TEXT PRIMARY KEY,
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        file_size INTEGER NOT NULL CHECK (file_size >= 0),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE import_jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (
            status IN ('queued', 'processing', 'completed', 'completed_with_errors', 'failed')
        ),
        storage_mode TEXT NOT NULL,
        total_items INTEGER NOT NULL DEFAULT 0,
        processed_items INTEGER NOT NULL DEFAULT 0,
        imported_count INTEGER NOT NULL DEFAULT 0,
        duplicate_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        slide_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        CHECK (total_items >= 0),
        CHECK (processed_items >= 0)
    )
    """,
    """
    CREATE TABLE import_job_items (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
        source_name TEXT NOT NULL,
        logical_source_path TEXT NOT NULL,
        staging_path TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('queued', 'processing', 'imported', 'duplicate', 'failed', 'skipped')
        ),
        message TEXT,
        deck_id TEXT REFERENCES decks(id) ON DELETE SET NULL,
        slide_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE slide_renderings (
        slide_id TEXT PRIMARY KEY REFERENCES slides(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'available', 'unavailable', 'failed')
        ),
        thumbnail_path TEXT,
        error TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE tags (
        id TEXT PRIMARY KEY,
        tag_type TEXT NOT NULL CHECK (tag_type IN ('deck_type', 'slide_type', 'topic')),
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
        created_at TEXT NOT NULL,
        UNIQUE (tag_type, normalized_name)
    )
    """,
    """
    CREATE TABLE tag_aliases (
        id TEXT PRIMARY KEY,
        tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        alias TEXT NOT NULL,
        normalized_alias TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE deck_tags (
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        assignment_status TEXT NOT NULL DEFAULT 'Manually Edited',
        PRIMARY KEY (deck_id, tag_id)
    )
    """,
    """
    CREATE TABLE slide_tags (
        slide_id TEXT NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        assignment_status TEXT NOT NULL DEFAULT 'Manually Edited',
        PRIMARY KEY (slide_id, tag_id)
    )
    """,
    """
    CREATE TABLE ai_enrichment_results (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK (entity_type IN ('deck', 'slide')),
        entity_id TEXT NOT NULL,
        field_name TEXT NOT NULL,
        model TEXT NOT NULL,
        content TEXT NOT NULL,
        review_status TEXT NOT NULL CHECK (
            review_status IN ('AI Suggested', 'Reviewed', 'Manually Edited')
        ),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE edit_history (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK (entity_type IN ('deck', 'slide')),
        entity_id TEXT NOT NULL,
        field_name TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        edited_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_decks_imported_at ON decks(imported_at DESC)",
    "CREATE INDEX idx_slides_deck_number ON slides(deck_id, slide_number)",
    "CREATE INDEX idx_source_locations_deck ON source_locations(deck_id)",
    "CREATE INDEX idx_import_jobs_created ON import_jobs(created_at DESC)",
    "CREATE INDEX idx_import_items_job ON import_job_items(job_id, created_at)",
    "CREATE INDEX idx_import_items_status ON import_job_items(status)",
    "CREATE INDEX idx_tags_type_status ON tags(tag_type, status)",
    "CREATE INDEX idx_deck_tags_tag ON deck_tags(tag_id)",
    "CREATE INDEX idx_slide_tags_tag ON slide_tags(tag_id)",
    "CREATE INDEX idx_ai_enrichment_entity ON ai_enrichment_results(entity_type, entity_id)",
    "CREATE INDEX idx_edit_history_entity ON edit_history(entity_type, entity_id, edited_at)",
)


@contextmanager
def connect_database(
    database_path: Path,
) -> Generator[sqlite3.Connection, None, None]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: Path) -> None:
    with connect_database(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO app_metadata (key, value)
            VALUES ('schema_version', '1')
            """
        )
        version_row = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        version = int(version_row["value"])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than supported "
                f"version {SCHEMA_VERSION}"
            )
        if version < 2:
            for statement in MIGRATION_2:
                connection.execute(statement)
            connection.execute(
                "UPDATE app_metadata SET value = '2' WHERE key = 'schema_version'"
            )
            version = 2
        if version < 3:
            connection.execute(
                "ALTER TABLE import_job_items ADD COLUMN source_modified_at TEXT"
            )
            connection.execute(
                "UPDATE app_metadata SET value = '3' WHERE key = 'schema_version'"
            )
            version = 3
        if version < 4:
            connection.execute(
                """
                ALTER TABLE slides ADD COLUMN title_source TEXT NOT NULL
                    DEFAULT 'fallback'
                    CHECK (title_source IN (
                        'placeholder', 'inferred', 'fallback',
                        'manual', 'ai_suggested'
                    ))
                """
            )
            connection.execute(
                """
                UPDATE app_metadata SET value = '4' WHERE key = 'schema_version'
                """
            )
            version = 4
        if version < 5:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table, statement in MIGRATION_5:
                if table in existing_tables:
                    connection.execute(statement)
            connection.execute(
                "UPDATE app_metadata SET value = '5' WHERE key = 'schema_version'"
            )
        if version < 6:
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(import_jobs)")}
            for column in (
                "stage TEXT NOT NULL DEFAULT 'queued'",
                "current_item_id TEXT",
                "current_slides_processed INTEGER NOT NULL DEFAULT 0",
                "current_slides_total INTEGER",
                "discovery_complete INTEGER NOT NULL DEFAULT 0",
            ):
                if job_columns and column.split()[0] not in job_columns:
                    connection.execute(f"ALTER TABLE import_jobs ADD COLUMN {column}")
            deck_columns = {row["name"] for row in connection.execute("PRAGMA table_info(decks)")}
            for column in (
                "source_metadata TEXT NOT NULL DEFAULT '{}'",
                "metadata_provenance TEXT NOT NULL DEFAULT '{}'",
                "author TEXT", "subject TEXT", "subtitle TEXT",
                "review_status TEXT NOT NULL DEFAULT 'Not Reviewed' CHECK (review_status IN ('Not Reviewed', 'Reviewed'))",
                "reviewed_at TEXT",
            ):
                if deck_columns and column.split()[0] not in deck_columns:
                    connection.execute(f"ALTER TABLE decks ADD COLUMN {column}")
            if job_columns:
                connection.execute("UPDATE import_jobs SET discovery_complete = 1, stage = 'finished' WHERE status IN ('completed', 'completed_with_errors', 'failed')")
            connection.execute("UPDATE app_metadata SET value = '6' WHERE key = 'schema_version'")
        if version < 7:
            for statement in (
                """CREATE TABLE IF NOT EXISTS enrichment_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, model TEXT NOT NULL,
                    options TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                    current_entity_id TEXT, error TEXT)""",
                """CREATE TABLE IF NOT EXISTS enrichment_items (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES enrichment_jobs(id) ON DELETE CASCADE,
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, deck_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', error TEXT, result_id TEXT,
                    started_at TEXT, completed_at TEXT)""",
                "CREATE INDEX IF NOT EXISTS idx_enrichment_items_job ON enrichment_items(job_id, status)",
                """CREATE TABLE IF NOT EXISTS enrichment_reviews (
                    id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES ai_enrichment_results(id) ON DELETE CASCADE,
                    action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
            ):
                connection.execute(statement)
            columns = {r["name"] for r in connection.execute("PRAGMA table_info(ai_enrichment_results)")}
            for column in ("fingerprint TEXT", "decision TEXT NOT NULL DEFAULT 'pending'", "error TEXT"):
                if columns and column.split()[0] not in columns:
                    connection.execute(f"ALTER TABLE ai_enrichment_results ADD COLUMN {column}")
            if columns:
                connection.execute("CREATE INDEX IF NOT EXISTS idx_ai_fingerprint ON ai_enrichment_results(entity_type, entity_id, fingerprint)")
            connection.execute("UPDATE app_metadata SET value = '7' WHERE key = 'schema_version'")
        if version < 8:
            job_columns = {r["name"] for r in connection.execute("PRAGMA table_info(import_jobs)")}
            for column in ("enrichment_options TEXT", "enrichment_job_id TEXT", "enrichment_error TEXT"):
                if job_columns and column.split()[0] not in job_columns:
                    connection.execute(f"ALTER TABLE import_jobs ADD COLUMN {column}")
            columns = {r["name"] for r in connection.execute("PRAGMA table_info(enrichment_jobs)")}
            if "import_job_id" not in columns:
                connection.execute("ALTER TABLE enrichment_jobs ADD COLUMN import_job_id TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_enrichment_import ON enrichment_jobs(import_job_id) WHERE import_job_id IS NOT NULL")
            slide_columns = {r["name"] for r in connection.execute("PRAGMA table_info(slides)")}
            if slide_columns and "metadata_provenance" not in slide_columns:
                connection.execute("ALTER TABLE slides ADD COLUMN metadata_provenance TEXT NOT NULL DEFAULT '{}'")
            connection.execute("UPDATE app_metadata SET value = '8' WHERE key = 'schema_version'")
        if version < 9:
            connection.execute("""CREATE TABLE IF NOT EXISTS thumbnail_jobs (
                id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
                processed INTEGER NOT NULL DEFAULT 0, total INTEGER,
                error TEXT, created_at TEXT NOT NULL, completed_at TEXT)""")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_thumbnail_active ON thumbnail_jobs(deck_id) WHERE status IN ('queued', 'processing')")
            connection.execute("UPDATE app_metadata SET value = '9' WHERE key = 'schema_version'")
        if version < 10:
            for table in ("decks", "slides"):
                columns = {r["name"] for r in connection.execute(f"PRAGMA table_info({table})")}
                additions = ("keywords TEXT NOT NULL DEFAULT '[]'", "industry TEXT") + (("purpose TEXT",) if table == "decks" else ())
                for column in additions:
                    if columns and column.split()[0] not in columns:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            connection.execute("UPDATE app_metadata SET value = '10' WHERE key = 'schema_version'")
        if version < 11:
            from app.taxonomy_migration import migrate
            migrate(connection, database_path, version)
        if version < 12:
            from app.search import migrate as migrate_search
            migrate_search(connection)
            connection.execute("UPDATE app_metadata SET value='12' WHERE key='schema_version'")
