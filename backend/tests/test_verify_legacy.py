from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert
from app.models import LegacyRecord
from scripts.verify_legacy import verify_archive


def test_archive_verification_detects_changed_and_duplicate_rows():
    source = create_engine("sqlite://")
    target = create_engine("sqlite://")
    meta = MetaData()
    table = Table("old_table", meta, Column("value", String), Column("number", Integer))
    meta.create_all(source)
    LegacyRecord.__table__.create(target)
    with source.begin() as db:
        db.execute(insert(table), [{"value": "a", "number": 1}, {"value": "a", "number": 1}])
    with target.begin() as db:
        db.execute(insert(LegacyRecord.__table__), [
            {"source_table": "old_table", "source_key": "1", "payload": {"value": "a", "number": 1}},
            {"source_table": "old_table", "source_key": "2", "payload": {"number": 1, "value": "a"}},
        ])
    with source.connect() as a, target.connect() as b:
        assert verify_archive(a, b) == 0
    with target.begin() as db:
        db.execute(LegacyRecord.__table__.update().where(LegacyRecord.source_key == "2").values(payload={"value": "a", "number": 2}))
    with source.connect() as a, target.connect() as b:
        assert verify_archive(a, b) == 2
    source.dispose()
    target.dispose()
