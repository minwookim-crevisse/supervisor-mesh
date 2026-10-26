"""The marker self-test as a unit test: no planted string may reach the evidence dir."""
import unittest

import redaction_selftest


class Redaction(unittest.TestCase):
    def test_no_planted_string_reaches_evidence(self):
        self.assertEqual(redaction_selftest.run(), 0)


if __name__ == "__main__":
    unittest.main()
