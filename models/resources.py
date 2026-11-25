import math
from odoo import api, fields, models, _
from odoo.fields import Domain
from odoo.exceptions import AccessError, UserError
from odoo.exceptions import ValidationError


class SiteResource(models.Model):
    _name = 'acme.site.resource'
    _description = 'Site Vehicle Equipment and Tool Register'
    _order = 'kind, name'

    name = fields.Char(required=True)
    kind = fields.Selection([('vehicle', 'Vehicle'), ('equipment', 'Equipment'), ('tool', 'Tool')], required=True, default='equipment')
    identifier = fields.Char(string='Registration / Serial / Identifier')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    active = fields.Boolean(default=True)


class ResourceUse(models.Model):
    _name = 'acme.task.resource.use'
    _inherit = 'acme.task.site.entry'
    _description = 'Task Site Resource Utilization'
    _order = 'date, sequence, id'
    _check_company_auto = True

    resource_id = fields.Many2one('acme.site.resource', required=True, check_company=True, ondelete='restrict')
    quantity = fields.Float(default=1, required=True)
    days_used = fields.Float(string='Days Used')

    @api.constrains('quantity', 'days_used')
    def _check_usage(self):
        for entry in self:
            if not math.isfinite(entry.quantity) or entry.quantity <= 0 or not math.isfinite(entry.days_used) or entry.days_used < 0:
                raise ValidationError(_('Resource quantity must be positive and days used nonnegative.'))


class ManpowerUse(models.Model):
    _name = 'acme.task.manpower'
    _inherit = 'acme.task.site.entry'
    _description = 'Task Site Manpower Deployment'
    _order = 'date, sequence, id'
    _check_company_auto = True

    user_id = fields.Many2one('res.users', string='Assigned Person', required=True, domain="[('share', '=', False)]")
    job_position = fields.Char(string='Job Position', required=True)
    days_used = fields.Float(string='Days Deployed')

    @api.constrains('days_used', 'user_id', 'task_id')
    def _check_deployment(self):
        for entry in self:
            if entry.user_id not in entry.task_id.user_ids:
                raise ValidationError(_('Select a person assigned to this task.'))
            if not math.isfinite(entry.days_used) or entry.days_used < 0:
                raise ValidationError(_('Days deployed must be nonnegative.'))


class ProjectDelayReview(models.Model):
    _inherit = 'project.project'

    def action_acme_overdue_tasks(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Delay / Overdue Tasks'),
            'res_model': 'project.task',
            'view_mode': 'list',
            'views': [(self.env.ref('acme_project_core.task_delay_review_list').id, 'list')],
            'domain': [('project_id', '=', self.id), ('acme_open_overdue', '=', True)],
        }


class TaskResources(models.Model):
    _inherit = 'project.task'

    acme_delay_reason = fields.Selection([
        ('client', 'Client'), ('weather', 'Weather'), ('material', 'Materials'),
        ('design', 'Design'), ('crew', 'Crew'), ('other', 'Other'),
    ], string='Delay Reason', tracking=True)
    acme_delay_note = fields.Text(string='Delay Explanation')
    acme_resource_ids = fields.One2many('acme.task.resource.use', 'task_id', string='Vehicles / Equipment / Tools', copy=False)
    acme_manpower_ids = fields.One2many('acme.task.manpower', 'task_id', string='Manpower Deployment', copy=False)
    acme_late_predecessor_ids = fields.Many2many('project.task', compute='_compute_acme_late_predecessors')
    acme_late = fields.Boolean(compute='_compute_acme_late_predecessors')

    acme_delay_planned_end = fields.Date(string='Planned End Date', compute='_compute_acme_overdue_review')
    acme_delay_days = fields.Integer(string='Delay Days', compute='_compute_acme_overdue_review')
    acme_open_overdue = fields.Boolean(compute='_compute_acme_overdue_review', search='_search_acme_open_overdue')
    acme_can_edit_delay_reason = fields.Boolean(compute='_compute_acme_overdue_review')

    def _acme_delay_due_date(self):
        self.ensure_one()
        return self.acme_end_date or fields.Date.to_date(self.date_deadline)

    def _acme_is_delay_editor(self):
        self.ensure_one()
        return self.env.user == self.project_id.user_id or self.env.user in self.user_ids

    @api.depends('acme_end_date', 'date_deadline', 'state', 'project_id.user_id', 'user_ids')
    @api.depends_context('uid', 'tz')
    def _compute_acme_overdue_review(self):
        today = fields.Date.context_today(self)
        for task in self:
            due = task._acme_delay_due_date()
            overdue = bool(due and due < today and task.state not in ('1_done', '1_canceled'))
            task.acme_delay_planned_end = due
            task.acme_open_overdue = overdue
            task.acme_delay_days = (today - due).days if overdue else 0
            task.acme_can_edit_delay_reason = overdue and task._acme_is_delay_editor()

    def _search_acme_open_overdue(self, operator, value):
        if operator not in ('in', 'not in'):
            return NotImplemented
        if len(set(value)) != 1:
            return Domain.TRUE if bool(value) == (operator == 'in') else Domain.FALSE
        today = fields.Date.context_today(self).isoformat()
        domain = Domain('state', 'not in', ['1_done', '1_canceled']) & (
            Domain('acme_end_date', '<', today)
            | (Domain('acme_end_date', '=', False) & Domain('date_deadline', '<', today))
        )
        return domain if bool(next(iter(value))) == (operator == 'in') else ~domain

    def _acme_is_late(self, as_of):
        self.ensure_one()
        planned = self._acme_delay_due_date()
        if not planned or self.state == '1_canceled':
            return False
        if self.state == '1_done':
            return bool(self.date_end and fields.Date.context_today(self, self.date_end) > planned)
        return planned < as_of

    @api.depends('acme_end_date', 'date_deadline', 'date_end', 'state', 'depend_on_ids.acme_end_date',
                 'depend_on_ids.date_deadline', 'depend_on_ids.date_end', 'depend_on_ids.state')
    def _compute_acme_late_predecessors(self):
        today = fields.Date.context_today(self)
        for task in self:
            task.acme_late = task._acme_is_late(today)
            task.acme_late_predecessor_ids = task.depend_on_ids.filtered(lambda predecessor: predecessor._acme_is_late(today))

    def _acme_check_delay_editor(self):
        for task in self:
            if not task._acme_is_delay_editor():
                raise AccessError(_('Only the task assignee or assigned PM may record its delay reason.'))
            if not task._acme_is_late(fields.Date.context_today(task)):
                raise UserError(_('Record delay reasons only on late tasks.'))

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks.filtered(lambda task: task.acme_delay_reason or task.acme_delay_note)._acme_check_delay_editor()
        return tasks

    def write(self, vals):
        if {'acme_delay_reason', 'acme_delay_note'}.intersection(vals):
            self._acme_check_delay_editor()
        return super().write(vals)
