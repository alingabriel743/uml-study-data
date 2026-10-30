"""Small, auditable interchange model; no project extractor is used here.

The PlantUML member reader supports the line-oriented output of pyreverse,
tplant and ph-puml. Unsupported member lines are errors, never silently lost.
It is deliberately not a general PlantUML parser.
"""

from dataclasses import asdict, dataclass, field
import re

from app.evaluation.puml_parser import parse_puml


@dataclass
class ClassFacts:
    name: str
    kind: str = "class"
    attributes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


@dataclass
class Facts:
    classes: list[ClassFacts] = field(default_factory=list)
    # Edges are directed child -> parent, including interface realization.
    inheritance: list[tuple[str, str]] = field(default_factory=list)
    associations: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Facts":
        return cls(classes=[ClassFacts(**c) for c in data["classes"]],
                   inheritance=[tuple(e) for e in data.get("inheritance", [])],
                   associations=[tuple(e) for e in data.get("associations", [])],
                   warnings=data.get("warnings", []))


CLASS_KINDS = {"class", "abstract class", "abstract", "interface", "enum", "annotation", "record", "struct", "entity"}
TOKEN = r'(?:"(?:\\.|[^"\\])*"|[\w$]+(?:(?:[.\\]|::)[\w$]+)*)'
DECL = re.compile(r'^(?:abstract\s+class|class|interface|enum|annotation|record|struct|entity)\s+(' + TOKEN + r')(?:\s+as\s+(' + TOKEN + r'))?')


def member_name(line: str) -> tuple[str, str] | None:
    """Return category/name for name-first UML and Java/PHP signatures."""
    line = re.sub(r'\{(?:static|abstract|field|method)\}|<<.*?>>', '', line).strip()
    line = line.lstrip('+#~- ').strip()
    if not line or re.fullmatch(r'[-.= _]+', line):
        return None
    if '(' in line and not any(s in line.split('(', 1)[0] for s in (':', '=')):
        # PHP/Java visibility keywords and return type may precede the name.
        head = line.split('(', 1)[0].strip()
        match = re.search(r'([\w$]+)\s*$', head)
        if match:
            return "methods", match[1].lstrip('$')
    # Default values can contain parentheses: strip them for attributes first.
    field = line.split('=', 1)[0].strip()
    dollar = re.search(r'\$([A-Za-z_]\w*)', field)
    if dollar:
        return "attributes", dollar[1]
    if ':' in field:
        match = re.fullmatch(r'([\w$]+)\s*', field.split(':', 1)[0])
        if match:
            return "attributes", match[1].lstrip('$')
    match = re.search(r'([A-Za-z_]\w*)\s*$', field)
    if match:
        return "attributes", match[1]
    raise ValueError(f"Unsupported tool member syntax: {line!r}")


def read_puml(text: str) -> Facts:
    """Read native tool declarations, members and typed relationships.

    Structure/aliases are checked using the production evaluator's parser; the
    member reader independently associates a body with its declared alias.
    """
    diagram = parse_puml(text, "class")
    facts = Facts(warnings=list(diagram.parse_warnings))
    by_id: dict[str, ClassFacts] = {}
    for key, element in diagram.elements.items():
        if element.kind in CLASS_KINDS:
            item = ClassFacts(element.label or element.name, element.kind)
            # pyreverse can reuse a display label for distinct declarations
            # (for example a decorated wrapper and its public class). Its dotted
            # alias retains their distinct identity for collision disclosure.
            if element.alias and '.' in element.alias:
                item.name = element.alias
            # Use scoped identity unless the tool supplies a fully qualified label.
            if not element.label and element.qualified_name:
                item.name = element.qualified_name
            facts.classes.append(item)
            by_id[key] = item
    def endpoint(key: str) -> str:
        return by_id[key].name if key in by_id else key
    for edge in diagram.relationships:
        if edge.is_generalization:
            child, parent = (edge.src, edge.dst) if '|>' in edge.arrow else (edge.dst, edge.src)
            facts.inheritance.append((endpoint(child), endpoint(parent)))
        else:
            facts.associations.append((endpoint(edge.src), endpoint(edge.dst)))
    current: ClassFacts | None = None
    for raw in text.splitlines():
        line = raw.strip()
        match = DECL.match(line)
        if match:
            tokens = {t.strip('"') for t in match.groups() if t}
            identity = (match[2] or match[1]).strip('"')
            candidates = [by_id[k] for k, e in diagram.elements.items() if k in by_id
                          and identity in {e.alias, e.qualified_name, k}]
            if not candidates:
                candidates = [by_id[k] for k, e in diagram.elements.items() if k in by_id and tokens & e.names()]
            if len(candidates) != 1:
                raise ValueError(f"Ambiguous/unsupported tool declaration: {line}")
            current = candidates[0] if '{' in line else None
            if '{' in line and line.split('{', 1)[1].strip() not in {'', '}'}:
                raise ValueError(f"Inline tool member bodies are unsupported: {line}")
            if line.endswith('}'):
                current = None
            continue
        if current:
            if line == '}':
                current = None
            elif line and not line.startswith("'"):
                member = member_name(line)
                if member:
                    getattr(current, member[0]).append(member[1])
    for item in facts.classes:
        item.attributes = sorted(set(item.attributes))
        item.methods = sorted(set(item.methods))
    declarations = sum(bool(DECL.match(line.strip())) for line in text.splitlines())
    if declarations != len(facts.classes):
        raise ValueError(f'Evaluator found {len(facts.classes)} classes in {declarations} native declarations')
    return facts


def render_puml(facts: Facts) -> str:
    """Lossless name-level serialization for the custom javac adapter only."""
    classes = sorted(facts.classes, key=lambda c: c.name)
    names = {c.name for c in classes}
    names.update(p for _, p in facts.inheritance)
    aliases = {n: f"C{i}" for i, n in enumerate(sorted(names))}
    lines = ['@startuml', 'set namespaceSeparator none']
    for c in classes:
        kind = c.kind if c.kind in {"interface", "enum", "annotation"} else "class"
        name = c.name.replace('"', '\\"')
        lines.append(f'{kind} "{name}" as {aliases[c.name]} {{')
        lines.extend('  ' + a for a in sorted(set(c.attributes)))
        lines.extend('  ' + m + '()' for m in sorted(set(c.methods)))
        lines.append('}')
    for parent in sorted(names - {c.name for c in classes}):
        lines.append(f'class "{parent}" as {aliases[parent]} <<external>>')
    kinds = {c.name: c.kind for c in classes}
    for child, parent in sorted(set(facts.inheritance)):
        arrow = '..|>' if kinds.get(parent) == 'interface' and kinds.get(child) != 'interface' else '--|>'
        lines.append(f'{aliases[child]} {arrow} {aliases[parent]}')
    for source, dest in sorted(set(facts.associations)):
        lines.append(f'{aliases[source]} --> {aliases[dest]}')
    return '\n'.join([*lines, '@enduml', ''])
