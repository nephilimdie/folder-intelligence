# 🤖 folderIntelligence

*English · [Italiano](README.it.md)*

Talk to your folders: find duplicate files, **understand the content** of documents, PDFs
(scanned ones too), images, Office files, audio and video, search them by meaning and organize
them. Everything runs with local AI (llama.cpp inside the app and/or Ollama): no data leaves your Mac.

## Installation

```bash
cd ~/Progetti/folder-intelligence
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # or: .venv/bin/pip install -r requirements.txt
```

Local AI (optional, recommended):

```bash
pip install llama-cpp-python # in-app engine for text and search (see "AI engines")
ollama pull qwen2.5:14b      # download the models (llama.cpp reuses the same files)
ollama pull bge-m3
ollama pull qwen3.5:9b       # images: needs `ollama serve` running
```

Models used (all optional, configurable with `--model`, `--vision-model`, `--embed-model`):

| Role | Default | Used for |
|---|---|---|
| text | `qwen2.5:14b` | category, description, tags, answers to questions, request router |
| vision | `qwen3.5:9b` | describing photos, screenshots, scans, video frames |
| embeddings | `bge-m3` | search by meaning (multilingual) |

System tools used when present: `tesseract` (OCR), `pdftotext`/`pdftoppm` (poppler),
`ffprobe`/`ffmpeg`, `sips`, `textutil`, `mdls`/`mdfind` (Spotlight).
Without AI everything still works: rules, OCR and text search are used instead.

## Languages

The interface, the answers and the AI-generated content (titles, descriptions, tags) are in
**English** or **Italian**. The language follows macOS; change it with `--lang en|it` or `FI_LANG`.
Commands can be written in either language, and search also finds documents written in the other
one (bge-m3 embeddings are multilingual and queries are expanded in English and Italian).
The API picks the language per request from the `Accept-Language` header (or `?lang=`).

Translations live in `src/folder_intelligence/locales/<language>.json` (gettext style: the Italian
text in the code is the key, e.g. `_("Nessun file duplicato in {root}.", root=…)`). To add a
language, copy `en.json`, translate the values and add the code to `SUPPORTED` in `i18n.py`;
`tests/test_i18n.py` reports missing texts, mismatched placeholders and stale translations
(`python -m folder_intelligence.i18n_extract --missing <language>` lists what is missing).

## Usage

### Interactive chat

```bash
./start_chat.sh                          # chat on ~/Downloads
.venv/bin/python __main__.py ~/Desktop   # chat on another folder
folder-intelligence ~/Desktop            # if installed with pip install -e .
```

```
You › find duplicates
Found 3 groups of duplicates in ~/Downloads (4 copies, 12.3 MB recoverable):
[1] holiday.jpg — 4.1 MB × 2 (images)
    ✅ keep:   holiday.jpg
    🚮 copy:   holiday copy.jpg
You › delete the duplicates
I will move 4 copies (12.3 MB) to the Trash … Confirm? (yes/no)
You › yes
✅ 4 files moved to the Trash.
You › undo the last operation
🔙 Operation 3f2a9c: restored 4 files to their original location.
```

The chat has **panels**: the conversation on the left, the **Activity** box on the right (below,
on narrow terminals). Every request becomes a task: if it takes more than ~1.5 s it moves to the
**background** and you can keep typing; the box shows its phase, progress, current file and time.
When it finishes, the result appears in the conversation (with a confirmation selector if needed).
`activity` lists running tasks, `stop 3` stops #3, Ctrl+C stops the latest, Ctrl+D or `exit` quits
(asking first if tasks are running). `--classic` starts the simple chat without panels.

