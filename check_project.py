"""Run the recorder fixture self-check and this project's full unittest suite."""
import io
import sys
import unittest
from contextlib import redirect_stdout


def main():
    # 1. The documented restart recordings must keep their exact source bytes,
    #    order, and reused slot; a drift here invalidates the whole business case.
    import restart_fixture

    try:
        with redirect_stdout(io.StringIO()):
            restart_fixture.main()
    except AssertionError as error:
        print(f'FIXTURE FAIL {error}', file=sys.stderr)
        return 1
    print('FIXTURE OK restart recordings, reused slot, and late reply intact')

    # 2. Original README regressions plus the restart business regressions.
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
