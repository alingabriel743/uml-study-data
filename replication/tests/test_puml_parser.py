"""unittest-compatible parser regressions and the complete 917-file study sweep.

Run: revision_work/.venv/bin/python -m pytest -q tests/
Use -s for the corpus mean E/R table. BEFORE totals were captured from the
unmodified parser before this change; AFTER totals pin the reviewed corpus.
No SDK, Java runtime, network, or PlantUML compiler is required.
"""

import unittest
from collections import Counter, defaultdict
from pathlib import Path

from app.evaluation.puml_parser import parse_members, parse_puml


CORPUS = Path(__file__).resolve().parents[1] / "uml-study-data-main"
# type: (files, total elements, total relationships)
BEFORE = {
    "activity": (218, 12718, 16839),
    "class": (211, 3627, 4159),
    "component": (12, 484, 549),
    "deployment": (12, 392, 259),
    "sequence": (233, 2260, 12550),
    "system_context": (12, 221, 235),
    "use_case": (219, 3707, 4518),
}


def named_edges(diagram):
    """Human-readable graph topology, retaining parallel edges."""
    return Counter(
        (diagram.elements[r.src].name, diagram.elements[r.dst].name, r.label)
        for r in diagram.relationships
    )


class NamedDiagramTests(unittest.TestCase):
    def test_identifier_as_label_does_not_collide_with_identifier(self):
        d = parse_puml('''class ScriptEvent as "Event"
class Event
ScriptEvent --|> Event : extends''', "class")
        self.assertEqual(set(d.elements), {"ScriptEvent", "Event"})
        self.assertEqual(d.elements["ScriptEvent"].name, "ScriptEvent")
        self.assertEqual(d.elements["ScriptEvent"].label, "Event")
        self.assertEqual((d.relationships[0].src, d.relationships[0].dst),
                         ("ScriptEvent", "Event"))
        self.assertTrue(d.relationships[0].is_generalization)

    def test_both_alias_forms_with_stereotypes(self):
        for keyword in ("class", "participant", "node", "component"):
            with self.subTest(keyword=keyword):
                d = parse_puml(f'''{keyword} X <<service>> as "Display"
{keyword} "Display" <<service>> as Y
X --> Y''', "component")
                self.assertEqual(set(d.elements), {"X", "Y"})
                self.assertEqual(d.elements["X"].label, "Display")
                self.assertEqual(d.elements["Y"].name, "Display")
                self.assertEqual(d.elements["Y"].alias, "Y")

    def test_duplicate_display_labels_keep_distinct_identities(self):
        d = parse_puml('''class "Same" as A
class B as "Same"
class Same
A --> B
B --> Same''', "class")
        self.assertEqual(set(d.elements), {"A", "B", "Same"})
        self.assertEqual([(r.src, r.dst) for r in d.relationships], [("A", "B"), ("B", "Same")])

    def test_literal_multiline_label_is_one_declaration(self):
        d = parse_puml('''rectangle "Agent
file upload
class Pretend
[Browser]" as Agent
component "Storage
Service" as Storage
Agent --> Storage''', "component")
        self.assertEqual(set(d.elements), {"Agent", "Storage"})
        self.assertIn("file upload", d.elements["Agent"].label)
        self.assertEqual(d.relationship_count, 1)

    def test_alias_and_stereotype_on_following_lines(self):
        d = parse_puml('''class
"Display
Name"
<<service>>
as A
class B
as "Other"
A --> B''', "class")
        self.assertEqual(set(d.elements), {"A", "B"})
        self.assertEqual(d.elements["A"].label, "Display\nName")
        self.assertEqual(d.elements["B"].label, "Other")

    def test_shorthand_components_including_isolated_and_multiline(self):
        d = parse_puml('''[Isolated] as I
[Multi
Line] as M
[Another]
M --> [Another]''', "component")
        self.assertEqual(set(d.elements), {"I", "M", "Another"})
        self.assertEqual(d.elements["M"].kind, "component")
        self.assertEqual(d.element_count, 3)
        self.assertEqual(d.relationship_count, 1)

    def test_package_and_namespace_scope_with_forward_edges(self):
        d = parse_puml('''namespace p {
 A --> B
 class A
 package nested {
  class A
 }
 class B
}
namespace q { class A }
p.A --> q::A
p.nested.A --> p.B''', "class")
        self.assertEqual(set(d.elements), {"p::A", "p::nested::A", "p::B", "q::A"})
        self.assertEqual([(r.src, r.dst) for r in d.relationships],
                         [("p::A", "p::B"), ("p::A", "q::A"), ("p::nested::A", "p::B")])
        self.assertTrue(all("A" in e.names() for k, e in d.elements.items() if k.endswith("::A")))
        self.assertEqual(d.elements["p::A"].qualified_name, "p::A")

    def test_same_alias_in_distinct_scopes_and_unique_external_alias(self):
        d = parse_puml('''package p {
 class "P display" as A
 A --> A
}
package q {
 class "Q display" as A
 class "Unique" as U
 A --> U
}
p.A --> q.A
U --> p.A''', "class")
        self.assertEqual(set(d.elements), {"p::A", "q::A", "q::U"})
        self.assertEqual([(r.src, r.dst) for r in d.relationships],
                         [("p::A", "p::A"), ("q::A", "q::U"), ("p::A", "q::A"), ("q::U", "p::A")])

    def test_qualified_quoted_endpoints_are_not_multiplicities_or_labels(self):
        d = parse_puml(r'''package "DDTrace\\Contracts" { interface Scope }
package DDTrace { class Scope }
DDTrace.Scope ..|> "DDTrace\\Contracts"::Scope : implements
"DDTrace\\Contracts"::"Scope" --> DDTrace.Scope : uses''', "class")
        contracts = r"DDTrace\\Contracts::Scope"
        self.assertEqual(set(d.elements), {contracts, "DDTrace::Scope"})
        self.assertEqual([(r.src, r.dst, r.label) for r in d.relationships],
                         [("DDTrace::Scope", contracts, "implements"),
                          (contracts, "DDTrace::Scope", "uses")])

    def test_no_whitespace_before_relationship_label(self):
        d = parse_puml("A->B:message", "sequence")
        self.assertEqual(set(d.elements), {"A", "B"})
        self.assertEqual(d.relationships[0].label, "message")

    def test_class_members_do_not_become_declarations_or_change_scope(self):
        d = parse_puml('''package p {
 class A {
  +{abstract} void run()
  +{static} String value
  class misleading
  file upload
 }
 class B {}
}
A --> B''', "class")
        self.assertEqual(set(d.elements), {"p::A", "p::B"})
        self.assertEqual(d.relationship_count, 1)

    def test_comments_notes_style_and_legend_never_add_elements(self):
        d = parse_puml('''@startuml
/' class Block
class Comment '/
skinparam class {
 BackgroundColor red
}
<style>
class {
 FontColor blue
}
</style>
legend right
class Pretend
A --> B
endlegend
floating note left
file upload
end note
note right: class Nope
class Real
@enduml''', "class")
        self.assertEqual(set(d.elements), {"Real"})
        self.assertEqual(d.relationship_count, 0)

    def test_cardinalities_colors_and_generalization(self):
        d = parse_puml('''class A
interface B
A "0..*" o-- "1" B : owns
A -[#red,dashed]-> B : calls
A .up.|> B : implements
A -[hidden]-> LayoutOnly''', "class")
        self.assertEqual((d.element_count, d.relationship_count), (2, 3))
        self.assertEqual([r.label for r in d.relationships], ["owns", "calls", "implements"])
        self.assertTrue(d.relationships[-1].is_generalization)
        self.assertTrue(all((r.src, r.dst) == ("A", "B") for r in d.relationships))

    def test_use_case_and_actor_shorthand(self):
        d = parse_puml(''':Customer:
(Place order) as Order
:Customer: --> Order
Order ..> (Pay) : includes''', "use_case")
        self.assertEqual(set(d.elements), {"Customer", "Order", "Pay"})
        self.assertEqual(d.elements["Customer"].kind, "actor")
        self.assertEqual(d.elements["Pay"].kind, "usecase")


