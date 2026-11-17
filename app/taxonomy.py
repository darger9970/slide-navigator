"""Canonical taxonomy administration with transactional impact confirmation."""
import hashlib
import json
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.database import connect_database
from app.library_tags import assigned_tags, replace_tags, categories


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    label: str = Field(min_length=1, max_length=100)
    allow_deck: bool = True
    allow_slide: bool = True


class TagPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active", "archived"] | None = None


class Destructive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete", "merge"]
    target_id: str | None = None
    token: str | None = None


class Bulk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["deck", "slide"]
    ids: list[str] = Field(min_length=1, max_length=1000)
    tag_ids: list[str] = Field(min_length=1, max_length=100)
    operation: Literal["add", "remove"] = "add"


def audit(db, action, payload):
    db.execute("INSERT INTO taxonomy_history VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), action, json.dumps(payload), datetime.now(timezone.utc).isoformat()))


def listing(settings, q="", category=None, unused=False):
    with connect_database(settings.database_path) as db:
        rows = [dict(row) for row in db.execute("""SELECT t.*,
            (SELECT COUNT(*) FROM deck_tags WHERE tag_id=t.id) AS deck_count,
            (SELECT COUNT(*) FROM slide_tags WHERE tag_id=t.id) AS slide_count
            FROM tags t ORDER BY t.tag_type,t.name COLLATE NOCASE""")]
        for row in rows:
            row["aliases"] = [r[0] for r in db.execute("SELECT alias FROM tag_aliases WHERE tag_id=?", (row["id"],))]
            row["similar"] = [other["name"] for other in rows if other["id"] != row["id"] and other["tag_type"] == row["tag_type"] and SequenceMatcher(None, re.sub(r"\W", "", row["name"].casefold()), re.sub(r"\W", "", other["name"].casefold())).ratio() >= .88]
        return {"categories": categories(db), "items": [r for r in rows if
            (not q or q.casefold() in (r["name"] + " " + " ".join(r["aliases"])).casefold()) and
            (not category or r["tag_type"] == category) and (not unused or not r["deck_count"] + r["slide_count"])]}


def add_category(settings, value):
    if not value.label.strip() or not (value.allow_deck or value.allow_slide):
        raise HTTPException(422, "A category needs a label and at least one assignment scope.")
    with connect_database(settings.database_path) as db:
        if db.execute("SELECT 1 FROM tag_categories WHERE id=? OR label=? COLLATE NOCASE", (value.id, value.label.strip())).fetchone():
            raise HTTPException(409, "Category already exists.")
        db.execute("INSERT INTO tag_categories VALUES (?, ?, ?, ?)", (value.id, value.label.strip(), int(value.allow_deck), int(value.allow_slide)))
        audit(db, "create_category", value.model_dump())
    return value.model_dump()


def rename(settings, tag_id, patch):
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        tag = db.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not tag:
            raise HTTPException(404, "Tag not found.")
        name = " ".join((patch.name if patch.name is not None else tag["name"]).split())
        if not name:
            raise HTTPException(422, "Enter a tag name.")
        existing = db.execute("SELECT id FROM tags WHERE tag_type=? AND normalized_name=? AND id<>?", (tag["tag_type"], name.casefold(), tag_id)).fetchone()
        alias = db.execute("SELECT a.tag_id FROM tag_aliases a JOIN tags t ON t.id=a.tag_id WHERE a.normalized_alias=? AND t.tag_type=? AND t.id<>?", (name.casefold(), tag["tag_type"], tag_id)).fetchone()
        if existing or alias:
            raise HTTPException(409, "That canonical name or alias already belongs to another tag. Use Merge.")
        db.execute("UPDATE tags SET name=?, normalized_name=?, status=? WHERE id=?", (name, name.casefold(), patch.status or tag["status"], tag_id))
        if name.casefold() != tag["normalized_name"]:
            db.execute("INSERT OR IGNORE INTO tag_aliases VALUES (?, ?, ?, ?)", (str(uuid.uuid4()), tag_id, tag["name"], tag["normalized_name"]))
        audit(db, "edit_tag", {"before": dict(tag), "name": name, "status": patch.status})
    return {"id": tag_id, "name": name}


def add_alias(settings, tag_id, name):
    name = " ".join(name.split())
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        tag = db.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not tag:
            raise HTTPException(404, "Tag not found.")
        if not name:
            raise HTTPException(422, "Enter an alias.")
        if db.execute("SELECT 1 FROM tag_aliases WHERE normalized_alias=?", (name.casefold(),)).fetchone() or db.execute("SELECT 1 FROM tags WHERE tag_type=? AND normalized_name=?", (tag["tag_type"], name.casefold())).fetchone():
            raise HTTPException(409, "Name already exists.")
        db.execute("INSERT INTO tag_aliases VALUES (?, ?, ?, ?)", (str(uuid.uuid4()), tag_id, name, name.casefold()))
        audit(db, "add_alias", {"tag_id": tag_id, "alias": name})
    return {"tag_id": tag_id, "alias": name}


def impact(db, tag_id, action, target_id):
    tag = db.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
    if not tag:
        raise HTTPException(404, "Tag not found.")
    target = None
    if action == "merge":
        target = db.execute("SELECT * FROM tags WHERE id=?", (target_id,)).fetchone()
        if not target or target_id == tag_id or target["tag_type"] != tag["tag_type"] or target["status"] != "active":
            raise HTTPException(422, "Merge into a different active tag in the same category.")
    assignments = {}
    for kind in ("deck", "slide"):
        assignments[kind] = [dict(r) for r in db.execute(f"SELECT * FROM {kind}_tags WHERE tag_id IN (?, ?) ORDER BY {kind}_id,tag_id", (tag_id, target_id))]
    payload = {"tag": dict(tag), "target": dict(target) if target else None, "assignments": assignments,
               "aliases": [dict(r) for r in db.execute("SELECT * FROM tag_aliases WHERE tag_id IN (?, ?) ORDER BY id", (tag_id, target_id))], "action": action}
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload, {"token": token, "deck_count": len({r["deck_id"] for r in assignments["deck"]}),
                     "slide_count": len({r["slide_id"] for r in assignments["slide"]})}


def change(settings, tag_id, request, execute=False):
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        payload, summary = impact(db, tag_id, request.action, request.target_id)
        if not execute:
            return summary
        if request.token != summary["token"]:
            raise HTTPException(409, "Tag usage changed. Preview the operation again.")
        for kind, rows in payload["assignments"].items():
            for entity_id in {row[kind + "_id"] for row in rows if row["tag_id"] == tag_id}:
                ids = {r["id"] for r in assigned_tags(db, kind, entity_id)} - {tag_id}
                if request.action == "merge":
                    ids.add(request.target_id)
                replace_tags(db, kind, entity_id, sorted(ids))
        if request.action == "merge":
            db.execute("UPDATE tag_aliases SET tag_id=? WHERE tag_id=?", (request.target_id, tag_id))
            db.execute("INSERT OR IGNORE INTO tag_aliases VALUES (?, ?, ?, ?)", (str(uuid.uuid4()), request.target_id, payload["tag"]["name"], payload["tag"]["normalized_name"]))
        audit(db, request.action, payload)
        db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Tag operation violated referential integrity.")
        return summary


def bulk(settings, request):
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        table = "decks" if request.kind == "deck" else "slides"
        for entity_id in set(request.ids):
            if not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone():
                raise HTTPException(404, "A selected library item no longer exists.")
            old = {t["id"] for t in assigned_tags(db, request.kind, entity_id)}
            ids = old | set(request.tag_ids) if request.operation == "add" else old - set(request.tag_ids)
            if len(ids) > 200:
                raise HTTPException(422, "Too many tags assigned.")
            try:
                replace_tags(db, request.kind, entity_id, sorted(ids))
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        audit(db, "bulk_assign", request.model_dump())
    return {"updated": len(set(request.ids))}