Under the Activity box there is the **📅 Agenda**: the things to do you add from the chat
("remind me to pay the rent tomorrow", "add to my agenda renew the passport on Dec 20"), ordered by
date — overdue in red, today highlighted, then upcoming ones, then those without a date; the last
ones done appear greyed out. Every request to be reminded goes into the agenda (only statements like
"remember that …" stay notes); **without a date it is due in 6 hours**. Dates and times are understood
in English and Italian: today/tomorrow, weekdays, "in 3 days", "Nov 20", "20/12" (day/month),
"2026-12-20", "at 3pm", "at 15:30". Each item also becomes an **event in the macOS Calendar**
(calendar "folderIntelligence", created on first use; 30 minutes with an alert 15 minutes before,
or all-day without a time; "done" marks it ✓, "delete reminder" removes it). macOS asks once for
permission (Privacy & Security → Automation). `FI_CALENDAR=<name>` picks another calendar,
`FI_CALENDAR=off` disables it. "done 2" marks #2 as done, "reopen 2"
reopens it, "delete reminder 2" removes it. Stored encrypted (`agenda.enc`); API: `GET/POST /agenda`,
`PATCH/DELETE /agenda/{id}`.

**Search results** go into **boxes in the right column**, under Activity and Agenda, without covering
the chat: every search adds a box and the previous ones stay (the last 5). The active box is expanded
(the 3 most relevant first, then grouped by folder, with a preview of the matching passage), the others
show their first lines. **F4** moves from the chat into the boxes, **Tab/⇧Tab** switch box, **Esc**
goes back to the chat, **x** closes a box. **Mouse**: click a box or a file to select it, double-click opens it, the wheel scrolls. A **💡 Useful commands** box in the bottom right shows the keys and phrases that make sense right now. Inside a box: Enter opens the file, space shows it in
**Quick Look**, `f` reveals it in Finder, `c` copies the path, `?` asks about that file, `e` puts the
results in Finder, `s` saves the search, `/` types a filter (word filters act on the selected box). **Refine by talking**: "only the pdfs", "from 2024", "exclude those in Downloads",
"sort by date", "show all". **Put the results in Finder**: "put the results in Finder" / "tag these
results as Bills" adds a **Finder tag** (no file moved; default) or creates a **folder of aliases**
(choose in the settings, folder `~/Risultati`). That also saves the search: new matching files are added
by the background service. "save this search as Trips", "saved searches", "open the search Trips",
"recent searches", "remove the tag Bills". API: `GET /results/search` (filters), `POST /results/finder`,
`GET/POST /searches`, `POST /searches/{name}/run`, `DELETE /searches/{name}`.

The **🌙 Automatic indexing** box in the right column shows the background service (off / paused / working, with progress), for each watched folder how many files are already understood by the AI (bar, % and count), and the latest runs. While the chat is open it holds the index, so the service pauses: instead the chat itself does the same work when nothing is running and you have not typed for a minute (same conditions: enabled, on power, Mac not busy), and stops after the current file as soon as you type.

**Settings** (`settings`, or F3): the folders to index automatically in the background, when to do it
(folders are chosen in the native macOS "Choose folder" window, starting from home; idle for 2–30 minutes, only when plugged in, only when the Mac isn't doing anything else — measured
on the CPU load average), the summary model, Calendar events, language and MCP memory. Turning on
"Index in the background" installs the LaunchAgent; every change is saved immediately (encrypted
`settings.enc`). "index Documents automatically" adds a folder straight from the chat. The service
then scans the watched folders by itself: new or changed files (also from Finder) become searchable,
deleted ones leave the index, and the AI understanding follows. API: `GET/PUT /settings`,
`POST/DELETE /settings/folders`, `POST /settings/background`. `FI_*` environment variables always win.

To read back: **mouse wheel/trackpad**, PgUp/PgDn or ⇧↑/⇧↓; Esc or End jump to the bottom. If
results arrive while you are reading further up, the view stays put and "● new messages" appears.
Since the app captures the mouse for the wheel, press **Ctrl+T** (or F2) to **select text**: the
mouse goes back to the terminal (drag, ⌘C) and the screen **stays still** (macOS Terminal clears a
selection whenever the screen is redrawn; updates arrive when you leave), Ctrl+T or Esc restores the wheel. **Ctrl+Y** copies the
last answer to the clipboard. `--no-mouse` never captures the mouse.

