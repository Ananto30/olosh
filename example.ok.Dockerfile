FROM python:3.11-alpine
RUN cat > /test_ok.py <<EOF
import unittest

class TestOK(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1, 1, "Should pass")

if __name__ == "__main__":
    unittest.main()
EOF
RUN python /test_ok.py