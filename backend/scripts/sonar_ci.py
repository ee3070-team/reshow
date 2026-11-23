"""Reuse ReShow's disposable SonarQube runner for the backend project."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import sonar

sonar.PROJECT = 'reshow-backend'
sonar.GATE_NAME = 'ReShow backend zero vulnerabilities'
sonar.GATE_METRICS = ('vulnerabilities',)

if __name__ == '__main__':
    {'bootstrap': sonar.bootstrap, 'report': sonar.report, 'enforce': sonar.enforce}[sys.argv[1]]()
