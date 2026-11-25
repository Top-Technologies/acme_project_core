from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError

class Guarantee(models.Model):
    _name = 'acme.project.guarantee'
    _description = 'Project Bank Guarantee'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    name = fields.Char(string='Guarantee Reference', required=True, tracking=True)
    project_id = fields.Many2one('project.project', required=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='project_id.acme_contract_currency_id')
    guarantee_type = fields.Selection([('performance', 'Performance Bond'), ('advance', 'Advance Guarantee')], required=True, default='performance', tracking=True)
    bank_id = fields.Many2one('res.bank', string='Bank', tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    issue_date = fields.Date(tracking=True)
    expiry_date = fields.Date(tracking=True)
    scan = fields.Binary(string='Signed Guarantee Scan', attachment=True)
    scan_filename = fields.Char()
    document_id = fields.Many2one('documents.document', string='Stored Guarantee Document', readonly=True, copy=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('released', 'Released'),
    ], default='draft', required=True, tracking=True)
    signed_contract = fields.Binary(string='Signed Contract', attachment=True)
    signed_contract_filename = fields.Char(string='Signed Contract Filename')

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_activate(self):
        self.write({'state': 'active'})

    def action_release(self):
        self.write({'state': 'released'})

    @api.model
    def _cron_guarantee_expiry(self):
        today = fields.Date.context_today(self)
        activity_type = self.env.ref('acme_project_core.activity_project_guarantee_expiry')
        edm_users = self.env.ref('acme_project_core.group_acme_edm').all_user_ids.sorted('id')
        active = self.search([('state', '=', 'active'), ('expiry_date', '!=', False)])
        overdue = active.filtered(lambda guarantee: guarantee.expiry_date < today)
        overdue.write({'state': 'expired'})

        for guarantee in active - overdue:
            company = guarantee.company_id
            lead_days = max(company.acme_guarantee_lead_days, 0)
            if guarantee.expiry_date > today + timedelta(days=lead_days):
                continue
            self.env.cr.execute('UPDATE acme_project_guarantee SET id = id WHERE id = %s', [guarantee.id])
            project = guarantee.project_id
            if self.env['mail.activity'].search_count([
                ('acme_guarantee_reminder_id', '=', guarantee.id),
                ('activity_type_id', '=', activity_type.id), ('date_deadline', '=', guarantee.expiry_date),
            ], limit=1):
                continue

            candidates = company.acme_guarantee_reminder_user_id | project.user_id | edm_users
            for user in candidates:
                if not company or not user.active or user.share or company not in user.company_ids:
                    continue
                if not project.with_user(user).with_context(allowed_company_ids=company.ids).has_access('read'):
                    continue
                project.activity_schedule(
                    'acme_project_core.activity_project_guarantee_expiry',
                    date_deadline=guarantee.expiry_date,
                    summary=_('Extend or Release Guarantee: %s', guarantee.display_name),
                    user_id=user.id, acme_guarantee_reminder_id=guarantee.id,
                )
                break

    def action_store_scan(self):
        self.ensure_one()
        self.check_access('write')
        if not self.scan or not self.project_id.acme_documents_folder_id:
            raise UserError(_('Upload the scan and select the Project Documents Folder first.'))
        if self.document_id:
            self.document_id.attachment_id.write({'datas': self.scan, 'name': self.scan_filename or self.name})
            return {'type': 'ir.actions.act_window', 'res_model': 'documents.document', 'view_mode': 'form', 'res_id': self.document_id.id}
        attachment = self.env['ir.attachment'].create({'name': self.scan_filename or self.name, 'datas': self.scan, 'res_model': 'project.project', 'res_id': self.project_id.id})
        self.document_id = self.env['documents.document'].create({'name': attachment.name, 'attachment_id': attachment.id, 'folder_id': self.project_id.acme_documents_folder_id.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'documents.document', 'view_mode': 'form', 'res_id': self.document_id.id}

    @api.constrains('amount', 'issue_date', 'expiry_date', 'project_id')
    def _check_details(self):
        for guarantee in self:
            if guarantee.amount <= 0:
                raise ValidationError(_('Guarantee amount must be positive.'))
            if guarantee.issue_date and guarantee.expiry_date and (guarantee.expiry_date < guarantee.issue_date):
                raise ValidationError(_('Guarantee expiry cannot precede its issue date.'))


class GuaranteeReminderActivity(models.Model):
    _inherit = 'mail.activity'

    acme_guarantee_reminder_id = fields.Many2one(
        'acme.project.guarantee', index=True, copy=False, ondelete='set null',
    )
