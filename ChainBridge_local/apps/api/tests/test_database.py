from sqlalchemy.pool import NullPool, StaticPool

from app.database import build_session_factory


def test_file_backed_sqlite_does_not_retain_connections(tmp_path):
    factory = build_session_factory(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")

    assert isinstance(factory.kw["bind"].pool, NullPool)


def test_in_memory_sqlite_keeps_one_shared_connection():
    factory = build_session_factory("sqlite:///:memory:")

    assert isinstance(factory.kw["bind"].pool, StaticPool)
