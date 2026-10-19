"""tushare 接口字段清单（显式 fields）。

背景：tushare 不传 fields 时只返回"默认列"，而规则需要的若干字段
（credit_impa_loss、inv_turn 等）属于可选列，必须显式请求；同时部分
历史字段名与当前 API 不一致（biz_tax_surchg、lt_amor_exp、c_fr_sale_sg、
c_pay_acq_const_fiolta、non_cur_liab_due_1y、depr_fa_coga_dpba）。
集中在此处，避免各层各自猜字段名。
"""
from __future__ import annotations

_INCOME = (
    "ts_code,end_date,ann_date,report_type,"
    "revenue,oper_cost,biz_tax_surchg,oth_b_income,oth_income,"
    "assets_impair_loss,credit_impa_loss,"
    "sell_exp,admin_exp,rd_exp,fin_exp,int_exp,"
    "operate_profit,total_profit,n_income,n_income_attr_p"
)

_BALANCE = (
    "ts_code,end_date,ann_date,report_type,"
    "money_cap,st_borr,lt_borr,bond_payable,non_cur_liab_due_1y,"
    "total_assets,accounts_receiv,acct_payable,inventories,cip,fix_assets,"
    "goodwill,total_hldr_eqy_exc_min_int,oth_receiv,lt_amor_exp"
)

_CASHFLOW = (
    "ts_code,end_date,ann_date,report_type,"
    "n_cashflow_act,n_cashflow_inv_act,free_cashflow,"
    "c_pay_acq_const_fiolta,c_fr_sale_sg,"
    "depr_fa_coga_dpba,lt_amort_deferred_exp"
)

_INDICATOR = (
    "ts_code,end_date,ann_date,report_type,"
    "grossprofit_margin,netprofit_margin,roe,roic,"
    "inv_turn,ar_turn,debt_to_assets,profit_dedt,eps,fcff,assets_turn"
)

# 排雷/质量/模块层共用的"年报全字段"口径
ANNUAL_FULL_FIELDS: dict[str, str] = {
    "income": _INCOME,
    "balancesheet": _BALANCE,
    "cashflow": _CASHFLOW,
    "fina_indicator": _INDICATOR,
}
