"""An ephemeral scan must evaluate explicit overall conditions, never an empty gate."""
import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sonar', 'scripts/sonar.py')
sonar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonar)


def passing_gate():
    return {'projectStatus': {'status': 'OK', 'ignoredConditions': False, 'conditions': [
        {'metricKey': metric, 'comparator': 'GT', 'errorThreshold': '0', 'actualValue': '0', 'status': 'OK'}
        for metric in sonar.GATE_METRICS
    ]}}


class GateTests(unittest.TestCase):
    def test_password_composition_does_not_depend_on_random_character_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = str(Path(temporary) / 'environment')
            with patch.object(sonar.secrets, 'token_urlsafe', return_value='a' * 43), patch.object(sonar, 'print'), patch.dict(os.environ, {'GITHUB_ENV': environment}), patch.object(sonar, 'api') as api:
                api.side_effect = lambda path, *args: {'status': 'UP'} if path == 'system/status' else {'token': 'masked-token'}
                sonar.bootstrap()
            password = next(call.args[1]['password'] for call in api.call_args_list if call.args[0] == 'users/change_password')
            self.assertGreaterEqual(len(password), 32)
            for pattern in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[^a-zA-Z0-9]'):
                self.assertRegex(password, pattern)
            self.assertNotIn(password, Path(environment).read_text())

    def test_bootstrap_creates_and_selects_overall_zero_issue_conditions(self):
        with patch.object(sonar, 'api') as api:
            sonar.configure_gate('test-credential')
        calls = api.call_args_list
        self.assertEqual(calls[0].args[:2], ('qualitygates/create', {'name': sonar.GATE_NAME}))
        self.assertEqual([call.args[1] for call in calls[1:4]], [
            {'gateName': sonar.GATE_NAME, 'metric': metric, 'op': 'GT', 'error': '0'}
            for metric in sonar.GATE_METRICS
        ])
        self.assertEqual(calls[4].args[:2], ('qualitygates/select', {'gateName': sonar.GATE_NAME, 'projectKey': 'reshow'}))

    def test_complete_successful_overall_gate_passes(self):
        sonar.validate_gate(passing_gate())

    def test_empty_partial_changed_ignored_or_failed_conditions_block(self):
        examples = []
        for conditions in ([], passing_gate()['projectStatus']['conditions'][:2]):
            gate = passing_gate()
            gate['projectStatus']['conditions'] = conditions
            examples.append(gate)
        gate = passing_gate()
        gate['projectStatus']['conditions'][0]['errorThreshold'] = '1'
        examples.append(gate)
        gate = passing_gate()
        gate['projectStatus']['ignoredConditions'] = True
        examples.append(gate)
        gate = passing_gate()
        gate['projectStatus']['conditions'][0]['status'] = 'ERROR'
        examples.append(gate)
        gate = passing_gate()
        gate['projectStatus']['status'] = 'ERROR'
        examples.append(gate)
        for gate in examples:
            with self.subTest(gate=gate), self.assertRaises(RuntimeError):
                sonar.validate_gate(copy.deepcopy(gate))


if __name__ == '__main__':
    unittest.main()
