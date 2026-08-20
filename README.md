# Resume Tailor

Resume Tailor is a cross-platform CLI that turns a Markdown, DOCX, or text-based PDF master CV and job
description into a source-grounded, ATS-friendly PDF resume. It improves keyword
alignment and clarity; it cannot guarantee an interview or hiring outcome.

Every generated summary sentence and bullet is tied to master-CV evidence. The final
artifact directory contains that mapping, plus the `.tex`, PDF, and validation reports.

## Requirements

- Python 3.11–3.14
- One local TeX engine: Tectonic, XeLaTeX, or pdfLaTeX
- An API key only when using a remote provider (not required for `doctor` or tests)

## Install

Install directly from this GitHub repository (the package is not published to PyPI):

```bash
pipx install --force "git+https://github.com/aritra0309/resume_builder.git"
resume-tailor --version
resume-tailor doctor
```

Markdown and text-PDF ingestion are included in the base package. To ingest DOCX files, install the
optional extra instead:

```bash
pipx install --force "resume-tailor[docx] @ git+https://github.com/aritra0309/resume_builder.git"
# or: uv tool install --from "git+https://github.com/aritra0309/resume_builder.git" resume-tailor
```

Or with uv:

```bash
uv tool install --from "git+https://github.com/aritra0309/resume_builder.git" resume-tailor
resume-tailor doctor
```

For development, create a virtual environment and install the development extra:

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
pytest
```

## TeX setup

Run `resume-tailor doctor` after installation. It checks the output path and reports
the first supported engine it finds.

- **Tectonic:** install through your platform package manager or Conda, then ensure
  `tectonic` is on `PATH`.
- **TeX Live:** install the distribution and ensure `xelatex` or `pdflatex` is on
  `PATH`. On macOS, MacTeX provides both; on Windows, MiKTeX or TeX Live work.

If compilation fails, the command reports a concise suggestion. `--debug` includes a
redacted traceback. Do not put API keys in debug output or issue reports.

## Configure a provider

DeepSeek is the default provider. Set its key outside the repository:

```bash
export DEEPSEEK_API_KEY='…' # PowerShell: $env:DEEPSEEK_API_KEY = '…'
resume-tailor auth status deepseek
```

Alternatively, store a key in your operating-system keyring with
`resume-tailor auth set deepseek`; input is hidden. Keys are never written to
`config.toml`, reports, manifests, or command output. Do not use API-key flags.

Any LiteLLM-supported registered provider can be selected. For example, a generic
OpenAI-compatible endpoint uses the `custom` provider and an explicit model/base URL:

```bash
resume-tailor generate --provider custom --model vendor/model --api-base https://api.example.com/v1
```

Supply credentials through that provider's environment variable or keyring when it
requires them. `resume-tailor auth status PROVIDER` reveals only the credential source.

## Generate a resume

`--master-cv` accepts `.md`, `.markdown`, `.docx`, and text-based `.pdf` inputs. DOCX inputs need the
`docx` extra. Image-only PDFs are deliberately rejected: Resume Tailor does not run OCR, because OCR
can change facts and anchors. Export a searchable PDF, DOCX, or Markdown instead.

Start the guided wizard and follow its prompts for CV, JD source, provider, model,
hidden key entry, page target, output, and TeX engine:

```bash
resume-tailor generate
```

For automation, pass every required input and prevent prompts:

```bash
resume-tailor generate --non-interactive \
  --master-cv Master_CV.md \
  --jd job-description.txt \
  --provider deepseek \
  --model deepseek-v4-flash \
  --output-dir output \
  --pages 1 \
  --tex-engine auto
```

Use exactly one JD input: `--jd FILE`, `--jd-text TEXT`, or `--jd-stdin`. The wizard's
paste mode ends when `END` appears by itself on a line. Very short JDs require the
explicit `--allow-weak-jd` override.

Each success creates a unique `output/run-<uuid>/` directory:

- `resume.pdf` and `resume.tex`
- `resume.json` — grounded content plan
- `validation.json` and `validation.md` — machine/human validation, with source lines
- `run.json` — hashes, provider/model, compiler, retries, and usage; never secrets

Runs are staged privately and atomically published only after validation passes; an
existing successful run is never overwritten.

### Review and resume

Interactive generation requires review by default. The tool shows every generated claim, its source
evidence, and a word-level diff; approve, edit, restore, reject, or defer it. Ingestion warnings (for
example an ambiguous PDF layout) must be acknowledged before provider text is sent.

Review drafts are local, hash-bound checkpoints containing the evidence needed to resume and the
reviewed decisions. Keep them private. These commands work without provider credentials:

```bash
resume-tailor review status review.json
resume-tailor review resume review.json
resume-tailor review export review.json --format markdown > reviewed-plan.md
resume-tailor review invalidate review.json
```

For automation, `--non-interactive` never prompts. It requires an explicit `--review disabled`, or a
matching approved `--review-file` with `--review required`/`optional`; it never silently skips approval.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| No TeX engine found | Install Tectonic or TeX Live, add it to `PATH`, then rerun `doctor`. |
| Missing TeX package or font | Install the package/font indicated by the compiler message, or use the fixed default template and pdfLaTeX. |
| Provider authentication error | Set the provider environment variable or run `auth set PROVIDER`; verify with `auth status`. |
| Invalid model / endpoint | Check `--model` and `--api-base`; custom endpoints need a LiteLLM-compatible model name. |
| PDF has too many pages | Use `--pages 2` or shorten low-priority content in the master CV. |
| DOCX support is not installed | Reinstall with `pip install 'resume-tailor[docx]'`, then rerun `doctor`. |
| PDF has no extractable text | Resume Tailor does not perform OCR. Export a searchable PDF, DOCX, or Markdown. |
| PDF/DOCX warning requires acknowledgement | Inspect the reported source blocks, correct the document if needed, then explicitly acknowledge during review. |
| Review was interrupted | Use `resume-tailor review status DRAFT`, then `review resume DRAFT`; the same source, JD, and plan hashes are required. |
| JD rejected as weak | Provide a fuller description or consciously pass `--allow-weak-jd`. |
| Need more diagnostic detail | Re-run the same command with `--debug`; redaction remains enabled. |

## Configuration and safety

`resume-tailor config path` prints the platform-specific `config.toml` location.
Configuration precedence is CLI flags, `RESUME_TAILOR_*` environment variables, user
TOML, then defaults. Supported settings include `PROVIDER`, `MODEL`, `MASTER_CV`,
`OUTPUT_DIR`, `PAGES`, and `TEX_ENGINE`; secrets are intentionally not settings.

The tool rejects unsupported claims, prompt injection instructions embedded in a JD,
unsafe LaTeX commands, unsafe DOCX containers (macros, embedded objects, traversal), and PDFs that
cannot be validated. It never executes document actions, fetches remote document content, or logs the
complete extracted CV/review edits by default. Keep CVs and review files in a private directory, and
review the evidence mapping before submitting any resume.
