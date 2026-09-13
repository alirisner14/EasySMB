"""Catch %-format collisions before they reach a user.

This exists because of a real bug: the PowerShell command that stops the web
dashboard contains literal percent signs (LIKE '%--headless%'), and it was being
built with %-formatting. Python read '%--he' as a float conversion and the
"Turn on phone access" button died with:

    TypeError: must be real number, not str

Nothing catches that until the button is pressed - the file imports and parses
perfectly. compile.bat runs this check so the same class of bug cannot ship
again.

Usage:  python tools/check_format_strings.py [file.py ...]
Exit:   0 = clean, 1 = problems found
"""
import ast
import io
import os
import sys

VALID_CONVERSIONS = set("diouxXeEfFgGcrsa%")


def offending_specs(fmt):
    """Return the '%...' sequences in fmt that are not valid conversions."""
    bad, i = [], 0
    while i < len(fmt):
        if fmt[i] != "%":
            i += 1
            continue
        j = i + 1
        if j < len(fmt) and fmt[j] == "(":              # %(name)s
            close = fmt.find(")", j)
            if close == -1:
                bad.append(fmt[i:i + 12])
                break
            j = close + 1
        while j < len(fmt) and fmt[j] in "#0- +":        # flags
            j += 1
        while j < len(fmt) and (fmt[j].isdigit() or fmt[j] == "*"):
            j += 1
        if j < len(fmt) and fmt[j] == ".":               # precision
            j += 1
            while j < len(fmt) and (fmt[j].isdigit() or fmt[j] == "*"):
                j += 1
        while j < len(fmt) and fmt[j] in "hlL":          # length modifier
            j += 1
        if j >= len(fmt) or fmt[j] not in VALID_CONVERSIONS:
            bad.append(fmt[i:min(i + 12, len(fmt))])
        i = j + 1
    return bad


def check_file(path):
    try:
        src = io.open(path, encoding="utf-8").read()
    except Exception as e:
        print("  could not read %s: %s" % (path, e))
        return 1
    try:
        tree = ast.parse(src, path)
    except SyntaxError as e:
        print("  %s has a syntax error on line %s: %s" % (path, e.lineno, e.msg))
        return 1

    problems = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)):
            continue
        left = node.left
        if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
            continue
        bad = offending_specs(left.value)
        if not bad:
            continue
        problems += 1
        preview = " ".join(left.value.split())[:96]
        print("")
        print("  %s line %d" % (os.path.basename(path), node.lineno))
        print("    a literal %% will be read as a conversion: %s"
              % ", ".join(repr(b) for b in bad))
        print("    in: %s" % preview)
        print("    fix: double the literal percent signs, or build the string by")
        print("         concatenation instead of %-formatting.")
    return problems


def main(argv):
    targets = argv[1:]
    if not targets:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        targets = [os.path.join(here, "smb_manager_GUI.pyw")]

    total = 0
    for path in targets:
        total += check_file(path)

    if total:
        print("")
        print("  %d format-string problem(s) found." % total)
        return 1
    print("  No format-string problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
