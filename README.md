# LLM Remark Generator for Anki

An Anki add-on that generates explanations for selected Browser notes or the
current review card with an
OpenAI-compatible LLM. It can combine multiple source fields, optionally use web
search through Brave and Tavily, optionally search a local document directory,
and write the final explanation into a target field such as `Remark`.

## Behavior

- Run from the Anki Browser on selected notes.
- Append a new LLM remark from the card review page with the `Append LLM Remark`
  button without blocking further review.
- Stop an in-progress Browser batch from the progress dialog.
- Configure core settings with the add-on's graphical configuration dialog.
- Configure mappings by note type, for example `Question + Options + Answer -> Remark`.
- Skip a Browser-selected note before any search or LLM call when the target
  field already has content.
- The review-page append button does not check whether the target field already
  has content; it appends the new LLM result to the field.
- Let the LLM request local document search or web search during generation when needed.
- Extract local documents manually from the configuration dialog, then use the
  existing index during generation.
- Write concise HTML into the configured target field.
- Optionally combine final explanation generation for multiple notes into one LLM request.

## Configuration

Open the add-on config dialog in Anki and set:

- `llm.base_url`, `llm.api_key`, `llm.api_type`, `llm.model`
- `search.providers`, `search.brave_api_key`, `search.tavily_api_key`
- `documents.enabled`, `documents.directory`, `documents.converter_path`
- `mappings`, keyed by exact Anki note type name
- `batch.enabled` and its safety limits if you want combined final generation requests

The model field can be typed manually or selected after clicking `Fetch models`,
which calls the OpenAI-compatible `GET /v1/models` endpoint for the configured
base URL and API key.

`llm.api_type` defaults to `completion`, which uses the OpenAI-compatible
`/chat/completions` endpoint. Set it to `response` to use OpenAI's `/responses`
endpoint.

Local document search is disabled by default. To use it, download the converter
executable for your platform from the GitHub release, set
`documents.converter_path` to that executable, set `documents.directory` to your
reference document folder, click `Extract documents`, then enable
`documents.enabled`. Extraction writes Markdown/text into
`<documents.directory>/.llm_remark_index`. Generation uses the existing index and
does not call the converter. Plain `.txt` and `.md` files are synchronized
lightly during generation. The converter keeps source filenames in the generated
output names.

Example mapping:

```json
{
  "mappings": {
    "Choice": {
      "source_fields": ["Question", "Options", "Answer"],
      "target_field": "Remark"
    }
  }
}
```

Batch generation is disabled by default. When enabled, the add-on still checks
each note separately for mapping, existing target content, missing fields, and
search needs. Only the final explanation request is combined. The batch response
must return valid JSON with exactly one HTML result for each requested `note_id`;
otherwise the add-on either falls back to single-note generation or marks the
batch failed, depending on `batch.fallback_to_single_on_error`.

## Development Verification

The core modules are written so they can be tested without Anki installed:

```bash
uv run python -m unittest discover -s tests
```

## Packaging

Build an Anki add-on package:

```bash
uv run python package_addon.py
```

The package is written to `dist/llm_remark_generator.ankiaddon`.
Release builds also publish standalone converter executables:

- `llm-document-converter`
- `llm-document-converter.exe`

## Release

GitHub Actions builds and publishes the package when a `v*` tag is pushed. The
same workflow can also be run manually with a release tag.
