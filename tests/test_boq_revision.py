from odoo import Command
from odoo.tests.common import new_test_user, tagged
from odoo.exceptions import UserError, ValidationError

from .test_project_flow import ProjectFlowCase


@tagged('post_install', '-at_install')
class TestBoqRevision(ProjectFlowCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.approver = new_test_user(cls.env, login='acme_boq_reviewer', groups='project.group_project_user')
        cls.project.acme_boq_approver_id = cls.approver

    def _propose(self, changes):
        self.project.action_acme_edit_boq()
        self.project.acme_boq_reason = 'Approved product substitution test'
        self.boq.write(changes)

    def test_inline_proposal_only_applies_after_approval(self):
        self._propose({'proposed_name': 'Install 45W lamps', 'proposed_product_name': '45W lamp', 'proposed_quantity': 60})
        self.assertEqual(self.boq.quantity, 50)
        self.assertEqual(self.boq.name, 'Install lamps')
        self.project.action_acme_submit_boq()
        history = self.project.acme_boq_pending_id
        with self.assertRaises(UserError):
            self.boq.write({'proposed_quantity': 70})
        with self.assertRaises(UserError):
            self.project.action_acme_approve_boq()
        self.project.with_user(self.approver).action_acme_approve_boq()
        self.assertEqual(self.boq.quantity, 60)
        self.assertEqual(self.boq.name, 'Install 45W lamps')
        self.assertEqual(self.project.acme_boq_revision, 1)
        self.assertEqual(history.state, 'approved')
        self.assertEqual(history.changes[0]['before']['quantity'], 50)
        self.assertEqual(history.changes[0]['after']['quantity'], 60)
        self.assertEqual(self.order.order_line.product_uom_qty, 50)
        with self.assertRaises(UserError):
            history.write({'reason': 'tampered'})

    def test_certified_snapshots_survive_boq_revision(self):
        first = self._certificate(30)
        first.action_certify()
        self._propose({'proposed_name': 'Replacement specification', 'proposed_quantity': 60, 'proposed_unit_rate': 7000})
        self.project.action_acme_submit_boq()
        self.project.with_user(self.approver).action_acme_approve_boq()
        self.assertEqual(first.line_ids.name, 'Install lamps')
        self.assertEqual(first.line_ids.contract_quantity, 50)
        self.assertEqual(first.line_ids.unit_rate, 10000)
        self.assertEqual(first.work_amount, 300000)
        later = self._certificate(10)
        self.assertEqual(later.line_ids.name, 'Replacement specification')
        self.assertEqual(later.line_ids.contract_quantity, 60)

    def test_stale_draft_requires_explicit_refresh(self):
        draft = self._certificate(10)
        self._propose({'proposed_name': '45W substitution'})
        self.project.action_acme_submit_boq()
        self.project.with_user(self.approver).action_acme_approve_boq()
        with self.assertRaises(UserError), self.cr.savepoint():
            draft.action_certify()
        draft.action_refresh_boq()
        self.assertEqual(draft.line_ids.name, '45W substitution')
        draft.action_certify()
        self.assertEqual(draft.state, 'certified')

    def test_rejection_keeps_effective_boq(self):
        self._propose({'proposed_quantity': 60})
        self.project.action_acme_submit_boq()
        history = self.project.acme_boq_pending_id
        with self.assertRaises(UserError):
            self.project.with_user(self.approver).action_acme_reject_boq()
        self.project.with_user(self.approver).write({'acme_boq_decision_note': 'Specification not accepted'})
        self.project.with_user(self.approver).action_acme_reject_boq()
        self.assertEqual(history.state, 'rejected')
        self.assertEqual(self.boq.quantity, 50)
        self.assertEqual(self.project.acme_boq_state, 'approved')
        self.assertEqual(self.project.acme_boq_revision, 0)

    def test_revision_cannot_reduce_already_certified_quantity(self):
        certificate = self._certificate(30)
        certificate.action_certify()
        self._propose({'proposed_quantity': 20})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.project.action_acme_submit_boq()
        self.assertEqual(self.boq.quantity, 50)

    def test_forged_context_and_direct_state_writes_blocked(self):
        with self.assertRaises(UserError):
            self.project.write({'acme_boq_state': 'pending'})
        with self.assertRaises(UserError):
            self.boq.with_context(acme_boq_token='approved').write({'quantity': 100})
        with self.assertRaises(UserError):
            self.env['acme.project.boq.revision'].create({'project_id': self.project.id, 'number': 1})

    def test_self_approval_configuration_does_not_allow_submission(self):
        self.project.acme_boq_approver_id = self.env.user
        self._propose({'proposed_name': '45W lamps'})
        with self.assertRaises(UserError):
            self.project.action_acme_submit_boq()

    def test_task_and_subtask_dates(self):
        task = self.env['project.task'].create({
            'name': 'Installation WBS', 'project_id': self.project.id,
            'acme_start_date': '2026-11-18', 'acme_end_date': '2026-11-30',
            'child_ids': [Command.create({'name': 'Room A', 'project_id': self.project.id, 'acme_start_date': '2026-11-19', 'acme_end_date': '2026-11-20'})],
        })
        self.assertEqual(str(task.child_ids.acme_start_date), '2026-11-19')
        with self.assertRaises(ValidationError), self.cr.savepoint():
            task.child_ids.write({'acme_end_date': '2026-11-01'})
        self.assertEqual(str(task.acme_end_date), '2026-11-30')
