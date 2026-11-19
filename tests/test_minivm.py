import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from minivm import ast as A
from minivm import bytecode as B
from minivm.__main__ import main
from minivm.compiler import CompileError, compile_source
from minivm.lexer import LexError, tokenize
from minivm.parser import ParseError, parse
from minivm.vm import VM, VMError, run_source

EX = Path(__file__).resolve().parent.parent / "examples"


def run(src: str) -> list[str]:
    out: list[str] = []
    run_source(src, out=out.append)
    return out


def err(src: str) -> VMError:
    try:
        run(src)
    except VMError as e:
        return e
    raise AssertionError("expected runtime error")


class LexerTests(unittest.TestCase):
    def kinds(self, src):
        return [(t.kind, t.text) for t in tokenize(src)][:-1]

    def test_tokens(self):
        self.assertEqual(self.kinds("let x = 3.5;"),
                         [("KW", "let"), ("IDENT", "x"), ("OP", "="), ("NUM", "3.5"), ("OP", ";")])

    def test_two_char_ops_and_aliases(self):
        self.assertEqual([t for _, t in self.kinds("a <= b && c || !d != e")],
                         ["a", "<=", "b", "and", "c", "or", "!", "d", "!=", "e"])

    def test_numbers(self):
        toks = tokenize("12 0.25 7.")
        self.assertEqual((toks[0].value, toks[1].value), (12, 0.25))
        self.assertEqual(toks[3].text, ".")  # "7." is the int 7 then a dot

    def test_strings_and_escapes(self):
        self.assertEqual(tokenize(r'"a\n\"b\""')[0].value, 'a\n"b"')
        with self.assertRaises(LexError):
            tokenize('"open')

    def test_comments_and_lines(self):
        toks = tokenize("// header\nlet\n  x")
        self.assertEqual((toks[0].line, toks[1].line, toks[1].col), (2, 3, 3))

    def test_bad_char(self):
        with self.assertRaises(LexError) as cm:
            tokenize("let x = 1 @ 2;")
        self.assertEqual(cm.exception.col, 11)


class ParserTests(unittest.TestCase):
    def expr(self, src):
        return parse(src + ";").body[0].expr

    def test_precedence(self):
        e = self.expr("1 + 2 * 3 == 7")
        self.assertIsInstance(e, A.Binary)
        self.assertEqual(e.op, "==")
        self.assertEqual(e.left.op, "+")
        self.assertEqual(e.left.right.op, "*")

    def test_left_assoc(self):
        e = self.expr("10 - 4 - 3")
        self.assertEqual(e.left.op, "-")
        self.assertEqual(e.right.value, 3)

    def test_assignment_right_assoc(self):
        e = self.expr("a = b = 1")
        self.assertIsInstance(e.value, A.Assign)

    def test_logical_and_unary(self):
        e = self.expr("not a or b and -c")
        self.assertIsInstance(e, A.Logical)
        self.assertEqual(e.op, "or")
        self.assertIsInstance(e.left, A.Unary)
        self.assertEqual(e.right.op, "and")

    def test_call_index_chain(self):
        e = self.expr("f(1)(2)[0]")
        self.assertIsInstance(e, A.Index)
        self.assertIsInstance(e.target, A.Call)
        self.assertIsInstance(e.target.callee, A.Call)

    def test_for_desugars_to_while(self):
        blk = parse("for (let i = 0; i < 3; i = i + 1) print(i);").body[0]
        self.assertIsInstance(blk, A.Block)
        self.assertIsInstance(blk.body[1], A.While)
        self.assertIsNotNone(blk.body[1].step)

    def test_errors(self):
        for src, msg in [("let = 1;", "variable name"), ("1 +;", "expected expression"),
                         ("(1 + 2;", "expected ')'"), ("1 = 2;", "invalid assignment"),
                         ("return 1;", "outside function"), ("break;", "outside loop"),
                         ("fn f(a, a) {}", "duplicate parameter"), ("if (x) {", "'}'")]:
            with self.assertRaises(ParseError, msg=src) as cm:
                parse(src)
            self.assertIn(msg, str(cm.exception), src)

    def test_break_inside_fn_inside_loop_rejected(self):
        with self.assertRaises(ParseError):
            parse("while (true) { let f = fn() { break; }; }")


