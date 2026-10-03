"""RM1/RM2 + read-only access, against databases built by the real pipeline."""
import duckdb
import pytest

from api.domain.enums import EntityKind, MatchStatus
from api.domain.player_index import PlayerIndex
from api.repositories import catalog_repository, freshness_repository, player_index_repository
from api.repositories.database import Database
from api.repositories.exceptions import DatabaseUnavailable

HAALAND, SALAH, VAN_DRONGELEN, VINI = 239085, 209331, 233097, 238794


def _load(path):
    db = Database(path); db.open()
    with db.cursor() as cur:
        rows = player_index_repository.load_player_index_rows(cur)
        links = player_index_repository.load_link_states(cur, [r.ea_fc26_id for r in rows])
    return db, PlayerIndex(rows), {(l.ea_fc26_id, l.source): l for l in links}


def test_connection_is_read_only(small_db):
    db = Database(small_db); db.open()
    with db.cursor() as cur:
        with pytest.raises(duckdb.Error):
            cur.execute("create table should_fail(a int)")
        with pytest.raises(duckdb.Error):
            cur.execute("delete from players")
    db.close()


def test_missing_database_is_a_clean_error(tmp_path):
    with pytest.raises(DatabaseUnavailable):
        Database(tmp_path / "nope.duckdb").open()


def test_closed_database_cannot_hand_out_cursors(small_db):
    db = Database(small_db); db.open(); db.close()
    with pytest.raises(DatabaseUnavailable), db.cursor():
        pass
    assert db.ping() is False


def test_rm1_small(small_db):
    db, idx, _ = _load(small_db)
    assert len(idx) == 50 and not idx.duplicate_ids()
    assert idx.count(EntityKind.CANONICAL) == 5 and idx.count(EntityKind.EA_ONLY) == 45
    h = idx.get(HAALAND)
    assert (h.display_name, h.club, h.nationality, h.preferred_foot) == ("Erling Haaland", "Manchester City", "Norway", "Left")
    assert h.entity_kind is EntityKind.CANONICAL and h.transfermarkt_id == 418560 and h.wikidata_id == "Q28967995"
    assert h.dataset_version == "2025-09-19" and h.fetched_at.tzinfo is not None
    s = idx.get(SALAH)
    assert s.entity_kind is EntityKind.EA_ONLY and s.canonical_player_uid is None and s.transfermarkt_id is None and s.wikidata_id is None
    db.close()


def test_rm1_and_rm2_full_scale(full_db):
    db, idx, links = _load(full_db)
    assert len(idx) == 16107 and not idx.duplicate_ids()
    assert idx.count(EntityKind.CANONICAL) == 5 and idx.count(EntityKind.EA_ONLY) == 16102
    assert all(r.display_name for r in idx.rows)
    assert len(links) == 2 * 16107
    # real cases from the audit
    assert links[(HAALAND, "transfermarkt")].status is MatchStatus.MATCHED and links[(HAALAND, "transfermarkt")].confidence == 1.0
    assert links[(HAALAND, "wikidata")].status is MatchStatus.PROBABLE_MATCH and links[(HAALAND, "wikidata")].confidence == 0.85
    vd = links[(VAN_DRONGELEN, "transfermarkt")]
    assert vd.status is MatchStatus.AMBIGUOUS and vd.review_pending and vd.confidence is None and vd.matched_on is None
    assert idx.get(VAN_DRONGELEN).entity_kind is EntityKind.EA_ONLY
    salah = links[(SALAH, "transfermarkt")]
    assert salah.status is MatchStatus.UNMATCHED and salah.confidence is None and not salah.review_pending
    assert links[(SALAH, "wikidata")].status is MatchStatus.NOT_EVALUATED       # enrichment only covers canonical players
    pending = [l for l in links.values() if l.review_pending]
    assert len(pending) == 1
    statuses = {l.status for l in links.values() if l.source == "wikidata"}
    assert statuses <= {MatchStatus.MATCHED, MatchStatus.PROBABLE_MATCH, MatchStatus.NOT_EVALUATED}
    db.close()


def test_rm1_collapses_legacy_duplicates_with_latest_wins(db_copy):
    """A database built before the G9 fix may hold duplicate / superseded field rows. RM1 must stay one row per player."""
    con = duckdb.connect(str(db_copy))
    nid = con.execute("select max(id) from player_field_values").fetchone()[0]
    # legacy-style full duplicate (older), plus a CHANGED club appended later for Salah
    con.execute(f"insert into player_field_values select id + {nid}, player_uid, field_name, field_value, source, source_record_id, "
                "fetched_at - interval 1 day, dataset_version, is_current, confidence from player_field_values where source = 'ea_fc26'")
    nid = con.execute("select max(id) from player_field_values").fetchone()[0]
    con.execute("insert into player_field_values (id, player_uid, field_name, field_value, source, source_record_id, fetched_at, dataset_version, is_current, confidence) "
                f"values ({nid + 1}, NULL, 'club', 'Changed FC', 'ea_fc26', '{SALAH}', now() + interval 1 hour, '2025-09-19', true, NULL)")
    con.close()
    db, idx, _ = _load(db_copy)
    assert len(idx) == 50 and not idx.duplicate_ids()
    assert idx.get(SALAH).club == "Changed FC"                                    # latest wins; is_current is not consulted
    assert idx.get(HAALAND).club == "Manchester City"
    db.close()


def test_catalog_and_freshness_repositories(small_db):
    db = Database(small_db); db.open()
    with db.cursor() as cur:
        assert catalog_repository.missing_objects(cur) == ()
        assert catalog_repository.ea_attribute_count(cur) == 50 and catalog_repository.ml_columns_in_use(cur) == 0
        ea = freshness_repository.field_source_freshness(cur, "ea_fc26")
        mv = freshness_repository.market_value_freshness(cur, "transfermarkt_dataset")
        assert (ea.dataset_version, ea.record_count) == ("2025-09-19", 50)
        assert (mv.dataset_version, mv.record_count) == ("sample_2026-07-06", 5)
        assert freshness_repository.field_source_freshness(cur, "no_such_source") is None
    db.close()
