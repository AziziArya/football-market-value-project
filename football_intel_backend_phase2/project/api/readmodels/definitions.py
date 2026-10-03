"""SQL read-model DEFINITIONS (ARCHITECTURE_API.md section 5). Parameterised text only; executed by repositories.
No views and no tables are created: the database schema is not touched by the API.

Selection rule shared by all of them: the LATEST row per logical key (fetched_at/matched_at DESC, id DESC).
`is_current` is never used as a selection signal."""

REQUIRED_OBJECTS = (
    "players", "v_players", "player_field_values", "ea_fc26_attributes", "market_value_history",
    "identity_matches", "review_queue", "player_images", "player_wikidata_links",
    "injury_data_status", "injury_records", "schema_migrations",
)

LIST_OBJECTS = "select table_name from information_schema.tables"

# RM1 - one row per EA player. Latest EA field row per (player, field), pivoted; canonical link from v_players.
RM1_PLAYER_INDEX = """
with ranked as (
  select source_record_id, field_name, field_value, fetched_at,
         row_number() over (partition by source_record_id, field_name order by fetched_at desc, id desc) as rn
  from player_field_values where source = 'ea_fc26'
),
f as (
  select source_record_id,
         max(case when field_name = 'display_name'    then field_value end) as display_name,
         max(case when field_name = 'position'        then field_value end) as position,
         max(case when field_name = 'club'            then field_value end) as club,
         max(case when field_name = 'nationality'     then field_value end) as nationality,
         max(case when field_name = 'date_of_birth'   then field_value end) as date_of_birth,
         max(case when field_name = 'preferred_foot'  then field_value end) as preferred_foot,
         max(fetched_at) as fetched_at
  from ranked where rn = 1 group by source_record_id
)
select a.ea_fc26_id, f.display_name, f.position, f.club, f.nationality, f.date_of_birth, f.preferred_foot,
       a.overall_rating, a.potential, a.value_eur_ingame, a.dataset_version, f.fetched_at,
       v.player_uid, v.transfermarkt_id, v.wikidata_id
from ea_fc26_attributes a
left join f on f.source_record_id = cast(a.ea_fc26_id as varchar)
left join v_players v on v.ea_fc26_id = a.ea_fc26_id
order by a.ea_fc26_id
"""

# RM2 - transfermarkt side: latest is_best match per EA id.
RM2_TRANSFERMARKT_BEST = """
select ea_fc26_id, match_status, match_confidence, matched_on from (
  select *, row_number() over (partition by ea_fc26_id order by matched_at desc, id desc) as rn
  from identity_matches where is_best and ea_fc26_id is not null
) where rn = 1
"""
RM2_REVIEW_PENDING = "select query_source_record_id from review_queue where query_source = 'ea_fc26' and status = 'PENDING'"
# RM2 - wikidata side: only canonical players can have a link row.
RM2_WIKIDATA_LINKS = """
select v.ea_fc26_id, l.match_status, l.match_confidence, l.matched_on
from player_wikidata_links l join v_players v on v.player_uid = l.player_uid
where v.ea_fc26_id is not null
"""

# RM7 - freshness. Field-based sources: latest load of that source's identity fields.
RM7_FIELD_LATEST = ("select dataset_version, fetched_at from player_field_values where source = ? "
                    "order by fetched_at desc, id desc limit 1")
RM7_FIELD_COUNT = ("select count(distinct source_record_id) from player_field_values "
                   "where source = ? and field_name = 'display_name'")
# RM7 - market-value source: its own table.
RM7_MV_LATEST = ("select dataset_version, imported_at from market_value_history where source = ? "
                 "order by imported_at desc, id desc limit 1")
RM7_MV_COUNT = "select count(distinct player_id_in_source) from market_value_history where source = ?"

# Startup invariants.
INV_ML_COLUMNS_IN_USE = ("select count(*) from ea_fc26_attributes "
                         "where predicted_value_eur is not null or model_version is not null")
INV_EA_ATTRIBUTE_COUNT = "select count(*) from ea_fc26_attributes"
PING = "select 1"


# RM3 - aliases: non-EA display_name values of CANONICAL players (latest row per source record). Search only.
RM3_ALIASES = """
select v.ea_fc26_id, r.field_value, r.source
from (
  select *, row_number() over (partition by source, source_record_id, field_name order by fetched_at desc, id desc) as rn
  from player_field_values
  where field_name = 'display_name' and source <> 'ea_fc26' and player_uid is not null
) r
join v_players v on v.player_uid = r.player_uid
where r.rn = 1 and v.ea_fc26_id is not null
order by v.ea_fc26_id, r.source, r.field_value
"""

# RM4/RM5/RM6 - batch enrichment of the canonical players on ONE page: one query per table, never one per player.
# Latest market value = highest valuation_date, ties -> newest dataset_version, then newest import, then id.
BATCH_LATEST_MARKET_VALUE = """
select player_uid, value_eur, valuation_date, source, dataset_version, imported_at from (
  select *, row_number() over (partition by player_uid
                               order by valuation_date desc, dataset_version desc, imported_at desc, id desc) as rn
  from market_value_history where list_contains(?, player_uid)
) where rn = 1
"""
BATCH_INJURY_STATUS = "select player_uid, status, checked_at from injury_data_status where list_contains(?, player_uid)"
# Images: only allowed sources (decision D7 - Wikidata/Commons); latest primary image wins.
BATCH_PRIMARY_IMAGE = """
select player_uid, image_url, source, license, attribution from (
  select *, row_number() over (partition by player_uid order by id desc) as rn
  from player_images
  where list_contains(?, player_uid) and list_contains(?, source) and is_primary
) where rn = 1
"""


# Provenance of the Wikidata link of ONE canonical player (getPlayer): latest link row.
WIKIDATA_LINK_PROVENANCE = ("select dataset_version, linked_at from player_wikidata_links where player_uid = ? "
                            "order by linked_at desc, dataset_version desc limit 1")
