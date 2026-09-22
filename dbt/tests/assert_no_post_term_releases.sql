-- Control: a broker release must not be dated after the employee's HRIS
-- termination date. Any row returned here is a POST_TERM_RELEASE violation.
{{ config(store_failures=true) }}

select *
from {{ ref('fct_vest_reconciliation') }}
where termination_date is not null
  and release_date is not null
  and release_date > termination_date
