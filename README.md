# erola producció

Aquest projecte és una aplicació de Windows/Linux per gestionar la traçabilitat de materials i productes envasats.

## Funcionalitats principals

- Alta de productes rebuts amb nom, lot, procedència i caducitat.
- Reimpressió i edició de recepcions, conservant el codi de barres original.
- Importació i exportació de múltiples receptes en JSON UTF-8.
- Etiquetatge amb codi de barres en format Code128.
- Envasat d'un producte amb una o diverses matèries primeres i quantitat.
- Registre de barreges sense envasar, amb codi de barres propi i etiqueta amb la traçabilitat de tots els ingredients.
- Historial de preparacions a Receptes, amb envasat posterior i impressió d'una etiqueta per unitat.
- Importació de CSV amb selecció de columnes: SKU, nom i pes.
- Generació automàtica de lot d'envasat i càlcul de la caducitat mínima.
- Impressió d'etiquetes cap a la impressora Epson ColorWorks CW-C4000e si la impressora està configurada a l'ordinador.
- Base de dades SQLite amb historial complet.
- Cerca i exportació de la sessió actual.

## Requisits

- Python 3.11 o superior.
- Windows 10/11 o Linux.
- Una impressora d'etiquetes compatible amb CUPS o Windows Printer Driver (opcional).

## Instal·lació

1. Obre una terminal dins de la carpeta del projecte.
2. Crea un entorn virtual i activa'l:
   - Windows:
     - `python -m venv .venv`
     - `.venv\Scripts\activate`
   - Linux/macOS:
     - `python3 -m venv .venv`
     - `source .venv/bin/activate`
3. Installa les dependències:
   - `pip install -r requirements.txt`
4. Executa l'aplicació:
   - `python app.py`

## Configuració de la impressora

La impressora d'etiquetes es configura des del programa en la pestanya d'envasat si et cal. Si la impressora està connectada i disponible al sistema, el programa intenta enviar la feina d'impressió. Els fitxers es desen sempre a `output/labels`, també si no hi ha impressora o falla l'enviament, i es poden imprimir manualment.

Cada etiqueta té un nom únic amb producte, lot, data i hora, inclosos microsegons i un sufix per evitar col·lisions, per exemple `Farina_LOT-1_20261015_123045_123456_a1b2c3d4.png`. Els caràcters no vàlids per a noms de fitxer es substitueixen. Les reimpressions no sobreescriuen etiquetes anteriors. Si es demanen diverses unitats, es desa un fitxer per unitat abans de començar a imprimir.

### Formats impresos

- **Recepció (PNG, 300 ppp):** nom complet del producte, proveïdor, lot d'entrada, origen si està informat, caducitat i codi de barres Code128 amb el valor llegible a sota.
- **Envasat (PNG, 300 ppp):** nom complet del producte, número de lot i consum preferent; origen només si està informat. No inclou codi de barres ni les dades de recepció.
- **Recepta / barreja preparada (PDF A4):** nom de la barreja, lot de sortida, data de producció i consum preferent. La taula d'ingredients mostra nom, proveïdor, lot d'entrada, caducitat i origen, amb columnes proporcionades i text ajustat. Conserva els ingredients de barreges niades i els codis de traçabilitat. Les taules llargues continuen en pàgines numerades amb capçaleres repetides. També es manté el codi de barres de la barreja per poder-la escanejar a Envasat.

El full de recepta es genera en confirmar la impressió de traçabilitat quan es prepara una barreja a Envasat; no en desar només la definició d'una recepta sense ingredients rebuts ni lot de producció.

### Icona del programa

La icona original és `icon/erola-traçabilitat_ICON.png`. L'aplicació la carrega per a la finestra principal i els diàlegs, independentment de la carpeta de treball. El fitxer `GestorTracabilitat.spec` també la incorpora com a recurs de l'executable de Windows; PyInstaller la converteix automàticament al format d'icona necessari.

Per regenerar l'executable de Windows amb la icona, executa `build_exe.bat`. El resultat és `dist/windows/erola producció.exe`.

### Executable per a Linux Mint

L'executable Linux s'ha de construir en Linux: PyInstaller no converteix un EXE de Windows en un executable Linux. El mateix fitxer de configuració produeix `dist/linux/erola producció`, sense extensió `.exe`, amb el nom públic i la icona de l'aplicació.

