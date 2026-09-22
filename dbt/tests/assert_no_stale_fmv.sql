-- Control: payroll's FMV for a vest event must match the broker's FMV for
-- the same event. Any row returned here is a STALE_FMV_PAYROLL violation.
{{ config(store_failures=true) }}

select *
from {{ ref('fct_vest_reconciliation') }}
where broker_fmv_per_share is not null
  and payroll_fmv_per_share is not null
  and abs(broker_fmv_per_share - payroll_fmv_per_share) > 0.001
