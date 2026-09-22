-- Control: payroll must tax a vest in the employee's HRIS country as of the
-- vest date (accounting for transfers). Any row returned here is a
-- STALE_COUNTRY violation.
{{ config(store_failures=true) }}

with base as (
    select
        *,
        case
            when hris_transfer_date is not null and vest_date < hris_transfer_date
                then hris_prior_country
            else hris_country
        end as expected_country
    from {{ ref('fct_vest_reconciliation') }}
    where tax_country is not null
)

select *
from base
where tax_country is distinct from expected_country
