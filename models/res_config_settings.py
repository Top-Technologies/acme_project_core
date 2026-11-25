from odoo import fields, models
from odoo.exceptions import AccessError


_COSTING_POLICY = {
    'acme_earned_revenue_method', 'acme_material_cost_basis', 'acme_separate_workshop_time',
    'acme_staff_advance_account_id', 'acme_staff_reimbursement_account_id', 'acme_staff_settlement_days',
    'acme_wip_journal_id', 'acme_wip_asset_account_id', 'acme_wip_liability_account_id',
    'acme_wip_offset_account_id', 'acme_wip_reversal_policy',
}


class ResCompany(models.Model):
    _inherit = 'res.company'
    acme_earned_revenue_method = fields.Selection([
        ('physical', 'Reported Physical Progress'), ('cost_to_cost', 'Cost to Cost'),
        ('measured', 'Authorized Measured BOQ Value'),
    ], string='Project Earned Revenue Method')
    acme_material_cost_basis = fields.Selection([
        ('purchase', 'Expense at Purchase'), ('consumption', 'Value on Consumption'),
    ], string='Project Inventory Cost Basis')
    acme_separate_workshop_time = fields.Boolean(
        string='Workshop Labour Is Separate from Project Timesheets',
        help='Enable only when Finance has verified that work-order hours are not also recorded on project timesheets.',
    )

    def write(self, vals):
        if _COSTING_POLICY.intersection(vals):
            if not self.env.user.has_group('account.group_account_manager'):
                raise AccessError(self.env._('Only Accounting administrators may change project costing policy.'))
        return super().write(vals)
    acme_staff_advance_account_id = fields.Many2one(
        'account.account', string='Staff Advance Control Account', check_company=True,
        domain="[('account_type', '=', 'asset_current')]",
    )
    acme_staff_reimbursement_account_id = fields.Many2one(
        'account.account', string='Staff Reimbursement Payable Account', check_company=True,
        domain="[('account_type', '=', 'liability_payable')]",
    )
    acme_staff_settlement_days = fields.Integer(
        string='Staff Settlement Days after Disbursement',
        help='Set a positive lead time to enable reminders. Finance may set an explicit due date on a request.',
    )
    acme_wip_journal_id = fields.Many2one('account.journal', string='Project WIP Journal', check_company=True,
                                         domain="[('type', '=', 'general')]")
    acme_wip_asset_account_id = fields.Many2one('account.account', string='Earned but Unbilled', check_company=True,
                                               domain="[('account_type', '=', 'asset_current')]")
    acme_wip_liability_account_id = fields.Many2one('account.account', string='Billing Ahead of Earned Work', check_company=True,
                                                   domain="[('account_type', '=', 'liability_current')]")
    acme_wip_offset_account_id = fields.Many2one('account.account', string='WIP Revenue Offset', check_company=True,
                                                domain="[('account_type', 'in', ['income', 'income_other'])]")
    acme_wip_reversal_policy = fields.Selection(
        [('manual', 'Finance Selects Reversal Date'), ('next_day', 'Reverse on First Day after Cutoff')],
        string='WIP Reversal Policy',
    )
    acme_pc_allow_gross_posting = fields.Boolean(
        string='Allow Gross PC Invoices with Separate Deduction Settlement',
    )
    acme_variation_route_reductions_by_absolute = fields.Boolean(
        string='Route Negative Variations by Absolute Untaxed Impact',
    )
    acme_work_advance_finance_user_id = fields.Many2one('res.users', string='Mobilization Advance Finance Reviewer')
    acme_advance_vat_rate = fields.Float(string='Advance Requisition VAT (%)', default=15)
    acme_advance_invoice_tax_id = fields.Many2one(
        'account.tax', string='Advance Invoice Sales Tax', check_company=True,
        domain="[('type_tax_use', '=', 'sale')]",
    )
    acme_advance_invoice_product_id = fields.Many2one(
        'product.product', string='Advance Invoice Product', check_company=True,
    )

    acme_handover_template_id = fields.Many2one(
        'project.task', string='Handover Task Template', check_company=True,
        domain="[('is_template', '=', True)]",
    )
    acme_operations_partner_ids = fields.Many2many(
        'res.partner', 'acme_company_operations_partner_rel', string='Operations Recipients',
    )
    acme_handover_partner_ids = fields.Many2many(
        'res.partner', 'acme_company_handover_partner_rel',
        string='Finance, Warehouse, Supply and Workshop Followers',
    )

    acme_guarantee_reminder_user_id = fields.Many2one(
        'res.users', string='Guarantee Reminder Responsible',
        domain="[('share', '=', False), ('company_ids', 'in', [id])]",
        help='When this user cannot access the project, notify the project manager or an EDM with access.',
    )

    acme_guarantee_lead_days = fields.Integer(
        string='Guarantee Expiry Reminder Lead Days',
        default=30,
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    acme_guarantee_lead_days = fields.Integer(
        related='company_id.acme_guarantee_lead_days',
        readonly=False,
    )
