# Guida utente

Dall'immagine alla stampa, senza mai aprire Blender.

---

## Il percorso in quattro passi

### 1. Carica l'immagine

Trascina un'immagine nella finestra, incollala con <kbd>Ctrl</kbd>+<kbd>V</kbd>
oppure scegli il file.

**Che immagine funziona meglio**

- soggetto ben staccato dallo sfondo (sfondo uniforme, chiaro o scuro purché
  diverso dal soggetto);
- figura intera, in piedi, vista frontale;
- niente ombre marcate proiettate sullo sfondo;
- se hai più viste dello stesso soggetto — fronte, lato, retro — caricale tutte:
  il modello risulta molto più fedele, perché il volume viene ricostruito
  intersecando le silhouette.

Un'immagine con sfondo trasparente (PNG con canale alfa) è il caso ideale.

### 2. Scrivi la descrizione

Non serve un testo elaborato. Serve **nominare gli elementi**:

> personaggio in stile Funko Pop con cappello, barba e spada

Le parole riconosciute non guidano solo la generazione: determinano anche come
il modello verrà diviso in pezzi. Se scrivi «scarpe», l'applicazione cercherà di
staccare le scarpe dalle gambe; se non lo scrivi, il piede resta parte della
gamba.

Termini riconosciuti (italiano e inglese): capelli, cappello, barba, baffi,
occhi, sopracciglia, orecchie, mani, braccia, gambe, scarpe, vestiti, mantello,
accessori, armi, basetta, decorazioni.

Puoi anche escludere: «senza basetta» oppure «senza mantello».

### 3. Genera

Premi **Genera modello 3D** e osserva il registro: ogni passo dice che cosa sta
facendo e quanto ha impiegato. Un modello tipico richiede uno o due minuti,
più il tempo del generatore 3D.

Puoi annullare in qualunque momento.

### 4. Esporta

Al termine trovi:

- l'**anteprima 3D** navigabile, con la legenda dei pezzi (clicca sul pallino
  per nascondere un pezzo e capire come è stato diviso il modello);
- il **punteggio di stampabilità** da 0 a 100;
- l'elenco dei **pezzi** con le dimensioni;
- gli **incastri** creati, con diametro e gioco;
- il **piano colori AMS**, se il modello è multicolore;
- i **file** nei formati scelti, più le istruzioni di montaggio.

---

## Come leggere il punteggio di stampabilità

| Punteggio | Significato |
|-----------|-------------|
| 90-100 | Pronto per la stampa |
| 70-89 | Stampabile con accorgimenti (supporti, orientamento) |
| 50-69 | Richiede correzioni |
| sotto 50 | Non stampabile senza intervento |

Il punteggio complessivo pesa il **pezzo peggiore**, non la media: un modello si
stampa solo se si stampano tutti i suoi pezzi.

Apri le segnalazioni per vedere che cosa è stato trovato. L'applicazione corregge
automaticamente ciò che può correggere senza tradire il tuo intento, e ti dice
esplicitamente sia che cosa ha corretto sia che cosa ha rinunciato a correggere,
con la ragione.

---

## Scegliere la tolleranza degli incastri

La tolleranza è il gioco fra spina e alloggiamento. È il parametro che decide
se i pezzi si montano bene o si rompono.

| Tolleranza | Accoppiamento | Quando usarla |
|------------|---------------|---------------|
| 0,05 – 0,10 mm | Forzato | Tenuta massima, montaggio a pressione, non si smonta più |
| 0,10 – 0,20 mm | Preciso | Montaggio a mano, smontabile con un po' di sforzo |
| 0,20 – 0,30 mm | Scorrevole | Montaggio e smontaggio facili — **è il valore consigliato** |
| 0,30 – 0,50 mm | Largo | Pezzi grandi o stampante non calibrata |

I valori si riferiscono a PLA con ugello da 0,4 mm. L'applicazione corregge da
sé il valore in base a ugello e altezza layer, e aggiunge un po' di gioco ai
fori stampati in verticale, che risultano sempre più stretti del nominale.

**Se dopo la stampa l'incastro non va bene:**

