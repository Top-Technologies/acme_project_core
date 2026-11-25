def certificate_deductions(work, retention_percent, recovery_percent, advance_remaining, round_amount):
    """Amounts exclude tax. Recovery cannot exceed the unrecovered advance."""
    retention = round_amount(work * retention_percent / 100)
    recovery = round_amount(min(max(advance_remaining, 0), work * recovery_percent / 100))
    return retention, recovery, round_amount(work - retention - recovery)
