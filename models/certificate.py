from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError
from ..calculations import certificate_deductions

_CERTIFY_TOKEN = object()


class Certificate(models.Model):
    _name = 'acme.project.certificate'
    _description = 'Project Interim Payment Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, number desc, id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    project_id = fields.Many2one('project.project', required=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='project_id.acme_contract_currency_id')
    number = fields.Integer(readonly=True, copy=False)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection([('draft', 'Draft'), ('certified', 'Certified'), ('cancel', 'Cancelled')], default='draft', required=True, readonly=True, copy=False, tracking=True)
    line_ids = fields.One2many('acme.project.measurement', 'certificate_id', string='Takeoff Measurements', copy=True)
    retention_percent = fields.Float(required=True)
    recovery_percent = fields.Float(required=True)
    work_amount = fields.Monetary(compute='_compute_totals', store=True, string='Current Executed Work')
    retention_amount = fields.Monetary(compute='_compute_totals', store=True, string='Current Retention')
    recovery_amount = fields.Monetary(compute='_compute_totals', store=True, string='Current Advance Recovery')
    balance_before_tax = fields.Monetary(compute='_compute_totals', store=True, string='Current Balance Before Tax and Other Adjustments')
    recovery_limit = fields.Monetary(readonly=True, copy=False, string='Advance Available at Certification')
    previous_work = fields.Monetary(readonly=True, copy=False)
    previous_retention = fields.Monetary(readonly=True, copy=False)
    previous_recovery = fields.Monetary(readonly=True, copy=False)
    certified_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    certified_at = fields.Datetime(readonly=True, copy=False)
    invoice_id = fields.Many2one('account.move', string='Customer Invoice', ondelete='restrict', check_company=True, copy=False)
    signed_takeoff = fields.Binary(string='Signed Takeoff Sheet', attachment=True)
    signed_takeoff_filename = fields.Char()
    remarks = fields.Text()
    acme_rebate_amount = fields.Monetary(string='Rebate for Recovery Basis')
    acme_proposed_recovery = fields.Monetary(compute='_compute_proposed_recovery', store=True)
    acme_recovery_override = fields.Monetary()
    acme_recovery_override_reason = fields.Text(tracking=True)
    acme_approved_recovery = fields.Monetary(readonly=True, copy=False)
    acme_recovery_snapshotted = fields.Boolean(readonly=True, copy=False)

    @api.depends('line_ids.amount', 'acme_rebate_amount', 'project_id.acme_recovery_basis',
                 'project_id.acme_recovery_method', 'project_id.acme_recovery_installment',
                 'project_id.acme_recovery_percent', 'currency_id')
    def _compute_proposed_recovery(self):
        for certificate in self:
            project = certificate.project_id
            basis = sum(certificate.line_ids.mapped('amount'))
            if project.acme_recovery_basis == 'after_rebate':
                basis = max(0, basis - certificate.acme_rebate_amount)
            amount = (project.acme_recovery_installment if project.acme_recovery_method == 'fixed'
                      else basis * project.acme_recovery_percent / 100)
            certificate.acme_proposed_recovery = certificate.currency_id.round(amount) if certificate.currency_id else amount

    @api.depends('project_id.name', 'number')
    def _compute_name(self):
        for certificate in self:
            certificate.name = '%s / %s' % (certificate.project_id.name or '', 'PC %s' % certificate.number if certificate.number else _('Draft PC'))

    @api.depends('line_ids.amount', 'retention_percent', 'recovery_percent', 'recovery_limit',
                 'state', 'acme_proposed_recovery', 'acme_recovery_override', 'acme_recovery_override_reason',
                 'acme_approved_recovery', 'acme_recovery_snapshotted')
    def _compute_totals(self):
        for certificate in self:
            currency = certificate.currency_id
            rounding = currency.round if currency else lambda value: round(value, 2)
            certificate.work_amount = sum(certificate.line_ids.mapped('amount'))
            retention, recovery, _balance = certificate_deductions(certificate.work_amount, certificate.retention_percent, certificate.recovery_percent, certificate.recovery_limit, rounding)
            if certificate.acme_recovery_snapshotted:
                recovery = certificate.acme_approved_recovery
            elif certificate.state == 'draft':
                recovery = certificate._acme_effective_recovery()
            certificate.retention_amount = retention
            certificate.recovery_amount = recovery
            certificate.balance_before_tax = rounding(certificate.work_amount - retention - recovery)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if {'acme_approved_recovery', 'acme_recovery_snapshotted', 'acme_recovery_override',
                'acme_recovery_override_reason'}.intersection(vals):
                raise UserError(_('Save the draft before recording an advance recovery override.'))
            if any(vals.get(key) for key in ('number', 'certified_by_id', 'certified_at', 'previous_work', 'previous_retention', 'previous_recovery', 'recovery_limit')) or vals.get('state', 'draft') != 'draft':
                raise UserError(_('Certification audit values are system generated.'))
            project = self.env['project.project'].browse(vals.get('project_id'))
            project.check_access('read')
            vals.setdefault('retention_percent', project.acme_retention_percent)
            vals.setdefault('recovery_percent', project.acme_recovery_percent)
            vals['recovery_limit'] = project.acme_advance_balance
            previous = self.search([('project_id', '=', project.id), ('state', '=', 'certified')])
            vals['previous_work'] = sum(previous.mapped('work_amount'))
            vals['previous_retention'] = sum(previous.mapped('retention_amount'))
            vals['previous_recovery'] = sum(previous.mapped('recovery_amount'))
        return super().create(vals_list)

    @api.constrains('retention_percent', 'recovery_percent', 'project_id', 'invoice_id', 'date')
    def _check_values(self):
        for certificate in self:
            if certificate._acme_requires_boq() and (not certificate.project_id.acme_boq_initialized or not certificate.project_id.acme_boq_ids):
                raise ValidationError(_('The project needs a tender BOQ baseline.'))
            if any(not 0 <= rate <= 100 for rate in (certificate.retention_percent, certificate.recovery_percent)):
                raise ValidationError(_('Deduction percentages must be between 0 and 100.'))
            invoice = certificate.invoice_id
            project = certificate.project_id
            if invoice and (certificate.state != 'certified' or invoice.move_type != 'out_invoice' or invoice.state == 'cancel' or invoice.company_id != project.company_id or invoice.currency_id != certificate.currency_id or invoice.partner_id.commercial_partner_id != project.partner_id.commercial_partner_id or invoice.acme_project_id != project):
                raise ValidationError(_('Link a customer invoice carrying this project, customer, currency and company, after certification.'))
            if invoice and self.search_count([('invoice_id', '=', invoice.id), ('id', '!=', certificate.id)]):
                raise ValidationError(_('This invoice is already linked to another certificate.'))

    def _acme_requires_boq(self):
        return True

    def action_load_boq(self):
        self.ensure_one()
        if self.state != 'draft' or self.line_ids:
            raise UserError(_('Load the BOQ into an empty draft certificate.'))
        self.line_ids = [Command.create({'boq_id': boq.id}) for boq in self.project_id.acme_boq_ids]

    def action_refresh_boq(self):
        self.ensure_one()
        self.check_access('write')
        if self.state != 'draft':
            raise UserError(_('Only a draft certificate can refresh its BOQ details.'))
        for line in self.line_ids:
            line.with_context(acme_certify_token=_CERTIFY_TOKEN).write(line._acme_snapshot_values(line.boq_id))

    def action_certify(self):
        self.ensure_one()
        if not self.env.user.has_group('project.group_project_manager'):
            raise UserError(_('Only a Project Manager may certify work.'))
        if self.state != 'draft':
            raise UserError(_('Only a draft certificate can be certified.'))
        self.check_access('write')
        # A row version change also serializes concurrent approvals under repeatable read.
        self.env.cr.execute('UPDATE project_project SET id = id WHERE id = %s', [self.project_id.id])
        self.env.invalidate_all()
        if self.state != 'draft':
            raise UserError(_('This certificate has already been processed.'))
        previous = self.search([('project_id', '=', self.project_id.id), ('state', '=', 'certified')])
        if self.currency_id.compare_amounts(sum(previous.mapped('work_amount')) + self.work_amount, self.project_id.acme_contract_amount) > 0:
            raise UserError(_('Cumulative certified work exceeds the sales contract value. A separately agreed contract amendment is required.'))
        if previous and self.date < max(previous.mapped('date')):
            raise UserError(_('Certificate dates must follow the previously certified period.'))
        if not self.line_ids or self.work_amount <= 0:
            raise UserError(_('Enter positive current work quantities before certification.'))
        for line in self.line_ids:
            if any(line[key] != value for key, value in line._acme_snapshot_values(line.boq_id).items()):
                raise UserError(_('The approved BOQ has changed since this draft was prepared. Refresh BOQ Details, review the draft and certify again.'))
            prior_quantity = sum(previous.mapped('line_ids').filtered(lambda l: l.boq_id == line.boq_id).mapped('quantity'))
            if prior_quantity + line.quantity > line.boq_id.quantity + 1e-6:
                raise UserError(_('Cumulative work exceeds the approved BOQ quantity for %s.', line.boq_id.name))
            line.with_context(acme_certify_token=_CERTIFY_TOKEN).write({'previous_quantity': prior_quantity})
        limit = self.project_id.acme_advance_balance
        self._acme_validate_recovery(limit)
        self.with_context(acme_certify_token=_CERTIFY_TOKEN).write({
            'number': max(previous.mapped('number'), default=0) + 1,
            'previous_work': sum(previous.mapped('work_amount')),
            'previous_retention': sum(previous.mapped('retention_amount')),
            'previous_recovery': sum(previous.mapped('recovery_amount')),
            'recovery_limit': limit, 'state': 'certified',
            'acme_approved_recovery': self._acme_effective_recovery(),
            'acme_recovery_snapshotted': True,
            'certified_by_id': self.env.user.id, 'certified_at': fields.Datetime.now(),
        })

    def _acme_validate_recovery(self, limit):
        self.ensure_one()
        amount = self._acme_effective_recovery()
        if not 0 <= amount or self.currency_id.compare_amounts(amount, max(0, limit)) > 0:
            raise UserError(_('Advance recovery exceeds the available paid advance.'))
        if self.currency_id.compare_amounts(amount, self.work_amount - self.retention_amount) > 0:
            raise UserError(_('Advance recovery exceeds work after retention.'))

    def _acme_effective_recovery(self):
        self.ensure_one()
        return self.acme_recovery_override if self.acme_recovery_override_reason else self.acme_proposed_recovery

    def _acme_check_recovery_override(self, vals):
        if {'acme_recovery_override', 'acme_recovery_override_reason'}.intersection(vals):
            for certificate in self:
                if certificate.state != 'draft' or certificate.project_id.user_id != self.env.user:
                    raise UserError(_('Only the assigned PM may override recovery on a draft certificate.'))
                if not (vals.get('acme_recovery_override_reason', certificate.acme_recovery_override_reason) or '').strip():
                    raise UserError(_('Record a reason for overriding advance recovery.'))

    def action_cancel(self):
        if any(c.state != 'draft' for c in self):
            raise UserError(_('Only draft certificates can be cancelled. Certified work requires a separately approved correction process.'))
        return self.with_context(acme_certify_token=_CERTIFY_TOKEN).write({'state': 'cancel'})

    def write(self, vals):
        self._acme_check_recovery_override(vals)
        if self.env.context.get('acme_certify_token') is not _CERTIFY_TOKEN:
            audit = {'state', 'number', 'certified_by_id', 'certified_at', 'previous_work', 'previous_retention', 'previous_recovery', 'recovery_limit', 'acme_approved_recovery', 'acme_recovery_snapshotted'}
            if audit.intersection(vals):
                raise UserError(_('Use the certificate workflow buttons.'))
            for certificate in self:
                if 'invoice_id' in vals and certificate.invoice_id and certificate.invoice_id.state == 'posted' and vals['invoice_id'] != certificate.invoice_id.id:
                    raise UserError(_('A posted invoice link cannot be replaced or removed.'))
                if certificate.state != 'draft' and set(vals) - {'invoice_id', 'signed_takeoff', 'signed_takeoff_filename', 'message_main_attachment_id'}:
                    raise UserError(_('Certified and cancelled certificates cannot be edited.'))
                if vals.get('project_id') and certificate.line_ids:
                    raise UserError(_('A certificate with measurements cannot change project.'))
        result = super().write(vals)
        if {'acme_recovery_override', 'acme_recovery_override_reason'}.intersection(vals):
            for certificate in self:
                certificate.message_post(body=_('Advance recovery override: %s', certificate.acme_recovery_override_reason))
        return result

    def unlink(self):
        if any(c.state != 'draft' for c in self):
            raise UserError(_('Only draft certificates can be deleted.'))
        return super().unlink()

    def action_print(self):
        return self.env.ref('acme_project_core.report_certificate').report_action(self)


