# Come far girare tutto sul tuo Mac

Questa guida spiega, passo per passo, come mettere in piedi l'ambiente e far
partire il gNB emulato, l'xApp e la generazione dei CSV, così da poter
registrare il video dimostrativo. È scritta assumendo che tu non abbia mai
usato Docker prima, quindi va con calma e non saltare i passaggi.

Se qualcosa non corrisponde esattamente a quello che vedi sul tuo Mac (nomi
di cartelle diversi, ecc.), è normale: le immagini Docker del corso sono
precostruite e non ho potuto ispezionarle direttamente da qui. Dove non ero
sicuro al 100% di un percorso esatto, te lo segnalo e ti do un comando per
scoprirlo da solo in pochi secondi.

## 0. Cosa c'è in questa cartella

| File | A cosa serve |
|---|---|
| `ran_messages.proto` | Il nuovo schema protobuf (protocollo xApp <-> gNB) |
| `ran_metrics_xapp.py` | La xApp Python riscritta (sostituisce `myxapp.py`) |
| `gnb_message_handlers.c` / `.h` | L'handler C del gNB emulato riscritto |
| `visualize_ran_metrics.py` | Script per generare i grafici dai CSV |
| `report.tex` / `report.pdf` | Report da 3 pagine |
| `Technical_Report_PHY_MAC_Metrics_xApp.docx` | Report tecnico esteso (facoltativo) |

## 1. Cosa installare prima

Ti servono tre cose sul Mac:

