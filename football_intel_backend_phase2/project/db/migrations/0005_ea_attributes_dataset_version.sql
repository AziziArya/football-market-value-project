-- ═══════════════════════════════════════════════════════════
-- Migration 0005: ea_fc26_attributes needs BOTH a dataset_version
-- (which EA/FC26 snapshot this row came from, e.g. '2025-09-19') AND a
-- model_version (which ML model produced predicted_value_eur, e.g.
-- 'baseline_catboost_v1') — these are two different things that
-- migration 0001 conflated into a single `model_version` column.
-- Found while wiring up load_ea_attributes() in Phase 2.1.
-- ═══════════════════════════════════════════════════════════

ALTER TABLE ea_fc26_attributes ADD COLUMN dataset_version VARCHAR;
