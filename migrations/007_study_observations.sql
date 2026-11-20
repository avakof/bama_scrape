-- Preserve the raw search denominator and the decision about each observation.
-- Legacy rows retain their original meaning and have no study assessment.
ALTER TABLE monitoring_runs ADD COLUMN study_profile {{TEXT}} NOT NULL DEFAULT 'legacy';
ALTER TABLE monitoring_runs ADD COLUMN candidate_count {{INT}};
ALTER TABLE daily_ad_observations ADD COLUMN candidate_present {{BOOL}};
ALTER TABLE daily_ad_observations ADD COLUMN eligibility_status {{TEXT}};
ALTER TABLE daily_ad_observations ADD COLUMN eligibility_reason {{TEXT}};
ALTER TABLE daily_ad_observations ADD COLUMN eligibility_source {{TEXT}};
ALTER TABLE daily_ad_observations ADD COLUMN raw_card_price_normalized {{INT}};
ALTER TABLE daily_ad_observations ADD COLUMN assessed_price_toman {{INT}};
ALTER TABLE daily_ad_observations ADD COLUMN eligible_price_toman {{INT}};
ALTER TABLE daily_ad_observations ADD COLUMN study_state_json {{JSON}};
