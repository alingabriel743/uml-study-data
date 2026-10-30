import com.sun.source.tree.*;
import com.sun.source.util.JavacTask;
import java.nio.file.*;
import java.util.*;
import javax.tools.*;

/** Parse only: no project build, annotation processing, class loading or attribution.
 * Custom adapter over the established OpenJDK compiler, NOT JavaParser or a
 * third-party reverse-engineering product. Output is consumed by run.py.
 */
public class JavacReference {
    static String quote(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"")
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "\"";
    }
    static String array(List<String> s) {
        return "[" + String.join(",", s.stream().map(JavacReference::quote).toList()) + "]";
    }
    static void visit(ClassTree c, String prefix, String source, List<String> imports, List<String> out) {
        String simple = c.getSimpleName().toString();
        if (simple.isEmpty()) return; // anonymous classes have no stable UML identity
        String name = prefix.isEmpty() ? simple : prefix + "." + simple;
        List<String> attrs = new ArrayList<>(), methods = new ArrayList<>();
        List<String> parents = new ArrayList<>(), refs = new ArrayList<>();
        if (c.getExtendsClause() != null) parents.add(c.getExtendsClause().toString());
        for (Tree t : c.getImplementsClause()) parents.add(t.toString());
        for (Tree member : c.getMembers()) {
            if (member instanceof VariableTree v) {
                attrs.add(v.getName().toString());
                if (v.getType() != null) refs.add(v.getType().toString());
            } else if (member instanceof MethodTree m) {
                methods.add(m.getName().contentEquals("<init>") ? simple : m.getName().toString());
            } else if (member instanceof ClassTree nested) {
                visit(nested, name, source, imports, out);
            }
        }
        String kind = switch (c.getKind()) {
            case INTERFACE -> "interface";
            case ENUM -> "enum";
            case ANNOTATION_TYPE -> "annotation";
            case RECORD -> "record";
            default -> "class";
        };
        out.add("{\"name\":" + quote(name) + ",\"kind\":" + quote(kind)
            + ",\"source_file\":" + quote(source) + ",\"attributes\":" + array(attrs)
            + ",\"methods\":" + array(methods) + ",\"parents\":" + array(parents)
            + ",\"field_types\":" + array(refs) + ",\"imports\":" + array(imports) + "}");
    }
    public static void main(String[] args) throws Exception {
        Path root = Path.of(args[0]);
        List<Path> files;
        try (var paths = Files.walk(root)) {
            files = paths.filter(p -> p.toString().endsWith(".java")).sorted().toList();
        }
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("A full JDK is required");
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        List<String> classes = new ArrayList<>(), errors = new ArrayList<>();
        try (StandardJavaFileManager fm = compiler.getStandardFileManager(diagnostics, Locale.ROOT, null)) {
            // Separate tasks keep parser memory bounded and diagnostics attributable.
            for (Path file : files) {
                JavacTask task = (JavacTask) compiler.getTask(null, fm, diagnostics,
                    List.of("-proc:none"), null, fm.getJavaFileObjects(file));
                for (CompilationUnitTree unit : task.parse()) {
                    String pkg = unit.getPackageName() == null ? "" : unit.getPackageName().toString();
                    List<String> imports = unit.getImports().stream()
                        .filter(i -> !i.isStatic()).map(i -> i.getQualifiedIdentifier().toString()).toList();
                    for (Tree t : unit.getTypeDecls()) if (t instanceof ClassTree c)
                        visit(c, pkg, root.relativize(file).toString(), imports, classes);
                }
            }
        }
        for (Diagnostic<? extends JavaFileObject> d : diagnostics.getDiagnostics())
            if (d.getKind() == Diagnostic.Kind.ERROR) errors.add(d.toString());
        System.out.println("{\"classes\":[" + String.join(",", classes)
            + "],\"failures\":" + array(errors) + ",\"source_files\":" + files.size() + "}");
    }
}
