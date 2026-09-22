-- Typed pass over the Stage 1 payroll RSU-income extract.
select
    payroll_line_id,
    emp_id,
    grant_id,
    tranche,
    cast(vest_date as date) as vest_date,
    cast(pay_date as date) as pay_date,
    earning_code,
    fmv_per_share,
    taxable_income,
    tax_country,
    tax_rate,
    tax_withheld,
    withholding_refund
from read_csv_auto('../data/payroll_rsu.csv')
