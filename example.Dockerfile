FROM python:3.11-alpine
RUN echo 'import unittest\n\nclass TestFailure(unittest.TestCase):\n    def test_fail(self):\n        self.assertEqual(1, 0, "Deliberate failure")\n\nif __name__ == "__main__":\n    unittest.main()' > /test_fail.py
RUN python /test_fail.py