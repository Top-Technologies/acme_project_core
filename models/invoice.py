from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError

class AccountMove(models.Model):
    _inherit = 'account.move'
    acme_project_id = fields.Many2one('project.project', string='ACME Project', ondelete='restrict', check_company=True, copy=False)
    acme_certificate_ids = fields.One2many('acme.project.certificate', 'invoice_id', string='Payment Certificates')
