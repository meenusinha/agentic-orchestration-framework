# Knowledge Base

Drop your Markdown documentation files here. The RAG agent will index them automatically on first startup.

## What to put here

Any `.md` file works. Good candidates:

- Architecture overviews
- Component / module descriptions
- API or interface contracts
- Design decisions and their rationale
- Glossary of domain terms
- README extracts from important subsystems

## Tips

- **More specific = better retrieval.** A file per component is better than one giant file.
- **Plain prose beats bullet soup.** The RAG model retrieves by paragraph — write in paragraphs.
- **No need to format specially.** Standard Markdown is fine.
- `README.md` (this file) is skipped during indexing.

## Re-indexing

The index is cached in `.chroma_db/`. To force a re-index after updating docs:

```bash
rm -rf .chroma_db
```

The next MCP server startup will rebuild it.
