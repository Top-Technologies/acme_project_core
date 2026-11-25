from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError
_IMPORT_TOKEN = object()

class ProjectBoq(models.Model):
    _name = 'acme.project.boq'
    _description = 'Project BOQ Contract Baseline'
    _order = 'sequence, id'
    _check_company_auto = True
    project_id = fields.Many2one('project.project', required=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='project_id.acme_contract_currency_id')
    source_line_id = fields.Many2one('sale.order.line', ondelete='restrict', check_company=True)
    sequence = fields.Integer()
    reference = fields.Char()
    name = fields.Char(required=True)
    bill_no = fields.Char()
    category = fields.Char()
    product_name = fields.Char(string='Product')
    unit = fields.Char()
    quantity = fields.Float()
    unit_rate = fields.Monetary(currency_field='currency_id')
    selling_rate = fields.Monetary(string='Contract Selling Rate Excluding Tax', currency_field='currency_id')
    amount = fields.Monetary(currency_field='currency_id', compute='_compute_amount', store=True)

    acme_original_quantity = fields.Float(string='Original Qty', compute='_compute_approved_quantity_variation')
    acme_quantity_variation = fields.Char(string='Variation', compute='_compute_approved_quantity_variation')

    @api.model
    def _acme_signed_boq_values(self, project):
        baseline = {}
        for revision in project.acme_boq_history_ids.filtered(lambda item: item.state == 'approved').sorted('number'):
            for change in revision.changes or []:
                original = baseline.setdefault(change['line_id'], {})
                before = change['before']
                for key in ('quantity', 'unit_rate', 'selling_rate'):
                    if not before or key in before:
                        original.setdefault(key, before.get(key, 0))
        return baseline

    @api.depends('quantity', 'project_id.acme_boq_history_ids.state', 'project_id.acme_boq_history_ids.number',
                 'project_id.acme_boq_history_ids.changes')
    def _compute_approved_quantity_variation(self):
        for project, lines in self.grouped('project_id').items():
            original = self._acme_signed_boq_values(project) if project else {}
            for line in lines:
                line.acme_original_quantity = original.get(line.id, {}).get('quantity', line.quantity)
                delta = line._acme_quantity_delta()
                line.acme_quantity_variation = ('+' if delta > 0 else chr(0x2212)) + format(abs(delta), '.15g') if delta else '0'

    def _acme_quantity_delta(self):
        self.ensure_one()
        return round(self.quantity - self.acme_original_quantity, 12)

    @api.depends('quantity', 'unit_rate')
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.unit_rate

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            project = self.env['project.project'].browse(vals.get('project_id'))
            project.check_access('write')
            if not project.company_id:
                raise UserError(_('Set the Project company under Contract and Payments before adding BOQ lines.'))
        if self.env.context.get('acme_import_token') is not _IMPORT_TOKEN:
            if not self.env.user.has_group('project.group_project_manager'):
                raise UserError(_('Only a Project Manager may prepare a manual initial BOQ.'))
            for vals in vals_list:
                project = self.env['project.project'].browse(vals.get('project_id'))
                if project.acme_boq_initialized or vals.get('source_line_id'):
                    raise UserError(_('Add manual BOQ rows only before initialization. Tender source links are populated by the bridge.'))
        return super().create(vals_list)

    def write(self, vals):
        from .boq_revision import _BOQ_TOKEN, PROPOSAL_FIELDS
        if self.env.context.get('acme_boq_token') is _BOQ_TOKEN:
            return super().write(vals)
        initial = {'sequence', 'reference', 'name', 'bill_no', 'category', 'product_name', 'unit', 'quantity', 'unit_rate', 'selling_rate'}
        if set(vals) <= initial and all(not line.project_id.acme_boq_initialized for line in self):
            self.mapped('project_id').check_access('write')
            if not self.env.user.has_group('project.group_project_manager'):
                raise UserError(_('Only a Project Manager may edit the manual initial BOQ.'))
            return super().write(vals)
        if set(vals) - set(PROPOSAL_FIELDS):
            raise UserError(_('Approved BOQ values are locked. Use Edit BOQ Proposal and obtain approval.'))
        for project in self.mapped('project_id').sorted('id'):
            project._acme_lock_boq()
        for line in self:
            if line.project_id.acme_boq_state != 'draft' or line.project_id.acme_boq_requester_id != self.env.user:
                raise UserError(_('Only the requester may edit a draft BOQ proposal.'))
        return super().write(vals)

    def unlink(self):
        if any(line.project_id.acme_boq_initialized for line in self) or not self.env.user.has_group('project.group_project_manager'):
            raise UserError(_('Finalized BOQ rows cannot be deleted.'))
        self.mapped('project_id').check_access('write')
        return super().unlink()
