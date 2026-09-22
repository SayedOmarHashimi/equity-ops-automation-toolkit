-- Typed pass over the Stage 1 HRIS extract.
select
    employee_id,
    first_name,
    last_name,
    department,
    level,
    cast(hire_date as date) as hire_date,
    cast(termination_date as date) as termination_date,
    status,
    country,
    prior_country,
    cast(transfer_date as date) as transfer_date
from read_csv_auto('../data/hris_employees.csv')
