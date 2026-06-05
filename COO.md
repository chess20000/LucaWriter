# COO Export and cooverter

LucaWriter exports `.coo` files for Coobox. A `.coo` file is a ZIP package with
one book, book-related AI data, and signed provenance metadata.

## Provenance

Every compliant COO writer appends a signed event to:

- `META-INF/coo-history.jsonl`
- `META-INF/coo-keys.json`

Each event records:

- user name
- client name/version/id
- changed file hashes
- previous event hash
- Ed25519 signature

The latest event must match the current package payload. If any chapter, cover,
manifest, AI database, or vector DB file is changed without a new valid event,
`cooverter expose` and Coobox will report a failed tamper check.

## cooverter

`cooverter` is a small command-line converter that reuses LucaWriter's import
parsers.

```bash
python cooverter.py path/to/book.epub
python cooverter.py path/to/book.txt
python cooverter.py expose path/to/book.coo
```

On Windows:

```bat
cooverter.bat path\to\book.epub
cooverter.bat expose path\to\book.coo
```

`cooverter <path>` writes the `.coo` file next to the source file. Converted
books carry `Cooverter` as the pen name, `cooverter` as the provenance client
name, and a persistent `cooverter_...` client id stored under the user's home
directory.
