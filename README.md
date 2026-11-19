# mini-vm

`mini` is a small, statically scoped scripting language. A hand-written lexer, a recursive-descent parser and a single-pass AST compiler turn it into bytecode for a stack-based VM.

It has:
- closures and first-class functions
- `while`/`for` loops with `break` and `continue`
- lists and strings
- runtime errors with a stack trace

It needs only the Python 3.10+ standard library.

```bash
python -m minivm                          # REPL
python -m minivm examples/closures.mini   # run a script
python -m minivm examples/fib.mini --dis  # print bytecode
python -m unittest discover -s tests -t . # 42 tests
```

## The language

```js
fn make_counter() {
  let n = 0;
  return fn() { n = n + 1; return n; };    // captures n by reference
}
let a = make_counter();
a(); a();
print(a());                                 // 3

let xs = [1, 2, 3];
for (let i = 0; i < len(xs); i = i + 1) {
  if (xs[i] == 2) continue;
  print(xs[i] * 10);
}
```

- **Values:** `nil`, `true`/`false`, numbers (ints stay ints; `/` gives a float only when the division isn't exact), strings, lists, functions.
- **Truthiness:** only `nil` and `false` are falsy. `0` and `""` are truthy.
- **Equality:** `1 == 1.0` is true. `true == 1` is false. Lists and functions compare by identity.
- **Operators:** `and`/`or` (also `&&`/`||`) short-circuit and return the deciding operand, so `nil or 5` gives `5`.
- **Built-ins:** `print(...)`, `len`, `str`, `type`, `push`, `pop`, `assert(cond, msg)`, `floor`, `clock`.
- **`fn name() {}`** is sugar for `let name = fn() {}`. The name is bound before the body is compiled, so local functions can recurse.

### Scoping

Every name is resolved at compile time to one of three things:

| kind | where it lives | instruction |
|---|---|---|
| local | a slot in the current call frame | `GET_LOCAL slot` |
| upvalue | a variable from an enclosing function, captured by a closure | `GET_UPVAL index` |
| global | a dictionary keyed by name, bound late | `GET_GLOBAL name` |

Scoping is lexical. A function sees the variables around its *definition*, not around its *call site*; `test_static_scoping_not_dynamic` checks this. Redeclaring a name in the same block is a compile error, and so is reading a variable inside its own initializer. Shadowing in an inner block is allowed.

## How closures work

This is the upvalue model from Lua and clox. While a captured variable is still on the stack, the closure's `Upvalue` points at its stack slot. That makes sibling closures share one variable, and it makes writes visible to the enclosing function. When the variable's scope ends, the compiler emits `CLOSE_UPVAL` instead of `POP`. The VM then copies the value into the `Upvalue` object, and every closure holding it keeps working.

`break` and `continue` leave scopes early, so they emit the same `POP`/`CLOSE_UPVAL` sequence for every local declared inside the loop before they jump. This is checked by `test_break_closes_captured_locals` and `test_stack_is_balanced`.

A `for (let i ...)` loop has **one** `i` for the whole loop, as `var` does in JavaScript. A closure that should capture each iteration's value must copy it into a variable declared inside the body (`let j = i;`). See `examples/closures.mini`.

## Instruction set

Code is a flat list of Python ints: an opcode, then its operands. Jump targets are absolute.

| opcode | operands | stack effect |
|---|---|---|
| `CONST` | k | → consts[k] |
| `NIL` `TRUE` `FALSE` | none | → value |
| `POP` | none | a → |
| `GET_LOCAL` / `SET_LOCAL` | slot | → v / v → v (assignment is an expression) |
| `GET_GLOBAL` / `SET_GLOBAL` / `DEF_GLOBAL` | name const | → v / v → v / v → |
| `GET_UPVAL` / `SET_UPVAL` | index | → v / v → v |
| `CLOSE_UPVAL` | none | hoists the top local to the heap, then pops it |
| `ADD` `SUB` `MUL` `DIV` `MOD` | none | a b → a∘b (`+` also joins strings and lists) |
| `EQ` `NE` `LT` `LE` `GT` `GE` | none | a b → bool |
| `NEG` `NOT` | none | a → result |
| `JUMP` / `LOOP` | target | none |
| `JUMP_IF_FALSE` | target | cond → (pops it) |
| `JUMP_IF_FALSE_KEEP` / `JUMP_IF_TRUE_KEEP` | target | cond → cond (used for `and`/`or`) |
| `CALL` | argc | f a1..an → result |
| `CLOSURE` | k, n, n × (is_local, index) | → closure |
| `RETURN` | none | v → (unwinds the frame and closes its upvalues) |
| `BUILD_LIST` | n | a1..an → list |
| `INDEX` / `SET_INDEX` | none | l i → v / l i v → v |
| `PRINT_RESULT` | none | REPL only: prints a non-nil top value, then pops it |

**Call frames.** Slot 0 of each frame holds the callee itself, and the arguments are slots 1..n. Calling a function pushes no extra copies of the arguments; the frame simply starts at the callee's position on the stack. Every instruction has a matching line-table entry, and the stack trace is built from those entries:

```
$ python -m minivm examples/error.mini
examples/error.mini: runtime error: bad operands for '*': string and number
  at parse_price (line 2)
  at total (line 5)
  at checkout (line 8)
  at <script> (line 9)
```

Exit codes follow sysexits: `65` for syntax errors and `70` for runtime errors. Recursion deeper than 1,000 frames reports a stack overflow instead of crashing Python.

## Performance

Measured on Windows 11, Python 3.14:

| program | instructions | time | throughput |
|---|---:|---:|---:|
| `fib(20)` | 240,804 | 0.11 s | 2.2 M instr/s |
| `fib(25)` | 2,670,638 | 1.13 s | 2.4 M instr/s |
| 1M-iteration sum loop | 15,000,010 | 5.81 s | 2.6 M instr/s |

That is roughly 50 to 100× slower than clox in C. The dispatch loop is an `if/elif` chain ordered by how often each opcode runs, with the stack's `append` and `pop` cached as locals. The next steps would be superinstructions (such as `GET_LOCAL` + `CONST` + `ADD`) or a computed jump table of bound methods.

## Limitations

- There is no garbage collector of its own; it relies on Python's reference counting. A cycle that runs through a closure's upvalues is freed by Python's cycle collector.
- No maps or records, no modules, no string formatting. `str()` and `+` cover the basics.
- No tail calls, so deep recursion hits the 1,000-frame limit.
- The REPL's continuation detection counts braces, so a `{` inside a string literal confuses it.

## Layout

```
minivm/   lexer.py · ast.py · parser.py · compiler.py · bytecode.py (ISA + disassembler) · vm.py · __main__.py (CLI/REPL)
examples/ closures · higher_order · fizzbuzz · fib · error
tests/    42 tests: lexer, parser (precedence, errors), compiler (resolution, upvalues), VM, errors, CLI
```

## License

MIT
