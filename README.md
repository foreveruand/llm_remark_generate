# LLM Remark Generator for Anki

An Anki add-on that generates explanations for selected Browser notes with an
OpenAI-compatible LLM. It can combine multiple source fields, optionally use web
search through Brave and Tavily, and write the final explanation into a target
field such as `Remark`.

## Behavior

- Run from the Anki Browser on selected notes.
- Stop an in-progress batch from the progress dialog.
- Configure core settings with the add-on's graphical configuration dialog.
- Configure mappings by note type, for example `Question + Options + Answer -> Remark`.
- Skip a note before any search or LLM call when the target field already has content.
- Ask the LLM whether web search is needed, then call enabled providers when needed.
- Write concise HTML into the configured target field.
- Optionally combine final explanation generation for multiple notes into one LLM request.

## Configuration

Open the add-on config dialog in Anki and set:

- `llm.base_url`, `llm.api_key`, `llm.model`
- `search.providers`, `search.brave_api_key`, `search.tavily_api_key`
- `mappings`, keyed by exact Anki note type name
- `batch.enabled` and its safety limits if you want combined final generation requests

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

## Release

GitHub Actions builds and publishes the package when a `v*` tag is pushed. The
same workflow can also be run manually with a release tag.