In the chat:
- **Tab** completes folders (`~/Doc` → `~/Documents/`, spaces too: `Holiday\ Photos/`) and commands (`find d` → `find duplicates`);
- **↑/↓** browse previous commands (encrypted, kept across sessions); accept the grey suggestion
  from history with **→**, **Tab** or Ctrl+E;
- `help` shows the 6 most used requests, `help all` every command;
- confirmations (indexing, moving to the Trash, organizing) use a **selector**: ←/→ (or the
  initial letter) to choose, **Enter** to confirm, Esc to cancel; the input line is hidden meanwhile,
  so typing can never confirm by accident. For changes to files the preselected option is "No";
- the status bar shows at most 3 shortcuts, the ones that matter right now;
- long operations show a progress bar (🔍 finding files → 🧬 comparing contents → 🤖 classifying).

Commands (natural language, type `help` in the chat):

| Request | What it does |
|---|---|
| `analyze the Downloads folder`, `analyze ~/Desktop` | scan and summary |
| `find duplicates` | groups of identical files |
| `delete the duplicates` | moves the copies to the **Trash** (with confirmation) |
| `find similar photos` · `find similar items` · `are there almost identical documents?` | groups of **similar but not identical** files (suggestions only, nothing is touched) |
| `classify the files`, `what files are there?` | categories (rules + AI on the content) |
| `organize the files`, `organize into ~/Archive` | moves into folders by category (with confirmation) |
| `show me the 10 largest files` | biggest files (instant) |
| `index the folder` | reads and understands the content of every file |
| `what's in IMG_2041.HEIC?` | explains what a file contains |
| `what's in the Documents folder, give me a summary` | overview of topics, document types, periods |
| `search the hotel receipt`, `find the documents about the car` | search by name, content and meaning |
| `how much did I pay for the hotel in Rome?` | answer from your documents, with sources |
| `export the report as json/csv/md` | saves a report |
| `undo the last operation` | restores the moved files |
| `show the history` | audit of all changes to files |
| `undo operation 2`, `undo operation <id>` | restores a specific operation |
| `remind me to pay the rent on Friday`, `what do I have to do?`, `done 2`, `delete reminder 2` | **agenda**: things to do with an optional date, shown under the Activity box |
| `remember that bills go in Documents/Home`, `what do you remember?` | persistent memory (notes) |
| `go to ~/Documents` | changes the working folder |

Requests are interpreted by the local AI (see "How requests are understood").

### AI engines (strategy pattern)

The code only talks to a facade (`ai.AIEngine`); underneath, each capability uses an
interchangeable **engine** (`engines/`):

| Engine | Text / router | Embeddings | Images | Notes |
|---|---|---|---|---|
| `llamacpp` | ✅ | ✅ | — | inside the app (llama-cpp-python, Metal GPU); JSON constrained by grammar |
| `ollama` | ✅ | ✅ | ✅ | external `ollama serve` service |
| `mlx` | prepared | prepared | prepared | not implemented yet |

Default (`auto`): **text and embeddings with llama.cpp** when installed and the model is
available, **images with Ollama**. Choose with `--engine llamacpp|ollama|mlx|auto`,
`--vision-engine …`, `--embed-engine …` (or `FI_ENGINE`, `FI_VISION_ENGINE`, `FI_EMBED_ENGINE`).

llama.cpp **reuses the models already downloaded by Ollama** (`qwen2.5:14b`, `qwen2.5:7b`,
`bge-m3`: nothing new to download) or any `.gguf` file (`--model ~/models/x.gguf`). Models that
Ollama stores as separate tensors (e.g. `qwen3.5:9b`) can't be read by llama.cpp, which is why
images stay on Ollama. Embeddings are identical to Ollama's (similarity 1.0), so an existing
index stays valid.

Memory: models load on first use and are released after 10 minutes of inactivity
(`FI_LLAMA_IDLE`). If Ollama keeps the same model loaded, that copy is released (Ollama reloads it
when needed); if the GPU is full, llama.cpp releases Ollama's models and retries once
(`FI_LLAMA_FREE_OLLAMA=0` to disable). Other options: `FI_LLAMA_CTX` (context, 8192),
`FI_LLAMA_BATCH` (2048), `FI_LLAMA_FLASH_ATTN` (1), `FI_LLAMA_EMBED_CTX` (2048).

