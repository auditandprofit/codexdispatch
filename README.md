# codexdispatch

Simple dispatcher for running the bundled Codex binary over multiple files in parallel.

## Usage

```
python dispatch.py TEMPLATE DATA_DIR OUTPUT_DIR WORKERS [-C WORK_DIR] [--codex-bin PATH]
```

- `TEMPLATE` - path to the prompt template.
- `DATA_DIR` - directory containing input files.
- `OUTPUT_DIR` - directory where results will be written.
- `WORKERS` - number of parallel workers.
- `-C`, `--work-dir` - working directory to execute Codex in. Defaults to the current directory.
- `--codex-bin` - path to the codex binary. If not provided, the script looks for
  `codex` in `PATH` and then searches the current directory.

Each file in `DATA_DIR` is appended to the template and sent to Codex. The result for a file named `example.txt` will be written to `OUTPUT_DIR/example.txt-codex`.
