from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True, slots=True)
class FixtureEntity:
    id: str
    value: str
    parent_id: str | None


class FixtureRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, entity_id: str) -> FixtureEntity | None:
        row = self._connection.execute(
            "SELECT id, value, parent_id FROM fixture_entity WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        return FixtureEntity(row["id"], row["value"], row["parent_id"])

    def add(self, entity: FixtureEntity) -> None:
        self._connection.execute(
            "INSERT INTO fixture_entity (id, value, parent_id) VALUES (?, ?, ?)",
            (entity.id, entity.value, entity.parent_id),
        )