Installation: `pip install llama-cpp-python` (compiles with Metal in a few minutes).

**Indexing with little memory** (< 32 GB, or `FI_LOW_MEMORY=1`) and different engines for text
and images: files are analyzed in blocks of 16 (`FI_PHASE_BATCH`): first the visual reads of the
block (text model unloaded), then the understanding (vision model unloaded). The two large models
are never in memory together; the cost is a few seconds of reloading per block.

**Adding an engine:** create `engines/<name>.py` with a subclass of `EngineStrategy`
(`generate_json`, `chat`, `describe_image`, `embed`, `available`, `release`) and register it in
`engines/__init__.py`. `GET /status` shows which engine serves each capability.

## How requests are understood (AI router)

Every request goes through a **router**: the local AI receives the list of commands (grouped into
analysis, search, organization, memory, system, with their arguments) and returns a **plan** of
1-4 steps, constrained by a JSON schema. Examples:

| Request | Plan |
|---|---|
| `index Documents and then find similar documents` | `index ~/Documents` → `similar ~/Documents (documents)` |
| `how much space do the duplicates in downloads waste? then export a csv report` | `duplicates ~/Downloads` → `export csv` |
| `I made a mess, put everything back as it was` | `undo` |
| `what's the difference between jpg and png?` | no command: the AI answers |
| `rename all photos with their date` | no command: explains it can't and what it can do instead |

Guarantees:
- unknown commands and invalid arguments are dropped; the AI can't invent commands;
- at most **one** command that modifies files per request, always **last**, always confirmed;
- if a step asks for confirmation (e.g. "I need to index first: go ahead?") the plan pauses and
  resumes after "yes"; a new request cancels it;
- a step without a folder uses the previous step's folder;
- instant shortcuts without AI: yes/no, exit, help, cancel, go to…, history;
- if the AI engine is unavailable or doesn't answer within 25 s, keyword rules are used.

Options: `--router rules` (keywords only), `--router-model qwen2.5:7b` (faster, less accurate; works
with any engine; also `FI_ROUTER_MODEL`). API: `POST /route {"message": …}` shows the plan
**without** running it; `POST /chat` runs it and returns `data.route` and each step's results in
`data.steps`. New commands join the router's catalog automatically: `description`, `group` and
`params` are all it needs.

## Batch mode (no chat)

```bash
python3 __main__.py ~/Downloads --duplicates                 # list duplicates
python3 __main__.py ~/Downloads --full-scan                  # full classification
python3 __main__.py ~/Downloads --dedupe                     # dry run of duplicate removal
python3 __main__.py ~/Downloads --dedupe --apply             # run it (Trash)
python3 __main__.py ~/Downloads --organize --target ~/Archive --apply
python3 __main__.py ~/Downloads --export csv -o report.csv
python3 __main__.py ~/Documents --index                      # index the contents
python3 __main__.py ~/Documents --search "car insurance"
python3 __main__.py ~/Documents --ask "when does the lease expire?"
python3 __main__.py --history                                # audit of changes
python3 __main__.py --undo                                   # undo the last operation
python3 __main__.py --undo 3f2a9c                            # undo a specific operation (id or number)
```

Options: `--lang`, `--no-ai`, `--no-vision`, `--model`, `--vision-model`, `--embed-model`,
`--ollama-url`, `--ai-limit N`, `--index-limit N`, `--memory json|inmemory|lightrag`.

## Content understanding (document intelligence)

`index` analyzes each file with the first suitable **extractor**, then the AI derives its
category, title, description, tags, date and amount; finally it computes the embedding.
The result goes into the local encrypted index (SQLite + FTS5), updated incrementally (only
changed files are analyzed again).

