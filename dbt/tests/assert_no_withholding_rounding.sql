-- Control: shares withheld must be at least ceil(shares_vested x tax_rate).
-- Any row returned here is a WITHHOLDING_ROUNDING violation.
--
-- Events already caught by assert_no_stale_country are excluded: payroll's
-- tax_rate there reflects the wrong country, so it can't be trusted to
-- compute the expected withholding without producing false positives.
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
    where shares_vested is not null
      and tax_rate is not null
),

stale_country_events as (
    select grant_id, tranche
    from base
    where tax_country is distinct from expected_country
)

select b.*
from base b
left join stale_country_events sc
    on b.grant_id = sc.grant_id
    and b.tranche = sc.tranche
where sc.grant_id is null
  and b.shares_withheld < ceil(b.shares_vested * b.tax_rate)
