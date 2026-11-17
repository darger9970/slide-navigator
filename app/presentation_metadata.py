"""Deterministic import choices; raw source properties remain immutable."""

import re
import uuid
from datetime import datetime
from pathlib import PurePosixPath


def metadata_states(entity, kind):
    provenance = entity.get("metadata_provenance", {})
    result = {}
    for field in (["title", "description", "keywords", "industry"] if kind == "slide" else ["title", "organisation", "year", "author", "subject", "subtitle", "description", "purpose", "industry", "keywords"]):
        origin = provenance.get(field, {}).get("source")
        manual = origin == "manual" or (kind == "slide" and field == "title" and entity.get("title_source") == "manual" and origin != "ai_accepted")
        manual |= field == "description" and entity.get("description_status") == "Manually Edited"
        approved = origin == "ai_accepted" or entity.get("review_status") == "Reviewed" or (field == "description" and entity.get("description_status") == "Reviewed")
        result[field] = "user_edited" if manual else "approved" if approved else "existing_unreviewed"
    return result


def meaningful_title(value):
    title = (value or "").strip()
    if not title or title.casefold() in {"powerpoint presentation", "presentation", "untitled", "microsoft powerpoint presentation"}:
        return False
    try:
        uuid.UUID(title)
        return False
    except ValueError:
        return True


def imported_metadata(extracted, filename, modified_at):
    properties = extracted["source_metadata"]["document_properties"]
    slide_content = extracted["source_metadata"]["slide_content"]
    provenance = {}
    if meaningful_title(properties.get("title")):
        title, title_source = properties["title"].strip(), "document_property"
    elif meaningful_title(PurePosixPath(filename).stem):
        title, title_source = filename, "filename"
    elif extracted["slides"] and meaningful_title(extracted["slides"][0]["title"]):
        title, title_source = extracted["slides"][0]["title"], "slide_content"
    else:
        title, title_source = filename, "filename"
    provenance["title"] = {"source": title_source, "detail": "Deterministic title fallback; original document title preserved."}
    candidates = []
    filename_years = {int(value) for value in re.findall(r"(?<!\d)(?:19\d{2}|20\d{2}|2100)(?!\d)", filename)}
    if len(filename_years) == 1:
        candidates.append({"value": filename_years.pop(), "source": "filename", "evidence": filename})
    # Only explicit dates on the opening slide are considered, not arbitrary years in body text.
    first_text = extracted["slides"][0]["text_content"] if extracted["slides"] else ""
    dates = re.findall(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\s+\d{4})\b", first_text, re.I)
    content_years = set()
    for date in dates:
        cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", date, flags=re.I)
        for pattern in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
            try:
                year = datetime.strptime(cleaned, pattern).year
                if 1900 <= year <= 2100:
                    content_years.add(year)
                break
            except ValueError:
                pass
    if len(content_years) == 1:
        candidates.append({"value": content_years.pop(), "source": "opening_slide_date", "evidence": dates})
    for timestamp, source in [(properties.get("created"), "document_created"), (modified_at, "filesystem_modified_fallback")]:
        if timestamp:
            try:
                year = datetime.fromisoformat(timestamp).year
                if 1900 <= year <= 2100:
                    candidates.append({"value": year, "source": source, "evidence": timestamp})
            except ValueError:
                pass
    chosen = candidates[0] if candidates else {"value": None, "source": "unknown"}
    provenance["year"] = {**chosen, "candidates": candidates,
        "filename_years": sorted(int(value) for value in re.findall(r"(?<!\d)(?:19\d{2}|20\d{2}|2100)(?!\d)", filename)),
        "detail": "Filename year, opening-slide date, document creation, then file modification fallback. File modification is not an authorship date."}
    values = {"name": title, "year": chosen["value"], "subtitle": slide_content.get("subtitle")}
    for field in ["author", "subject"]:
        values[field] = (properties.get(field) or "").strip() or None
        provenance[field] = {"source": "document_property" if values[field] else "unknown"}
    values["description"] = (properties.get("comments") or "").strip() or None
    provenance["description"] = {"source": "document_property" if values["description"] else "unknown", "property": "comments"}
    provenance["subtitle"] = {"source": "title_slide_subtitle_placeholder" if values["subtitle"] else "unknown"}
    # Keep editable values compatible with the PATCH schema; raw values are intact.
    for field, limit in {"name": 250, "author": 250, "subject": 1000, "subtitle": 2000, "description": 20000}.items():
        if values[field] and len(values[field]) > limit:
            values[field] = values[field][:limit]
            provenance["title" if field == "name" else field]["truncated_for_editing"] = True
    return values, provenance