| Type | How it is read |
|---|---|
| text, code, CSV, JSON, email | read directly |
| PDF | `pdftotext`; if scanned → OCR (`pdftoppm` + `tesseract`) + vision model |
| Word/Excel/PowerPoint/OpenDocument | internal XML (no dependencies) |
| .doc, .rtf, .html, .webarchive | `textutil` (macOS) |
| images (HEIC too) | `sips` → `tesseract` OCR + description by the vision model |
| audio/video | `ffprobe` (duration, tags, GPS) + a frame described by the vision model |
| zip/tar archives | list of contained files |
| any file | Spotlight metadata (`mdls`: download origin, author…) + `file` |

**Search** merges four signals (reciprocal rank fusion): text (FTS5, accent-insensitive),
meaning (embeddings, with the query expanded by the AI), Spotlight (`mdfind`) and file name.
Each result says *why* it was found.

**External document-intelligence service (optional).** If you set `FI_DOCINTEL_URL` (e.g.
`http://localhost:18081`) and `FI_DOCINTEL_TOKEN`, PDFs and images go through that service first
(Tesseract OCR + docling layout + vision model); if it doesn't answer, the local extractors are used.

## Performance and iCloud folders

- Scanning reads **metadata only** (name, size, date): thousands of files in tenths of a second.
  Content is read only when needed (duplicates, indexing).
- **Dates in searches** ("bank statement 31/03/2024", "March 2024", "03-2024", "statement March 31, 2024")
  are a filter, not ordinary words: only documents of that date are kept — the date written in the
  text ("31/03/2024" is found as a phrase), the date detected by the AI, or the date in the file name.
- Commands with an explicit folder ("index Documents") make it the working folder, so the next
  "search …" looks there. If the current folder isn't indexed, search and questions use the other
  indexed folders and say so.
- **Indexing in two steps.** `index` first does a **fast pass** on all files (text, OCR,
  embeddings: ~0.1 s per file, e.g. 711 documents in 76 s) and announces "search ready" right away;
  then the **AI understanding** (summaries with `qwen2.5:7b`, `FI_INDEX_MODEL`; images with the vision
  model) continues on files that are already searchable. `index fast` skips the second step.
  `--index-limit N` limits the files.
- Files that are not documents are never read: app libraries and backups (`.abbu` Contacts backups,
  Photos/Lightroom libraries…) are treated as packages; contact cards, lock files and internal
  fragments with id-like names are skipped.
- **Background service** (`folder-intelligence --install-background`): a LaunchAgent that completes
  the AI understanding while the Mac is **charging and idle** for `FI_IDLE_MINUTES` (default 5),
  with background priority (`taskpolicy -b`, low-priority I/O). It pauses as soon as you use the
  Mac or open the chat (an index lock makes sure only one process writes the encrypted index; the
  chat asks the service to save and waits a few seconds). The chat tells you what it completed
  ("🌙 While you weren't using the app I understood N files"). `--background-status`,
  `--uninstall-background`. Logs contain only counts.
- Search ("search"): a document appears only if it is really relevant — the text must contain most
  of the words searched (2 of 2-3, half beyond; articles and elisions like Italian "dell'" ignored)
  and the semantic similarity must exceed 0.53 (`FI_SEMANTIC_MIN`; measured: relevant 0.55–0.71,
  unrelated ≤ 0.50). If nothing is relevant it says "no relevant files" and how many files haven't
  been read yet. Questions ("ask") use looser criteria: the model then picks the sources.
- Similar items (`similarity.py`), suggestions only:
  - **photos**: 64-bit dHash visual fingerprint (image reduced to 9×8 with `sips`, HEIC/RAW too);
    similar when they differ by ≤9 bits ("very similar" ≤5, "vaguely similar" ≤13). Finds resized,
    recompressed (e.g. sent via WhatsApp), converted photos and bursts. Fingerprints are stored in
    the encrypted index: the second search is instant. Suggests keeping the largest.
  - **documents**: text from the index (needs "index"), compared on 5-word shingles: the same text
    in different formats (pdf/docx), drafts contained in the final version. Suggests the most recent.
  - **versions by name** in the same folder: `quote.pdf` / `quote v2.pdf` / `… FINAL` / `… (1)`;
    numbering (`statement_1`, `_2`) is not considered a version.
  - API: `POST /commands/similar {"path": "~/Pictures", "args": {"kind": "images|documents|all",
    "strictness": "strict|normal|loose"}}` → background job, result in `result.data.groups`.
