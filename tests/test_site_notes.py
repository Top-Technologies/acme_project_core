from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .test_project_flow import ProjectFlowCase


@tagged('post_install', '-at_install')
class TestSiteNotes(ProjectFlowCase):
    def _task(self):
        return self.env['project.task'].create({
            'name': 'Install lamps', 'project_id': self.project.id,
            'acme_start_date': '2026-11-01', 'acme_end_date': '2026-11-30',
            'acme_uom_id': self.env.ref('uom.product_uom_unit').id,
            'acme_planned_quantity': 10, 'acme_executed_quantity': 5,
        })

    def test_main_remaining_and_manual_additional_remaining_are_separate(self):
        task = self._task()
        self.assertEqual(task.acme_remaining_quantity, 5)
        task.write({'acme_additional_work_ids': [Command.create({
            'date': '2026-11-18', 'work_type': 'Extra brackets',
            'uom_id': self.env.ref('uom.product_uom_unit').id,
            'completed_quantity': 3, 'remaining_quantity': 7, 'remark': 'Materials outstanding',
        })]})
        line = task.acme_additional_work_ids
        self.assertEqual(line.remaining_quantity, 7)
        task.acme_executed_quantity = 8
        self.assertEqual(task.acme_remaining_quantity, 2)
        self.assertEqual(line.remaining_quantity, 7)
        line.completed_quantity = 4
        self.assertEqual(line.remaining_quantity, 7)

    def test_bad_quantities_and_missing_unit_blocked(self):
        task = self._task()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            task.write({'acme_executed_quantity': -1})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            task.write({'acme_uom_id': False})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['acme.task.additional.work'].create({
                'task_id': task.id, 'work_type': 'Invalid extra work',
                'uom_id': self.env.ref('uom.product_uom_unit').id, 'remaining_quantity': -1,
            })

    def test_date_filtered_additional_and_challenge_entries(self):
        task = self._task()
        self.env['acme.task.additional.work'].create([
            {'task_id': task.id, 'date': '2026-11-18', 'work_type': 'Inside period', 'uom_id': task.acme_uom_id.id, 'completed_quantity': 2, 'remaining_quantity': 1},
            {'task_id': task.id, 'date': '2026-10-31', 'work_type': 'Outside period', 'uom_id': task.acme_uom_id.id, 'completed_quantity': 3},
        ])
        self.env['acme.task.challenge'].create([
            {'task_id': task.id, 'date': '2026-11-18', 'challenge': 'Missing materials', 'measure_taken': 'Requested delivery'},
            {'task_id': task.id, 'date': '2026-10-31', 'challenge': 'Old issue'},
        ])
        report = self.env['acme.project.site.report'].create({'project_id': self.project.id, 'date_from': '2026-11-01', 'date_to': '2026-11-30'})
        data = report._report_data()
        self.assertEqual(data['additional'].mapped('work_type'), ['Inside period'])
        self.assertEqual(data['challenges'].mapped('challenge'), ['Missing materials'])
        self.assertIn(task, data['tasks'])

    def test_subtask_support_and_no_copy_of_site_entries(self):
        parent = self._task()
        child = self.env['project.task'].create({'name': 'Room A', 'parent_id': parent.id, 'project_id': self.project.id})
        child.acme_challenge_ids = [Command.create({'date': '2026-11-18', 'challenge': 'Access denied', 'measure_taken': 'Contacted client'})]
        self.assertEqual(child.acme_challenge_ids.project_id, self.project)
        duplicate = child.copy({'name': 'Room B'})
        self.assertFalse(duplicate.acme_challenge_ids)

    def test_invalid_report_period(self):
        report = self.env['acme.project.site.report'].create({'project_id': self.project.id, 'date_from': '2026-11-30', 'date_to': '2026-11-01'})
        with self.assertRaises(ValidationError):
            report.action_print()
