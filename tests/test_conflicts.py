"""End to end tests checking conflicts detection"""

from click.testing import CliRunner
import pytest
from pipcompilemulti.cli_v1 import cli


@pytest.mark.parametrize('conflict', ['merge', 'ref'])
def test_conflict_detected(conflict):
    """Following types of version conflicts are detected:

    1. Two files have different version and referenced from the third file.
    2. File adds new constraint on package from referenced file.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ['--directory', 'conflicting-in-' + conflict])
    assert result.exit_code == 1
    assert 'Please add constraints' in str(result.exception)
