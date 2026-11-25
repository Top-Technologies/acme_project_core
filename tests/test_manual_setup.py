from odoo.tests.common import new_test_user, tagged
from odoo.exceptions import AccessError, UserError
from .test_project_flow import ProjectFlowCase


@tagged('post_install', '-at_install')
class TestManualInitialization(ProjectFlowCase):
    def test_companyless_project_requires_explicit_setup_before_boq(self):
        project = self.env['project.project'].create({'name': 'Existing Shared Project', 'company_id': False})
        self.assertFalse(project.company_id)
        with self.assertRaisesRegex(UserError, 'Set the Project company under Contract and Payments'):
            self.env['acme.project.boq'].create({'project_id': project.id, 'name': 'Manual work'})
        project.company_id = self.env.company
        line = self.env['acme.project.boq'].create({'project_id': project.id, 'name': 'Manual work'})
        self.assertEqual(line.company_id, self.env.company)

    def test_new_acme_setup_defaults_company_but_ordinary_project_does_not(self):
        ordinary = self.env['project.project'].create({'name': 'Ordinary shared project', 'company_id': False})
        acme = self.env['project.project'].create({'name': 'New ACME setup', 'acme_contract_amount': 100})
        self.assertFalse(ordinary.company_id)
        self.assertEqual(acme.company_id, self.env.company)

    def test_cross_company_boq_creation_is_denied(self):
        other_company = self.env['res.company'].create({'name': 'Other ACME Company'})
        project = self.env['project.project'].with_context(
            allowed_company_ids=[self.env.company.id, other_company.id]
        ).create({'name': 'Other Company Project', 'company_id': other_company.id})
        restricted_manager = new_test_user(
            self.env, login='acme_restricted_manager', groups='project.group_project_manager',
            company_id=self.env.company.id, company_ids=[(6, 0, [self.env.company.id])],
        )
        with self.assertRaises(AccessError):
            self.env['acme.project.boq'].with_user(restricted_manager).with_context(
                allowed_company_ids=[self.env.company.id]
            ).create({'project_id': project.id, 'name': 'Forbidden work'})

    def test_finalize_lists_every_missing_value(self):
        project = self.env['project.project'].create({
            'name': 'Incomplete Setup', 'company_id': False, 'partner_id': False,
            'acme_contract_currency_id': False,
        })
        with self.assertRaisesRegex(
            UserError, 'Missing: customer, company, contract currency, BOQ lines'
        ):
            project.action_acme_finalize_boq()

    def test_manual_boq_without_tender(self):
        project = self.env['project.project'].create({'name': 'Manual Training Project', 'company_id': self.env.company.id,
            'partner_id': self.customer.id, 'acme_contract_amount': 100000, 'acme_contract_total': 115000})
        self.env['acme.project.boq'].create({'project_id': project.id, 'name': 'Manual work', 'unit': 'Pieces', 'quantity': 10, 'unit_rate': 6000, 'selling_rate': 10000})
        project.action_acme_finalize_boq()
        self.assertTrue(project.acme_boq_initialized)
        with self.assertRaises(UserError):
            self.env['acme.project.boq'].create({'project_id': project.id, 'name': 'Unapproved extra item'})

    def test_initialization_flag_cannot_be_forged(self):
        with self.assertRaises(UserError):
            self.project.write({'acme_boq_initialized': False})