1. **Docker Desktop** (per Mac). Scaricalo da [docker.com](https://www.docker.com/products/docker-desktop/) e installalo come una qualunque app. Dopo l'installazione aprilo almeno una volta e aspetta che l'icona della balena in alto nella barra dei menu smetta di "muoversi": significa che Docker è pronto.
   - Se hai un Mac con chip Apple Silicon (M1/M2/M3/M4), non cambia nulla nell'installazione, ma tienilo a mente per la sezione "Problemi comuni" più sotto.
2. **Git**. Se hai già usato il terminale per progetti universitari probabilmente ce l'hai già. Per controllare, apri il Terminale e scrivi:
   ```
   git --version
   ```
   Se non è installato, il Mac ti proporrà da solo di installare gli "Xcode Command Line Tools": accetta.
3. **Python 3** (solo per generare i grafici alla fine, non serve per far girare l'xApp che gira dentro Docker). I Mac moderni di solito hanno già `python3` preinstallato. Controlla con:
   ```
   python3 --version
   ```

Non ti serve installare `protoc`, `protoc-c` o un compilatore C: se ne occupa Docker, come vedrai al passo 3.

## 2. Scarica l'ambiente RIC + gNB emulato

Questo progetto si appoggia a un ambiente già pronto (RIC + gNB emulato + xApp)
distribuito dal corso come immagini Docker. Apri il Terminale, spostati nella
cartella dove vuoi lavorare (per esempio la tua cartella Documenti) e scrivi:

```bash
git clone --recurse-submodules https://github.com/ANTLab-polimi/ric-composer.git
cd ric-composer
```

`--recurse-submodules` è importante: dentro questo repository c'è un
sottoprogetto (`oai-oran-protolib`) che contiene proprio il file
`ran_messages.proto` che abbiamo riscritto.

## 3. Rigenera i file protobuf dal nuovo schema

Il file `ran_messages.proto` va copiato al posto di quello originale dentro
`oai-oran-protolib`, e poi va "compilato" per generare sia la versione Python
(usata dall'xApp) sia la versione C (usata dal gNB emulato).

```bash
cp /percorso/a/refactored/ran_messages.proto ric-composer/oai-oran-protolib/ran_messages.proto
cd ric-composer/oai-oran-protolib
```

Dentro questa cartella c'è già un piccolo `Dockerfile` pensato apposta per
questo: costruisce un'immagine Docker con `protoc` e `protoc-c` già installati
e, quando la fai partire, rigenera automaticamente tutto dentro `builds/`.
Da qui, senza installare nulla in locale:

```bash
docker build -t proto-builder .
docker run --rm -v "$(pwd)":/oai-oran-protolib proto-builder
```

Se tutto va bene, dentro `oai-oran-protolib/builds/` troverai `ran_messages_pb2.py`
(per la xApp) e `ran_messages.pb-c.c` / `ran_messages.pb-c.h` (per il gNB).

## 4. Avvia l'ambiente RIC

Torna nella cartella principale e avvia tutti i container con un solo comando:

```bash
cd ../..        # torna dentro ric-composer
docker-compose up -d
```

`-d` lo fa girare in background così il terminale resta libero. La prima
volta scaricherà diverse immagini da Docker Hub, può volerci qualche minuto.
Per controllare che tutto sia partito:

```bash
docker ps
```

Dovresti vedere container con nomi tipo `db`, `e2mgr`, `e2term`, `xapp`, `gnb`.

## 5. Sostituisci il codice della xApp

Copia i due file Python dentro il container `xapp` (che internamente lavora
nella cartella `/python_xapp`):

```bash
docker cp /percorso/a/refactored/ran_metrics_xapp.py xapp:/python_xapp/ran_metrics_xapp.py
docker cp ric-composer/oai-oran-protolib/builds/ran_messages_pb2.py xapp:/python_xapp/ran_messages_pb2.py
```

## 6. Sostituisci il codice del gNB emulato

Qui serve un piccolo passo di esplorazione, perché non ho potuto vedere da qui
esattamente come è organizzato il codice sorgente dentro l'immagine del gNB.
Apri una shell dentro il container:

```bash
docker exec -it gnb bash
```

e cerca dove si trova il file originale:

```bash
find / -name "gnb_message_handlers.c" 2>/dev/null
```

Questo ti darà il percorso esatto (qualcosa tipo `/qualche/percorso/gnb_message_handlers.c`).
Esci dalla shell (`exit`) e copia i due file al posto giusto usando il
percorso che hai appena trovato:

```bash
docker cp /percorso/a/refactored/gnb_message_handlers.c gnb:/quel/percorso/gnb_message_handlers.c
docker cp /percorso/a/refactored/gnb_message_handlers.h gnb:/quel/percorso/gnb_message_handlers.h
docker cp ric-composer/oai-oran-protolib/builds/ran_messages.pb-c.c gnb:/quel/percorso/ran_messages.pb-c.c
docker cp ric-composer/oai-oran-protolib/builds/ran_messages.pb-c.h gnb:/quel/percorso/ran_messages.pb-c.h
```

Poi rientra nel container e ricompila:

```bash
docker exec -it gnb bash
cd /quel/percorso        # la cartella dove hai trovato i file, o la cartella build/ accanto
make                     # oppure il comando di build che trovi lì (guarda se c'è un Makefile)
```

Se `make` non esiste o dà errore, cerca un Makefile o uno script di build
nella stessa cartella (`ls`) e usa quello. Se proprio non trovi nulla, il
container potrebbe non avere il compilatore: in quel caso puoi comunque
mostrare nel video il codice riscritto e la verifica di sintassi che ho
già fatto, spiegando che il rebuild va rifatto sull'ambiente completo del
corso.

## 7. Avvia gNB emulato, agente E2 e xApp

Servono tre finestre di Terminale separate (o tre schede).

**Finestra 1 - gNB emulator:**
```bash
docker exec -it gnb bash
./build/gnb_e2server_emu
```

**Finestra 2 - agente E2 (fa da ponte tra il gNB emulato e il RIC):**
```bash
docker exec -it gnb bash
cd ../ocp-e2sim
./run_e2sim.sh
```

**Finestra 3 - la xApp:**
```bash
docker exec -it xapp /bin/sh
cd /python_xapp
python3 ran_metrics_xapp.py
```

Se tutto è collegato correttamente, nella finestra 3 vedrai i log della xApp
che scopre il gNB, invia la sottoscrizione ed entra nel ciclo di polling ogni
500 ms.

## 8. Guarda i CSV crescere in tempo reale

I due file `e2sm_data.csv` ed `e2smue_data.csv` vengono scritti dentro il
container `xapp`, nella cartella `/python_xapp`. Per vederli crescere live
mentre registri il video, apri una quarta finestra di Terminale:

```bash
docker exec -it xapp sh -c "tail -f /python_xapp/e2smue_data.csv"
```

Quando vuoi analizzarli o mostrare i grafici, copiali sul Mac:

```bash
docker cp xapp:/python_xapp/e2sm_data.csv .
docker cp xapp:/python_xapp/e2smue_data.csv .
```

## 9. Genera i grafici

Sul tuo Mac (non dentro Docker), installa le due librerie Python necessarie
e lancia lo script:

```bash
pip3 install pandas matplotlib
python3 /percorso/a/refactored/visualize_ran_metrics.py \
  --cell-load-csv e2sm_data.csv \
  --ue-metrics-csv e2smue_data.csv \
  --out-dir plots
```

Alla fine trovi le immagini dentro la cartella `plots/`: aprile con
l'app Anteprima per controllarle prima di inserirle nel video.

## 10. Registra il video

Su Mac puoi registrare lo schermo senza installare nulla:

- **Cmd + Shift + 5** apre la barra degli strumenti di cattura schermo, poi scegli "Registra schermo intero" o "Registra parte selezionata".
- In alternativa, apri QuickTime Player, vai su *File > Nuova registrazione schermo*.

Cosa ha senso mostrare nei ~60 secondi richiesti dalla consegna:

1. Le tre finestre di terminale con gNB, agente E2 e xApp già avviati.
2. Il `tail -f` che mostra righe nuove che compaiono nel CSV mentre la xApp gira.
3. L'esecuzione di `visualize_ran_metrics.py` e i grafici risultanti aperti in Anteprima.

## Problemi comuni

- **"no matching manifest for linux/arm64" o simili su Mac Apple Silicon**: le immagini del corso sono costruite per `linux/amd64`. Aggiungi `platform: linux/amd64` sotto ogni servizio nel `docker-compose.yaml`, oppure lancia `docker-compose up` con `DOCKER_DEFAULT_PLATFORM=linux/amd64 docker-compose up -d`. Sarà più lento (emulazione), ma funziona.
- **Una porta è già occupata**: qualche altro programma sta già usando quella porta. Chiudi l'altro programma oppure cambia la porta nel `docker-compose.yaml`.
- **`docker-compose` non viene riconosciuto**: nelle versioni recenti di Docker Desktop il comando è `docker compose` (senza trattino). Prova quello se il primo non funziona.
- **I container non si vedono tra loro**: controlla con `docker network ls` che esista la rete `ric` e che tutti i container ci siano dentro (`docker inspect ric | grep -A5 Containers`).
- **Vuoi ripartire da zero**: `docker-compose down` ferma e rimuove tutti i container mantenendo le immagini scaricate, così la volta dopo riparte più veloce.

## Riepilogo comandi (una volta capito il flusso)

```bash
# una tantum
git clone --recurse-submodules https://github.com/ANTLab-polimi/ric-composer.git
cd ric-composer/oai-oran-protolib
cp /percorso/a/refactored/ran_messages.proto .
docker build -t proto-builder .
docker run --rm -v "$(pwd)":/oai-oran-protolib proto-builder
cd ../..

# ogni volta che vuoi fare una demo
docker-compose up -d
docker cp /percorso/a/refactored/ran_metrics_xapp.py xapp:/python_xapp/ran_metrics_xapp.py
docker cp oai-oran-protolib/builds/ran_messages_pb2.py xapp:/python_xapp/ran_messages_pb2.py
# (gNB: vedi passo 6, solo se non l'hai già fatto in precedenza)
# poi le tre finestre del passo 7, e infine visualize_ran_metrics.py sul Mac
```