class Measurement(models.Model):
    _name = 'acme.project.measurement'
    _description = 'Certificate Takeoff Measurement'
    _check_company_auto = True

    certificate_id = fields.Many2one('acme.project.certificate', required=True, ondelete='cascade', check_company=True)
    project_id = fields.Many2one(related='certificate_id.project_id', store=True)
    company_id = fields.Many2one(related='certificate_id.company_id', store=True)
    currency_id = fields.Many2one(related='certificate_id.currency_id')
    boq_id = fields.Many2one('acme.project.boq', required=True, ondelete='restrict', check_company=True)
    name = fields.Char(readonly=True, copy=False)
    reference = fields.Char(readonly=True, copy=False)
    product_specification = fields.Char(readonly=True, copy=False)
    unit = fields.Char(readonly=True, copy=False)
    contract_quantity = fields.Float(readonly=True, copy=False)
    unit_rate = fields.Monetary(readonly=True, copy=False)
    previous_quantity = fields.Float(readonly=True, copy=False)
    quantity = fields.Float(string='Current Quantity')
    cumulative_quantity = fields.Float(compute='_compute_cumulative_quantity')
    amount = fields.Monetary(compute='_compute_amount', store=True)

    _unique_boq = models.Constraint('UNIQUE(certificate_id, boq_id)', 'A BOQ item can appear only once per certificate.')

    @api.depends('quantity', 'unit_rate', 'currency_id')
    def _compute_amount(self):
        for line in self:
            line.amount = line.currency_id.round(line.quantity * line.unit_rate) if line.currency_id else line.quantity * line.unit_rate

    @api.depends('previous_quantity', 'quantity')
    def _compute_cumulative_quantity(self):
        for line in self:
            line.cumulative_quantity = line.previous_quantity + line.quantity

    @api.constrains('quantity', 'boq_id', 'certificate_id')
    def _check_measurement(self):
        for line in self:
            if line.quantity < 0:
                raise ValidationError(_('Current quantities must be nonnegative. Actual quantities above contract can be recorded in draft; certification requires approved contract capacity.'))
            if line.boq_id.project_id != line.project_id:
                raise ValidationError(_('The BOQ item must belong to this certificate project.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if {'name', 'reference', 'product_specification', 'unit', 'contract_quantity', 'unit_rate'}.intersection(vals):
                raise UserError(_('Measurement descriptions, units and selling rates come from the approved BOQ.'))
            certificate = self.env['acme.project.certificate'].browse(vals.get('certificate_id'))
            certificate.check_access('write')
            if certificate.state != 'draft' or vals.get('previous_quantity'):
                raise UserError(_('Measurements may only be added to draft certificates.'))
            previous_lines = self.search([('project_id', '=', certificate.project_id.id), ('boq_id', '=', vals.get('boq_id')), ('certificate_id.state', '=', 'certified')])
            vals['previous_quantity'] = sum(previous_lines.mapped('quantity'))
            boq = self.env['acme.project.boq'].browse(vals.get('boq_id'))
            boq.check_access('read')
            vals.update(self._acme_snapshot_values(boq))
        return super().create(vals_list)

    @api.model
    def _acme_snapshot_values(self, boq):
        return {'name': boq.name, 'reference': boq.reference, 'product_specification': boq.product_name,
                'unit': boq.unit, 'contract_quantity': boq.quantity, 'unit_rate': boq.selling_rate}

    def write(self, vals):
        if self.env.context.get('acme_certify_token') is not _CERTIFY_TOKEN:
            if {'name', 'reference', 'product_specification', 'unit', 'contract_quantity', 'unit_rate'}.intersection(vals):
                raise UserError(_('Use Refresh BOQ Details to update a draft measurement snapshot.'))
            if 'previous_quantity' in vals:
                raise UserError(_('Previous quantities are recorded during certification.'))
            if 'certificate_id' in vals or any(l.certificate_id.state != 'draft' for l in self):
                raise UserError(_('Certified measurements cannot be edited or moved.'))
            self.mapped('certificate_id').check_access('write')
            if vals.get('boq_id'):
                vals = dict(vals)
                vals.update(self._acme_snapshot_values(self.env['acme.project.boq'].browse(vals['boq_id'])))
        return super().write(vals)

    def unlink(self):
        if any(l.certificate_id.state != 'draft' for l in self):
            raise UserError(_('Certified measurements cannot be deleted.'))
        self.mapped('certificate_id').check_access('write')
        return super().unlink()
