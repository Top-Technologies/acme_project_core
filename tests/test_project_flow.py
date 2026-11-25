from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError, ValidationError
from ..models.boq import _IMPORT_TOKEN

class ProjectFlowCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Project Customer'})
        cls.product = cls.env['product.product'].create({'name': 'Installation work', 'type': 'service'})
        cls.order = cls.env['sale.order'].create({'partner_id': cls.customer.id, 'order_line': [Command.create({'product_id': cls.product.id, 'product_uom_qty': 50, 'price_unit': 10000, 'tax_ids': [Command.clear()]})]})
        cls.project = cls.env['project.project'].create({'name': 'Test Contract', 'partner_id': cls.customer.id, 'company_id': cls.order.company_id.id, 'acme_order_id': cls.order.id, 'acme_advance_received': 40000, 'acme_retention_percent': 10, 'acme_recovery_percent': 10})
        cls.boq = cls.env['acme.project.boq'].with_context(acme_import_token=_IMPORT_TOKEN).create({'project_id': cls.project.id, 'reference': '1.1', 'name': 'Install lamps', 'quantity': 50, 'unit_rate': 6000, 'selling_rate': 10000})

        cls.project.action_acme_finalize_boq()

    def _certificate(self, quantity):
        return self.env['acme.project.certificate'].create({'project_id': self.project.id, 'line_ids': [Command.create({'boq_id': self.boq.id, 'quantity': quantity})]})

@tagged('post_install', '-at_install')
class TestProjectFlow(ProjectFlowCase):

    def test_certification_previous_and_recovery_cap(self):
        first = self._certificate(30)
        first.action_certify()
        self.assertEqual(first.number, 1)
        self.assertEqual(first.work_amount, 300000)
        self.assertEqual(first.retention_amount, 30000)
        self.assertEqual(first.recovery_amount, 30000)
        self.assertEqual(first.balance_before_tax, 240000)
        second = self._certificate(20)
        second.action_certify()
        self.assertEqual(second.number, 2)
        self.assertEqual(second.line_ids.previous_quantity, 30)
        self.assertEqual(second.line_ids.cumulative_quantity, 50)
        self.assertEqual(second.previous_work, 300000)
        self.assertEqual(second.recovery_amount, 10000)
        self.assertEqual(self.project.acme_advance_balance, 0)
        self.assertEqual(first.recovery_amount, 30000)

    def test_over_certification_blocked(self):
        first = self._certificate(30)
        first.action_certify()
        second = self._certificate(21)
        with self.assertRaises(UserError), self.cr.savepoint():
            second.action_certify()

    def test_locked_certified_data(self):
        certificate = self._certificate(10)
        certificate.action_certify()
        with self.assertRaises(UserError):
            certificate.write({'retention_percent': 0})
        with self.assertRaises(UserError):
            certificate.line_ids.write({'quantity': 20})
        with self.assertRaises(UserError):
            certificate.write({'state': 'draft'})
        with self.assertRaises(UserError):
            certificate.action_certify()

    def test_baseline_and_cross_project_protection(self):
        with self.assertRaises(UserError):
            self.boq.write({'selling_rate': 1})
        with self.assertRaises(UserError):
            self.env['acme.project.boq'].create({'project_id': self.project.id, 'name': 'Forged'})
        other = self.project.copy({'name': 'Other Project', 'acme_order_id': self.order.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['acme.project.certificate'].create({'project_id': other.id, 'line_ids': [Command.create({'boq_id': self.boq.id, 'quantity': 1})]})

    def test_guarantee_date_validation(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['acme.project.guarantee'].create({'name': 'Invalid guarantee', 'project_id': self.project.id, 'amount': 1000, 'issue_date': '2026-11-18', 'expiry_date': '2026-11-01'})
