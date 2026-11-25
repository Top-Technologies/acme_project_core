from odoo import api, models, _
from odoo.exceptions import ValidationError, UserError


class ProjectInvoiceRules(models.Model):
    _inherit = 'account.move'

    @api.constrains('acme_project_id', 'company_id', 'partner_id', 'currency_id', 'move_type')
    def _check_project_invoice(self):
        for move in self:
            project = move.acme_project_id
            if project and (move.move_type != 'out_invoice' or move.company_id != project.company_id or move.currency_id != project.acme_contract_currency_id or move.partner_id.commercial_partner_id != project.partner_id.commercial_partner_id):
                raise ValidationError(_('The customer invoice must match the project company, customer and contract currency.'))

    def write(self, vals):
        if 'acme_project_id' in vals and any(move.state != 'draft' for move in self):
            raise UserError(_('Project invoice links cannot change after posting.'))
        return super().write(vals)

    def _post(self, soft=True):
        self._check_project_invoice()
        return super()._post(soft=soft)
