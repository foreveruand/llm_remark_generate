# LLM Remark Generator for Anki

An Anki add-on that generates explanations for selected Browser notes with an
OpenAI-compatible LLM. It can combine multiple source fields, optionally use web
search through Brave and Tavily, and write the final explanation into a target
field such as `Remark`.

## Behavior

- Run from the Anki Browser on selected notes.
- Configure mappings by note type, for example `Question + Options + Answer -> Remark`.
- Skip a note before any search or LLM call when the target field already has content.
- Ask the LLM whether web search is needed, then call enabled providers when needed.
- Write concise HTML into the configured target field.

## Configuration

Edit the add-on config in Anki and set:

- `llm.base_url`, `llm.api_key`, `llm.model`
- `search.providers`, `search.brave_api_key`, `search.tavily_api_key`
- `mappings`, keyed by exact Anki note type name

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
