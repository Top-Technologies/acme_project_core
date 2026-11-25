from odoo import Command
from odoo.tests.common import new_test_user, tagged
from odoo.exceptions import UserError
from odoo.addons.acme_project_core.tests.test_project_flow import ProjectFlowCase

@tagged('post_install', '-at_install')
class TestCoreExecution(ProjectFlowCase):

    def test_actual_above_contract_is_recorded_not_automatically_certified(self):
        self.boq.executed_quantity = 60
        self.assertEqual(self.boq.quantity, 50)
        self.assertEqual(self.boq.executed_amount, 600000)
        certificate = self.env['acme.project.certificate'].create({'project_id': self.project.id})
        certificate.action_load_executed_boq()
        self.assertEqual(certificate.line_ids.quantity, 60)
        with self.assertRaises(UserError), self.cr.savepoint():
            certificate.action_certify()

    def test_site_resource_and_manpower_period(self):
        task = self.env['project.task'].create({'name': 'Site work', 'project_id': self.project.id, 'user_ids': [Command.set(self.env.user.ids)]})
        resource = self.env['acme.site.resource'].create({'name': 'Truck 1', 'kind': 'vehicle'})
        self.env['acme.task.resource.use'].create({'task_id': task.id, 'date': '2026-11-18', 'resource_id': resource.id, 'quantity': 1, 'days_used': 2})
        self.env['acme.task.manpower'].create({'task_id': task.id, 'date': '2026-11-18', 'user_id': self.env.user.id, 'job_position': 'Technician', 'days_used': 2})
        report = self.env['acme.project.site.report'].create({'project_id': self.project.id, 'date_from': '2026-11-01', 'date_to': '2026-11-30'})
        data = report._report_data()
        self.assertEqual(data['resources'].resource_id, resource)
        self.assertEqual(data['manpower'].days_used, 2)
