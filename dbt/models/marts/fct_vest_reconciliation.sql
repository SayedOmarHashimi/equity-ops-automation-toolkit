-- One row per vest event known to either the broker or payroll feed (full outer
-- join on grant_id + tranche), enriched with the HRIS facts needed to evaluate
-- the business-rule controls in tests/.
with broker as (
    select * from {{ ref('stg_broker_releases') }}
),

payroll as (
    select * from {{ ref('stg_payroll_rsu') }}
),

hris as (
    select * from {{ ref('stg_hris_employees') }}
),

joined as (
    select
        coalesce(broker.grant_id, payroll.grant_id) as grant_id,
        coalesce(broker.tranche, payroll.tranche) as tranche,
        coalesce(broker.participant_id, payroll.emp_id) as employee_id,
        broker.release_ref,
        broker.release_date,
        broker.shares_vested,
        broker.fmv_per_share as broker_fmv_per_share,
        broker.shares_withheld,
        broker.shares_released,
        payroll.payroll_line_id,
        payroll.vest_date,
        payroll.fmv_per_share as payroll_fmv_per_share,
        payroll.tax_country,
        payroll.tax_rate,
        payroll.tax_withheld
    from broker
    full outer join payroll
        on broker.grant_id = payroll.grant_id
        and broker.tranche = payroll.tranche
)

select
    joined.*,
    hris.termination_date,
    hris.country as hris_country,
    hris.prior_country as hris_prior_country,
    hris.transfer_date as hris_transfer_date
from joined
left join hris
    on joined.employee_id = hris.employee_id
