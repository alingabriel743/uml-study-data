"""Real tool checks; missing optional toolchains are explicitly skipped."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from experiments.reverse_engineering.run import java_executable, run_project
from experiments.reverse_engineering.validate_extractor import validate_project


def arguments(tmp_path):
    corpus = tmp_path/'corpus.json'
    corpus.write_text('{"projects": []}')
    return argparse.Namespace(data_root=tmp_path, corpus=corpus, timeout=60,
                              java=None, ph_puml=str(tmp_path/'absent'))


def test_javac_without_build_and_repeatability(tmp_path):
    try:
        java = java_executable()
    except FileNotFoundError:
        pytest.skip('JDK unavailable')
    if Path(java) == Path('/usr/bin/java') and not Path('/Library/Java/JavaVirtualMachines').exists():
        pytest.skip('macOS Java stub only')
    snapshot = tmp_path/'fixture/snapshot'
    snapshot.mkdir(parents=True)
    (snapshot/'Main.java').write_text('''package p;
import missing.Library;
interface Contract { void run(); }
class Parent { int a, b; }
class Main extends Parent implements Contract {
  private Library unresolved;
  private Parent parent;
  Main() {}
  public void run() {}
  public void run(int n) {}
  static class Nested { int n; }
}
''')
    project = {'id': 'fixture', 'language': 'java'}
    first = run_project(project, arguments(tmp_path))
    assert first['status'] == 'ok', first['failures']
    assert first['classes'] == 4
    assert first['inheritance'] == 2
    assert first['relations'] == 3
    assert first['methods'] == 3  # Contract.run, Main constructor, Main.run
    assert run_project(project, arguments(tmp_path))['output_sha256'] == first['output_sha256']
    (tmp_path/'fixture/ir_postprocessed.json').write_text('{"classes": []}')
    assert validate_project(project, tmp_path)['status'] == 'ok'
    (snapshot/'Main.java').write_text('class Changed {}')
    assert validate_project(project, tmp_path)['status'] == 'unavailable'


def test_javac_syntax_errors_fail_closed(tmp_path):
    if not Path('/opt/homebrew/opt/openjdk/bin/java').exists() and not shutil.which('javac'):
        pytest.skip('JDK unavailable')
    snapshot = tmp_path/'bad/snapshot'
    snapshot.mkdir(parents=True)
    (snapshot/'Bad.java').write_text('public class Bad { this is not java; }')
    manifest = run_project({'id': 'bad', 'language': 'java'}, arguments(tmp_path))
    assert manifest['status'] == 'error'
    assert 'syntax errors' in manifest['failures'][0]
    assert not (snapshot.parent/'reference/class').exists()


@pytest.mark.skipif(importlib.util.find_spec('pylint') is None, reason='optional pyreverse not installed')
def test_real_pyreverse_nonpackage_files_members_and_relations(tmp_path):
    snapshot = tmp_path/'python/snapshot'
    snapshot.mkdir(parents=True)
    (snapshot/'sample.py').write_text('''class Base:
    value: int = 1
    def run(self): pass
class Child(Base):
    def __init__(self): self.name = 'hello'
    def _hidden(self): pass
''')
    manifest = run_project({'id': 'python', 'language': 'python'}, arguments(tmp_path))
    assert manifest['status'] == 'ok', manifest['failures']
    assert manifest['classes'] == 2
    assert manifest['inheritance'] == 1
    assert manifest['methods'] == 3
    facts = json.loads((snapshot.parent/'reference/reference_facts.json').read_text())
    assert next(c for c in facts['classes'] if c['name'].endswith('Child'))['attributes'] == ['name']


def test_failed_rerun_archives_previous_diagram(tmp_path):
    out = tmp_path/'fixture/reference/class'
    out.mkdir(parents=True)
    (out/'stale.puml').write_text('@startuml\nclass Stale\n@enduml\n')
    result = run_project({'id': 'fixture', 'language': 'python'}, arguments(tmp_path))
    assert result['status'] == 'error'
    assert not out.exists()
    assert list((out.parent/'_previous_runs').rglob('stale.puml'))


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1]/'experiments/reverse_engineering/node_modules/tplant/package.json').exists(),
                    reason='optional tplant not installed; JS compatibility unverified')
@pytest.mark.parametrize('extension', ['js', 'ts'])
def test_real_tplant_class_inheritance_and_members(tmp_path, extension):
    snapshot = tmp_path/'script/snapshot'
    snapshot.mkdir(parents=True)
    (snapshot/f'classes.{extension}').write_text('''export class Parent {
  value = 1;
  run() { return this.value; }
}
export class Child extends Parent {
  name = "child";
  send() { return this.name; }
}
''')
    manifest = run_project({'id': 'script', 'language': 'javascript'}, arguments(tmp_path))
    assert manifest['status'] == 'ok', manifest['failures']
    assert manifest['classes'] == 2
    assert manifest['inheritance'] == 1
    facts = json.loads((snapshot.parent/'reference/reference_facts.json').read_text())
    child = next(c for c in facts['classes'] if c['name'].endswith('Child'))
    assert 'name' in child['attributes'] and 'send' in child['methods']