class CompilerTests(unittest.TestCase):
    def ops(self, fn):
        out, i = [], 0
        while i < len(fn.code):
            name = B.NAMES[fn.code[i]]
            out.append(name)
            i += 3 + 2 * fn.code[i + 2] if name == "CLOSURE" else 1 + B.OPS[name]
        return out

    def test_globals_vs_locals(self):
        fn = compile_source("let g = 1; { let l = 2; l = g; }")
        ops = self.ops(fn)
        self.assertIn("DEF_GLOBAL", ops)
        self.assertIn("GET_GLOBAL", ops)
        self.assertIn("SET_LOCAL", ops)

    def test_captured_local_is_closed_not_popped(self):
        fn = compile_source("{ let x = 1; let f = fn() { return x; }; }")
        self.assertIn("CLOSE_UPVAL", self.ops(fn))
        inner = next(c for c in fn.consts if isinstance(c, B.Function))
        self.assertEqual(inner.upvalue_count, 1)
        self.assertIn("GET_UPVAL", self.ops(inner))

    def test_upvalue_through_two_levels(self):
        fn = compile_source("fn a() { let x = 1; return fn() { return fn() { return x; }; }; }")
        a = fn.consts[0] if isinstance(fn.consts[0], B.Function) else fn.consts[1]
        mid = next(c for c in a.consts if isinstance(c, B.Function))
        inner = next(c for c in mid.consts if isinstance(c, B.Function))
        self.assertEqual((mid.upvalue_count, inner.upvalue_count), (1, 1))

    def test_const_dedup_respects_types(self):
        fn = compile_source("let a = 1; let b = 1; let c = 1.0; let d = true;")
        self.assertEqual([c for c in fn.consts if c == 1], [1, 1.0])

    def test_compile_errors(self):
        with self.assertRaises(CompileError):
            compile_source("{ let x = x; }")
        with self.assertRaises(CompileError):
            compile_source("{ let x = 1; let x = 2; }")
        compile_source("{ let x = 1; { let x = 2; } }")  # shadowing in inner scope is fine

    def test_line_table(self):
        fn = compile_source("let a = 1;\n\nlet b = 2;")
        self.assertEqual(len(fn.lines), len(fn.code))
        self.assertEqual(set(fn.lines), {1, 3})

    def test_disassembler(self):
        text = B.disassemble(compile_source("fn f(x) { return x + 1; }"))
        self.assertIn("== f (arity 1", text)
        self.assertIn("ADD", text)


