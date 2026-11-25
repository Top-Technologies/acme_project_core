from collections import defaultdict
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class Project(models.Model):
    _inherit = 'project.project'

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        readonly=True,
    )
    acme_order_id = fields.Many2one('sale.order', string='Contract Sales Order', copy=False, ondelete='restrict', check_company=True)
    acme_project_sequence = fields.Char(string='Project Code', copy=False, index=True, tracking=True)
    acme_lifecycle_state = fields.Selection(
        [('draft', 'Draft'), ('created', 'Created'), ('verified', 'Verified')],
        default='draft', required=True, readonly=True, copy=False, tracking=True,
    )
    acme_contract_reference = fields.Char(string='Contract Reference', tracking=True)
    acme_boq_vat_rate = fields.Float(string='Tender BOQ VAT (%)')
    acme_documents_folder_id = fields.Many2one('documents.document', string='Project Documents Folder', domain="[('type', '=', 'folder')]", copy=False)
    acme_contract_currency_id = fields.Many2one('res.currency', string='Contract Currency', default=lambda self: self.env.company.currency_id)
    acme_contract_amount = fields.Monetary(string='Contract Value Excluding Tax', currency_field='acme_contract_currency_id')
    acme_contract_total = fields.Monetary(string='Contract Value Including Tax', currency_field='acme_contract_currency_id')
    acme_bond_basis = fields.Selection([('untaxed', 'Contract Excluding Tax'), ('total', 'Contract Including Tax')], string='Performance Bond Basis', required=True, default='untaxed', tracking=True)
    acme_performance_bond_amount = fields.Monetary(string='Performance Bond Required (10%)', currency_field='acme_contract_currency_id', compute='_compute_bond')
    acme_retention_percent = fields.Float(string='Retention (%)', default=10, tracking=True)
    acme_recovery_percent = fields.Float(string='Advance Recovery (%)', tracking=True, help='Percentage of current certified work; configure the agreed rate. No rate is assumed.')
    acme_boq_ids = fields.One2many('acme.project.boq', 'project_id', string='BOQ', copy=False)
    acme_guarantee_ids = fields.One2many('acme.project.guarantee', 'project_id', string='Guarantees', copy=False)
    acme_certificate_ids = fields.One2many('acme.project.certificate', 'project_id', string='Payment Certificates', copy=False)
    acme_summary = fields.Json(compute='_compute_summary')
    acme_summary_html = fields.Html(string='Summary Sheet', compute='_compute_summary', sanitize=True)
    acme_certificate_count = fields.Integer(compute='_compute_counts')
    acme_advance_received = fields.Monetary(string='Advance Received Excluding Tax', currency_field='acme_contract_currency_id', tracking=True, help='Record the actual customer advance received, on the agreed tax-exclusive basis. An approved request or unpaid invoice is not cash received.')
    acme_recovered = fields.Monetary(currency_field='acme_contract_currency_id', compute='_compute_recovered', store=True, string='Advance Recovered')
    acme_advance_balance = fields.Monetary(currency_field='acme_contract_currency_id', compute='_compute_recovered', store=True, string='Outstanding Advance')
    acme_advance_type = fields.Selection([('fixed', 'Fixed'), ('percentage', 'Percentage')], default='fixed', string='Contract Advance Type')
    acme_advance_percent = fields.Float(string='Contract Advance (%)')
    acme_advance_fixed_amount = fields.Monetary(string='Fixed Contract Advance', currency_field='acme_contract_currency_id')
    acme_contract_advance_amount = fields.Monetary(
        string='Contract Advance Amount', currency_field='acme_contract_currency_id',
        compute='_compute_contract_advance_amount',
    )
    acme_advance_guarantee_required = fields.Boolean(string='Advance Requires Guarantee')
    acme_advance_required_before_work = fields.Boolean(string='Advance Required Before Work Starts')
    acme_recovery_basis = fields.Selection(
        [('gross', 'Gross'), ('after_rebate', 'After Rebate')], required=True, default='gross',
    )
    acme_recovery_method = fields.Selection(
        [('percentage', 'Percentage'), ('fixed', 'Fixed Installment')], required=True, default='percentage',
    )
    acme_recovery_installment = fields.Monetary(currency_field='acme_contract_currency_id')

    @api.depends('acme_advance_type', 'acme_advance_fixed_amount', 'acme_advance_percent',
                 'acme_contract_amount', 'acme_contract_currency_id.rounding')
    def _compute_contract_advance_amount(self):
        for project in self:
            if project.acme_advance_type == 'percentage':
                amount = project.acme_contract_amount * project.acme_advance_percent / 100
                currency = project.acme_contract_currency_id
                project.acme_contract_advance_amount = currency.round(amount) if currency else amount
            else:
                project.acme_contract_advance_amount = project.acme_advance_fixed_amount

    @api.constrains('acme_advance_percent', 'acme_advance_fixed_amount', 'acme_recovery_installment')
    def _check_contract_advance_terms(self):
        for project in self:
            if not 0 <= project.acme_advance_percent <= 100 or not project.acme_advance_fixed_amount >= 0:
                raise ValidationError(_('Advance percentage must be between 0 and 100 and fixed advance cannot be negative.'))
            if not project.acme_recovery_installment >= 0:
                raise ValidationError(_('The recovery installment cannot be negative.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Project itself deliberately remains company-optional in Odoo. Only
            # records explicitly being configured for ACME contracting get this
            # active-company default.
            acme_setup = self.env.context.get('acme_project_setup') or any(
                key in vals for key in (
                    'acme_order_id', 'acme_contract_reference',
                    'acme_contract_amount', 'acme_contract_total',
                    'acme_cost_budget', 'acme_boq_vat_rate', 'acme_boq_ids',
                )
            )
            if acme_setup and 'company_id' not in vals:
                vals['company_id'] = self.env.company.id
            if vals.get('acme_order_id'):
                order = self.env['sale.order'].browse(vals['acme_order_id'])
                order.check_access('read')
                vals.update(company_id=order.company_id.id, partner_id=order.partner_id.id,
                            acme_contract_amount=order.amount_untaxed, acme_contract_total=order.amount_total,
                            acme_contract_currency_id=order.currency_id.id)
        return super().create(vals_list)

    @api.depends('acme_contract_amount', 'acme_contract_total', 'acme_bond_basis')
    def _compute_bond(self):
        for project in self:
            base = project.acme_contract_total if project.acme_bond_basis == 'total' else project.acme_contract_amount
            project.acme_performance_bond_amount = project.acme_contract_currency_id.round(base * 0.1) if project.acme_contract_currency_id else base * 0.1

    @api.depends('acme_boq_ids.amount', 'acme_boq_ids.quantity', 'acme_boq_ids.selling_rate',
                 'acme_boq_ids.bill_no', 'acme_boq_ids.category', 'acme_boq_vat_rate')
    def _compute_summary(self):
        from markupsafe import Markup, escape
        for project in self:
            totals = defaultdict(float)
            for line in project.acme_boq_ids:
                totals[line.bill_no or '', line.category or ''] += line.amount
            project.acme_summary = [{'bill': key[0], 'category': key[1], 'amount': amount} for key, amount in sorted(totals.items())]
            rows = [Markup('<tr><td>%s</td><td>%s</td><td>%.2f</td></tr>') % (escape(key[0]), escape(key[1]), amount) for key, amount in sorted(totals.items())]
            untaxed = sum(totals.values())
            vat = untaxed * project.acme_boq_vat_rate / 100
            selling = sum(line.quantity * line.selling_rate for line in project.acme_boq_ids)
            project.acme_summary_html = Markup('<table class="table table-sm"><thead><tr><th>Bill</th><th>Category</th><th>Amount</th></tr></thead><tbody>%s</tbody><tfoot><tr><th colspan="2">BOQ excluding tax</th><th>%.2f</th></tr><tr><th colspan="2">BOQ VAT</th><th>%.2f</th></tr><tr><th colspan="2">BOQ including tax</th><th>%.2f</th></tr><tr><th colspan="2">Effective BOQ selling value excluding tax</th><th>%.2f</th></tr></tfoot></table>') % (Markup('').join(rows), untaxed, vat, untaxed + vat, selling)

    @api.depends('acme_advance_received', 'acme_certificate_ids.state', 'acme_certificate_ids.recovery_amount')
    def _compute_recovered(self):
        for project in self:
            project.acme_recovered = sum(project.acme_certificate_ids.filtered(lambda c: c.state == 'certified').mapped('recovery_amount'))
            project.acme_advance_balance = project.acme_advance_received - project.acme_recovered

    @api.constrains('acme_retention_percent', 'acme_recovery_percent', 'acme_advance_received',
                    'acme_order_id', 'partner_id', 'company_id', 'acme_contract_currency_id')
    def _check_contract(self):
        for project in self:
            if any((not 0 <= rate <= 100 for rate in (project.acme_retention_percent, project.acme_recovery_percent))):
                raise ValidationError(_('Retention and recovery percentages must be between 0 and 100.'))
            if project.acme_advance_received < 0 or project.acme_advance_received < project.acme_recovered:
                raise ValidationError(_('Advance received cannot be negative or less than advance already recovered.'))
            order = project.acme_order_id
            if order and (order.company_id != project.company_id
                          or order.partner_id.commercial_partner_id != project.partner_id.commercial_partner_id
                          or order.currency_id != project.acme_contract_currency_id):
                raise ValidationError(_('The contract order must match the project company, customer and contract currency.'))

    def write(self, vals):
        protected = {'acme_order_id', 'partner_id', 'company_id', 'acme_contract_amount',
                     'acme_contract_total', 'acme_contract_currency_id'}
        if protected.intersection(vals) and any((p.acme_boq_initialized or p.acme_certificate_ids for p in self)):
            raise UserError(_('The source contract cannot be replaced after importing the BOQ.'))
        if {'acme_retention_percent', 'acme_recovery_percent', 'acme_advance_received', 'acme_contract_reference', 'acme_bond_basis', 'acme_documents_folder_id'}.intersection(vals):
            if not self.env.user.has_group('project.group_project_manager'):
                raise UserError(_('Only a Project Manager may change project financial terms.'))
        return super().write(vals)

    @api.constrains('acme_documents_folder_id')
    def _check_documents_folder(self):
        for project in self:
            if project.acme_documents_folder_id and project.acme_documents_folder_id.type != 'folder':
                raise ValidationError(_('Select a Documents folder.'))

    def action_acme_certificates(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Payment Certificates'), 'res_model': 'acme.project.certificate', 'view_mode': 'list,form', 'domain': [('project_id', '=', self.id)], 'context': {'default_project_id': self.id}}
