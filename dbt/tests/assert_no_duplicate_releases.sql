-- Control: each (grant_id, tranche) should appear at most once in the broker
-- file. Any row returned here is a DUPLICATE_RELEASE violation.
{{ config(store_failures=true) }}

select grant_id, tranche, count(*) as release_count
from {{ ref('stg_broker_releases') }}
group by grant_id, tranche
having count(*) > 1