- macOS packages (`.rtfd`, `.pages`, `.numbers`, `.key`, `.app`, Photos libraries…) are treated as a
  single file and never opened: moving their content would break them.
- Duplicates: size → hash of the first 64KB → full SHA-256, in parallel.
  - **byte-for-byte identical content** (not "similar": resized photos or re-saved PDFs are not duplicates);
  - hardlinks and APFS clones (`cp -c`, Finder "Duplicate" on APFS) are reported but **not** counted
    as recoverable space, because they take no extra space;
  - the original kept: a name without copy markers (`photo 2`, `(1)`, `copy`, `Copy of…`), then outside
    Downloads/Desktop, then the oldest;
  - before trashing each copy it is **re-checked** that original and copy exist and are still identical
    (files may change in Finder after the scan); scans older than 2 minutes are redone;
  - "delete everything"/"delete them" mean duplicate removal only as a bare phrase: "delete all pdf
    files" is **not** interpreted as duplicate removal.
- **iCloud-only** files (not downloaded) are never read, so as not to force a download.
- Every read has a **timeout**: a file locked by syncing is skipped and reported instead of freezing
  the app. **Ctrl+C** interrupts the running operation.

## Privacy and encryption

Everything the app stores is **encrypted at rest with AES-256-GCM**; the master key (256 bits,
random) is in the **macOS Keychain** (service `folder-intelligence`). Data folder
`~/.folder_intelligence` with permissions 0700, files 0600.

| Data | File | Protection |
|---|---|---|
| content index (text, OCR, descriptions, embeddings, photo fingerprints) | `index.db.enc` | whole SQLite DB encrypted, used only in memory |
| memory/notes | `memory.enc` | encrypted |
| audit of changes | `audit.jsonl` | each line encrypted and authenticated (tampering detected) |
| command history (↑/↓) | `history.enc` | each entry encrypted |
| MCP sync state | `mcp_state.enc` | encrypted |

Old plaintext files are migrated automatically and overwritten before deletion. Temporary files
(converted images, rendered PDF pages) are in a 0700 folder removed on exit. Exported reports
(`export …`) are plaintext, because you ask for them.

Honest limits: whoever uses your unlocked macOS session can read the Keychain; on SSD/APFS
overwriting does not guarantee physical destruction of old blocks (FileVault covers that).

## MCP memory (local-memory)

After every `index`, the understanding of the files is recorded in the shared MCP memory
(`local-memory`, `http://127.0.0.1:8000`, namespace `files:folder-intelligence`), replacing the
previous version (`supersedes`). Since that memory is **shared with every agent** (some send context
to cloud models) and is not encrypted by this app, there are 3 modes:

| `--mcp-mode` / `FI_MCP_MODE` | What goes into the MCP memory |
|---|---|
| `sealed` (default) | one inventory per folder: how many files per category/type and period. No names, titles, text or subfolders |
| `summary` | also one entry per file: title, type, date, description and tags, cleaned of emails, IBANs, tax codes, phone numbers, plates, long numbers (proper names may remain) |
| `off` | nothing |

## Safety

- No permanent deletion: duplicates go to the Trash (`~/.Trash`).
- Every operation shows its plan first and asks for confirmation (batch mode needs `--apply`).
- **Internal audit**: every change is recorded in `~/.folder_intelligence/audit.jsonl` (append-only,
  one encrypted line per event, written with lock + fsync). For each file: origin, destination, size,
  modification date and SHA-256 (when known), plus the source (`cli`/`chat`/`api`). Undos are events
  too: the history is never rewritten. **Any** operation can be undone, even partially (e.g. if a file
  had disappeared from the Trash, it can be restored later). Files modified after the move are
  restored and reported.
