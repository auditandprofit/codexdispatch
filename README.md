# codexdispatch

Simple dispatcher for running the bundled Codex binary over multiple files in parallel.

## Usage

```
python dispatch.py TEMPLATE --data-dir DIR --output-dir OUT --workers N [-C WORK_DIR] [--codex-bin PATH] [--timeout SEC]
python dispatch.py TEMPLATE --tree-dirs DIR [DIR ...] --output-dir OUT --workers N [--recursive/--no-recursive] [-C WORK_DIR] [--codex-bin PATH] [--timeout SEC]
python dispatch.py TEMPLATE --file-list LIST --output-dir OUT --workers N -C WORK_DIR [--codex-bin PATH] [--timeout SEC]
```

- `TEMPLATE` - path to the prompt template.
- `--data-dir` - directory containing input files (flat mode).
- `--tree-dirs` - one or more directories to recursively walk (tree mode).
- `--file-list` - text file containing paths to process, one per line (file-list mode).
- `--output-dir` - directory where results will be written.
- `--workers` - number of parallel workers.
- `-C`, `--work-dir` - working directory to execute Codex in. Defaults to the current directory. In file-list mode this directory is used for all files; in tree mode the default is each file's parent.
- `--recursive` / `--no-recursive` - control whether tree mode walks subdirectories (default: recursive).
- `--codex-bin` - path to the codex binary. If not provided, the script looks for
  `codex` in `PATH` and then searches the current directory.
- `--timeout` - per-file wall clock limit in seconds (default 900). Set to `0`
  to disable; can also be specified via `CODEX_DISPATCH_TIMEOUT` environment
  variable.

In flat mode, each file in `DATA_DIR` is appended to the template and sent to Codex. In tree mode every file discovered under `--tree-dirs` is processed the same way, with its parent directory used as the working directory. Results mirror the source tree: a file `src/example.txt` will produce `OUTPUT_DIR/src/example.txt-codex`. This avoids collisions when different directories contain files with the same name.

File-list mode reads paths from a text file and processes exactly those files. Each prompt contains the full resolved path on a separate line before its contents. The working directory provided via `-C` is used for all Codex executions, which can be helpful when a shared virtual environment or imports are needed.

With `--output-dir` and `--workers` provided as options, arguments can be specified in any order.

## Security audit mode

Running with `--security-audit` processes files in a BFS search. The generic template at `prompts/security_audit_generic.txt` is always used as the system prompt. Provide a goal for the audit using the mandatory `--audit-focus` option. The focus text is appended after the template for each evaluation.