class SequenceTests(unittest.TestCase):
    def test_creation_activation_deactivation_destruction_suffixes(self):
        for suffix in ("**", "++", "--", "!!", "--++", "++ #red"):
            with self.subTest(suffix=suffix):
                d = parse_puml(f'participant A\nA -> B {suffix} : message', "sequence")
                self.assertEqual((d.element_count, d.relationship_count), (2, 1))
                self.assertEqual((d.relationships[0].src, d.relationships[0].dst), ("A", "B"))
                self.assertFalse(d.relationships[0].external)

    def test_found_lost_and_short_messages_exclude_external_elements(self):
        d = parse_puml('''[-> A ** : found
A ->] : lost right
A -> [ : lost left
] -> A : found right
?-> A : short found
A ->? : short lost
A -> B ++ : local''', "sequence")
        self.assertEqual(set(d.elements), {"A", "B"})
        self.assertEqual(d.relationship_count, 7)
        self.assertEqual(sum(r.external for r in d.relationships), 6)
        self.assertEqual(sum(r.src_external for r in d.relationships), 3)
        self.assertEqual(sum(r.dst_external for r in d.relationships), 3)
        for r in d.relationships:
            self.assertEqual(r.src in d.elements, not r.src_external)
            self.assertEqual(r.dst in d.elements, not r.dst_external)

    def test_reverse_open_cross_circle_and_self_arrows(self):
        for arrow in ("<-", "<<--", "->>", r"-\\", "-//", "->x", "o->o", "x<->x"):
            with self.subTest(arrow=arrow):
                d = parse_puml(f'A {arrow} A : message', "sequence")
                self.assertEqual((d.element_count, d.relationship_count), (1, 1))
                self.assertEqual((d.relationships[0].src, d.relationships[0].dst), ("A", "A"))