En una versió recent de Linux Mint amb Python 3.11 o superior, instal·la les eines necessàries i executa:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk python3-pip binutils cups-client
bash build_linux.sh
```

El compilador comprova la versió de Python, crea `.venv-linux`, instal·la les dependències i genera el binari. Si el Python que inclou la teva versió de Mint és anterior a 3.11, cal disposar abans d'un Python 3.11 o superior amb els seus paquets Tkinter i venv; el compilador no modifica els repositoris del sistema ni substitueix el Python de Mint.

La compatibilitat del binari Linux depèn de l'arquitectura i de les biblioteques del sistema on es construeix. Construir-lo en el mateix Linux Mint on s'utilitzarà evita assumir una versió concreta de Mint. No es pot donar per verificat fins que la construcció i l'arrencada s'hagin provat en aquell sistema.

Per obrir-lo després de construir-lo:

```bash
"./dist/linux/erola producció"
```

Els executables són portables: les carpetes `data` i `output` es creen al costat del binari. Cal posar-lo en una carpeta amb permisos d'escriptura. La construcció no incorpora la base de dades real, les receptes ni les etiquetes de l'usuari; aquestes dades s'han de traslladar separadament si cal conservar-les.

## Estructura del projecte

- `app.py`: punt d'entrada.
- `src/app.py`: interfície principal.
- `src/db.py`: base de dades SQLite.
- `src/core.py`: lògica de lot, caducitat i CSV.
- `src/label_print.py`: generació i impressió d'etiquetes.
- `src/csv_import.py`: lectura d'imports CSV.
- `data/`: base de dades i fitxers persistents.
- `output/labels/`: etiquetes generades.

## Ús bàsic

1. A la pestanya de recepció, registra cada producte rebut.
2. Escaneja o escriu el codi i selecciona el material en la pestanya d'envasat.
3. Indica la quantitat i crea el lot d'envasat.
4. Importa el teu CSV per a productes envasats per pes.
5. A Historial / exportació, selecciona una sessió a l'esquerra i exporta-la amb el botó de sota.

Els camps de text editables ressalten amb un fons groc clar mentre tenen el focus i recuperen el fons original en sortir-ne. Això també s'aplica als editors de cel·les i als diàlegs; els camps de només lectura no es ressalten.

### Sessions de l'historial

La pestanya Historial / exportació mostra una llista de sessions a l'esquerra de la taula, amb identificador i data d'inici. Per defecte se selecciona la sessió actual, marcada amb «(actual)». Seleccionar-ne una altra mostra els seus envasats; per defecte, la cerca per nom, lot, origen o dates només s'aplica a aquesta sessió.

El checkbox «Totes les sessions», al costat de «Buscar», amplia la cerca i els suggeriments a totes les sessions. En aquest mode, els resultats es mostren agrupats, amb una franja separadora que identifica cada sessió i la seva data d'inici. Les sessions que contenen resultats de la cerca ressalten en verd clar i negreta a la llista de l'esquerra, sense canviar la sessió seleccionada per exportar. El ressaltat desapareix quan es neteja el text, no hi ha resultats o es desactiva la cerca global.

Els suggeriments del cercador inclouen noms, lots, orígens i dates del mateix abast. Ignoren accents, admeten errades i variacions dels espais, i prioritzen les coincidències exactes abans de les aproximades; per exemple, «Frnaa» pot suggerir «Farina». Pots seleccionar-ne un amb el ratolí o amb fletxa avall i Intro per aplicar la cerca amb el text correcte.

El botó «✕» neteja el text, tanca els suggeriments i torna a mostrar els registres sense filtre de text, mantenint l'estat de «Totes les sessions».

El botó sota la llista mostra «Exportar sessió actual» quan està seleccionada la sessió actual i «Exportar sessió seleccionada» en qualsevol altra. Exporta tots els envasats d'aquella sessió, encara que la cerca en mostri només una part o estigui activada la cerca global, a `output/sessions/sessio_<id>.csv`. En obrir una altra base de dades se selecciona la seva nova sessió actual.

### Editar i reimprimir recepcions

A «Últims productes rebuts», el botó quadrat amb una icona d'impressora a la dreta de cada fila torna a imprimir-ne l'etiqueta. Fes doble clic a una recepció per modificar nom, proveïdor, lot, origen, caducitat, codi de barres i data de recepció. «Guardar» desa els canvis; «Cancel·lar» els descarta; «Imprimir» imprimeix les dades visibles sense desar-les. La data de recepció accepta `AAAA-MM-DD HH:MM:SS` o una data sense hora (que es desa a les 00:00:00). No es permet un codi de barres buit o utilitzat per una altra recepció. Els registres d'envasat i les preparacions anteriors conserven la seva traçabilitat històrica.

### Importar i exportar receptes

A sota de la llista «Receptes existents», a la mateixa línia que la cerca i alineats a la dreta, hi ha els botons «Importar...» i «Exportar...». Per exportar diverses receptes, selecciona-les amb Ctrl+clic o Maj+clic; si no hi ha cap selecció, s'exporten totes. La importació permet seleccionar diversos fitxers i incorpora totes les receptes que contenen, sense necessitat que els productes ja existeixin al catàleg.

El format és JSON UTF-8, llegible per editors de text i altres programes. Inclou nom, SKU i ingredients amb les quantitats (un número o `null` si no s'han indicat). Els SKU són text per conservar zeros inicials. Exemple:

```json
{
   "format": "erola.recipes",
   "version": 1,
   "recipes": [
      {
         "sku": "001",
         "name": "Barreja",
         "ingredients": [
            {"sku": "002", "name": "Farina", "quantity": 2.5},
            {"sku": "003", "name": "Sal", "quantity": null}
         ]
      }
   ]
}
```

També es pot importar directament la llista de receptes, sense l'objecte exterior. Si ja existeix una recepta amb el mateix SKU (o el mateix nom si no té SKU), es demana confirmació abans de substituir-la, inclosos els ingredients. Es valida tot el contingut abans d'importar i qualsevol error impedeix desar parcialment el conjunt. La importació i l'edició de recepcions es poden desfer i refer des del menú «Edita».

### Barreges i envasat posterior

A Envasat, afegeix els ingredients i confirma el producte final. En confirmar la quantitat amb Intro, una barreja de diversos ingredients es desa com a preparació i es pregunta si vols imprimir-ne l'etiqueta de traçabilitat. Deixa la quantitat buida per preparar-la sense envasar; si introdueixes una quantitat, també se'n registra l'envasat i s'imprimeix una etiqueta de producte per unitat.

El codi de les noves barreges té el format `YYYYMMDD[SKU][NN]`: data de producció, SKU complet (conservant zeros inicials) i seqüència de dos dígits, de 01 a 99, per SKU i dia. Per exemple, el primer codi disponible del SKU `003` el 15/10/2026 és `2026101500301`. Recepcions i barreges eviten els codis ja ocupats en qualsevol dels dos registres per no confondre'ls en escanejar; per això una seqüència pot saltar un número utilitzat per una recepció. Si s'esgoten els dos dígits, el programa avisa i no desa una preparació parcial. Si el producte no té SKU, s'utilitza una seqüència diària comuna sense el segment SKU. Els codis antics `MIX-...` es continuen llegint sense modificar les preparacions existents.

Amb el formulari d'Envasat buit, escanejar el codi d'una barreja recupera automàticament tots els ingredients originals, el producte final i el lot de sortida. Es pot envasar amb aquest lot sense tornar a preguntar pel full de producció. Si s'afegeix o es retira qualsevol ingredient, es crea una nova preparació amb un lot i un codi nous i es torna a preguntar si es vol imprimir el full, encara que només quedi un ingredient. Aquests canvis es poden desfer i refer.

També pots trobar la preparació pel nom o SKU al mateix cercador dels productes rebuts. Si ja hi ha ingredients afegits, seleccionar una altra barreja la incorpora com a ingredient niat, conservant-ne la traçabilitat original i sense esborrar els ingredients que s'havien introduït.

A la part inferior de Receptes apareix l'historial de preparacions amb nom, SKU, lot, caducitat, data i codi de barres. L'historial d'envasats també mostra el codi de la preparació vinculada, si n'hi ha. Selecciona una preparació i prem Intro per passar al camp Quantitat. Introdueix un enter positiu i prem Intro o el botó «Imprimir i registrar envasat». Es manté el lot de la preparació, es registra l'envasat i s'imprimeix el nombre indicat d'etiquetes de producte. Si falla la impressió, el registre es conserva i es mostra un avís.

Fes doble clic a una preparació per obrir «Modificar preparació». Nom i SKU són de només lectura. Pots canviar el lot, el codi de barres, el consum preferent, la data de producció i les dades dels ingredients. Fes doble clic en una cel·la per editar-la; també pots afegir o eliminar ingredients. Les barreges niades es mostren desplegades i es conserven les dades no modificades. «Guardar» valida i desa; «Cancel·lar» descarta els canvis. L'edició es pot desfer i refer. Els envasats anteriors mantenen els seus lots i les còpies històriques dels ingredients.

Els suggeriments de productes, ingredients, proveïdors i orígens ignoren majúscules i accents, admeten fragments del text i ofereixen coincidències aproximades si hi ha petites errades. Per exemple, «Marialluisa» suggereix «Marialluïsa» i «Reloj» suggereix «El Reloj». Les coincidències exactes tenen prioritat i es conserven els noms originals i els lots individuals.

## Comprovar i obrir el programa amb doble clic

Fes doble clic a `run_and_verify.bat`.

Aquesta és l'única manera recomanada: primer valida el programa (proves + compilació) i, si tot està correcte, obre l'aplicació automàticament.

## Notes

Aquest programa està pensat per a un ús pràctic i senzill en equips modestos. Si vols, el següent pas és adaptar-lo a la teva impressora Epson ColorWorks CW-C4000e i a la configuració concreta del teu procés de producció.
