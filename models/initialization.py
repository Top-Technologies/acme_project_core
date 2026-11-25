import math
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from .boq import _IMPORT_TOKEN


class ProjectInitialization(models.Model):
    _inherit = 'project.project'

    acme_boq_initialized = fields.Boolean(string='BOQ Initialized', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get('acme_boq_initialized') for vals in vals_list):
            raise UserError(_('Finalize the initial BOQ using its workflow action.'))
        return super().create(vals_list)

    def _compute_counts(self):
        for project in self:
            project.acme_certificate_count = self.env['acme.project.certificate'].search_count([('project_id', '=', project.id)]) if self.env.user.has_group('project.group_project_user') else 0

    @api.onchange('acme_order_id')
    def _onchange_contract_order(self):
        if self.acme_order_id and not self.acme_boq_initialized:
            order = self.acme_order_id
            self.company_id, self.partner_id = order.company_id, order.partner_id
            self.acme_contract_currency_id = order.currency_id
            self.acme_contract_amount, self.acme_contract_total = order.amount_untaxed, order.amount_total

    def write(self, vals):
        if 'acme_boq_initialized' in vals and self.env.context.get('acme_import_token') is not _IMPORT_TOKEN:
            raise UserError(_('Use Finalize Initial BOQ.'))
        if {'acme_cost_budget', 'acme_boq_vat_rate'}.intersection(vals):
            if any(project.acme_boq_initialized for project in self) or not self.env.user.has_group('project.group_project_manager'):
                raise UserError(_('Only a Project Manager may set the initial cost budget/VAT before BOQ initialization.'))
        if vals.get('acme_order_id') and not any(project.acme_boq_initialized for project in self):
            order = self.env['sale.order'].browse(vals['acme_order_id'])
            order.check_access('read')
            vals = dict(vals, acme_contract_amount=order.amount_untaxed, acme_contract_total=order.amount_total,
                        acme_contract_currency_id=order.currency_id.id, company_id=order.company_id.id, partner_id=order.partner_id.id)
        return super().write(vals)

    @api.constrains('acme_contract_amount', 'acme_contract_total', 'acme_cost_budget', 'acme_boq_vat_rate')
    def _check_initial_financials(self):
        for project in self:
            if any(not math.isfinite(value) or value < 0 for value in (project.acme_contract_amount, project.acme_contract_total, project.acme_cost_budget, project.acme_boq_vat_rate)):
                raise ValidationError(_('Initial contract values, budget and BOQ VAT must be finite nonnegative numbers.'))

    def action_acme_finalize_boq(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('project.group_project_manager'):
            raise UserError(_('Only a Project Manager may finalize the initial BOQ.'))
        self._acme_lock_boq()
        missing = []
        if not self.partner_id:
            missing.append(_('customer'))
        if not self.company_id:
            missing.append(_('company'))
        if not self.acme_contract_currency_id:
            missing.append(_('contract currency'))
        if not self.acme_boq_ids:
            missing.append(_('BOQ lines'))
        if self.acme_boq_initialized:
            raise UserError(_('The initial BOQ is already finalized.'))
        if missing:
            raise UserError(_('Cannot finalize Initial BOQ. Missing: %s.', ', '.join(missing)))
        self._check_contract()
        for line in self.acme_boq_ids:
            if any(not math.isfinite(value) or value < 0 for value in (line.quantity, line.unit_rate, line.selling_rate)):
                raise ValidationError(_('BOQ quantities and rates must be finite nonnegative numbers.'))
        self.with_context(acme_import_token=_IMPORT_TOKEN).write({'acme_boq_initialized': True})
        self.message_post(body=_('Initial BOQ finalized. Later baseline edits require the inline approval workflow.'))
