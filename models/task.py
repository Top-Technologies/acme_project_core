import math

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ProjectTask(models.Model):
    _inherit = 'project.task'

    acme_start_date = fields.Date(string='Start Date', tracking=True, copy=False, help='Internal work-plan date for this task or subtask.')
    acme_end_date = fields.Date(string='End Date', tracking=True, copy=False, help='Internal work-plan date; separate from the native deadline and actual completion timestamps.')
    acme_uom_id = fields.Many2one('uom.uom', string='Unit of Measure', tracking=True)
    acme_planned_quantity = fields.Float(string='Planned Quantity', tracking=True)
    acme_executed_quantity = fields.Float(string='Executed Quantity', tracking=True, copy=False, help='Current cumulative work completed. Marking a task Done does not automatically fill this value.')
    acme_remaining_quantity = fields.Float(string='Remaining Quantity', compute='_compute_acme_remaining', store=True)
    acme_additional_work_ids = fields.One2many('acme.task.additional.work', 'task_id', string='Additional Remarks', copy=False)
    acme_challenge_ids = fields.One2many('acme.task.challenge', 'task_id', string='Challenges and Solutions', copy=False)

    @api.depends('acme_planned_quantity', 'acme_executed_quantity')
    def _compute_acme_remaining(self):
        for task in self:
            task.acme_remaining_quantity = task.acme_planned_quantity - task.acme_executed_quantity

    @api.constrains('acme_planned_quantity', 'acme_executed_quantity', 'acme_uom_id')
    def _check_acme_quantities(self):
        for task in self:
            if any(not math.isfinite(value) or value < 0 for value in (task.acme_planned_quantity, task.acme_executed_quantity)):
                raise ValidationError(_('Planned and executed quantities must be finite nonnegative numbers.'))
            if (task.acme_planned_quantity or task.acme_executed_quantity) and not task.acme_uom_id:
                raise ValidationError(_('Select a unit of measure when recording task quantities.'))

    @api.constrains('acme_start_date', 'acme_end_date')
    def _check_acme_task_dates(self):
        for task in self:
            if task.acme_start_date and task.acme_end_date and task.acme_end_date < task.acme_start_date:
                raise ValidationError(_('Task End Date cannot be before Start Date.'))
