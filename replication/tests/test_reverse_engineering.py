"""Identity, metric denominators and native-tool PlantUML regression tests."""

import json

import pytest

from experiments.reverse_engineering.facts import ClassFacts, Facts, member_name, read_puml, render_puml
from experiments.reverse_engineering.validate_extractor import compare, ir_index, reference_index, score, validate_project


def test_pyreverse_native_members_and_arrow_direction():
    text = '''@startuml classes_fixture
set namespaceSeparator none
class "sample.Base" as sample.Base {
  value : int
  {abstract}method()
}
class "sample.Child" as sample.Child {
  name : str
  __init__()
  {abstract}_hidden()
}
sample.Child --|> sample.Base
@enduml
'''
    facts = read_puml(text)
    assert facts.inheritance == [('sample.Child', 'sample.Base')]
    assert [(c.name, c.attributes, c.methods) for c in facts.classes] == [
        ('sample.Base', ['value'], ['method']),
        ('sample.Child', ['name'], ['__init__', '_hidden'])]


@pytest.mark.parametrize(('line', 'expected'), [
    ('+name: string', ('attributes', 'name')),
    ('{static} -privateValue : int', ('attributes', 'privateValue')),
    ('+private string $subject', ('attributes', 'subject')),
    ('+count : int = count(items)', ('attributes', 'count')),
    ('+string send($message): bool', ('methods', 'send')),
    ('{abstract}#send(value: Item): Promise<void>', ('methods', 'send')),
    ('__init__(value)', ('methods', '__init__')),
    ('--', None),
])
def test_member_syntax(line, expected):
    assert member_name(line) == expected


def test_render_roundtrip_realization_and_overloads():
    facts = Facts([ClassFacts('pkg.Contract', 'interface', [], ['run']),
                   ClassFacts('pkg.Child', methods=['Child', 'run', 'run'], attributes=['id'])],
                  [('pkg.Child', 'pkg.Contract')])
    text = render_puml(facts)
    assert '..|>' in text
    restored = read_puml(text)
    assert set(restored.inheritance) == set(facts.inheritance)
    assert next(c for c in restored.classes if c.name == 'pkg.Child').methods == ['Child', 'run']


def test_precision_recall_direction_and_wrong_parent():
    ir = ir_index({'classes': [
        {'name': 'A', 'attributes': [{'name': '$id'}], 'methods': [{'name': 'ok'}, {'name': 'extra'}], 'extends': ['Wrong']},
        {'name': 'Base'}, {'name': 'Wrong'}, {'name': 'spurious'}]})
    reference = reference_index(Facts([ClassFacts('ns.A', attributes=['id', 'missing'], methods=['ok']),
                                        ClassFacts('Base'), ClassFacts('Wrong')], [('ns.A', 'Base')]))
    result = compare(ir, reference)
    assert result['classes']['precision'] == 3/4
    assert result['classes']['recall'] == 1
    assert result['inheritance']['precision'] == 0
    assert result['inheritance']['recall'] == 0
    assert result['attributes']['precision'] == 1
    assert result['attributes']['recall'] == 1/2
    assert result['methods']['precision'] == 1/2
    assert result['methods']['recall'] == 1


def test_php_procedural_pseudo_classes_are_not_class_entities():
    index = ir_index({'classes': [{'kind': 'file', 'name': 'procedural_code'},
                                   {'kind': 'trait', 'name': 'T'}, {'kind': 'interface', 'name': 'I'}]})
    assert index.classes == {'t', 'i'}
    assert index.ignored == {'file': 1}


def test_members_are_scoped_to_owner():
    predicted = ir_index({'classes': [{'name': 'A', 'methods': [{'name': 'run'}]}, {'name': 'B'}]})
    expected = reference_index(Facts([ClassFacts('A'), ClassFacts('B', methods=['run'])]))
    assert compare(predicted, expected)['methods']['matched'] == 0


def test_external_inheritance_does_not_create_false_negatives():
    predicted = ir_index({'classes': [{'name': 'A', 'base_classes': ['external.Exception']} ]})
    result = compare(predicted, reference_index(Facts([ClassFacts('A')])))
    assert result['inheritance']['precision'] is None
    assert result['excluded_external_inheritance']['ir'] == [('a', 'exception')]


def test_namespace_collisions_are_reported_and_excluded():
    predicted = ir_index({'classes': [{'name': 'Thing', 'namespace': 'A'}, {'name': 'Thing', 'namespace': 'B'}]})
    expected = reference_index(Facts([ClassFacts('A.Thing'), ClassFacts('B.Thing')]))
    result = compare(predicted, expected)
    assert result['classes']['ir_count'] == 0
    assert 'thing' in result['excluded_ambiguous_names']
    assert result['classes']['precision'] is None


def test_method_overloads_are_name_level_sets():
    predicted = ir_index({'classes': [{'name': 'A', 'methods': [{'name': 'run'}, {'name': 'run'}]}]})
    expected = reference_index(Facts([ClassFacts('A', methods=['run'])]))
    assert compare(predicted, expected)['methods']['ir_count'] == 1


def test_empty_denominators_are_not_perfect_scores():
    assert score(set(), set())['precision'] is None
    assert score(set(), {'a'})['recall'] == 0
    assert score({'a'}, set())['recall'] is None


def test_failed_reference_has_no_metrics(tmp_path):
    ref = tmp_path/'P/reference'
    ref.mkdir(parents=True)
    (ref/'reference_manifest.json').write_text(json.dumps({'status': 'error', 'failures': ['missing tool']}))
    report = validate_project({'id': 'P', 'language': 'php'}, tmp_path)
    assert report['status'] == 'unavailable'
    assert report['metrics'] is None
    assert report['reason'] == ['missing tool']