class VMTests(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(run("print(1 + 2 * 3, 7 / 2, 8 / 2, 7 % 3, -(2 - 5));"), ["7 3.5 4 1 3"])

    def test_strings_lists(self):
        self.assertEqual(run('let s = "ab" + "cd"; print(s, len(s), s[1]);'), ["abcd 4 b"])
        self.assertEqual(run("let l = [1, 2]; push(l, 3); l[0] = 9; print(l, len(l)); print(pop(l), l);"),
                         ["[9, 2, 3] 3", "3 [9, 2]"])

    def test_equality_and_truthiness(self):
        self.assertEqual(run("print(1 == 1.0, true == 1, nil == false, [1] == [1]);"),
                         ["true false false false"])
        self.assertEqual(run('print(!0, !nil, !"", not false);'), ["false true false true"])

    def test_short_circuit(self):
        out = run("fn boom() { print(\"called\"); return true; } print(false and boom(), true or boom());")
        self.assertEqual(out, ["false true"])
        self.assertEqual(run("print(nil or 5, 3 and 4);"), ["5 4"])

    def test_scoping(self):
        src = "let x = \"global\"; { let x = \"outer\"; { let x = \"inner\"; print(x); } print(x); } print(x);"
        self.assertEqual(run(src), ["inner", "outer", "global"])

    def test_static_scoping_not_dynamic(self):
        src = """
        let x = "global";
        fn show() { print(x); }
        fn shadow() { let x = "local"; show(); }
        shadow();
        """
        self.assertEqual(run(src), ["global"])

    def test_closure_counter(self):
        src = """
        fn counter() { let n = 0; return fn() { n = n + 1; return n; }; }
        let a = counter(); let b = counter();
        a(); a();
        print(a(), b());
        """
        self.assertEqual(run(src), ["3 1"])

    def test_closure_shared_and_closed(self):
        src = """
        fn make() { let x = 0; let inc = fn() { x = x + 1; }; let get = fn() { return x; }; return [inc, get]; }
        let p = make(); p[0](); p[0]();
        print(p[1]());
        """
        self.assertEqual(run(src), ["2"])

    def test_closure_captures_loop_body_variable(self):
        src = """
        let fs = [];
        for (let i = 0; i < 3; i = i + 1) { let j = i; push(fs, fn() { return j; }); }
        print(fs[0](), fs[1](), fs[2]());
        """
        self.assertEqual(run(src), ["0 1 2"])

    def test_recursion_local_and_global(self):
        self.assertEqual(run("fn f(n) { if (n < 2) return n; return f(n-1) + f(n-2); } print(f(15));"), ["610"])
        src = "fn outer() { fn fact(n) { if (n <= 1) return 1; return n * fact(n - 1); } return fact(10); } print(outer());"
        self.assertEqual(run(src), ["3628800"])

    def test_loops_break_continue(self):
        src = """
        let s = 0;
        for (let i = 0; i < 10; i = i + 1) { let sq = i * i; if (i == 7) break; if (i % 2 == 1) continue; s = s + sq; }
        print(s);
        """
        self.assertEqual(run(src), ["56"])  # 0 + 4 + 16 + 36

    def test_break_closes_captured_locals(self):
        src = """
        let f = nil;
        while (true) { let v = "kept"; f = fn() { return v; }; break; }
        print(f());
        """
        self.assertEqual(run(src), ["kept"])

    def test_stack_is_balanced(self):
        vm = VM(out=lambda s: None)
        run_source("for (let i = 0; i < 100; i = i + 1) { let a = [i]; if (i > 50) continue; }", vm=vm)
        self.assertEqual(vm.stack, [])
        self.assertEqual(vm.open_upvalues, {})

    def test_first_class_functions(self):
        src = "fn twice(f) { return fn(x) { return f(f(x)); }; } print(twice(fn(x) { return x * 3; })(2));"
        self.assertEqual(run(src), ["18"])


class ErrorTests(unittest.TestCase):
    def test_stack_trace(self):
        e = err("fn a() { return 1 + nil; }\nfn b() { return a(); }\nb();")
        self.assertIn("bad operands for '+': number and nil", e.msg)
        self.assertEqual(e.trace, ["  at a (line 1)", "  at b (line 2)", "  at <script> (line 3)"])

    def test_messages(self):
        cases = {
            "print(undefined_thing);": "undefined variable 'undefined_thing'",
            "y = 1;": "assignment to undefined variable 'y'",
            "fn f(a) {} f();": "f() takes 1 argument(s), got 0",
            "let x = 3; x();": "can only call functions, got number",
            "print(1 / 0);": "division by zero",
            "print([1][5]);": "index 5 out of range",
            'print("s"[0] = "x");': "strings are immutable",
            "print(-\"a\");": "unary '-'",
            "len(5);": "len(): len() expects",
            "assert(1 == 2, \"math broke\");": "math broke",
        }
        for src, msg in cases.items():
            self.assertIn(msg, err(src).msg, src)

    def test_stack_overflow(self):
        e = err("fn f() { return f(); } f();")
        self.assertIn("stack overflow", e.msg)
        self.assertLessEqual(len(e.trace), 1001)

    def test_vm_usable_after_error(self):
        vm = VM(out=lambda s: None)
        with self.assertRaises(VMError):
            run_source("let a = 1; a();", vm=vm)
        out = []
        vm.out = out.append
        run_source("print(a + 1);", vm=vm)
        self.assertEqual(out, ["2"])


class CliTests(unittest.TestCase):
    def capture(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_examples_run(self):
        expected = {"closures": "a: 3 b: 1", "higher_order": "sum: 220",
                    "fizzbuzz": "sum of odd numbers below 100: 2500", "fib": "fib(25) = 75025"}
        for name, text in expected.items():
            code, out = self.capture(str(EX / f"{name}.mini"))
            self.assertEqual(code, 0, name)
            self.assertIn(text, out, name)

    def test_error_example_exit_code(self):
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()) as err_buf:
            code, _ = self.capture(str(EX / "error.mini"))
        self.assertEqual(code, 70)
        self.assertIn("at parse_price (line 2)", err_buf.getvalue())

    def test_dis_flag(self):
        code, out = self.capture(str(EX / "fib.mini"), "--dis")
        self.assertEqual(code, 0)
        self.assertIn("== fib (arity 1", out)


if __name__ == "__main__":
    unittest.main()
