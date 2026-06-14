# Moshpit Orchestrator

Moshpit Orchestrator is a local, privacy-first Apple Music playlist generator. It processes multi-modal assets — such as
event webpage URLs, concert posters, or plain text files—using local OpenAI-compatible LLMs (such as Ollama, oMLX, LM
Studio, or vLLM) to extract artist lineups, and then automates Apple Music via macOS JavaScript for Automation (JXA) to
compile top tracks into a playlist.

## Prerequisites

- **Operating System**: macOS (Darwin) is required to run JXA osascript automation.
- **Apple Music**: An active Apple Music subscription is required to search the global shared catalog.
- **Local LLM Runner**: A running instance of an OpenAI-compatible local LLM server (such as Ollama, oMLX, LM Studio, or
  vLLM).
- **Terminal Permissions**: When running the script, macOS will prompt you to grant Terminal or your IDE permission to
  control "System Events" and "Music". You must grant these automation permissions.

## Installation

We standardize on `uv` for package and environment management.

1. Install `uv` if you haven't already:
   ```bash
   brew install uv
   ```
2. Clone the repository and install dependencies:
   ```bash
   make install
   ```
   This will automatically create a virtual environment (`.venv`) and sync all packages.

## Configuration

Configure the orchestrator using environment variables (with fallback defaults). You can define these in your shell
profile or prefix your commands:

| Variable                            | Description                                         | Default                  |
|-------------------------------------|-----------------------------------------------------|--------------------------|
| `MOSHPIT_LLM_BASE_URL`              | Base URL of your local OpenAI-compatible LLM server | `http://localhost:11434` |
| `MOSHPIT_LLM_MODEL`                 | Model name to use for extraction                    | `llava`                  |
| `MOSHPIT_LLM_TIMEOUT`               | Timeout limit (in seconds) for LLM queries          | `120.0`                  |
| `MOSHPIT_DEFAULT_TRACKS_PER_ARTIST` | Default number of tracks to add per performer       | `20`                     |
| `MOSHPIT_JXA_TIMEOUT`               | Timeout (in seconds) for Apple Music Apple Events   | `30.0`                   |

> [!NOTE]
> The default base URL (`http://localhost:11434`) and model (`llava`) are set up for Ollama. The application sends
> requests to the standard `/v1/chat/completions` endpoint relative to `MOSHPIT_LLM_BASE_URL`.
> Legacy environment variables (`MOSHPIT_OLLAMA_BASE_URL`, `MOSHPIT_OLLAMA_MODEL`, `MOSHPIT_OLLAMA_TIMEOUT`) are
> supported as fallback aliases for backwards compatibility.

### Compatible Local LLM Runners & Models

You can use any local LLM runner that provides an OpenAI-compatible `/v1/chat/completions` endpoint:

#### 1. Ollama

Ollama exposes its OpenAI-compatible endpoint at port `11434`.

* **VLM (Vision-Language Models)** (for poster images & lineups):
    * **`llava`** (Default): Excellent, well-rounded vision model. Recommended for lineup poster analysis.
    * **`bakllava`**: A LLaVA-based model utilizing Mistral backing, yielding higher quality extraction on complex
      layouts.
    * **`moondream`**: A lightweight (1.6B parameter) vision model. Extremely fast and resource-efficient.
* **Text-Only Models** (for webpage HTML text):
    * **`llama3` / `llama3.1`**: Highly capable and instruction-tuned. Excellent at formatting JSON lists.
    * **`mistral`**: Great instruction following and reasoning behavior.
    * **`gemma2`**: Google's lightweight open model family, highly structured and precise.

#### 2. oMLX (MLX-compatible local runners)

oMLX provides Apple Silicon optimized local inference.

* Configure the environment:
  ```bash
  export MOSHPIT_LLM_BASE_URL="http://localhost:8000"  # or whichever port your oMLX server runs on
  export MOSHPIT_LLM_MODEL="mlx-community/Llama-3-8B-Instruct-4bit"
  ```

#### 3. LM Studio / vLLM

Both runners expose standard OpenAI compatibility.

* For LM Studio:
  ```bash
  export MOSHPIT_LLM_BASE_URL="http://localhost:1234"
  export MOSHPIT_LLM_MODEL="model-identifier-in-lm-studio"
  ```
* For vLLM:
  ```bash
  export MOSHPIT_LLM_BASE_URL="http://localhost:8000"
  export MOSHPIT_LLM_MODEL="your-deployed-vllm-model"
  ```

## Usage

Run the program using `uv run moshpit run`.

### 1. Web Scraper Ingestion

Fetches an event schedule URL, strips nav/footers/styling, clean-formats body text, and extracts artists:

```bash
uv run moshpit run "https://aftershockfestival.com/lineup" --playlist "Aftershock 2026"
```

### 2. Vision Lineup Ingestion

Reads a local poster/flyer image and runs VLM extraction:

```bash
uv run moshpit run "lineup_flyer.jpg" --playlist "Festival Poster Lineup"
```

### 3. Plain Text Ingestion

Reads a local plain text file containing a list of artists (one per line):

```bash
uv run moshpit run "artists.txt" --playlist "Favorite Artists"
```

### CLI Command Options

```bash
uv run moshpit run [OPTIONS] INPUT_PATH
```

- `INPUT_PATH`: Path to local image file, text file, or schedule HTTP/HTTPS URL.
- `-p, --playlist TEXT`: Name of the target Apple Music playlist. If omitted, the name is auto-generated from the
  filename or URL domain.
- `-t, --tracks-per-artist INTEGER`: Overrides the number of top tracks to append per artist (default: `3`).
- `--dry-run`: Extracts artists and performs Apple Music catalog checks, but does **not** modify or create any
  playlists.
- `--print-artists`: Extracts and prints the list of artists to standard output, then exits immediately. This option
  skips JXA/macOS validation checks, allowing you to test extraction on non-macOS platforms.
- `-v, --verbose`: Enables detailed `DEBUG` console log tracing.

## Developer CLI

Run Makefile helper targets for quality checks:

- **Install Dependencies**: `make install`
- **Auto-Format Code**: `make format`
- **Lint & Type-Check**: `make lint`
- **Run Pytest Coverage**: `make test`
- **Clean Cache Files**: `make clean`

## Telemetry & Unresolved Matches

If any artists extracted by the LLM are skipped (e.g. they aren't found in Apple Music's global catalog or trigger
osascript execution timeouts), they are recorded in `failure_manifest.json` under your workspace root directory. This
telemetry report allows you to adjust spelling, billing stage designations, or search bounds offline. If a subsequent
run completes with zero failures, any previous failure manifest is cleaned up automatically.
