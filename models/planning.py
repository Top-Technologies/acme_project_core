from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class ProjectPreparation(models.Model):
    _inherit = 'project.project'

    acme_kickoff_event_ids = fields.One2many('calendar.event', 'acme_kickoff_project_id')

    def _acme_check_preparation_editor(self, allow_dm=False):
        self.check_access('write')
        groups = ['acme_project_core.group_acme_odm']
        if allow_dm:
            groups.append('acme_project_core.group_acme_dm')
        if not any(self.env.user.has_group(group) for group in groups) and any(
            project.user_id != self.env.user for project in self
        ):
            raise AccessError(_('Only the assigned PM or an authorized Operations manager may change project preparation.'))

    def action_acme_open_kickoff(self):
        self.ensure_one()
        self._acme_check_preparation_editor()
        meeting = self.acme_kickoff_event_ids
        return {
            'type': 'ir.actions.act_window', 'res_model': 'calendar.event', 'view_mode': 'form',
            'res_id': meeting.id,
            'context': {'default_acme_kickoff_project_id': self.id,
                        'default_res_model_id': self.env['ir.model']._get_id('project.project'),
                        'default_res_id': self.id,
                        'default_name': _('Project Kickoff: %s', self.name)},
        }

    def _acme_preparation_snapshot(self):
        self.ensure_one()
        scopes = self.task_ids.filtered('acme_execution_approach')
        meeting = self.acme_kickoff_event_ids
        if not scopes or not meeting:
            raise UserError(_('Set the execution approach on the scope tasks and schedule the kickoff first.'))
        return {
            'meeting': [meeting.id, str(meeting.start), str(meeting.stop), sorted(meeting.partner_ids.ids)],
            'scopes': [task._acme_execution_snapshot() for task in scopes.sorted('id')],
        }


class ProjectExecutionScope(models.Model):
    _inherit = 'project.task'

    acme_execution_approach = fields.Selection(
        [('own_crew', 'Own Crew'), ('subcontractor', 'Subcontractor')], tracking=True,
    )
    acme_execution_skill_ids = fields.Many2many('hr.skill', string='Required Execution Skills')
    acme_execution_role_id = fields.Many2one('planning.role', string='Execution Planning Role')

    def _acme_execution_snapshot(self):
        self.ensure_one()
        if not self.user_ids or any(not user.active or user.share for user in self.user_ids):
            raise UserError(_('Assign active internal team members to %s.', self.display_name))
        if not self.acme_execution_skill_ids:
            raise UserError(_('Configure required skills for execution scope %s.', self.display_name))
        if self.acme_execution_approach == 'own_crew' and not self.acme_execution_role_id:
            raise UserError(_('Configure a Planning role for %s.', self.display_name))
        employees = self.env['hr.employee'].search([
            ('company_id', '=', self.project_id.company_id.id), ('user_id', 'in', self.user_ids.ids),
        ])
        today = fields.Date.context_today(self)
        skills = self.env['hr.employee.skill'].search([
            ('employee_id', 'in', employees.ids), ('skill_id', 'in', self.acme_execution_skill_ids.ids),
            ('valid_from', '<=', today), '|', ('valid_to', '=', False), ('valid_to', '>=', today),
        ])
        current = skills.grouped(lambda skill: skill.employee_id.id)
        for user in self.user_ids:
            candidates = employees.filtered(lambda employee: employee.user_id == user)
            if self.acme_execution_role_id:
                candidates = candidates.filtered(lambda employee: self.acme_execution_role_id in employee.resource_id.role_ids)
            if not any(self.acme_execution_skill_ids <= current.get(employee.id, skills.browse()).skill_id for employee in candidates):
                raise UserError(_('%(user)s lacks the configured current skills or Planning role for %(task)s.',
                                  user=user.display_name, task=self.display_name))
        return [self.id, self.name, self.acme_execution_approach, sorted(self.user_ids.ids),
                sorted(self.acme_execution_skill_ids.ids), self.acme_execution_role_id.id,
                str(self.acme_start_date or ''), str(self.acme_end_date or '')]

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks.filtered('acme_execution_approach').project_id._acme_check_preparation_editor(allow_dm=True)
        for task, vals in zip(tasks, vals_list):
            if task.acme_execution_approach and vals.get('user_ids'):
                task.project_id._acme_check_preparation_editor()
        return tasks

    def write(self, vals):
        if {'acme_execution_approach', 'acme_execution_skill_ids', 'acme_execution_role_id'}.intersection(vals):
            projects = self.project_id | self.env['project.project'].browse(vals.get('project_id') or [])
            projects._acme_check_preparation_editor(allow_dm=True)
        if {'user_ids', 'project_id'}.intersection(vals):
            scopes = self if vals.get('acme_execution_approach') else self.filtered('acme_execution_approach')
            projects = scopes.project_id | self.env['project.project'].browse(vals.get('project_id') or [])
            if scopes:
                projects._acme_check_preparation_editor()
        return super().write(vals)

    def action_acme_plan_crew(self):
        self.ensure_one()
        self.check_access('read')
        if self.acme_execution_approach != 'own_crew':
            raise UserError(_('Planning shifts are available for Own Crew scopes.'))
        return {
            'type': 'ir.actions.act_window', 'name': _('Scope Planning Shifts'),
            'res_model': 'planning.slot', 'view_mode': 'gantt,list,form',
            'domain': [('acme_scope_task_id', '=', self.id)],
            'context': {'default_acme_scope_task_id': self.id, 'default_project_id': self.project_id.id,
                        'default_role_id': self.acme_execution_role_id.id},
        }


class PlanningSlot(models.Model):
    _inherit = 'planning.slot'

    acme_scope_task_id = fields.Many2one('project.task', string='Execution Scope', ondelete='restrict', copy=False)

    @api.constrains('acme_scope_task_id', 'project_id', 'company_id', 'role_id')
    def _check_acme_scope(self):
        for slot in self.filtered('acme_scope_task_id'):
            scope = slot.acme_scope_task_id
            if (scope.acme_execution_approach != 'own_crew' or scope.project_id != slot.project_id
                    or scope.project_id.company_id != slot.company_id or not slot.role_id):
                raise ValidationError(_('Use an Own Crew scope from this project and company, and specify a Planning role.'))


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    acme_kickoff_project_id = fields.Many2one('project.project', ondelete='restrict', copy=False)
    _acme_unique_kickoff = models.Constraint(
        'UNIQUE(acme_kickoff_project_id)', 'This project already has a kickoff meeting.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        meetings = super().create(vals_list)
        meetings.acme_kickoff_project_id._acme_check_preparation_editor()
        return meetings

    def write(self, vals):
        if 'acme_kickoff_project_id' in vals:
            projects = self.acme_kickoff_project_id | self.env['project.project'].browse(vals['acme_kickoff_project_id'] or [])
            projects._acme_check_preparation_editor()
        return super().write(vals)
