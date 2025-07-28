# codexdispatch

Simple dispatcher for running the bundled Codex binary over multiple files in parallel.

## Usage

```
python dispatch.py TEMPLATE DATA_DIR OUTPUT_DIR WORKERS [-C WORK_DIR] [--codex-bin PATH]
python dispatch.py TEMPLATE --tree-dirs DIR [DIR ...] OUTPUT_DIR WORKERS [--recursive/--no-recursive] [-C WORK_DIR] [--codex-bin PATH]
```

- `TEMPLATE` - path to the prompt template.
- `DATA_DIR` - directory containing input files (flat mode).
- `--tree-dirs` - one or more directories to recursively walk (tree mode).
- `OUTPUT_DIR` - directory where results will be written.
- `WORKERS` - number of parallel workers.
- `-C`, `--work-dir` - working directory to execute Codex in. Defaults to the current directory. In tree mode the work directory defaults to each file's parent.
- `--recursive` / `--no-recursive` - control whether tree mode walks subdirectories (default: recursive).
- `--codex-bin` - path to the codex binary. If not provided, the script looks for
  `codex` in `PATH` and then searches the current directory.

In flat mode, each file in `DATA_DIR` is appended to the template and sent to Codex. In tree mode every file discovered under `--tree-dirs` is processed the same way, with its parent directory used as the working directory. Results for a file named `example.txt` will be written to `OUTPUT_DIR/example.txt-codex`.