- Hidden files, symlinks, `.git`, `node_modules` and macOS bundles are ignored.

## API for the Mac app

```bash
folder-intelligence --serve                 # http://127.0.0.1:8765 · interactive docs at /docs
```

- Listens on `127.0.0.1` only. Every endpoint (except `/health`) requires
  `Authorization: Bearer <token>`; the token is in `~/.folder_intelligence/api_token` (0600).
- Messages follow `Accept-Language` (`it`/`en`) or `?lang=`; operation statuses are stable codes
  (`done`, `undone`, …) with a translated `status_label`.
- The OpenAPI schema (`/openapi.json`) can generate the Swift client (e.g. `swift-openapi-generator`).
- Changes go through the same plans, confirmations and audit as the CLI.

| Method | Endpoint | Use |
|---|---|---|
| GET | `/health`, `/status` | server status, AI availability, language |
| POST | `/scans` `{path, duplicates, classify, index}` | start a scan/indexing in the background → `job` |
| GET · DELETE | `/scans/{id}` | progress (`phase`, `done`, `total`, `fraction`, `detail`) · cancel |
| GET | `/commands` | all chat commands, with parameters and endpoint |
| POST | `/commands/{name}` `{path, target, args, wait}` | run **any command** as a job; complete answer if it finishes within `wait` seconds, otherwise `202` |
| GET · DELETE | `/jobs` · `/jobs/{id}` | list/status/result (text + structured data) · stop |
| POST | `/jobs/{id}/confirm` `{confirm}` | answer confirmations (`needs_confirmation`): moves, indexing |
| GET | `/jobs/events` | Server-Sent Events: real-time progress of all jobs |
| POST | `/route` `{message}` | plan preview: which commands, in which order |
| GET | `/search?q=&path=` | hybrid search (with `matched_by`) |
| POST | `/ask` `{question, path}` | answer from the documents with sources |
| GET | `/describe?path=` · `/index/stats` | analysis of a file · index status |
| GET | `/summary?path=` | folder summary |
| GET | `/files?path=&category=&q=&sort=&limit=&offset=` | paginated file list |
| GET | `/duplicates?path=` | duplicate groups |
| GET | `/folders?path=` · `/complete?text=` | folder navigation · autocompletion |
| POST | `/plans` `{kind: dedupe\|organize, path, target?}` | create a plan (preview) |
| POST · DELETE | `/plans/{id}/apply` · `/plans/{id}` | apply · discard the plan |
| GET | `/operations` · `/operations/{id}` | audit of changes |
| POST | `/operations/{id}/undo` · `/undo` | restore an operation · the last one |
| POST | `/chat` `{message, session_id?}` | chat (with `pending` when confirmation is needed) |
| GET · POST | `/memory` | memory |
| GET | `/export?path=&format=json\|md\|csv` | download a report |

Every chat command has its API: `POST /commands/largest {"path": "~/Documents", "args": {"n": 10}}`,
`/commands/search {"args": {"query": "car insurance"}}`, `/commands/dedupe` (→ `needs_confirmation`,
then `POST /jobs/{id}/confirm {"confirm": true}`), etc. `result.data` holds the structured data
(file lists, duplicate groups, plans, search results…).

Example:

```bash
T=$(cat ~/.folder_intelligence/api_token)
curl -s -X POST -H "Authorization: Bearer $T" -H 'content-type: application/json' \
     -H 'Accept-Language: en' -d '{"path":"~/Downloads"}' localhost:8765/scans
```

## Adding a command (skill)

Each command is a class in `src/folder_intelligence/commands/` (one file per command), loaded
automatically. You can also put them in `~/.folder_intelligence/commands/`.

```python
# src/folder_intelligence/commands/free_space.py
from .. import shell
from ..i18n import N_, _
from .base import Command


class FreeSpaceCommand(Command):
    name = "free_space"
    group = "sistema"                                       # analisi | ricerca | organizzazione | memoria | sistema
    description = N_("Mostra lo spazio libero sul disco")   # used by the AI router and by 'help' (translated)
    examples = (N_("quanto spazio libero ho?"),)            # shown in 'help' and given to the router
    keywords = ("spazio libero", "disco pieno", "free space", "disk full")  # fallback without AI, any language
    priority = 30                                           # lower = evaluated first

    async def run(self, ctx, intent):
        res = await shell.run(["df", "-h", str(ctx.directory)])   # system commands: always a list, never a shell
        return _("Spazio sul disco:") + "\n" + res.stdout
```

