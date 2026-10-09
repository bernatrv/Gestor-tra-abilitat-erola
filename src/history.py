from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

APPLIED = "aplicada"
UNDONE = "desfeta"
DISCARDED = "descartada"


@dataclass
class Snapshot:
    db: bytes
    ui: Any


@dataclass
class HistoryEntry:
    id: int
    description: str
    timestamp: str
    before: Snapshot | None
    status: str = APPLIED
    chain_index: int | None = field(default=None)


class ActionHistory:
    """Historial lineal d'accions amb desfer/refer basat en instantànies de l'estat anterior a cada acció.

    Totes les accions es conserven al registre. Les desfetes es poden refer fins que es fa una acció
    nova; llavors passen a "descartada" (es mostren però ja no es poden recuperar).
    """

    def __init__(self):
        self.entries: list[HistoryEntry] = []
        self._chain: list[HistoryEntry] = []
        self.position = 0
        self._tip: Snapshot | None = None
        self._next_id = 1

    def clear(self):
        self.entries.clear()
        self._chain.clear()
        self.position = 0
        self._tip = None
        self._next_id = 1

    @property
    def can_undo(self) -> bool:
        return self.position > 0

    @property
    def can_redo(self) -> bool:
        return self.position < len(self._chain)

    def record(self, description: str, before: Snapshot) -> HistoryEntry:
        for entry in self._chain[self.position:]:
            entry.status = DISCARDED
            entry.before = None
            entry.chain_index = None
        del self._chain[self.position:]
        self._tip = None
        entry = HistoryEntry(
            id=self._next_id,
            description=description,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            before=before,
            chain_index=len(self._chain),
        )
        self._next_id += 1
        self.entries.append(entry)
        self._chain.append(entry)
        self.position = len(self._chain)
        return entry

    def position_of(self, entry: HistoryEntry) -> int | None:
        """Posició (nombre d'accions aplicades) que té l'estat just abans de `entry`."""
        return entry.chain_index if entry.status != DISCARDED else None

    def move_to(self, target: int, capture_current: Callable[[], Snapshot]) -> Snapshot | None:
        """Mou l'historial a l'estat amb `target` accions aplicades i retorna la instantània a restaurar."""
        target = max(0, min(target, len(self._chain)))
        if target == self.position:
            return None
        if self.position == len(self._chain):
            self._tip = capture_current()
        snapshot = self._chain[target].before if target < len(self._chain) else self._tip
        self.position = target
        for index, entry in enumerate(self._chain):
            entry.status = APPLIED if index < target else UNDONE
        return snapshot
