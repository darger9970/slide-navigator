"""Preserve canonical IDs and referencing tables while extending category validation."""
import sqlite3
from contextlib import closing

INITIAL = [("deck_type", "Deck Type", 1, 0), ("slide_type", "Slide Type", 0, 1),
           ("topic", "Topic", 1, 1), ("industry", "Industry", 1, 1),
           ("technology", "Technology", 1, 1), ("organisation", "Client / Organisation", 1, 1)]


def migrate(db, path, original_version):
    db.commit()
    if original_version == 10:
        backup = path.with_name(path.name + ".before-v11.sqlite3")
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as target:
                db.backup(target)
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS tag_categories (
            id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE,
            allow_deck INTEGER NOT NULL CHECK(allow_deck IN (0,1)),
            allow_slide INTEGER NOT NULL CHECK(allow_slide IN (0,1)),
            CHECK(allow_deck + allow_slide > 0))""")
        db.executemany("INSERT OR IGNORE INTO tag_categories VALUES (?, ?, ?, ?)", INITIAL)
        definition = db.execute("SELECT sql FROM sqlite_master WHERE name = 'tags'").fetchone()
        if definition is None or "REFERENCES tag_categories" not in definition[0]:
            db.execute("""CREATE TABLE tags_extended (
                id TEXT PRIMARY KEY, tag_type TEXT NOT NULL REFERENCES tag_categories(id),
                name TEXT NOT NULL, normalized_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
                created_at TEXT NOT NULL, UNIQUE(tag_type, normalized_name))""")
            if definition:
                db.execute("INSERT INTO tags_extended SELECT * FROM tags")
                db.execute("DROP TABLE tags")
            db.execute("ALTER TABLE tags_extended RENAME TO tags")
            db.execute("CREATE INDEX idx_tags_type_status ON tags(tag_type,status)")
        db.execute("""CREATE TABLE IF NOT EXISTS taxonomy_history (
            id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Taxonomy migration failed referential-integrity validation.")
        db.execute("UPDATE app_metadata SET value = '11' WHERE key = 'schema_version'")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")
