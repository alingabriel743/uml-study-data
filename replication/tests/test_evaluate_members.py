"""Offline regressions for ambiguity exclusions and conditional member metrics."""

import csv
import json
import sys

import pytest

from app.evaluation.evaluate import IRIndex, aggregate, evaluate_file, ir_members, main, normalize_type


def index(tmp_path, classes, **collections):
    path = tmp_path / 'ir_postprocessed.json'
    path.write_text(json.dumps({'classes': classes, **collections}))
    return IRIndex.load(path)


def evaluate(tmp_path, ir, text, kind='class', filename='diagram.puml'):
    path = tmp_path / filename
    path.write_text(text)
    return evaluate_file(path, kind, ir)


def test_ambiguous_names_ground_but_do_not_supply_edge_or_member_evidence(tmp_path):
    ir = index(tmp_path, [
        {'name': 'one.Shared', 'source_file': 'one.java', 'extends': 'Base', 'methods': [{'name': 'run'}]},
        {'name': 'two.Shared', 'source_file': 'two.java', 'extends': 'Other'},
        {'name': 'Base', 'source_file': 'base.java'},
        {'name': 'Child', 'extends': 'Base', 'source_file': 'child.java'},
    ])
    result = evaluate(tmp_path, ir, '''class Shared { run() }
class Base
class Child
Shared --|> Base
Child --|> Base
Missing --> Base''')
    batch = aggregate([result], ir, 'class')
    assert len(ir.declarations['shared']) == 2
    assert {d['source_file'] for d in ir.declarations['shared']} == {'one.java', 'two.java'}
    assert (batch['ir_entities'], batch['ir_distinct_names'], batch['ir_ambiguous_names']) == (4, 3, 1)
    assert result.grounded_elements == 3
    assert batch['entity_recall'] == 1.0
    assert batch['grounded_element_rate'] == 0.75
    assert batch['endpoint_grounding'] == 0.6667
    assert batch['ambiguous_elements'] == batch['member_excluded_elements'] == 1
    assert batch['relationship_precision_excluded_edges'] == 1
    assert batch['generalization_precision_excluded_edges'] == 1
    assert batch['relationship_precision_ir'] == 0.5  # unmatched edge remains in denominator
    assert batch['generalization_precision'] == 1.0
    assert batch['eligible_classes'] == 2
    assert batch['listed_members'] == 0
    assert batch['member_precision'] is None


def test_ambiguous_parent_is_excluded_and_library_parent_remains_eligible(tmp_path):
    ir = index(tmp_path, [{'name': 'Parent', 'source_file': 'one.java'},
                          {'name': 'Parent', 'source_file': 'two.java'},
                          {'name': 'Child', 'extends': 'Parent', 'methods': []},
                          {'name': 'Concrete', 'implements': ['Serializable']}])
    result = evaluate(tmp_path, ir, 'class Parent\nclass Child\nclass Concrete\n'
                      'Child --|> Parent\nConcrete ..|> Serializable')
    batch = aggregate([result], ir, 'class')
    assert result.generalization_precision_excluded_edges == 1
    assert batch['generalization_precision'] == 1.0
    assert batch['endpoint_grounding'] == 0.5


def test_all_ambiguous_edges_yield_na_instead_of_zero(tmp_path):
    ir = index(tmp_path, [{'name': 'A'}, {'name': 'A'}, {'name': 'B'}])
    result = evaluate(tmp_path, ir, 'class A\nclass B\nA --|> B')
    batch = aggregate([result], ir, 'class')
    assert batch['endpoint_grounding'] == 1
    assert batch['relationship_precision_ir'] is None
    assert batch['generalization_precision'] is None


def test_incidental_alias_cannot_override_the_matched_parent(tmp_path):
    ir = index(tmp_path, [{'name': 'Shared'}, {'name': 'Shared'}, {'name': 'Good'},
                          {'name': 'Child', 'extends': 'Shared'}])
    result = evaluate(tmp_path, ir, 'class Good as "Shared"\nclass Child\nChild --|> Good')
    assert result.ambiguous_elements == 0
    assert result.generalization_precision_excluded_edges == 0
    assert result.correct_generalizations == 0


def test_member_counts_collapse_overloads_keep_accessors_and_condition_on_drawn_classes(tmp_path):
    ir = index(tmp_path, [
        {'name': 'Sample', 'attributes': [{'declaration': 'private List<String> names;'},
                                         {'name': '$count', 'type': 'int'}],
         'methods': [{'name': 'Sample'}, {'name': 'run'}, {'name': 'run', 'parameters': ['int n']},
                     {'name': 'getCount'}, {'name': 'setCount'}]},
        {'name': 'NotDrawn', 'methods': [{'name': 'irrelevant'}]},
    ])
    result = evaluate(tmp_path, ir, '''class Sample {
 - names: java.util.List<Integer>
 - count: string
 +Sample()
 +run()
 +run(int n)
 +getCount(): int
 +invented()
}
class External { fabricated() }''')
    assert result.eligible_classes == 1
    assert (result.listed_members, result.matched_members, result.ir_members, result.captured_members) == (6, 5, 6, 5)
    assert result.member_precision == result.member_recall == 0.8333
    assert (result.typed_comparisons, result.matching_attribute_types) == (2, 1)
    assert result.attribute_type_agreement == 0.5