Then add the translations of the new texts to `locales/en.json` (the test lists them for you).

Inside `run` you have `ctx` with: `ctx.directory`, `await ctx.get_scan()`, `await ctx.get_duplicates(scan)`,
`ctx.ai` (AI engine facade: `generate_json`, `chat`, `describe_image`, `embed`), `ctx.intelligence`
(analysis/index/search), `ctx.memory`, `ctx.pending` (plan to confirm → goes through the audit),
`ctx.progress`, `ctx.cancel`. The AI router learns the command from `description`, `examples`,
`group` and `params`: nothing else is needed for it to be used in free-form requests.

In the same way, a new file type is supported with a class in `extractors/` (a subclass of
`Extractor` with `extensions` and `async extract(path, ctx)`).

## Architecture

```
src/folder_intelligence/
  commands/      one file per chat command (plugin) + registry (base.py, __init__.py)
  engines/       interchangeable AI engines (strategy): llamacpp, ollama, mlx
  extractors/    one file per content type (plugin): pdf, image, office, media, docintel…
  locales/       translations (en.json); i18n.py: _(), macOS language/--lang/Accept-Language
  agenda.py      things to do with dates (encrypted), shown in the panel
  dates.py       dates in natural language (it/en): search filters, due dates
  router.py      from a request to a plan of commands (AI + fallback rules)
  intelligence.py understanding pipeline, hybrid search, questions (local RAG)
  similarity.py  similar photos and documents
  ai.py          AIEngine facade: one engine for text, vision, embeddings
  crypto.py      AES-256-GCM, key in the macOS Keychain
  history.py     encrypted command history
  mcp_memory.py  recording in the MCP memory (sealed/summary/off)
  privacy.py     removal of personal data from outgoing text
  index.py       SQLite FTS5 index + embeddings + photo fingerprints
  shell.py       safe system commands (mdfind, mdls, ffprobe, tesseract…)
  threads.py     I/O with timeouts in daemon threads (iCloud/network folders)
  plugins.py     automatic plugin loading
  models.py      FileInfo, DuplicateGroup, ScanResult
  scanner.py     metadata scan + on-demand duplicates
  classifier.py  categories, RuleClassifier, FileClassifier
  organizer.py   organize/dedupe plans, apply, undo (per operation)
  audit.py       append-only record of changes to files
  completion.py  folder/command autocompletion (CLI and API)
  api.py         FastAPI HTTP API for the Mac app
  memory.py      Strategy: inmemory | json (default) | lightrag (with fallback)
  exporter.py    JSON / Markdown / CSV reports
  intents.py     request normalization and path extraction
  assistant.py   session: the `ctx` passed to commands
  cli.py         argparse, classic chat, batch, panel UI start
  tui.py         panel chat with the Activity box (prompt_toolkit full-screen)
  jobs.py        background tasks shared by chat and API
  selector.py    yes/no selector with arrow keys (classic chat)
tests/           pytest
```

Environment variables: `FI_LANG`, `FI_ENGINE`, `FI_ROUTER_MODEL`, `FI_OLLAMA_URL`, `FI_OLLAMA_MODEL`,
`FI_VISION_MODEL`, `FI_EMBED_MODEL`, `FI_DOCINTEL_URL`, `FI_DOCINTEL_TOKEN`, `FI_MCP_MODE`,
`FI_MCP_URL`, `FI_MCP_NAMESPACE`, `FI_MASTER_KEY`, `FI_DEBUG`, `FI_HOME` (default
`~/.folder_intelligence`), `FI_TRASH`, `FI_API_TOKEN`.

## Tests

```bash
.venv/bin/python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).