- la spina non entra → aumenta la tolleranza di 0,05 mm e ristampa il pezzo
  maschio (non serve rifare l'altro);
- il pezzo balla → riduci di 0,05 mm;
- entra ma si spacca la sede → passa alla spina conica, che si autocentra e
  distribuisce meglio lo sforzo.

---

## Tipi di incastro

**Spina cilindrica** — la scelta predefinita. Semplice, resistente, si stampa
bene. Con l'antirotazione attiva viene aggiunta una seconda spina più piccola
dove c'è spazio, così il pezzo non gira.

**Spina conica** — si autocentra durante il montaggio e perdona piccoli
disallineamenti. Ottima per la testa sul collo.

**Incastro quadrato** — impedisce la rotazione con un solo elemento. Utile dove
non c'è spazio per due spine.

**Incastro magnetico** — scava una sede su entrambi i pezzi per un magnete al
neodimio. Permette di smontare e rimontare all'infinito. Le istruzioni di
montaggio indicano quanti magneti servono e di che misura.

---

## Stampa multicolore (AMS / MMU)

Se il modello ha colori, l'applicazione costruisce un piano che assegna ogni
pezzo a uno slot di filamento e ordina la stampa per gruppi di colore.

Il vantaggio non è marginale: ogni cambio filamento costa oltre cento
millimetri cubi di spurgo. Ordinando i pezzi per colore, i cambi scendono dal
numero di pezzi al numero di colori.

**Il consiglio più efficace**: stampa un gruppo di colore alla volta,
disattivando gli altri oggetti nello slicer. Così la torre di spurgo non serve
proprio, e lo spreco è zero.

---

## Portare i file nello slicer

**Bambu Studio, OrcaSlicer, PrusaSlicer** → importa il file **3MF combinato**.
Contiene tutti gli oggetti già separati e i colori assegnati. Poi associa gli
slot AMS seguendo la legenda dei colori.

**Cura, Anycubic Slicer** → carica i singoli file **STL**, uno per pezzo. Sono
già appoggiati sulla faccia più stabile.

I pezzi vengono esportati già orientati e centrati: nella maggior parte dei casi
non serve ruotarli.

---

## Modalità principiante ed esperto

In **modalità principiante** l'interfaccia mostra solo l'essenziale: immagine,
descrizione, pulsante Genera.

In **modalità esperto** compare il pannello delle impostazioni avanzate:

- profilo stampante e altezza del modello;
- tipo di incastro e tolleranza, con la descrizione dell'accoppiamento che stai
  scegliendo;
- numero massimo di pezzi e separazione destra/sinistra;
- budget triangoli e conservazione del dettaglio;
- svuotamento del modello per risparmiare filamento;
- formati di esportazione.

Puoi passare da una all'altra in qualunque momento: le impostazioni restano.

---

## Cronologia, annulla, elaborazione batch

Ogni modifica viene salvata su disco. **Annulla** e **Ripeti** sono illimitati e
sopravvivono alla chiusura dell'applicazione.

Il salvataggio automatico scrive i progetti modificati ogni venti secondi, senza
riempire la cronologia di voci che non hai chiesto.

Per elaborare più progetti insieme, spunta le caselle nell'elenco a sinistra e
premi **Elabora in batch**.

---

## Problemi frequenti

**«Impossibile isolare il soggetto»**
Lo sfondo è troppo simile al soggetto. Usa uno sfondo uniforme e ben
contrastato, oppure ritaglia l'immagine con lo sfondo trasparente.

**Il modello viene diviso male**
Cita esplicitamente gli elementi nella descrizione. Se il modello non è una
figura umanoide, l'applicazione lo riconosce e non applica i tagli anatomici.

**Nessun incastro creato**
I pezzi non hanno superfici di contatto abbastanza ampie, oppure il materiale
disponibile è troppo sottile per scavare la sede. Il rapporto elenca le rinunce
con la ragione. Prova ad aumentare l'altezza del modello.

**Il punteggio resta basso**
Apri le segnalazioni. Isole e sbalzi si risolvono con i supporti dello slicer,
non modificando la geometria: l'applicazione non li tocca di proposito.

**La generazione è lenta**
Il generatore locale lavora su una griglia di voxel: la risoluzione predefinita
è un compromesso fra dettaglio e tempo. Configurando un provider cloud i tempi
si spostano sulla rete.
