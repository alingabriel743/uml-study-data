"""Compare postprocessed IR with independently produced tool facts.

    python -m experiments.reverse_engineering.validate_extractor

Precision = matched / IR facts; recall = matched / tool facts. These measure
agreement with a fallible tool reference, not correctness against expert truth.
Undefined denominators are JSON null. No successful reference means no scores.
"""

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

from app.evaluation.evaluate import normalize
from experiments.reverse_engineering.facts import Facts
from experiments.reverse_engineering.run import EXTENSIONS, ROOT, write_json

KINDS = {'class', 'interface', 'enum', 'trait', 'record', 'struct', 'annotation',
         'abstract', 'abstract class'}


@dataclass
class Index:
    classes: set[str] = field(default_factory=set)
    inheritance: set[tuple[str, str]] = field(default_factory=set)
    attributes: set[tuple[str, str]] = field(default_factory=set)
    methods: set[tuple[str, str]] = field(default_factory=set)
    raw_names: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    ignored: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add_class(self, name: str, attrs: list, methods: list) -> str:
        owner = normalize(name)
        if not owner:
            raise ValueError(f'Empty class name after normalization: {name!r}')
        self.raw_names[owner].add(name)
        self.classes.add(owner)
        for key, members in [('attributes', attrs), ('methods', methods)]:
            for member in members:
                raw = member.get('name', '') if isinstance(member, dict) else str(member)
                if normalized := normalize(raw):
                    getattr(self, key).add((owner, normalized))
        return owner


def reference_index(facts: Facts) -> Index:
    index = Index()
    for item in facts.classes:
        index.add_class(item.name, item.attributes, item.methods)
    index.inheritance = {(normalize(a), normalize(b)) for a, b in facts.inheritance}
    return index


def ir_index(ir: dict) -> Index:
    index = Index()
    for key in ['classes', 'interfaces', 'enums', 'traits', 'structs', 'types']:
        for item in ir.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            kind = item.get('kind', 'class' if key == 'classes' else key.rstrip('s'))
            if kind not in KINDS:
                index.ignored[kind] += 1
                continue
            name = item.get('name')
            if not name:
                index.ignored['unnamed'] += 1
                continue
            # Namespace qualification aids collision detection but normalize()
            # itself remains exactly the evaluator's public function.
            scope = item.get('namespace') or item.get('package') or ''
            full_name = str(scope) + '.' + name if scope and not any(s in name for s in '.\\:') else name
            # Python has no namespace field: distinct source files can own an
            # identically named class. Detect these, too, without changing scores.
            if not scope and item.get('source_file'):
                full_name = item['source_file'] + '::' + name
            owner = index.add_class(full_name, item.get('attributes') or item.get('properties') or [],
                                    item.get('methods') or [])
            for parent_key in ['extends', 'implements', 'base_classes']:
                parents = item.get(parent_key) or []
                if isinstance(parents, str):
                    parents = [parents]
                for parent in parents:
                    if isinstance(parent, str) and normalize(parent):
                        index.inheritance.add((owner, normalize(parent)))
    return index


def score(predicted: set, expected: set) -> dict:
    matches = predicted & expected
    precision = len(matches)/len(predicted) if predicted else None
    recall = len(matches)/len(expected) if expected else None
    f1 = (2*precision*recall/(precision+recall) if precision is not None and recall is not None
          and precision+recall else 0.0 if predicted and expected else None)
    return {'ir_count': len(predicted), 'reference_count': len(expected), 'matched': len(matches),
            'precision': precision, 'recall': recall, 'f1': f1,
            'ir_only': sorted(predicted-expected), 'reference_only': sorted(expected-predicted)}