class ActivityTests(unittest.TestCase):
    def parse(self, text):
        d = parse_puml(text, "activity")
        self.assertTrue(d.activity_graph_complete, d.parse_warnings)
        self.assertEqual(d.actions, d.element_count)
        self.assertEqual(d.activity_flows, d.relationship_count)
        self.assertTrue(all(r.src in d.elements and r.dst in d.elements for r in d.relationships))
        return d

    def test_sequential_and_colored_multiline_actions(self):
        d = self.parse('''start
#pink:Read
input;
:Sort
(category first, quality;
alphabetical);
end''')
        self.assertEqual((d.element_count, d.relationship_count), (4, 3))
        self.assertEqual([e.kind for e in d.elements.values()], ["start", "action", "action", "end"])
        self.assertEqual(named_edges(d), Counter({
            ("start", "Read\ninput", ""): 1,
            ("Read\ninput", "Sort\n(category first, quality;\nalphabetical)", ""): 1,
            ("Sort\n(category first, quality;\nalphabetical)", "end", ""): 1,
        }))

    def test_if_else_exact_topology_hand_checked(self):
        d = self.parse('''start
if (ok?) then (yes)
:Accept;
else (no)
:Reject;
endif
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (6, 6))
        self.assertEqual(named_edges(d), Counter({
            ("start", "if (ok?) then (yes)", ""): 1,
            ("if (ok?) then (yes)", "Accept", "yes"): 1,
            ("if (ok?) then (yes)", "Reject", "no"): 1,
            ("Accept", "merge", ""): 1, ("Reject", "merge", ""): 1,
            ("merge", "stop", ""): 1,
        }))

    def test_empty_branches_are_distinct_parallel_edges(self):
        d = self.parse('''start
if (ok?) then (yes)
else (no)
endif
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (4, 4))
        decision = next(k for k, e in d.elements.items() if e.kind == "decision")
        merge = next(k for k, e in d.elements.items() if e.kind == "merge")
        self.assertEqual({r.label for r in d.relationships if (r.src, r.dst) == (decision, merge)},
                         {"yes", "no"})

    def test_if_without_else_has_bypass_edge(self):
        d = self.parse('''start
if (ok?) then (yes)
:Work;
endif
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (5, 5))
        self.assertIn(("if (ok?) then (yes)", "merge", "no"), named_edges(d))

    def test_elseif_nested_branch_and_early_stop(self):
        d = self.parse('''start
if (A?) then (yes)
stop
elseif (B?) then (yes)
if (C?) then (yes)
:Work;
endif
else (no)
:Fallback;
endif
:Continue;
end''')
        self.assertEqual(sum(e.kind == "decision" for e in d.elements.values()), 3)
        self.assertEqual(sum(e.kind == "merge" for e in d.elements.values()), 2)
        self.assertFalse(any(d.elements[r.src].kind == "stop" for r in d.relationships))
        self.assertIn(("if (A?) then (yes)", "elseif (B?) then (yes)", "no"), named_edges(d))

    def test_both_branches_terminate_no_dangling_merge(self):
        d = self.parse('''start
if (ok?) then (yes)
stop
else (no)
end
endif''')
        self.assertEqual((d.element_count, d.relationship_count), (4, 3))
        self.assertNotIn("merge", {e.kind for e in d.elements.values()})

    def test_fork_join_exact_topology_hand_checked(self):
        d = self.parse('''start
fork
:A;
fork again
:B;
end fork
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (6, 6))
        self.assertEqual(named_edges(d), Counter({
            ("start", "fork", ""): 1, ("fork", "A", ""): 1, ("fork", "B", ""): 1,
            ("A", "join", ""): 1, ("B", "join", ""): 1, ("join", "stop", ""): 1,
        }))

    def test_nested_forks_splits_and_end_merge(self):
        d = self.parse('''start
fork
split
:A;
split again
:B;
end split
fork again
:C;
end merge
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (9, 10))
        self.assertEqual(Counter(e.kind for e in d.elements.values()),
                         Counter(start=1, stop=1, fork=2, join=1, merge=1, action=3))

    def test_while_loop_exact_topology_hand_checked(self):
        d = self.parse('''start
while (more?) is (yes)
:Work;
endwhile (no)
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (4, 4))
        self.assertEqual(named_edges(d), Counter({
            ("start", "while (more?) is (yes)", ""): 1,
            ("while (more?) is (yes)", "Work", "yes"): 1,
            ("Work", "while (more?) is (yes)", ""): 1,
            ("while (more?) is (yes)", "stop", "no"): 1,
        }))

    def test_repeat_backward_only_on_back_edge(self):
        d = self.parse('''start
repeat
:Work;
backward:Wait;
repeat while (more?) is (yes) not (no)
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (6, 6))
        edges = named_edges(d)
        condition = "repeat while (more?) is (yes) not (no)"
        self.assertIn((condition, "Wait", "yes"), edges)
        self.assertIn(("Wait", "repeat", ""), edges)
        self.assertIn((condition, "stop", "no"), edges)
        self.assertNotIn(("Work", "Wait", ""), edges)

    def test_repeat_target_without_semicolon_does_not_swallow_next_condition(self):
        d = self.parse('''start
repeat :Next
if (ok?) then (yes)
:Work;
endif
repeat while (more?) is (yes)
stop''')
        self.assertEqual(sum(e.kind == "decision" for e in d.elements.values()), 2)
        self.assertIn("Next", {e.name for e in d.elements.values()})

    def test_break_leaves_nearest_loop_and_joins_exit(self):
        for opener, closer in (("repeat", "repeat while (more?) is (yes)"),
                               ("while (more?) is (yes)", "endwhile (no)")):
            with self.subTest(opener=opener):
                d = self.parse(f'''start
{opener}
if (bad?) then (yes)
:Abort;
break
endif
:Work;
{closer}
:After;
stop''')
                abort = next(k for k, e in d.elements.items() if e.name == "Abort")
                outgoing = [r for r in d.relationships if r.src == abort]
                self.assertEqual(len(outgoing), 1)
                self.assertEqual(d.elements[outgoing[0].dst].kind, "merge")
                self.assertNotEqual(d.elements[outgoing[0].dst].name, "Work")

    def test_switch_branch_labels_and_merge(self):
        d = self.parse('''start
switch (kind?)
case (A)
:One;
case (B)
:Two;
endswitch
stop''')
        self.assertEqual((d.element_count, d.relationship_count), (6, 6))
        self.assertIn(("switch (kind?)", "One", "A"), named_edges(d))
        self.assertIn(("switch (kind?)", "Two", "B"), named_edges(d))

    def test_detach_and_kill_terminate_without_inventing_nodes(self):
        for terminal in ("detach", "kill"):
            with self.subTest(terminal=terminal):
                d = self.parse(f'''start
if (bad?) then (yes)
:Abort;
{terminal}
endif
:Continue;
stop''')
                self.assertNotIn(terminal, {e.kind for e in d.elements.values()})
                abort = next(k for k, e in d.elements.items() if e.name == "Abort")
                self.assertFalse(any(r.src == abort for r in d.relationships))

    def test_multiline_condition_and_arrow_label(self):
        d = self.parse('''start
if (A AND
B?) then (yes)
-> accepted;
:Work;
endif
stop''')
        self.assertIn(("if (A AND\nB?) then (yes)", "Work", "accepted"), named_edges(d))

    def test_unknown_or_unbalanced_control_is_explicitly_flagged(self):
        for text in ("start\n(A)\nstop", "start\nif (ok?) then (yes)\n:Work;",
                     "start\nendif\nstop", "start\nfork\n:A;\nfork end\nstop",
                     "start\n:Unterminated", "start\nlabel x\ngoto x\nstop",
                     "!include extra.puml\nstart\nstop"):
            with self.subTest(text=text):
                d = parse_puml(text, "activity")
                self.assertFalse(d.activity_graph_complete)
                self.assertTrue(d.parse_warnings)
                self.assertGreater(d.element_count, 0)

    def test_empty_input(self):
        d = self.parse("")
        self.assertEqual((d.element_count, d.relationship_count), (0, 0))


class StudyCorpusTests(unittest.TestCase):
    # Hand audit of complete graphs: linear chain; optional four-action branch;
    # three-worker fork/join + collection while + nested conditional/kill;
    # repeat loop + colored failure action/kill. Counts follow the explicit CFG
    # convention (separate decisions/merges, no synthetic kill/detach symbols).
    def test_hand_checked_activity_graph_samples(self):
        samples = (
            ("JavaScript/impeccable/activity/activity_promo_tile_generation.puml",
             (17, 16), Counter(start=1, stop=1, action=15)),
            ("JavaScript/impeccable/activity/activity_extension_build_flow.puml",
             (22, 22), Counter(start=1, end=1, action=18, decision=1, merge=1)),
            ("Java/debezium/activity/activity_parallel_smt_consumer_processing.puml",
             (22, 25), Counter(start=1, stop=1, action=13, decision=3, merge=2, fork=1, join=1)),
            ("Python/lyra/activity/activity_guardrail_checkpoint_download.puml",
             (23, 24), Counter(start=1, stop=1, action=15, decision=3, merge=3)),
        )
        for relative, counts, kinds in samples:
            with self.subTest(file=relative):
                d = parse_puml((CORPUS / relative).read_text(encoding="utf-8"), "activity")
                self.assertTrue(d.activity_graph_complete, d.parse_warnings)
                self.assertEqual((d.element_count, d.relationship_count), counts)
                self.assertEqual(Counter(e.kind for e in d.elements.values()), kinds)

    def test_all_917_study_files_no_exceptions_and_consistent_graphs(self):
        paths = sorted(CORPUS.rglob("*.puml"))
        self.assertEqual(len(paths), 917)
        stats = defaultdict(lambda: [0, 0, 0])
        flagged = []
        for path in paths:
            with self.subTest(file=str(path.relative_to(CORPUS))):
                kind = path.parent.name
                d = parse_puml(path.read_text(encoding="utf-8"), kind)
                self.assertEqual(d.element_count, len(d.elements))
                self.assertEqual(d.relationship_count, len(d.relationships))
                for r in d.relationships:
                    self.assertTrue(r.src_external or r.src in d.elements)
                    self.assertTrue(r.dst_external or r.dst in d.elements)
                stats[kind][0] += 1
                stats[kind][1] += d.element_count
                stats[kind][2] += d.relationship_count
                if not d.activity_graph_complete:
                    self.assertEqual(kind, "activity")
                    self.assertTrue(d.parse_warnings)
                    flagged.append(str(path.relative_to(CORPUS)))
        print("\nStudy corpus: type N | mean E before -> after | mean R before -> after")
        for kind in sorted(BEFORE):
            n, e, r = BEFORE[kind]
            actual_n, actual_e, actual_r = stats[kind]
            self.assertEqual(actual_n, n)
            print(f"{kind:14} {n:3} | {e/n:.2f} -> {actual_e/n:.2f} | {r/n:.2f} -> {actual_r/n:.2f}")
        print(f"Activity graphs flagged incomplete: {len(flagged)}/218")
        self.assertEqual(set(flagged), {
            "Java/debezium/activity/activity_incremental_snapshot_execution.puml",
            "Java/debezium/activity/activity_mariadb_streaming_pipeline.puml",
            "Java/debezium/activity/activity_mysql_streaming_pipeline.puml",
            "JavaScript/evolver/activity/activity_recovery_loop.puml",
            "PHP/PHPMailer/activity/activity_datadog_download_and_deploy.puml",
            "PHP/laravel-ai/activity/activity_audio_and_transcription_flow.puml",
            "Python/lyra/activity/activity_base_pipeline_lifecycle.puml",
        })
        self.assertEqual({k: tuple(v) for k, v in stats.items()}, {
            "activity": (218, 15059, 17009), "class": (211, 3589, 4136),
            "component": (12, 510, 549), "deployment": (12, 361, 259),
            "sequence": (233, 2260, 12687), "system_context": (12, 215, 235),
            "use_case": (219, 3706, 4518),
        })


if __name__ == "__main__":
    unittest.main()


class ClassMemberTests(unittest.TestCase):
    def test_visibility_modifiers_types_and_constructors(self):
        diagram = parse_puml('''class Sample {
 +{abstract} find(id: int): java.util.List<String>
 #{static} void save(String name)
 ~Sample()
 - names : List<String>
 +private final java.util.Map<String, Integer> counts;
 -$enabled: bool
 {static} CODE = 1
 __init__(self, n: int = 1)
}''', 'class')
        members = diagram.elements['Sample'].members
        self.assertEqual([(m.kind, m.name) for m in members], [
            ('method', 'find'), ('method', 'save'), ('method', 'Sample'),
            ('attribute', 'names'), ('attribute', 'counts'), ('attribute', 'enabled'),
            ('attribute', 'CODE'), ('method', '__init__')])
        self.assertEqual(members[0].parameters, 'id: int')
        self.assertEqual(members[0].return_type, 'java.util.List<String>')
        self.assertEqual(members[1].return_type, 'void')
        self.assertEqual(members[4].type, 'java.util.Map<String, Integer>')
        self.assertEqual(members[-1].parameters, 'self, n: int = 1')
        self.assertFalse(diagram.elements['Sample'].unparsed_members)

    def test_separators_notes_and_directive_named_members(self):
        diagram = parse_puml('''interface Service {
 -- Fields --
 ..
 == Methods ==
 __
 note right
  fakeField: int
  fakeMethod()
 end note
 note left: alsoNotAMember()
 header
 note()
 title: str
 show()
}''', 'class')
        self.assertEqual([m.name for m in diagram.elements['Service'].members],
                         ['header', 'note', 'title', 'show'])
        self.assertFalse(diagram.elements['Service'].unparsed_members)

    def test_inline_bodies_aliases_and_package_ownership(self):
        diagram = parse_puml('''package p {
 class "a.One" as C1 { +int value }
 class Two {
   {static} run()
 }
}
C1 --> p.Two''', 'class')
        self.assertEqual([m.name for m in diagram.elements['p::C1'].members], ['value'])
        self.assertEqual([m.name for m in diagram.elements['p::Two'].members], ['run'])
        self.assertEqual(diagram.relationship_count, 1)

    def test_multiple_fields_initializers_and_array_suffixes(self):
        fields = parse_members('private java.util.Map<String, Integer> names = build(1, 2), other;')
        self.assertEqual([(m.name, m.type) for m in fields],
                         [('names', 'java.util.Map<String, Integer>'),
                          ('other', 'java.util.Map<String, Integer>')])
        self.assertEqual([(m.name, m.type) for m in parse_members('String first[], second;')],
                         [('first', 'String[]'), ('second', 'String')])
        self.assertEqual(parse_members('private Thing thing = new Thing();')[0].kind, 'attribute')

    def test_explicit_field_and_method_markers(self):
        diagram = parse_puml('''class S {
 {field} callback()
 {method} reset
}''', 'class')
        self.assertEqual([m.name for m in diagram.elements['S'].members], ['reset'])
        self.assertEqual(diagram.elements['S'].members[0].kind, 'method')
        self.assertEqual(diagram.elements['S'].unparsed_members, ['{field} callback()'])

    def test_object_shaped_types_do_not_split_member_bodies(self):
        diagram = parse_puml('''class Store {
 +send(opts: { name: string; meta: { id: int; } }): Promise<{ ok: boolean; }>
 +state: { ready: boolean; }
 +close(): void
}
class Other
Store --> Other''', 'class')
        members = diagram.elements['Store'].members
        self.assertEqual([m.name for m in members], ['send', 'state', 'close'])
        self.assertEqual(members[0].parameters, 'opts: { name: string; meta: { id: int; } }')
        self.assertEqual(members[0].return_type, 'Promise<{ ok: boolean; }>')
        self.assertEqual(members[1].type, '{ ready: boolean; }')
        self.assertFalse(diagram.elements['Store'].unparsed_members)
        self.assertEqual(diagram.relationship_count, 1)

    def test_member_body_after_multiline_name_or_alias(self):
        diagram = parse_puml('''class
"First" {
 +one()
}
class Second
as S {
 +two()
}''', 'class')
        self.assertEqual([m.name for m in diagram.elements['First'].members], ['one'])
        self.assertEqual([m.name for m in diagram.elements['S'].members], ['two'])


class HandValidatedMemberSamples(unittest.TestCase):
    """Counts read manually from five complete, existing diagrams.

    Counts include listed overloads; the evaluator separately collapses their
    names. Empty stubs are retained. Parameter/return object types are one member,
    and notes and separators are never members.
    """

    def test_five_complete_member_inventories(self):
        samples = {
            'uml-study-data-main/Java/shopping-cart/class/class_utility_layer.puml': {
                'DBUtil': (1, 5), 'JavaMailUtil': (0, 4), 'MailMessage': (0, 5),
                'IDUtil': (0, 2), 'TestMail': (0, 1), 'Connection': (0, 0),
                'ResultSet': (0, 0), 'PreparedStatement': (0, 0), 'Session': (0, 0),
                'Transport': (0, 0), 'MimeMessage': (0, 0), 'ResourceBundle': (0, 0),
                'SimpleDateFormat': (0, 0), 'Date': (0, 0)},
            'uml-study-data-main/PHP/composer/class/class_package_archiver.puml': {
                'FilterIterator': (0, 1), 'Finder': (0, 0), 'DownloadManager': (0, 0),
                'Loop': (0, 0), 'CompletePackageInterface': (0, 2), 'ArchiverInterface': (0, 2),
                'ArchiveManager': (4, 9), 'PharArchiver': (2, 2), 'ZipArchiver': (1, 3),
                'ArchivableFilesFinder': (1, 2), 'ArchivableFilesFilter': (1, 2),
                'BaseExcludeFilter': (2, 5), 'ComposerExcludeFilter': (0, 1), 'GitExcludeFilter': (0, 2)},
            'data/v2-evolver/runs/single_view/rep1/class/class_diagram.puml': {
                'EvoMapProxy': (5, 5), 'MailboxStore': (0, 5), 'LifecycleManager': (0, 5),
                'AuthError': (0, 1), 'SyncEngine': (2, 8), 'InboundSync': (0, 3),
                'OutboundSync': (0, 2), 'TaskMonitor': (0, 5), 'SkillUpdater': (0, 4),
                'DmHandler': (0, 5), 'SessionHandler': (0, 5), 'ProxyHttpServer': (0, 5),
                'WebUiServer': (0, 4), 'CycleTimeoutError': (0, 1)},
            'data/v2-composer/runs/single_view/rep1/class/class_diagram.puml': {
                'PackageInterface': (0, 5), 'CompletePackageInterface': (0, 5),
                'RootPackageInterface': (0, 5), 'BasePackage': (5, 5), 'Package': (5, 5),
                'AliasPackage': (5, 5), 'CompletePackage': (5, 5), 'CompleteAliasPackage': (1, 5),
                'RepositoryInterface': (0, 0), 'ConfigurableRepositoryInterface': (0, 0),
                'AdvisoryProviderInterface': (0, 0), 'FilterListProviderInterface': (0, 0),
                'ArrayRepository': (2, 5), 'ComposerRepository': (5, 5), 'VcsRepository': (5, 5),
                'VcsDriverInterface': (0, 0), 'VcsDriver': (5, 5), 'GitLabDriver': (5, 5),
                'GitHubDriver': (5, 5), 'DownloaderInterface': (0, 0), 'ChangeReportInterface': (0, 0),
                'VcsCapableDownloaderInterface': (0, 0), 'VcsDownloader': (5, 5), 'FileDownloader': (5, 5),
                'InstallationManager': (5, 5), 'Installer': (5, 5), 'BaseCommand': (2, 5),
                'ShowCommand': (3, 5), 'ConsoleIO': (5, 5), 'PluginManager': (5, 5),
                'Event': (4, 5), 'Perforce': (5, 5), 'Filesystem': (1, 5)},
            'data/v2-evolver/reference/class/classes.puml': {
                'CycleTimeoutError': (0, 0), 'DmHandler': (0, 4), 'SessionHandler': (0, 9),
                'SkillUpdater': (0, 3), 'EvoMapProxy': (1, 4), 'AuthError': (0, 0),
                'LifecycleManager': (2, 10), 'MailboxStore': (0, 25), 'ProxyHttpServer': (0, 4),
                'SyncEngine': (0, 7), 'InboundSync': (0, 2), 'OutboundSync': (0, 1),
                'TaskMonitor': (1, 9), 'WebUiServer': (0, 3)},
        }
        root = Path(__file__).resolve().parents[1]
        for relative, expected in samples.items():
            with self.subTest(file=relative):
                diagram = parse_puml((root / relative).read_text(), 'class')
                actual = {e.name: (sum(m.kind == 'attribute' for m in e.members),
                                   sum(m.kind == 'method' for m in e.members))
                          for e in diagram.elements.values()}
                self.assertEqual(actual, expected)
                self.assertFalse([line for e in diagram.elements.values() for line in e.unparsed_members])


class DirectiveKeywordsAsMembersTests(unittest.TestCase):
    def test_member_named_like_a_directive_does_not_swallow_the_diagram(self):
        from app.evaluation.puml_parser import parse_puml
        text = ('class "a.S" as C1 {\n  header\n  note()\n  title\n  show()\n}\n'
                'class "a.T" as C2 {\n}\nC1 --|> C2\n')
        d = parse_puml(text, "class")
        self.assertEqual(len(d.relationships), 1)
        self.assertEqual(sum(r.is_generalization for r in d.relationships), 1)

    def test_note_inside_package_is_still_a_note(self):
        from app.evaluation.puml_parser import parse_puml
        text = 'package P {\n  note "x" as N1\n  [A] --> [B]\n}\n'
        d = parse_puml(text, "component")
        self.assertNotIn("N1", " ".join(d.elements))


def test_bare_keyword_member_is_not_joined_to_next_member():
    d = parse_puml('@startuml\nclass "m.Vars" as C1 {\n  control\n  controlPath\n  queue\n  rotations\n}\n@enduml', 'class')
    members = [(m.name, m.type) for m in d.elements['C1'].members]
    assert members == [('control', ''), ('controlPath', ''), ('queue', ''), ('rotations', '')]
