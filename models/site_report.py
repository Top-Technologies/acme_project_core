from odoo import fields, models, _
from odoo.exceptions import ValidationError


class SiteReport(models.TransientModel):
    _name = 'acme.project.site.report'
    _description = 'Project Site Report Period'
    _check_company_auto = True

    project_id = fields.Many2one('project.project', required=True, check_company=True)
    company_id = fields.Many2one(related='project_id.company_id')
    date_from = fields.Date(string='From', required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    report_date = fields.Date(required=True, default=fields.Date.context_today)
    project_location = fields.Char()
    reported_by_id = fields.Many2one('res.users', string='Reported By', default=lambda self: self.env.user)

    def _report_data(self):
        self.ensure_one()
        self.project_id.check_access('read')
        if self.date_from > self.date_to:
            raise ValidationError(_('The report end date cannot precede its start date.'))
        tasks = self.env['project.task'].search([
            ('project_id', '=', self.project_id.id), ('state', '!=', '1_canceled'),
            ('acme_start_date', '<=', self.date_to), ('acme_end_date', '>=', self.date_from),
        ], order='acme_start_date, id')
        period = [('project_id', '=', self.project_id.id), ('date', '>=', self.date_from), ('date', '<=', self.date_to)]
        extra = self.env['acme.task.additional.work'].search(period)
        challenges = self.env['acme.task.challenge'].search(period)
        resources = self.env['acme.task.resource.use'].search(period)
        manpower = self.env['acme.task.manpower'].search(period)
        return {'tasks': tasks, 'additional': extra, 'challenges': challenges,
                'resources': resources, 'manpower': manpower,
                'assigned_users': (tasks | extra.mapped('task_id') | challenges.mapped('task_id')).mapped('user_ids').sorted('name')}

    def action_print(self):
        self._report_data()
        return self.env.ref('acme_project_core.report_site_performance').report_action(self)


class ProjectSiteReportAction(models.Model):
    _inherit = 'project.project'

    def action_acme_site_report(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_window', 'name': _('Site Performance Report'),
                'res_model': 'acme.project.site.report', 'view_mode': 'form', 'target': 'new',
                'context': {'default_project_id': self.id}}
