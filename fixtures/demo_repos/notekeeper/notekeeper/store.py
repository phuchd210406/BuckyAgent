"""A tiny note store. Contains DEMO BUG #6 (see fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.

The defect here is real and reproducible. The point of this case is that the
COMPLAINT does not identify it, or any other behaviour, so an agent that
patches this file has guessed. See fixtures/BUGS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Note:
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    archived: bool = False


class NoteStore:
    def __init__(self) -> None:
        self._notes: list[Note] = []

    def add(self, note: Note) -> Note:
        self._notes.append(note)
        return note

    def all(self, include_archived: bool = False) -> list[Note]:
        if include_archived:
            return list(self._notes)
        return [n for n in self._notes if not n.archived]

    def archive(self, title: str) -> None:
        for note in self._notes:
            if note.title == title:
                note.archived = True
                return
        raise KeyError(title)

    def tagged(self, tag: str) -> list[Note]:
        return [n for n in self.all() if tag in n.tags]

    def search(self, term: str) -> list[Note]:
        """Notes whose title or body mentions `term`."""
        # DEMO BUG #6: the comparison is case-sensitive, so searching for
        # "invoice" misses a note titled "Invoice". Nobody types their own
        # capitals back the way they wrote them.
        return [n for n in self.all() if term in n.title or term in n.body]
