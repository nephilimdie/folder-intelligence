# 🤖 folderIntelligence

*[English](README.md) · Italiano*

Parla con le tue cartelle: trova file duplicati, **comprende il contenuto** di documenti,
PDF (anche scansionati), immagini, Office, audio e video, li cerca per significato e li
organizza. Tutto con AI locale (llama.cpp dentro l'app e/o Ollama): nessun dato lascia il Mac.

## Installazione

```bash
cd ~/Progetti/folder-intelligence
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # oppure: .venv/bin/pip install -r requirements.txt
```

AI locale (opzionale, consigliata):

```bash
pip install llama-cpp-python # motore dentro l'app per testo e ricerca (vedi "Motori AI")
ollama pull qwen2.5:14b      # scarica i modelli (llama.cpp riusa gli stessi file)
ollama pull bge-m3
ollama pull qwen3.5:9b       # immagini: serve `ollama serve` attivo
```

Modelli usati (tutti opzionali, configurabili con `--model`, `--vision-model`, `--embed-model`):

| Ruolo | Default | Serve per |
|---|---|---|
| testo | `qwen2.5:14b` | categoria, descrizione, tag, risposte alle domande |
| visione | `qwen3.5:9b` | descrivere foto, screenshot, scansioni, fotogrammi video |
| embedding | `bge-m3` | ricerca per significato (multilingue) |

Strumenti di sistema usati se presenti: `tesseract` (OCR), `pdftotext`/`pdftoppm` (poppler),
`ffprobe`/`ffmpeg`, `sips`, `textutil`, `mdls`/`mdfind` (Spotlight).
Senza AI tutto funziona lo stesso: si usano regole, OCR e ricerca testuale.

## Lingue

Interfaccia, risposte e contenuti generati dall'AI (titoli, descrizioni, tag) sono in **italiano** o
**inglese**. La lingua è quella di macOS; si cambia con `--lang it|en` o `FI_LANG`. I comandi si
possono scrivere in entrambe le lingue; la ricerca trova anche documenti scritti nell'altra lingua
(gli embedding bge-m3 sono multilingua e la query viene espansa in italiano e inglese).
Le API scelgono la lingua per richiesta con l'header `Accept-Language` (o `?lang=`).

Le traduzioni sono in `src/folder_intelligence/locales/<lingua>.json` (il testo italiano nel codice
fa da chiave, stile gettext: `_("Nessun file duplicato in {root}.", root=…)`). Per aggiungere una
lingua: copia `en.json`, traduci i valori e aggiungi il codice a `SUPPORTED` in `i18n.py`;
`tests/test_i18n.py` segnala testi mancanti, segnaposto diversi e traduzioni non più usate
(`python -m folder_intelligence.i18n_extract --missing <lingua>` elenca cosa manca).

## Uso

### Chat interattiva

```bash
./start_chat.sh                          # chat su ~/Downloads
.venv/bin/python __main__.py ~/Desktop   # chat su un'altra cartella
folder-intelligence ~/Desktop            # se installato con pip install -e .
```

```
Tu › trovami i duplicati
Trovati 3 gruppi di duplicati in ~/Downloads (4 copie, 12.3 MB recuperabili):
[1] foto_vacanza.jpg — 4.1 MB × 2 (immagini)
    ✅ tengo:  foto_vacanza.jpg
    🗑️  copia: foto_vacanza copia.jpg
Tu › cancella i duplicati
Sposterò nel Cestino 4 copie (12.3 MB) … Confermi? (sì/no)
Tu › sì
✅ 4 file spostati nel Cestino.
Tu › annulla l'ultima operazione
↩️  Ripristinati 4 file nella posizione originale.
```

La chat è a **pannelli**: conversazione a sinistra, box **Attività** a destra (sotto, se il
terminale è stretto). Ogni richiesta diventa un'attività: se dura più di ~1,5 s passa in
**background** e puoi continuare a scrivere; il box ne mostra fase, avanzamento, file in
lavorazione e tempo. Al termine il risultato compare nella conversazione (con eventuale
selettore di conferma). `attività` elenca quelle in corso, `ferma 3` interrompe la #3,
Ctrl+C ferma l'ultima, Ctrl+D o `esci` chiude (chiedendo conferma se ci sono attività).
`--classic` avvia la chat semplice senza pannelli.

Per rileggere la conversazione: **rotellina/trackpad**, PagSu/PagGiù o ⇧↑/⇧↓; Esc o Fine tornano
in fondo. Se arrivano risultati mentre stai leggendo più in alto, la vista resta ferma e compare
l'avviso "● nuovi messaggi". Poiché l'app riceve il mouse, per **selezionare testo** tieni premuto
⌥ Option mentre trascini; in alternativa avvia con `--no-mouse` (o `FI_MOUSE=0`).

Nella chat:
- **Tab** completa cartelle (`~/Doc` → `~/Documents/`, anche con spazi: `Foto\ Vacanze/`) e comandi (`trov` → `trovami i duplicati`);
- **↑/↓** scorrono i comandi precedenti (salvati in `~/.folder_intelligence/history`, anche tra sessioni); il suggerimento grigio si accetta con →;
- le conferme (indicizzare, spostare nel Cestino, organizzare) si danno con un **selettore**:
  ←/→ per scegliere, Invio per confermare, `s`/`n` come scorciatoie, Esc per annullare.
  Per le modifiche ai file l'opzione preselezionata è "No";
- durante scansioni e classificazione compare una barra di avanzamento (🔍 ricerca file → 🧬 confronto contenuti → 🤖 classificazione).

Comandi (linguaggio naturale, scrivi `aiuto` in chat):

| Frase | Cosa fa |
|---|---|
| `analizza la cartella Downloads`, `analizza ~/Desktop` | scansione e riepilogo |
| `trovami i duplicati` | elenco gruppi di file identici |
| `cancella i duplicati` | sposta le copie nel **Cestino** (con conferma) |
| `trova foto simili` · `trova elementi simili` · `ci sono documenti quasi uguali?` | gruppi di file **simili ma non identici** (solo suggerimenti, nulla viene toccato) |
| `classifica i file`, `che file ci sono?` | categorie (regole + AI sul contenuto) |
| `organizza i file`, `organizza in ~/Archivio` | sposta in cartelle per categoria (con conferma) |
| `mostrami i 10 file più grandi` | file più ingombranti (istantaneo) |
| `indicizza la cartella` | legge e comprende il contenuto di ogni file |
| `cosa contiene IMG_2041.HEIC?` | spiega cosa c'è in un file |
| `cosa contiene la cartella Documents, fammi un riassunto` | panoramica di temi, tipi di documenti, periodi |
| `cerca la ricevuta dell'hotel`, `trova i documenti sulla macchina` | ricerca per nome, contenuto e significato |
| `quanto ho pagato l'hotel a Roma?` | risposta dai tuoi documenti, con fonti |
| `esporta il report in json/csv/md` | salva un report |
| `annulla l'ultima operazione` | ripristina i file spostati |
| `mostra lo storico` | audit di tutte le modifiche ai file |
| `annulla operazione 2`, `annulla operazione <id>` | ripristina un'operazione specifica |
| `ricordami di …`, `cosa ricordi?` | memoria persistente |
| `vai in ~/Documents` | cambia cartella di lavoro |

Le frasi vengono interpretate dall'AI locale (vedi 'Come capisce le richieste').

### Motori AI (strategy pattern)

Il codice parla solo con una facciata (`ai.AIEngine`); sotto, ogni capacità usa un **motore**
intercambiabile (`engines/`):

| Motore | Testo / router | Embedding | Immagini | Note |
|---|---|---|---|---|
| `llamacpp` | ✅ | ✅ | — | dentro l'app (llama-cpp-python, GPU Metal); JSON vincolato da grammatica |
| `ollama` | ✅ | ✅ | ✅ | servizio esterno `ollama serve` |
| `mlx` | predisposto | predisposto | predisposto | non ancora implementato |

Predefinito (`auto`): **testo e embedding con llama.cpp** se installato e se il modello è
disponibile, **immagini con Ollama**. Per scegliere: `--engine llamacpp|ollama|mlx|auto`,
`--vision-engine …`, `--embed-engine …` (o `FI_ENGINE`, `FI_VISION_ENGINE`, `FI_EMBED_ENGINE`).

llama.cpp **riusa i modelli già scaricati con Ollama** (`qwen2.5:14b`, `qwen2.5:7b`, `bge-m3`:
nessun nuovo download) oppure un file `.gguf` qualsiasi (`--model ~/modelli/x.gguf`). I modelli
salvati da Ollama nel formato a tensori separati (es. `qwen3.5:9b`) non sono leggibili da llama.cpp:
per questo le immagini restano su Ollama. Gli embedding sono identici a quelli di Ollama
(similarità 1.0), quindi l'indice esistente resta valido.

Memoria: i modelli si caricano al primo uso e si liberano dopo 10 minuti di inattività
(`FI_LLAMA_IDLE`). Se Ollama tiene in memoria la stessa copia del modello, viene liberata
(Ollama la ricarica da solo se serve); se la GPU è piena, llama.cpp libera i modelli di Ollama e
riprova una volta (`FI_LLAMA_FREE_OLLAMA=0` per disattivare). Altre opzioni: `FI_LLAMA_CTX`
(contesto, 8192), `FI_LLAMA_BATCH` (2048), `FI_LLAMA_FLASH_ATTN` (1).

Installazione: `pip install llama-cpp-python` (si compila con Metal in qualche minuto).

**Indicizzazione con poca memoria** (< 32 GB, o `FI_LOW_MEMORY=1`) e motori diversi per testo e
immagini: i file vengono analizzati a blocchi di 16 (`FI_PHASE_BATCH`): prima le letture visive
del blocco (modello di testo scaricato), poi la comprensione (modello visivo scaricato). I due
modelli grandi non stanno mai in memoria insieme; il costo è qualche secondo di ricarica per blocco.

**Aggiungere un motore:** crea `engines/<nome>.py` con una sottoclasse di `EngineStrategy`
(`generate_json`, `chat`, `describe_image`, `embed`, `available`) e registrala in
`engines/__init__.py`. `GET /status` mostra quale motore serve ogni capacità.

## Come capisce le richieste (router AI)

Ogni frase passa da un **router**: l'AI locale riceve l'elenco dei comandi (raggruppati in
analisi, ricerca, organizzazione, memoria, sistema, con i loro argomenti) e restituisce un **piano**
di 1-4 passi, vincolato da uno schema JSON. Esempi:

| Frase | Piano |
|---|---|
| `indicizza Documents e poi trova i documenti simili` | `index ~/Documents` → `similar ~/Documents (documents)` |
| `quanto spazio sprecano i doppioni nei download? poi esporta un report csv` | `duplicates ~/Downloads` → `export csv` |
| `ho fatto casino, rimetti tutto come prima` | `undo` |
| `che differenza c'è tra jpg e png?` | nessun comando: risponde l'AI |
| `rinomina tutte le foto con la data` | nessun comando: spiega che non può e cosa può fare |

Garanzie:
- comandi inesistenti e argomenti non validi vengono scartati; l'AI non può inventare comandi;
- al massimo **un** comando che modifica file per richiesta, sempre **per ultimo**, sempre con conferma;
- se un passo chiede una conferma (es. "prima devo indicizzare: procedo?") il piano si ferma e
  riprende dopo il "sì"; una nuova richiesta lo annulla;
- un passo senza cartella usa quella del passo precedente;
- scorciatoie senza AI (immediate): sì/no, esci, aiuto, annulla, vai in…, cronologia;
- se il motore AI non è disponibile o non risponde entro 25s si usano le regole a parole chiave.

Opzioni: `--router rules` (solo parole chiave), `--router-model qwen2.5:7b` (più veloce, meno preciso; funziona con qualsiasi motore;
anche `FI_ROUTER_MODEL`). API: `POST /route {"message": …}` mostra il piano **senza** eseguirlo;
`POST /chat` lo esegue e restituisce `data.route` e i risultati di ogni passo in `data.steps`.
Nuovi comandi entrano automaticamente nel catalogo del router: basta `description`, `group`, `params`.

## Modalità batch (senza chat)

```bash
python3 __main__.py ~/Downloads --duplicates                 # elenca duplicati
python3 __main__.py ~/Downloads --full-scan                  # classificazione completa
python3 __main__.py ~/Downloads --dedupe                     # dry-run rimozione duplicati
python3 __main__.py ~/Downloads --dedupe --apply             # esegue (Cestino)
python3 __main__.py ~/Downloads --organize --target ~/Archivio --apply
python3 __main__.py ~/Downloads --export csv -o report.csv
python3 __main__.py ~/Documents --index                      # indicizza i contenuti
python3 __main__.py ~/Documents --search "polizza auto"
python3 __main__.py ~/Documents --ask "quando scade l'affitto?"
python3 __main__.py --history                                # audit delle modifiche
python3 __main__.py --undo                                   # annulla l'ultima operazione
python3 __main__.py --undo 3f2a9c                            # annulla un'operazione specifica (id o n°)
```

Opzioni: `--no-ai`, `--no-vision`, `--model`, `--vision-model`, `--embed-model`, `--ollama-url`,
`--ai-limit N`, `--index-limit N`, `--memory json|inmemory|lightrag`.

## Comprensione dei contenuti (document intelligence)

`indicizza` analizza ogni file con il primo **estrattore** adatto, poi l'AI ne ricava
categoria, titolo, descrizione, tag, data e importo; infine calcola l'embedding.
Il risultato va nell'indice locale `~/.folder_intelligence/index.db` (SQLite + FTS5),
aggiornato in modo incrementale (rianalizza solo i file cambiati).

| Tipo | Come viene letto |
|---|---|
| testo, codice, CSV, JSON, email | lettura diretta |
| PDF | `pdftotext`; se scansionato → OCR (`pdftoppm` + `tesseract`) + modello visivo |
| Word/Excel/PowerPoint/OpenDocument | XML interno (nessuna dipendenza) |
| .doc, .rtf, .html, .webarchive | `textutil` (macOS) |
| immagini (anche HEIC) | `sips` → OCR `tesseract` + descrizione col modello visivo |
| audio/video | `ffprobe` (durata, tag, GPS) + fotogramma descritto dal modello visivo |
| archivi zip/tar | elenco dei file contenuti |
| qualsiasi file | metadati Spotlight (`mdls`: provenienza download, autore…) + `file` |

La **ricerca** fonde quattro segnali (reciprocal rank fusion): testo (FTS5, ignora accenti),
significato (embedding, con query espansa dall'AI in sinonimi), Spotlight (`mdfind`) e nome file.
Ogni risultato dice *perché* è stato trovato.

**Servizio document-intelligence esterno (opzionale).** Se configuri
`FI_DOCINTEL_URL` (es. `http://localhost:18081`) e `FI_DOCINTEL_TOKEN`, PDF e immagini passano
prima dal servizio (OCR Tesseract + layout docling + modello visivo); se non risponde si usano
gli estrattori locali.

## Prestazioni e cartelle iCloud

- La scansione legge **solo i metadati** (nome, dimensione, data): migliaia di file in decimi di secondo.
  Il contenuto viene letto solo quando serve (duplicati, indicizzazione).
- Ricerca ("cerca"): un documento compare solo se è davvero pertinente — nel testo devono esserci
  quasi tutte le parole cercate (2 su 2-3, metà oltre; articoli e forme elise come "dell'" ignorati)
  e la somiglianza di significato deve superare 0.53 (`FI_SEMANTIC_MIN`; misurato: pertinenti
  0.55–0.71, estranei ≤ 0.50). Se niente è pertinente risponde "nessun file pertinente" e dice
  quanti file non sono ancora stati letti. Le domande ("chiedi") usano criteri più larghi: le fonti
  le sceglie poi il modello.
- Elementi simili (`similarity.py`), solo suggerimenti:
  - **foto**: impronta visiva dHash 64 bit (immagine ridotta a 9×8 con `sips`, legge anche HEIC/RAW);
    simili se differiscono di ≤9 bit ("molto simili" ≤5, "vagamente simili" ≤13). Trova foto
    ridimensionate, ricompresse (es. inviate su WhatsApp), convertite, raffiche. Impronte salvate
    nell'indice cifrato: la seconda ricerca è istantanea. Suggerisce di tenere la più grande.
  - **documenti**: testo dall'indice (serve "indicizza"), confronto su gruppi di 5 parole: stesso testo
    in formati diversi (pdf/docx), bozze contenute nella versione finale. Suggerisce il più recente.
  - **versioni dal nome** nella stessa cartella: `preventivo.pdf` / `preventivo v2.pdf` / `… FINALE` /
    `… (1)`; le numerazioni (`estratto_1`, `_2`) non sono considerate versioni.
  - API: `POST /commands/similar {"path": "~/Pictures", "args": {"kind": "images|documents|all",
    "strictness": "strict|normal|loose"}}` → job in background, risultato in `result.data.groups`.
- Pacchetti macOS (`.rtfd`, `.pages`, `.numbers`, `.key`, `.app`, librerie Foto…) sono trattati come un
  file unico e mai aperti: spostarne il contenuto li romperebbe.
- Duplicati: dimensione → hash dei primi 64KB → SHA-256 completo, in parallelo.
  - **contenuto identico byte per byte** (non "simile": foto ridimensionate o PDF risalvati non sono duplicati);
  - hardlink e cloni APFS (`cp -c`, "Duplica" del Finder su APFS) sono segnalati ma **non** contati come
    spazio recuperabile, perché non occupano spazio in più;
  - originale da tenere: nome senza segni di copia (`foto 2`, `(1)`, `copia`, `Copia di…`), poi fuori da
    Download/Scrivania, poi il più vecchio;
  - prima di cestinare ogni copia viene **ricontrollato** che originale e copia esistano e siano ancora
    identici (i file possono essere cambiati dal Finder dopo la scansione); scansioni più vecchie di 2 minuti
    vengono rifatte;
  - "cancella tutto"/"eliminali" valgono come rimozione duplicati solo come frase secca: "cancella tutti i
    file pdf" **non** viene interpretato come rimozione duplicati.
- I file **solo su iCloud** (non scaricati) non vengono mai letti, per non forzarne il download.
- Ogni lettura ha un **timeout**: un file bloccato dalla sincronizzazione viene saltato e segnalato,
  non ferma più l'app. **Ctrl+C** interrompe l'operazione in corso.

## Privacy e cifratura

Tutto ciò che l'app salva è **cifrato a riposo con AES-256-GCM**; la chiave master (256 bit,
casuale) sta nel **Portachiavi di macOS** (servizio `folder-intelligence`). Cartella dati
`~/.folder_intelligence` con permessi 0700, file 0600.

| Dato | File | Protezione |
|---|---|---|
| indice dei contenuti (testi, OCR, descrizioni, embedding) | `index.db.enc` | DB SQLite cifrato per intero, usato solo in memoria |
| memoria/note | `memory.enc` | cifrata |
| audit delle modifiche | `audit.jsonl` | ogni riga cifrata e autenticata (manomissioni rilevate) |
| cronologia comandi (↑/↓) | `history.enc` | ogni voce cifrata |
| stato sincronizzazione MCP | `mcp_state.enc` | cifrato |

I vecchi file in chiaro vengono migrati automaticamente e sovrascritti prima della cancellazione.
I file temporanei (immagini convertite, pagine PDF renderizzate) sono in una cartella 0700
rimossa all'uscita. I report esportati (`esporta …`) sono invece in chiaro, perché li chiedi tu.

Limiti onesti: chi usa la tua sessione macOS sbloccata può leggere il Portachiavi; su SSD/APFS
la sovrascrittura non garantisce la distruzione fisica dei vecchi blocchi (FileVault la copre).

## Memoria MCP (local-memory)

Dopo ogni `indicizza`, la comprensione dei file viene registrata nella memoria MCP condivisa
(`local-memory`, `http://127.0.0.1:8000`, namespace `files:folder-intelligence`), sostituendo
la versione precedente (`supersedes`). Poiché quella memoria è **condivisa con tutti gli agenti**
(alcuni inviano il contesto a modelli cloud) e non è cifrata da questa app, ci sono 3 modalità:

| `--mcp-mode` / `FI_MCP_MODE` | Cosa finisce nella memoria MCP |
|---|---|
| `sealed` (default) | un inventario per cartella: quanti file per categoria/tipo e periodo. Nessun nome, titolo, testo o sottocartella |
| `summary` | anche una voce per file: titolo, tipo, data, descrizione e tag, ripuliti da email, IBAN, CF, telefoni, targhe, numeri lunghi (i nomi propri possono restare) |
| `off` | niente |

## Sicurezza

- Nessuna cancellazione definitiva: i duplicati vanno nel Cestino (`~/.Trash`).
- Ogni operazione mostra prima il piano e chiede conferma (in batch serve `--apply`).
- **Audit interno**: ogni modifica è registrata in `~/.folder_intelligence/audit.jsonl` (append-only,
  una riga cifrata per evento, scrittura con lock + fsync). Per ogni file: origine, destinazione, dimensione,
  data di modifica e SHA-256 (se noto), più la fonte (`cli`/`chat`/`api`). Gli annullamenti sono eventi a loro
  volta: lo storico non viene mai riscritto. Si può annullare **qualsiasi** operazione, anche in parte
  (es. se un file era sparito dal Cestino, lo si ripristina dopo). File modificati dopo lo spostamento
  vengono ripristinati e segnalati.
- File nascosti, symlink, `.git`, `node_modules` e bundle macOS (`.app`, `.photoslibrary`) vengono ignorati.
- L'originale tenuto in ogni gruppo è quello senza "copia"/"(1)" nel nome, poi il più vecchio.

## API per l'app Mac

```bash
folder-intelligence --serve                 # http://127.0.0.1:8765 · documentazione interattiva su /docs
```

- In ascolto solo su `127.0.0.1`. Ogni endpoint (tranne `/health`) richiede
  `Authorization: Bearer <token>`; il token è in `~/.folder_intelligence/api_token` (permessi 0600).
- Lo schema OpenAPI (`/openapi.json`) può generare il client Swift (es. `swift-openapi-generator`).
- Le modifiche passano dagli stessi piani, conferme e audit della CLI.

| Metodo | Endpoint | Uso |
|---|---|---|
| GET | `/health`, `/status` | stato server, AI disponibile |
| POST | `/scans` `{path, duplicates, classify, index}` | avvia scansione/indicizzazione in background → `job` |
| GET · DELETE | `/scans/{id}` | avanzamento (`phase`, `done`, `total`, `fraction`, `detail`) · annulla |
| GET | `/commands` | tutti i comandi della chat, con parametri ed endpoint |
| POST | `/commands/{name}` `{path, target, args, wait}` | esegue **qualsiasi comando** come job; risposta completa se finisce entro `wait` secondi, altrimenti `202` |
| GET · DELETE | `/jobs` · `/jobs/{id}` | elenco/stato/risultato (testo + dati strutturati) · interrompe |
| POST | `/jobs/{id}/confirm` `{confirm}` | risponde alle conferme (`needs_confirmation`): spostamenti, indicizzazione |
| GET | `/jobs/events` | Server-Sent Events: avanzamento in tempo reale di tutti i job |
| GET | `/search?q=&path=` | ricerca ibrida (con `matched_by`) |
| POST | `/ask` `{question, path}` | risposta dai documenti con fonti |
| GET | `/describe?path=` · `/index/stats` | analisi di un file · stato indice |
| GET | `/summary?path=` | riepilogo cartella |
| GET | `/files?path=&category=&q=&sort=&limit=&offset=` | elenco file paginato |
| GET | `/duplicates?path=` | gruppi di duplicati |
| GET | `/folders?path=` · `/complete?text=` | navigazione cartelle · autocompletamento |
| POST | `/plans` `{kind: dedupe\|organize, path, target?}` | crea un piano (anteprima) |
| POST · DELETE | `/plans/{id}/apply` · `/plans/{id}` | esegue · scarta il piano |
| GET | `/operations` · `/operations/{id}` | audit delle modifiche |
| POST | `/operations/{id}/undo` · `/undo` | ripristina un'operazione · l'ultima |
| POST | `/chat` `{message, session_id?}` | chat (con `pending` quando serve conferma) |
| GET · POST | `/memory` | memoria |
| GET | `/export?path=&format=json\|md\|csv` | download report |

Ogni comando della chat ha la sua API: `POST /commands/largest {"path": "~/Documents", "args": {"n": 10}}`,
`/commands/search {"args": {"query": "polizza auto"}}`, `/commands/dedupe` (→ `needs_confirmation`,
poi `POST /jobs/{id}/confirm {"confirm": true}`), ecc. Il campo `result.data` contiene i dati
strutturati (elenchi di file, gruppi di duplicati, piani, risultati di ricerca…).

Esempio:

```bash
T=$(cat ~/.folder_intelligence/api_token)
curl -s -X POST -H "Authorization: Bearer $T" -H 'content-type: application/json' \
     -d '{"path":"~/Downloads"}' localhost:8765/scans
```

## Aggiungere un comando (skill)

Ogni comando è una classe in `src/folder_intelligence/commands/` (un file per comando),
caricata automaticamente. Puoi metterne anche in `~/.folder_intelligence/commands/`.

```python
# src/folder_intelligence/commands/free_space.py
from .. import shell
from ..i18n import N_, _
from .base import Command


class FreeSpaceCommand(Command):
    name = "free_space"
    group = "sistema"                                       # analisi | ricerca | organizzazione | memoria | sistema
    description = N_("Mostra lo spazio libero sul disco")   # usata dal router AI e da 'aiuto' (tradotta)
    examples = (N_("quanto spazio libero ho?"),)            # mostrati in 'aiuto' e dati al router
    keywords = ("spazio libero", "disco pieno", "free space", "disk full")  # riserva senza AI, ogni lingua
    priority = 30                                           # più basso = valutato prima

    async def run(self, ctx, intent):
        res = await shell.run(["df", "-h", str(ctx.directory)])   # comandi di sistema: sempre lista, mai shell
        return _("Spazio sul disco:") + "\n" + res.stdout
```

Poi aggiungi le traduzioni dei nuovi testi in `locales/en.json` (il test te li elenca).

Dentro `run` hai `ctx` con: `ctx.directory`, `await ctx.get_scan()`, `await ctx.get_duplicates(scan)`,
`ctx.ai` (facciata dei motori AI: `generate_json`, `chat`, `describe_image`, `embed`), `ctx.intelligence`
(analisi/indice/ricerca), `ctx.memory`, `ctx.pending` (piano da confermare → passa dall'audit),
`ctx.progress`, `ctx.cancel`. Il router AI conosce il comando da `description`, `examples`, `group`
e `params`: non serve altro per farlo usare nelle frasi libere.

Allo stesso modo un nuovo tipo di file si supporta con una classe in `extractors/`
(sottoclasse di `Extractor` con `extensions` e `async extract(path, ctx)`).

## Architettura

```
src/folder_intelligence/
  commands/      un file per comando della chat (plugin) + registro (base.py, __init__.py)
  engines/       motori AI intercambiabili (strategy): llamacpp, ollama, mlx
  extractors/    un file per tipo di contenuto (plugin): pdf, image, office, media, docintel…
  locales/       traduzioni (en.json); i18n.py: _(), lingua di macOS/--lang/Accept-Language
  router.py      dalla frase al piano di comandi (AI + regole di riserva)
  similarity.py  foto e documenti simili
  intelligence.py pipeline di comprensione, ricerca ibrida, domande (RAG locale)
  crypto.py      AES-256-GCM, chiave nel Portachiavi macOS
  history.py     cronologia comandi cifrata
  mcp_memory.py  registrazione nella memoria MCP (sealed/summary/off)
  privacy.py     rimozione dati personali dai testi in uscita
  index.py       indice SQLite FTS5 + embedding
  ai.py          facciata AIEngine: un motore per testo, visione, embedding
  shell.py       comandi di sistema sicuri (mdfind, mdls, ffprobe, tesseract…)
  threads.py     I/O con timeout in thread daemon (cartelle iCloud/rete)
  plugins.py     caricamento automatico dei plugin
  models.py      FileInfo, DuplicateGroup, ScanResult
  scanner.py     scansione metadati + duplicati su richiesta
  classifier.py  categorie, RuleClassifier, FileClassifier
  organizer.py   piani organize/dedupe, apply, undo (per operazione)
  audit.py       registro append-only delle modifiche ai file
  completion.py  autocompletamento cartelle/comandi (CLI e API)
  api.py         API HTTP FastAPI per l'app Mac
  memory.py      Strategy: inmemory | json (default) | lightrag (con fallback)
  exporter.py    report JSON / Markdown / CSV
  intents.py     normalizzazione frasi ed estrazione percorsi
  assistant.py   sessione: il `ctx` passato ai comandi
  cli.py         argparse, chat classica, batch, avvio dell'interfaccia a pannelli
  tui.py         chat a pannelli con box Attività (prompt_toolkit full-screen)
  jobs.py        attività in background condivise da chat e API
  selector.py    selettore sì/no con le frecce (chat classica)
tests/           pytest
```

Variabili d'ambiente: `FI_LANG`, `FI_ENGINE`, `FI_ROUTER_MODEL`, `FI_OLLAMA_URL`, `FI_OLLAMA_MODEL`, `FI_VISION_MODEL`, `FI_EMBED_MODEL`,
`FI_DOCINTEL_URL`, `FI_DOCINTEL_TOKEN`, `FI_MCP_MODE`, `FI_MCP_URL`, `FI_MCP_NAMESPACE`, `FI_MASTER_KEY`, `FI_DEBUG`, `FI_HOME` (default `~/.folder_intelligence`), `FI_TRASH`, `FI_API_TOKEN`.

## Test

```bash
.venv/bin/python -m pytest
```

## Licenza

MIT — vedi [LICENSE](LICENSE).
