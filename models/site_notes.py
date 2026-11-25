import math

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class TaskSiteEntry(models.AbstractModel):
    _name = 'acme.task.site.entry'
    _description = 'Task Site Entry Access Controls'

    task_id = fields.Many2one('project.task', required=True, ondelete='cascade', check_company=True, index=True)
    project_id = fields.Many2one(related='task_id.project_id', store=True)
    company_id = fields.Many2one(related='task_id.company_id', store=True)
    date = fields.Date(string='Work / Observation Date', required=True, default=fields.Date.context_today, index=True)
    sequence = fields.Integer(default=10)
    recorded_by_id = fields.Many2one('res.users', string='Recorded By', readonly=True, default=lambda self: self.env.user, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            task = self.env['project.task'].browse(vals.get('task_id'))
            task.check_access('write')
            vals['recorded_by_id'] = self.env.user.id
        return super().create(vals_list)

    def write(self, vals):
        if {'recorded_by_id', 'project_id', 'company_id'}.intersection(vals):
            raise UserError(_('The task and current user determine entry ownership and company.'))
        self.mapped('task_id').check_access('write')
        if vals.get('task_id'):
            self.env['project.task'].browse(vals['task_id']).check_access('write')
        return super().write(vals)

    def unlink(self):
        self.mapped('task_id').check_access('write')
        return super().unlink()


class AdditionalWork(models.Model):
    _name = 'acme.task.additional.work'
    _inherit = 'acme.task.site.entry'
    _description = 'Task Additional Work and Remark'
    _order = 'date, sequence, id'
    _rec_name = 'work_type'
    _check_company_auto = True

    work_type = fields.Char(string='Type of Work', required=True)
    uom_id = fields.Many2one('uom.uom', string='Measurement / Unit', required=True)
    completed_quantity = fields.Float(string='Completed')
    remaining_quantity = fields.Float(string='Remaining Work', help='Entered manually: the source additional-work table has no planned quantity column.')
    remark = fields.Text(string='Remark')

    @api.constrains('completed_quantity', 'remaining_quantity')
    def _check_quantities(self):
        for entry in self:
            if any(not math.isfinite(value) or value < 0 for value in (entry.completed_quantity, entry.remaining_quantity)):
                raise ValidationError(_('Additional-work completed and remaining quantities must be finite nonnegative numbers.'))


class TaskChallenge(models.Model):
    _name = 'acme.task.challenge'
    _inherit = 'acme.task.site.entry'
    _description = 'Task Challenge and Measure Taken'
    _order = 'date, sequence, id'
    _rec_name = 'challenge'
    _check_company_auto = True

    challenge = fields.Text(string='Challenges Encountered', required=True)
    measure_taken = fields.Text(string='Measures Taken / Solution')
