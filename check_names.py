"""Find names a module uses but never defines.

Python only raises NameError when the line actually runs, so a constant
deleted by a careless edit can sit unnoticed until the one code path that
touches it executes -- which, for the login endpoints, was only ever on a
cron run with no cached token. Two outages came from exactly that.

This walks the syntax tree instead of waiting for the line to run.
"""

import ast
import builtins
import sys


def undefined_names(path):
    tree = ast.parse(open(path).read())

    defined = {n.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
               for n in ast.walk(node.targets[0]) if isinstance(n, ast.Name)}
    defined |= {n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {a.asname or a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}

    # Anything bound by a function, a comprehension, a with or an except is
    # that construct's business, not a module-level name. Comprehensions are
    # collected across the whole tree, not just inside functions -- a generator
    # at module level binds its variable exactly the same way.
    local = set()

    def bind(node):
        for v in ast.walk(node):
            if isinstance(v, ast.Name):
                local.add(v.id)

    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            for inner in ast.walk(n):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store):
                    local.add(inner.id)
                if isinstance(inner, ast.arg):
                    local.add(inner.arg)
        if isinstance(n, ast.ExceptHandler) and n.name:
            local.add(n.name)
        if isinstance(n, ast.withitem) and n.optional_vars is not None:
            bind(n.optional_vars)
        if isinstance(n, ast.comprehension):
            bind(n.target)
        if isinstance(n, (ast.For, ast.AsyncFor)):
            bind(n.target)

    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(used - defined - local - set(dir(builtins)) - {"__file__"})


def main():
    bad = False
    for path in sys.argv[1:]:
        missing = undefined_names(path)
        if missing:
            bad = True
            print(f"  {path}: uses but never defines {', '.join(missing)}")
        else:
            print(f"  {path}: ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
