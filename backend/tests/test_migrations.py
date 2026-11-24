"""The database the migrations build is the one the models describe, and an upgrade keeps what is in it.

Tables are rebuilt when SQLite cannot change them in place. A rebuild once dropped the "never reuse an id" rule
from the clips table, so a new clip could take the id of one deleted a moment before, and an old link or
notification then opened the wrong clip. These tests keep that from coming back."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers every table)
from app.db import Base

BACKEND = Path(__file__).resolve().parent.parent


def migrate(db_path: Path, revision: str = "head") -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic").replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}".replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, revision)


def table_sql(db_path: Path, table: str) -> str:
    with sqlite3.connect(db_path) as connection:
        return connection.execute("select sql from sqlite_master where type='table' and name=?", (table,)).fetchone()[0]


def test_every_table_that_must_never_reuse_an_id_is_built_that_way(template_db: Path):
    wanted = [table.name for table in Base.metadata.sorted_tables
              if table.dialect_options["sqlite"].get("autoincrement")]

    assert {"clips", "videos", "jobs", "projects", "accounts", "posts"} <= set(wanted)
    missing = [name for name in wanted if "AUTOINCREMENT" not in table_sql(template_db, name).upper()]
    assert missing == []


def test_the_migrations_build_the_tables_the_models_describe(template_db: Path):
    engine = create_engine(f"sqlite:///{template_db.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={
                "include_object": lambda obj, name, kind, *_: not (kind == "table" and name.startswith("transcript_fts"))})
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == []


def rebuild_losing_the_rule(db_path: Path) -> None:
    """What rolling a change back used to do to the clips table: rebuild it from what SQLite reports, which
    does not include the rule."""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("clips", recreate="always"):
                pass
    finally:
        engine.dispose()


def test_a_database_that_lost_the_rule_gets_it_back_and_keeps_every_clip(tmp_path: Path):
    db_path = tmp_path / "old.db"
    migrate(db_path, "0005")
    rebuild_losing_the_rule(db_path)
    assert "AUTOINCREMENT" not in table_sql(db_path, "clips").upper()          # the state one real install was in
    with sqlite3.connect(db_path) as connection:
        connection.execute("insert into videos (id, title, original_filename, storage_key, storage_mode, file_path, "
                           "size_bytes, fingerprint, duration, width, height, fps, video_codec, audio_codec, has_audio, "
                           "source_type, source_url, creator_name, creator_handle, permission_status, permission_note, "
                           "attribution, status, language, content_preset, thumb_path, last_error, created_at, "
                           "updated_at) values (1, 'Stream', 's.mp4', 'vkey', 'linked', 'C:/s.mp4', 1, '', 60, 1920, "
                           "1080, 30, 'h264', 'aac', 1, 'path', '', '', '', 'owned', '', '', 'ready', '', '', '', '', "
                           "'2026-11-01 00:00:00', '2026-11-01 00:00:00')")
        for clip_id, key in ((7, "a"), (8, "b"), (9, "c")):
            connection.execute(
                "insert into clips (id, video_id, storage_key, origin, title, hook_text, hook_options, description, "
                "hashtags, category, transcript_text, start, end, score, subscores, confidence, explanation, evidence, "
                "analysis_source, rank, review_status, favorite, settings, render_status, render_error, file_path, "
                "thumb_path, file_size, cover_text, cover_word, cover_path, tiktok_caption, out_width, out_height, "
                "out_duration, variant_of, variant_label, created_at, updated_at) values (?, 1, ?, 'ai', ?, '', '[]', "
                "'', '[]', 'funny', 'what was said', 1.0, 9.0, 80.0, '{}', 0.8, '', '[]', 'heuristic', 1, 'approved', 1, "
                "'{\"layout\": \"fill\"}', 'none', '', '', '', 0, '', '', '', 'my caption', 0, 0, 0.0, ?, '', "
                "'2026-11-01 00:00:00', '2026-11-01 00:00:00')",
                (clip_id, key, f"Clip {clip_id}", 7 if clip_id == 9 else None))
        connection.execute("insert into feedback (id, clip_id, label, note, snapshot, created_at) values "
                           "(1, 8, 'good', '', '{}', '2026-11-01 00:00:00')")
        connection.commit()

    migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute("pragma foreign_keys=on")
        rows = connection.execute("select id, storage_key, title, category, favorite, settings, tiktok_caption, "
                                  "variant_of from clips order by id").fetchall()
        assert rows == [(7, "a", "Clip 7", "funny", 1, '{"layout": "fill"}', "my caption", None),
                        (8, "b", "Clip 8", "funny", 1, '{"layout": "fill"}', "my caption", None),
                        (9, "c", "Clip 9", "funny", 1, '{"layout": "fill"}', "my caption", 7)]
        assert connection.execute("select clip_id from feedback").fetchall() == [(8,)]
        indexes = {row[1] for row in connection.execute("pragma index_list(clips)")}
        assert {"ix_clips_video_id", "ix_clips_score", "ix_clips_review_status", "ix_clips_render_status"} <= indexes
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        # the newest clip is deleted; the next one made must not get its id back
        connection.execute("delete from clips where id = 9")
        connection.execute(
            "insert into clips (video_id, storage_key, origin, title, hook_text, hook_options, description, hashtags, "
            "category, transcript_text, start, end, score, subscores, confidence, explanation, evidence, "
            "analysis_source, rank, review_status, favorite, settings, render_status, render_error, file_path, "
            "thumb_path, file_size, cover_text, cover_word, cover_path, tiktok_caption, out_width, out_height, "
            "out_duration, variant_label, created_at, updated_at) values (1, 'd', 'ai', 'New', '', '[]', '', '[]', '', "
            "'', 0, 5, 50, '{}', 0.5, '', '[]', 'heuristic', 1, 'pending', 0, '{}', 'none', '', '', '', 0, '', '', '', "
            "'', 0, 0, 0.0, '', '2026-11-02 00:00:00', '2026-11-02 00:00:00')")
        assert connection.execute("select max(id) from clips").fetchone()[0] == 10
    assert "AUTOINCREMENT" in table_sql(db_path, "clips").upper()


def test_rolling_changes_back_keeps_the_rule(tmp_path: Path):
    db_path = tmp_path / "back.db"
    migrate(db_path)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic").replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}".replace("%", "%%"))
    cfg.attributes["configure_logger"] = False

    command.downgrade(cfg, "0002")                                             # every change to clips rolled back

    assert "AUTOINCREMENT" in table_sql(db_path, "clips").upper()
    migrate(db_path)
    assert "AUTOINCREMENT" in table_sql(db_path, "clips").upper()


@pytest.mark.parametrize("table", ["clips", "videos", "jobs"])
def test_a_fresh_install_starts_with_the_rule(template_db: Path, table: str):
    assert "AUTOINCREMENT" in table_sql(template_db, table).upper()
