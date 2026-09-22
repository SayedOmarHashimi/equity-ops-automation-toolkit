-- Control: every broker release should have a matching payroll line for the
-- same (grant_id, tranche). Any row returned here is a MISSING_PAYROLL
-- violation.
{{ config(store_failures=true) }}

select *
from {{ ref('fct_vest_reconciliation') }}
where release_ref is not null
  and payroll_line_id is null
