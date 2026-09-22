-- Typed pass over the Stage 1 broker share-release extract.
select
    release_ref,
    participant_id,
    grant_id,
    tranche,
    cast(release_date as date) as release_date,
    shares_vested,
    fmv_per_share,
    shares_withheld,
    shares_released
from read_csv_auto('../data/broker_releases.csv')
