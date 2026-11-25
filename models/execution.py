import math
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError

class ProjectExecution(models.Model):
    _inherit = 'project.project'
    acme_contract_type = fields.Selection([('main', 'Main'), ('sub', 'Sub-contract')], string='Contract Type', default='main', required=True, tracking=True)
    acme_cost_budget = fields.Monetary(string='Cost Build-up Budget', currency_field='acme_contract_currency_id', copy=False)

    def action_acme_resources(self):
        return {'type': 'ir.actions.act_window', 'name': _('Site Resource Register'), 'res_model': 'acme.site.resource', 'view_mode': 'list,form'}
    acme_takeoff_file = fields.Binary(string='Signed Takeoff Sheet', attachment=True, copy=False)
    acme_takeoff_filename = fields.Char(copy=False)

class BoqExecution(models.Model):
    _inherit = 'acme.project.boq'
    executed_quantity = fields.Float(string='Executed Quantity', copy=False, help='Cumulative actual quantity from the signed takeoff; may exceed the approved contract quantity.')
    executed_amount = fields.Monetary(string='Executed Amount', compute='_compute_executed', store=True, help='Actual executed quantity × contract selling rate excluding tax. This is not automatically certified or invoiced.')
    execution_difference = fields.Float(string='Executed Less Contract', compute='_compute_executed', store=True)

    @api.depends('executed_quantity', 'selling_rate', 'quantity')
    def _compute_executed(self):
        for line in self:
            line.executed_amount = line.currency_id.round(line.executed_quantity * line.selling_rate) if line.currency_id else line.executed_quantity * line.selling_rate
            line.execution_difference = line.executed_quantity - line.quantity

    @api.constrains('executed_quantity')
    def _check_execution(self):
        for line in self:
            if not math.isfinite(line.executed_quantity) or line.executed_quantity < 0:
                raise ValidationError(_('Executed quantity must be a finite nonnegative number.'))

    def write(self, vals):
        if 'executed_quantity' in vals:
            self.mapped('project_id').check_access('write')
            remaining = {key: value for key, value in vals.items() if key != 'executed_quantity'}
            if remaining:
                super().write(remaining)
            from .boq_revision import _BOQ_TOKEN
            return super(BoqExecution, self.with_context(acme_boq_token=_BOQ_TOKEN)).write({'executed_quantity': vals['executed_quantity']})
        return super().write(vals)

class CertificateExecution(models.Model):
    _inherit = 'acme.project.certificate'

    def action_load_executed_boq(self):
        self.ensure_one()
        self.check_access('write')
        if self.state != 'draft' or self.line_ids:
            raise UserError(_('Load executed BOQ into an empty draft certificate.'))
        previous = self.search([('project_id', '=', self.project_id.id), ('state', '=', 'certified')])
        rows = []
        for boq in self.project_id.acme_boq_ids:
            certified = sum(previous.mapped('line_ids').filtered(lambda line: line.boq_id == boq).mapped('quantity'))
            current = max(boq.executed_quantity - certified, 0)
            if current:
                rows.append(Command.create({'boq_id': boq.id, 'quantity': current}))
        self.line_ids = rows
