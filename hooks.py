from odoo import _
from odoo.exceptions import UserError


def pre_init_hook(env):
    legacy = env['ir.module.module'].search([('name', '=', 'acme_project_extension'), ('state', 'in', ['installed', 'to upgrade', 'to install', 'to remove'])], limit=1)
    if legacy:
        raise UserError(_('The previous single-addon build is active or scheduled. This modular bundle is for a fresh test database; a deliberate migration is required for an installed legacy database. No automatic uninstall or data deletion will be performed.'))