def test_empty_drawn_class_has_zero_conditional_recall_and_undefined_precision(tmp_path):
    ir = index(tmp_path, [{'name': 'A', 'methods': [{'name': 'run'}]}])
    result = evaluate(tmp_path, ir, 'class A {}')
    assert result.eligible_classes == 1
    assert result.member_recall == 0
    assert result.member_precision is None


def test_batch_aggregation_pools_counts_and_merges_same_class_aliases_within_file(tmp_path):
    ir = index(tmp_path, [{'name': 'A', 'methods': [{'name': 'one'}, {'name': 'two'}]}])
    first = evaluate(tmp_path, ir, 'class "A" as X { one() }\nclass "A" as Y { two() }')
    second = evaluate(tmp_path, ir, 'class A { one() }', filename='second.puml')
    assert first.eligible_classes == 1
    batch = aggregate([first, second], ir, 'class')
    assert batch['eligible_classes'] == 2
    assert batch['listed_members'] == 3
    assert batch['ir_members'] == 4
    assert batch['member_precision'] == 1
    assert batch['member_recall'] == 0.75


@pytest.mark.parametrize('kind', ['sequence', 'activity', 'component', 'deployment', 'use_case', 'system_context'])
def test_member_metrics_are_na_for_other_diagram_types(tmp_path, kind):
    ir = index(tmp_path, [{'name': 'A', 'methods': [{'name': 'run'}]}])
    result = evaluate(tmp_path, ir, 'class A { run() }', kind)
    batch = aggregate([result], ir, kind)
    for metric in ('member_precision', 'member_recall', 'attribute_type_agreement'):
        assert getattr(result, metric) is None
        assert batch[metric] is None
    assert result.eligible_classes == 0


def test_unambiguous_function_is_not_an_eligible_class(tmp_path):
    ir = index(tmp_path, [], functions=[{'name': 'run', 'parameters': []}])
    result = evaluate(tmp_path, ir, 'class run { run() }')
    assert result.grounded_elements == 1
    assert result.eligible_classes == 0


def test_ir_schema_variants_and_missing_type_handling():
    members, unparsed = ir_members({
        'attributes': [{'declaration': 'private List<String> names;'},
                       'protected int a = 1, b = 2;', {'name': '$enabled', 'type': 'bool'},
                       {'name': '$unknown', 'type': None}],
        'properties': [{'name': 'size', 'type': 'number'}],
        'methods': [{'name': '__construct'}, 'run', 'public void save(int n)'],
    })
    assert unparsed == 0
    assert {(m.name, m.type) for m in members if m.kind == 'attribute'} == {
        ('names', 'List<String>'), ('a', 'int'), ('b', 'int'), ('enabled', 'bool'),
        ('unknown', ''), ('size', 'number')}
    assert {m.name for m in members if m.kind == 'method'} == {'__construct', 'run', 'save'}
    assert normalize_type('java.util.Map<String, List<Integer>>') == normalize_type('Map<X, Y>')
    assert normalize_type('Vendor\\Thing[]') == normalize_type('?Thing')
    assert normalize_type('A|B') == normalize_type('B|A')
    assert normalize_type('A|B') != normalize_type('B')
    assert normalize_type('A&B') != normalize_type('A|B')


def test_missing_attribute_type_is_not_invented(tmp_path):
    ir = index(tmp_path, [{'name': 'A', 'attributes': [{'name': '$value', 'type': ''}]}])
    result = evaluate(tmp_path, ir, 'class A { value: int }')
    assert result.member_precision == result.member_recall == 1
    assert result.typed_comparisons == 0
    assert result.attribute_type_agreement is None


def test_unparsed_members_are_counted_and_reported(tmp_path):
    ir = index(tmp_path, [{'name': 'A', 'attributes': [{'declaration': '???'}]}])
    result = evaluate(tmp_path, ir, 'class A { ??? }')
    assert result.unparsed_member_lines == result.ir_unparsed_member_entries == 1


def test_csv_exports_include_denominators_exclusions_and_member_metrics(tmp_path, monkeypatch):
    path = tmp_path / 'ir_postprocessed.json'
    index(tmp_path, [{'name': 'A', 'methods': [{'name': 'run'}]}, {'name': 'B'}, {'name': 'B'}])
    root = tmp_path / 'diagrams' / 'class'
    root.mkdir(parents=True)
    (root / 'one.puml').write_text('class A { run() }\nclass B\nA --> B')
    out = tmp_path / 'out'
    monkeypatch.setattr(sys, 'argv', ['evaluate', '--root', str(root.parent), '--ir', str(path),
                                    '--out', str(out), '--no-validate'])
    main()
    for filename in ('files.csv', 'batches.csv'):
        row = next(csv.DictReader((out / filename).open()))
        assert row['ir_entities'] == '3'
        assert row['ir_distinct_names'] == '2'
        assert row['ir_ambiguous_names'] == '1'
        assert row['relationship_precision_excluded_edges'] == '1'
        assert row['eligible_classes'] == row['listed_members'] == '1'
        assert row['member_precision'] == row['member_recall'] == '1.0'
