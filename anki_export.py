"""Generate Anki .apkg packages from a list of notes.

Cards are formatted as:
  Front: source citation (or "Snippet" if no source)
  Back:  the note body, plus tags
"""

import os
import tempfile

import genanki


_MODEL_ID = 1607392319
_DECK_ID = 2059400110

_MODEL = genanki.Model(
    _MODEL_ID,
    "Snippets",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Tags"},
    ],
    templates=[
        {
            "name": "Card 1",
            "qfmt": "<div style='font-family:serif;font-size:18px'>{{Front}}</div>",
            "afmt": (
                "{{FrontSide}}<hr id=answer>"
                "<div style='font-family:serif;font-size:16px'>{{Back}}</div>"
                "<br><div style='color:#888;font-size:12px'>{{Tags}}</div>"
            ),
        }
    ],
)


def _format_front(note: dict, source: dict | None) -> str:
    if source:
        front = source["name"]
        if note.get("locator_type") and note.get("locator_value"):
            front += f" ({note['locator_type']}: {note['locator_value']})"
        return front
    return "Snippet"


def _format_back(note: dict) -> str:
    # Anki accepts HTML; preserve line breaks from the markdown source.
    return (note["body"] or "").replace("\n", "<br>")


def build_apkg(notes: list[dict], sources_by_id: dict[int, dict],
               tags_by_note: dict[int, list[str]], deck_name: str = "Snippets export") -> bytes:
    deck = genanki.Deck(_DECK_ID, deck_name)
    for n in notes:
        src = sources_by_id.get(n.get("source_id")) if n.get("source_id") else None
        front = _format_front(n, src)
        back = _format_back(n)
        tags_str = ", ".join(tags_by_note.get(n["id"], []))
        deck.add_note(genanki.Note(
            model=_MODEL,
            fields=[front, back, tags_str],
            tags=[t.replace(" ", "_") for t in tags_by_note.get(n["id"], [])],
        ))
    fd, path = tempfile.mkstemp(suffix=".apkg")
    os.close(fd)
    try:
        genanki.Package(deck).write_to_file(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