def compare(ir: Index, reference: Index) -> dict:
    collisions = {name: {'ir': sorted(ir.raw_names.get(name, set())),
                         'reference': sorted(reference.raw_names.get(name, set()))}
                  for name in ir.classes | reference.classes
                  if len(ir.raw_names.get(name, set())) > 1 or len(reference.raw_names.get(name, set())) > 1}
    ambiguous = set(collisions)
    result = {'normalization': 'app.evaluation.evaluate.normalize',
              'member_identity': '(normalized owner, normalized declared member name); overloads collapsed',
              'inheritance_scope': 'direct internal extends/implements/base_classes; no transitive closure',
              'excluded_ambiguous_names': collisions, 'ignored_ir_kinds': dict(ir.ignored)}
    for category in ['classes', 'attributes', 'methods', 'inheritance']:
        predicted = getattr(ir, category)
        expected = getattr(reference, category)
        if category == 'classes':
            predicted, expected = predicted-ambiguous, expected-ambiguous
        else:
            predicted = {e for e in predicted if e[0] not in ambiguous}
            expected = {e for e in expected if e[0] not in ambiguous}
            if category == 'inheritance':
                # External library types are not consistently emitted by tools.
                # Keep IR-only internal classes so extractor false positives count.
                universe = (ir.classes | reference.classes)-ambiguous
                predicted = {e for e in predicted if e[1] in universe}
                expected = {e for e in expected if e[1] in universe}
        result[category] = score(predicted, expected)
    result['excluded_external_inheritance'] = {
        'ir': sorted(e for e in ir.inheritance if e[1] not in ir.classes | reference.classes),
        'reference': sorted(e for e in reference.inheritance if e[1] not in ir.classes | reference.classes)}
    return result


def validate_project(project: dict, data_root: Path) -> dict:
    directory = data_root/project['id']
    reference = directory/'reference'
    report = {'project': project['id'], 'language': project['language'], 'status': 'unavailable', 'metrics': None}
    try:
        manifest = json.loads((reference/'reference_manifest.json').read_text())
        if manifest['status'] != 'ok':
            report['reason'] = manifest['failures']
            return report
        source = directory / manifest.get('source_directory', 'snapshot')
        if manifest.get('source_rule') == 'v2-production':
            from experiments.reverse_engineering.run_v2 import source_files
            files = source_files(source, project['language'])
        else:
            files = sorted(p for p in source.rglob('*')
                           if p.is_file() and p.suffix in EXTENSIONS[project['language']])
        signature = hashlib.sha256()
        for p in files:
            signature.update(p.relative_to(source).as_posix().encode()+b'\0')
            signature.update(hashlib.sha256(p.read_bytes()).digest())
        if signature.hexdigest() != manifest['source_sha256']:
            raise ValueError('Snapshot changed since reference generation; regenerate the reference')
        ir_path = directory/'ir_postprocessed.json'
        facts_path = reference/'reference_facts.json'
        if hashlib.sha256(facts_path.read_bytes()).hexdigest() != manifest['facts_sha256']:
            raise ValueError('Reference facts changed since generation; regenerate the reference')
        for filename, checksum in manifest['output_sha256'].items():
            if hashlib.sha256((reference/'class'/filename).read_bytes()).hexdigest() != checksum:
                raise ValueError('Reference PlantUML changed since generation; regenerate the reference')
        facts = Facts.from_dict(json.loads(facts_path.read_text()))
        if not facts.classes:
            raise ValueError('Reference has no classes')
        report.update(status='ok', tool=manifest['tool'], version=manifest['version'],
                      source_commit=manifest['source_commit'],
                      ir_sha256=hashlib.sha256(ir_path.read_bytes()).hexdigest(),
                      metrics=compare(ir_index(json.loads(ir_path.read_text())), reference_index(facts)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['reason'] = str(exc)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT/'experiments/corpus.json')
    parser.add_argument('--data-root', type=Path, default=ROOT/'data')
    parser.add_argument('--projects', nargs='+')
    parser.add_argument('--out', type=Path, default=Path(__file__).parent/'results')
    args = parser.parse_args()
    projects = json.loads(args.corpus.read_text())['projects']
    unknown = set(args.projects or []) - {p['id'] for p in projects}
    if unknown:
        parser.error(f'Unknown project IDs: {sorted(unknown)}')
    results = [validate_project(p, args.data_root) for p in projects if not args.projects or p['id'] in args.projects]
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out/'extractor_validation.json', {'projects': results,
        'interpretation': 'Agreement with independent deterministic tool references; not expert ground truth. No scores for failed or missing references.'})
    fields = ['project', 'language', 'status', 'category', 'ir_count', 'reference_count', 'matched', 'precision', 'recall', 'f1']
    with (args.out/'extractor_validation.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        for result in results:
            for category in ['classes', 'inheritance', 'attributes', 'methods']:
                row = {k: result[k] for k in ['project', 'language', 'status']}
                row['category'] = category
                if result['metrics']:
                    row.update({k: v for k, v in result['metrics'][category].items() if k in fields})
                writer.writerow(row)
    for result in results:
        print(result['project'], result['status'])


if __name__ == '__main__':
    main()
