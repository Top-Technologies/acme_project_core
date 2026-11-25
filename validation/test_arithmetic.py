import importlib.util
from pathlib import Path
import unittest
from decimal import Decimal, ROUND_HALF_UP

spec = importlib.util.spec_from_file_location('calculations', Path(__file__).resolve().parents[1] / 'calculations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def round_currency(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


class TestCertificateArithmetic(unittest.TestCase):
    def test_meeting_example(self):
        self.assertEqual(module.certificate_deductions(300000, 10, 10, 1000000, round_currency), (30000, 30000, 240000))

    def test_last_recovery_is_capped(self):
        self.assertEqual(module.certificate_deductions(200000, 10, 10, 10000, round_currency), (20000, 10000, 170000))

    def test_no_advance_does_not_create_recovery(self):
        self.assertEqual(module.certificate_deductions(100000, 10, 10, 0, round_currency), (10000, 0, 90000))

    def test_zero_rate_does_not_assume_ten_percent(self):
        self.assertEqual(module.certificate_deductions(100000, 0, 0, 50000, round_currency), (0, 0, 100000))

    def test_currency_rounding(self):
        self.assertEqual(module.certificate_deductions(100.05, 10, 10, 100, round_currency), (10.01, 10.01, 80.03))


if __name__ == '__main__':
    unittest.main()
