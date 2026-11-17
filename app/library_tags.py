"""Library access to the canonical taxonomy (Tag Manager owns its lifecycle)."""

import json
import uuid
from datetime import datetime, timezone


def assigned_tags(connection, entity_type, entity_id):
    table = "deck_tags" if entity_type == "deck" else "slide_tags"
    return [dict(row) for row in connection.execute(
        f"""SELECT t.id, t.name, t.tag_type, t.status, a.assignment_status
            FROM {table} a JOIN tags t ON t.id = a.tag_id
            WHERE a.{entity_type}_id = ? ORDER BY t.tag_type, t.name COLLATE NOCASE""",
        (entity_id,),
    )]


def tag_filter(terms, parameters, entity_type, category, tag_ids):
    """OR inside a category; callers AND independent EXISTS clauses."""
    if not tag_ids:
        return
    placeholders = ",".join("?" for _ in tag_ids)
    if entity_type == "deck" and category == "slide_type":
        source = "slides child JOIN slide_tags a ON a.slide_id = child.id"
        identity = "child.deck_id = d.id"
    else:
        table, alias = ("deck_tags", "d") if entity_type == "deck" else ("slide_tags", "s")
        source = f"{table} a"
        identity = f"a.{entity_type}_id = {alias}.id"
    terms.append(
        f"EXISTS (SELECT 1 FROM {source} JOIN tags t ON t.id = a.tag_id "
        f"WHERE {identity} AND t.tag_type = ? AND t.status = 'active' "
        f"AND t.id IN ({placeholders}))"
    )
    parameters.extend([category, *tag_ids])


def filter_tags(connection):
    """Counts are global distinct entities, not sums of assignments or page counts."""
    return [dict(row) for row in connection.execute(
        """SELECT t.id, t.name, t.tag_type,
            (SELECT COUNT(DISTINCT deck_id) FROM deck_tags WHERE tag_id = t.id) AS deck_count,
            (SELECT COUNT(DISTINCT slide_id) FROM slide_tags WHERE tag_id = t.id) AS slide_count,
            (SELECT COUNT(DISTINCT s.deck_id) FROM slide_tags a
                JOIN slides s ON s.id = a.slide_id WHERE a.tag_id = t.id) AS containing_deck_count
            FROM tags t WHERE t.status = 'active'
            ORDER BY t.tag_type, t.name COLLATE NOCASE"""
    )]


def replace_tags(connection, entity_type, entity_id, tag_ids):
    """Assign existing canonical IDs only; validate before changing any assignments."""
    if len(tag_ids) != len(set(tag_ids)):
        raise ValueError("Choose each tag only once.")
    old = assigned_tags(connection, entity_type, entity_id)
    old_ids = {tag["id"] for tag in old}
    allowed = allowed_categories(connection, entity_type)
    for tag_id in tag_ids:
        tag = connection.execute("SELECT tag_type, status FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if tag is None or tag["tag_type"] not in allowed:
            raise ValueError("Unknown tag or tag from an incompatible taxonomy.")
        if tag["status"] != "active" and tag_id not in old_ids:
            raise ValueError("Archived tags cannot be newly assigned.")
    new_ids = set(tag_ids)
    if new_ids == old_ids:
        return
    table = "deck_tags" if entity_type == "deck" else "slide_tags"
    for tag_id in old_ids - new_ids:
        connection.execute(f"DELETE FROM {table} WHERE {entity_type}_id = ? AND tag_id = ?", (entity_id, tag_id))
    for tag_id in new_ids - old_ids:
        connection.execute(f"INSERT INTO {table} ({entity_type}_id, tag_id, assignment_status) VALUES (?, ?, 'Manually Edited')", (entity_id, tag_id))
    connection.execute(
        """INSERT INTO edit_history
        (id, entity_type, entity_id, field_name, old_value, new_value, edited_at)
        VALUES (?, ?, ?, 'tag_ids', ?, ?, ?)""",
        (str(uuid.uuid4()), entity_type, entity_id, json.dumps(sorted(old_ids)),
         json.dumps(sorted(new_ids)), datetime.now(timezone.utc).isoformat()),
    )


def allowed_categories(connection, entity_type):
    column = "allow_deck" if entity_type == "deck" else "allow_slide"
    return {row[0] for row in connection.execute(f"SELECT id FROM tag_categories WHERE {column} = 1")}


def categories(connection):
    return [dict(row) for row in connection.execute("SELECT * FROM tag_categories ORDER BY rowid")]


def generic_tag_filter(terms, parameters, entity_type, ids, database_path):
    if not ids:
        return
    from app.database import connect_database
    groups = {}
    with connect_database(database_path) as db:
        for value in ids:
            tag = db.execute("SELECT tag_type FROM tags WHERE id = ?", (value,)).fetchone()
            groups.setdefault(tag[0] if tag else "unknown", []).append(value)
    for category, values in groups.items():
        tag_filter(terms, parameters, entity_type, category, values)
