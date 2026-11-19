"""python -m minivm [script.mini] [--dis]   (no script: REPL)"""
from __future__ import annotations

import argparse
import sys

from .bytecode import disassemble
from .compiler import CompileError, compile_source
from .lexer import LexError
from .parser import ParseError
from .vm import VM, VMError


def run_file(path: str, dis: bool) -> int:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        fn = compile_source(src)
    except (LexError, ParseError, CompileError) as e:
        print(f"{path}: syntax error: {e}", file=sys.stderr)
        return 65
    if dis:
        print(disassemble(fn))
        return 0
    try:
        VM().run_function(fn)
    except VMError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 70
    return 0


def repl() -> int:
    vm = VM()
    print("mini REPL. Ctrl-D (Ctrl-Z Enter on Windows) to exit, :dis <expr> to disassemble.")
    buf = ""
    while True:
        try:
            line = input("... " if buf else ">>> ")
        except EOFError:
            print()
            return 0
        if line.startswith(":dis "):
            try:
                print(disassemble(compile_source(line[5:], repl=True)))
            except (LexError, ParseError, CompileError) as e:
                print(f"syntax error: {e}")
            continue
        buf += line + "\n"
        if buf.count("{") > buf.count("}"):
            continue  # keep reading a multi-line block
        src = buf if buf.rstrip().endswith((";", "}")) else buf.rstrip() + ";"
        buf = ""
        try:
            vm.run_function(compile_source(src, repl=True))
        except (LexError, ParseError, CompileError) as e:
            print(f"syntax error: {e}")
        except VMError as e:
            print(e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="minivm")
    ap.add_argument("script", nargs="?")
    ap.add_argument("--dis", action="store_true", help="print bytecode instead of running")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run_file(a.script, a.dis) if a.script else repl()


if __name__ == "__main__":
    raise SystemExit(main())
