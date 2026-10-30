"""Tool-native readers independent of the production evaluator's UML parser."""

import re

from experiments.reverse_engineering.facts import ClassFacts, Facts, member_name


def read_tplant(text: str) -> Facts:
    """Read tplant's class-only output, including inline extends declarations.

    TypeScript's inferred object types may contain unbalanced/truncated braces.
    Only a stand-alone closing brace closes a class; member types are not facts.
    """
    facts = Facts()
    current = None
    for line in map(str.strip, text.splitlines()):
        declaration = re.match(r'^(abstract class|class|interface|enum) ([\w$]+)(?:<[^>]*>)?(?: extends ([\w$.]+))?(?: implements ([\w$., ]+))?', line)
        if declaration:
            kind, name, parent, implements = declaration.groups()
            current = ClassFacts(name, kind)
            facts.classes.append(current)
            if parent:
                facts.inheritance.append((name, parent))
            for interface in (implements or '').split(','):
                if interface.strip():
                    facts.inheritance.append((name, interface.strip()))
            if not line.endswith('{'):
                current = None
        elif line == '}':
            current = None
        elif current and line:
            # Destructured arguments contain colons before '(' only in types;
            # tplant's method identifier is always at the start of its line.
            method = re.match(r'^[+#~ -]*(?:\{(?:static|abstract)\}\s*)?([\w$]+)\(', line)
            if method:
                current.methods.append(method[1])
            else:
                member = member_name(line)
                if member:
                    getattr(current, member[0]).append(member[1])
        elif relation := re.match(r'^([\w$]+)\s+([.o*<>|\-]+)\s+([\w$]+)', line):
            a, arrow, b = relation.groups()
            if '|>' in arrow:
                facts.inheritance.append((a, b))
            elif '<|' in arrow:
                facts.inheritance.append((b, a))
            else:
                facts.associations.append((a, b))
    for c in facts.classes:
        c.attributes = sorted(set(c.attributes))
        c.methods = sorted(set(c.methods))
    facts.inheritance = sorted(set(facts.inheritance))
    facts.associations = sorted(set(facts.associations))
    return facts
