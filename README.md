# erola producció

Aquest projecte és una aplicació per a Windows per gestionar la traçabilitat de materials i productes envasats.

## Versions del programa

La versió actual és **0.1.0-alpha.4**. El número es desa a `VERSION` i els paquets de cada versió es conserven en una carpeta separada dins de `versions/`, sense sobreescriure les anteriors. Consulta [l'historial de versions](versions/README.md) per veure les novetats i el procés de publicació.

Des de PowerShell a Windows, `./build_release.ps1` executa les proves i crea l'executable de Windows amb la versió al nom, juntament amb la suma SHA-256. No inclou les dades de l'usuari. Per publicar la següent alfa, actualitza `VERSION` a `0.1.0-alpha.5` i afegeix les novetats a l'historial abans d'executar el script.

## Funcionalitats principals

- Alta de productes rebuts amb nom, lot, procedència i caducitat.
- Reimpressió i edició de recepcions, conservant el codi de barres original.
- Recepció amb impressió automàtica, sense preguntar si es vol imprimir ni mostrar una confirmació de registre. Es mantenen els avisos si falta impressora, plantilla assignada o falla la impressió.
- Importació i exportació de múltiples receptes en JSON UTF-8.
- Etiquetatge amb codi de barres en format Code128.
- Envasat d'un producte amb una o diverses matèries primeres i quantitat.
- Registre de barreges sense envasar, amb codi de barres propi i etiqueta amb la traçabilitat de tots els ingredients.
- Historial de preparacions a Receptes, amb envasat posterior i impressió d'una etiqueta per unitat.
- Importació de CSV amb selecció de columnes: SKU, nom, pes i categoria, i exportació del catàleg actualitzat.
- Avís de productes fora del catàleg amb suggeriments aproximats i alta de productes nous.
- Edició i unificació de proveïdors i orígens a tota la base de dades.
- Generació automàtica de lot d'envasat i càlcul de la caducitat mínima.
- Impressió automàtica d'etiquetes amb Brother QL-600 o altres impressores instal·lades, com Epson ColorWorks.
- Productes amb subpestanyes Catàleg i Rebuts, amb historial complet, cerca, ordenació i edició de recepcions.
- Avisos en envasar productes caducats o que caduquen en menys de tres mesos naturals, també ingredients de barreges niades.
- Fulls de producció i sessions en PDF A4 amb diàleg estàndard d'impressió de Windows.
- Base de dades SQLite amb historial complet.
- Cerca i exportació de la sessió actual.

## Requisits

- Python 3.11 o superior.
- Windows 10/11.
- Una impressora d'etiquetes amb controlador de Windows (opcional).

## Instal·lació

1. Obre una terminal dins de la carpeta del projecte.
2. Crea un entorn virtual i activa'l:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
3. Installa les dependències:
   - `pip install -r requirements.txt`
4. Executa l'aplicació:
   - `python app.py`

## Configuració de la impressora

El menú **Impressió > Configurar impressores...** té apartats separats per a l'etiquetadora i la impressora normal A4. Selecciona manualment cada cua; el nom inclou el port, per distingir, per exemple, la Brother connectada per USB d'una cua `FILE:`. Per a l'etiquetadora indica l'amplada del rotlle i la llargada de l'etiqueta en mil·límetres. Aquestes dimensions s'apliquen explícitament a la feina; no es detecta automàticament la mida del rotlle. Si el controlador no accepta el format introduït, la impressió s'atura amb un error. Les preferències del controlador permeten configurar el tall, la qualitat i altres opcions del fabricant; a Windows es desen per a l'usuari i poden afectar altres aplicacions que utilitzin la mateixa cua.

Les recepcions utilitzen sempre una plantilla P-touch, tant en registrar-les com en reimprimir-les des de Productes > Rebuts o des del seu editor. Les etiquetes genèriques d'envasat, quan les plantilles estan desactivades, s'envien automàticament, sense diàleg per unitat, a l'etiquetadora seleccionada mitjançant Windows GDI i el controlador del fabricant. El programa torna a maquetar els textos i el codi de barres a l'amplada i la llargada indicades manualment. Les dades dels PNG anteriors es conserven per poder-los reimprimir; les plantilles P-touch conserven la seva mida pròpia. Als documents A4, el diàleg estàndard de Windows s'obre amb la impressora normal seleccionada, i encara permet canviar-la, les còpies i les pàgines. La configuració es desa a `data/printing.json`, independentment de la base de dades, i es manté en reiniciar. Les plantilles P-touch requereixen b-PAC.

Els documents PDF A4 no s'envien a la impressora d'etiquetes: a Windows obren el diàleg natiu amb selecció d'impressora, còpies, interval de pàgines i propietats del controlador. No cal tenir cap visor PDF associat: PyMuPDF renderitza les pàgines i GDI les envia a la cua. Cancel·lar el diàleg no és un error.

Els fitxers es desen sempre abans d'imprimir, també si no hi ha impressora o falla l'enviament. Confirmar l'enviament a la cua no garanteix la sortida física: paper, connexió i incidències del dispositiu es consulten a la cua de Windows.

Cada etiqueta té un nom únic amb producte, lot, data i hora, inclosos microsegons i un sufix per evitar col·lisions, per exemple `Farina_LOT-1_20261015_123045_123456_a1b2c3d4.png`. Els caràcters no vàlids per a noms de fitxer es substitueixen. Les reimpressions no sobreescriuen etiquetes anteriors. Si es demanen diverses unitats, es desa un fitxer per unitat abans de començar a imprimir.

### Plantilles P-touch d'etiquetes

**Impressió > Plantilles d'etiquetes...** permet gestionar dissenys `.lbx` de P-touch Editor per a recepcions i envasats. Els fulls de producció i les sessions A4 no canvien.

1. A **Plantilles**, prem **Importar...** i selecciona els fitxers originals. Pots canviar el nom de cada plantilla sense modificar el fitxer original.
2. Selecciona una plantilla i relaciona els seus noms d'objecte P-touch amb dades del programa: nom del producte, SKU, lot (d'entrada en recepció o d'envasat), caducitat ISO o dia/mes/any, origen i codi de barres. En recepció també hi ha **Proveïdor** i **Data de recepció** (AAAA-MM-DD); en envasat, **Pes** i **Data d'envasat**. El codi de recepció és el codi original del registre, també en reimprimir. **Desar camp** aplica cada relació. Els objectes no relacionats es mantenen fixos, inclosos logotips, textos i imatges. Si un nom no s'ha detectat, es pot introduir manualment; ha de coincidir exactament amb el nom d'objecte definit a P-touch Editor. No és el text visible de l'etiqueta. Cal configurar almenys un camp variable abans d'imprimir per evitar enviar textos de mostra. Si s'assigna un camp que no correspon a l'acció, la impressió s'atura amb un avís.
3. A **Accions**, selecciona **Recepció de productes**, tria la plantilla i prem **Assignar a l'acció**. La recepció necessita una plantilla: si no n'hi ha cap d'assignada, apareix un avís i no s'imprimeix cap etiqueta genèrica, però la recepció queda registrada.
4. Les plantilles d'envasat es gestionen a **Productes**: cerca i selecciona un o diversos productes, tria la plantilla i prem **Assignar als seleccionats**; **Cap plantilla** elimina l'assignació. S'aplica tant a productes simples com a barreges preparades. L'associació utilitza el SKU quan n'hi ha; si no, el nom del producte. Si es modifica aquesta identificació al catàleg, cal revisar-ne l'assignació. Activa **Utilitzar plantilles P-touch per als envasats** perquè s'utilitzin. Si falta una assignació o un objecte variable, apareix un avís i l'envasat continua registrat, sense imprimir una etiqueta genèrica en substitució.

**Substituir...** actualitza el disseny mantenint el nom, els camps i les associacions amb productes i accions. Si el nou disseny canvia els noms d'objecte, cal actualitzar-ne les relacions. **Editar a P-touch** obre una còpia de treball amb l'editor associat al sistema; després de desar al mateix fitxer i tancar l'editor, **Recollir canvis** incorpora el disseny actualitzat. La maquetació es fa amb P-touch Editor, no amb un editor de dissenys propi del programa. També es poden exportar els originals o eliminar plantilles que no tinguin productes ni accions assignats.

Els dissenys i les associacions es desen dins de la base de dades, sense dependre de la ubicació dels fitxers importats, i s'inclouen a **Desar com** i a desfer/refer. Les còpies de treball de l'editor es desen a `data/template_edits`; no s'eliminen automàticament, per no perdre una edició oberta.

La impressió de plantilles requereix **Windows, una Brother compatible, el controlador oficial i el component Brother b-PAC** de la mateixa arquitectura que l'aplicació (32 o 64 bits). P-touch Editor només és necessari per modificar el disseny. b-PAC s'instal·la per separat des de [Brother](https://support.brother.com/g/s/es/dev/en/bpac/download/index.html); no està inclòs a les dependències Python. La mida i el disseny es defineixen a la plantilla i cal que siguin compatibles amb el rotlle i el controlador. Aquest motor no és una solució per imprimir `.lbx` amb Epson ColorWorks; es pot desactivar per continuar amb les etiquetes genèriques.

Abans d'enviar una feina es desa un JSON a `output/labels` amb la plantilla completa, els valors i el nombre de còpies. **Impressió > Reimprimir feina de plantilla...** permet tornar-la a enviar, fins i tot després de canviar la plantilla actual, sense crear nous registres d'envasament. Cal comprovar la cua abans de repetir una feina fallida per evitar duplicats si se n'ha imprès una part.

### Formats impresos sense plantilla

- **Recepció (PNG, 300 ppp):** nom complet del producte, proveïdor, lot d'entrada, origen si està informat, caducitat i codi de barres Code128 amb el valor llegible a sota.
- **Envasat (PNG, 300 ppp):** nom complet del producte, número de lot, consum preferent i pes net per unitat en kg; origen només si està informat. No inclou codi de barres ni les dades de recepció.
- **Recepta / barreja preparada (PDF A4):** nom de la barreja, lot de sortida, data de producció i consum preferent. La taula d'ingredients mostra nom, proveïdor, lot d'entrada, caducitat i origen, amb columnes proporcionades i text ajustat. Conserva els ingredients de barreges niades i els codis de traçabilitat. Les taules llargues continuen en pàgines numerades amb capçaleres repetides. També es manté el codi de barres de la barreja per poder-la escanejar a Envasat.

El full de recepta es genera en confirmar la impressió de traçabilitat quan es prepara una barreja a Envasat; no en desar només la definició d'una recepta sense ingredients rebuts ni lot de producció.

Si no es pot imprimir una etiqueta, l'avís mostra «No s'ha trobat impressora, etiqueta guardada a» i el directori clicable on s'ha desat. L'enllaç obre la carpeta a l'Explorador de fitxers. Si falla un document A4, l'avís mostra el motiu i el camí del PDF conservat.

El checkbox «No mostrar més avisos d'impressora durant aquesta sessió» silencia aquests avisos fins que s'inicia una nova sessió (en reiniciar el programa o obrir una base de dades). Les etiquetes es continuen generant i desant, i el programa continua intentant imprimir-les.

### Icona del programa

La icona original és `icon/erola-traçabilitat_ICON.png`. L'aplicació la carrega per a la finestra principal i els diàlegs, independentment de la carpeta de treball. El fitxer `GestorTracabilitat.spec` també la incorpora com a recurs de l'executable de Windows; PyInstaller la converteix automàticament al format d'icona necessari.

Per regenerar l'executable de Windows amb la icona, executa `build_exe.bat`. El resultat és `dist/windows/erola producció.exe`.

### Executable portable

L'executable és portable: les carpetes `data` i `output` es creen al costat del fitxer `.exe`. Cal posar-lo en una carpeta amb permisos d'escriptura. La construcció no incorpora la base de dades real, les receptes ni les etiquetes de l'usuari; aquestes dades s'han de traslladar separadament si cal conservar-les.

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

L'Intro del teclat numèric té el mateix comportament que l'Intro normal, també als suggeriments, diàlegs i editors de cel·les.

### Catàleg de productes

La pestanya **Productes** conté **Catàleg** i **Rebuts**. Rebuts mostra totes les recepcions, no només les 20 més recents de Recepció: nom, SKU del catàleg actual, proveïdor, lot d'entrada, origen, caducitat, codi de barres i data de recepció. La cerca compartida amb suggeriments admet noms sense accents, SKU, codis, lots i dates ISO o dia/mes/any. Clicar una capçalera ordena la columna en tots dos sentits; fer doble clic en una fila obre «Modificar recepció».

Les recepcions caducades es mostren en vermell i les que caduquen en menys de tres mesos naturals en taronja. El dia de caducitat es considera dins l'avís, però no caducat fins l'endemà. El mateix criteri s'aplica als avisos d'envasament, inclosos els ingredients niats d'una barreja. L'avís informa, però no bloqueja el registre de l'envasament.

En confirmar un nom que no coincideix amb cap producte del catàleg, apareix un avís amb els noms més semblants. Un doble clic sobre una alternativa substitueix el nom introduït. També es pot seleccionar una alternativa i prémer Intro o «Seleccionar».

El mateix diàleg permet crear un producte amb SKU, nom, pes en kg i categoria. Només el nom és obligatori; el pes buit es desa sense valor, no com un pes zero. Els SKU es conserven com a text, inclosos els zeros inicials. No es permet crear un producte amb un nom o SKU ja existent. Cancel·lar conserva el text original. La comprovació es fa en confirmar el nom a Recepció i Envasat, i en confirmar el producte o desar els ingredients d'una recepta; també s'aplica a Recepció ràpida.

A la part inferior dreta de Productes, al costat del resum de modificacions, hi ha el botó de productes modificats i «Exportar catàleg CSV», que desa tots els productes, independentment de la cerca activa. El CSV UTF-8 inclou les columnes SKU, Nom, Pes i Categoria amb els valors actuals, incloses les modificacions i les altes manuals. Es pot tornar a importar seleccionant aquestes columnes; exportar no esborra les marques de modificació ni substitueix automàticament el CSV original.

### Proveïdors i orígens

El menú «Base de dades > Proveïdors i orígens...» obre una finestra amb dues llistes. Selecciona un valor, modifica «Nou valor» i prem «Desar». La confirmació aplica el canvi a totes les recepcions, preparacions i envasats relacionats, inclosos els ingredients de barreges niades i els ingredients carregats a Envasat.

Si el valor de destí ja existeix, les referències queden unificades: per exemple, canviar «España» a «Espanya» deixa un únic origen. El canvi es pot desfer i refer des del menú Edita. Els fitxers d'etiquetes i CSV ja exportats no es reescriuen; cal tornar-los a generar per reflectir la correcció.

La primera arrencada crea `data/Registre 1.db`. Si encara no existeix i hi ha una base antiga `data/trazabilitat.db`, s'obre aquesta per conservar l'accés a les dades existents.

A Envasat i Receptes, arrossega el separador horitzontal situat sobre l'historial per ajustar-ne l'alçada. Durant l'arrossegament es mou una guia i el contingut s'ajusta en deixar anar el ratolí per evitar parpelleigs; també s'aplica als separadors verticals d'Envasat. La part superior d'Envasat conserva espai per a almenys tres ingredients afegits i els camps de producte final i quantitat. Els historials d'envasats i el CSV de sessió inclouen el pes net per unitat en kg (columna `pes_kg` al CSV), conservat tal com era en registrar l'envasat encara que després es modifiqui el catàleg.

### Sessions de l'historial

La pestanya Historial / exportació mostra una llista de sessions a l'esquerra de la taula, amb identificador i data d'inici. Per defecte se selecciona la sessió actual, marcada amb «(actual)». Seleccionar-ne una altra mostra els seus envasats; per defecte, la cerca per nom, lot, origen o dates només s'aplica a aquesta sessió.

El checkbox «Totes les sessions», al costat de «Buscar», amplia la cerca i els suggeriments a totes les sessions. En aquest mode, els resultats es mostren agrupats, amb una franja separadora que identifica cada sessió i la seva data d'inici. Les sessions que contenen resultats de la cerca ressalten en verd clar i negreta a la llista de l'esquerra, sense canviar la sessió seleccionada per exportar. El ressaltat desapareix quan es neteja el text, no hi ha resultats o es desactiva la cerca global.

Els suggeriments del cercador inclouen noms, lots, orígens i dates del mateix abast. Ignoren accents, admeten errades i variacions dels espais, i prioritzen les coincidències exactes abans de les aproximades; per exemple, «Frnaa» pot suggerir «Farina». Pots seleccionar-ne un amb el ratolí o amb fletxa avall i Intro per aplicar la cerca amb el text correcte.

La cerca i els suggeriments també inclouen els ingredients de cada envasat, inclosos els de barreges niades. Buscar el nom, SKU, lot o codi d'un ingredient mostra els productes envasats que el contenen. Es consulten les dades històriques desades a cada envasat, no la definició actual de la recepta, i es respecta la sessió seleccionada o l'opció «Totes les sessions».

El botó «✕» neteja el text, tanca els suggeriments i torna a mostrar els registres sense filtre de text, mantenint l'estat de «Totes les sessions».

El botó sota la llista mostra «Exportar sessió actual» quan està seleccionada la sessió actual i «Exportar sessió seleccionada» en qualsevol altra. Exporta tots els envasats d'aquella sessió, encara que la cerca en mostri només una part o estigui activada la cerca global, a `output/sessions/sessio_<id>.csv`. En obrir una altra base de dades se selecciona la seva nova sessió actual.

El CSV incorpora `lot_entrada`, amb el lot del proveïdor per als productes simples i buit per a les barreges vinculades a un full de producció. La taula de l'historial mostra «Lot d'entrada» amb el mateix criteri. **Imprimir sessió** genera també `output/sessions/sessio_<id>.pdf`, amb tots els registres de la sessió seleccionada independentment de la cerca, en A4 horitzontal amb capçalera repetida i pàgines numerades, i obre el diàleg d'impressió estàndard.

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

El botó amb icona d'impressora a la dreta de cada preparació genera i imprimeix el seu full de producció PDF A4, també si ja s'ha envasat o mai no se n'havia generat el full. Utilitza les dades i els ingredients desats de la preparació, sense crear ni modificar registres de preparació o envasat. Si falla la impressora, el PDF es conserva i l'avís permet obrir-ne el directori.

El cercador de l'historial de preparacions filtra per nom, SKU, lot, codi i dates, i també per les dades dels ingredients, inclosos els niats. Mostra suggeriments que ignoren accents i admeten errades i variacions dels espais. Tria'ls amb el ratolí o amb fletxa avall i Intro. El botó «✕» o Escape netegen la cerca.

Fes doble clic a una preparació per obrir «Modificar preparació». Nom i SKU són de només lectura. Pots canviar el lot, el codi de barres, el consum preferent, la data de producció i les dades dels ingredients. Fes doble clic en una cel·la per editar-la; també pots afegir o eliminar ingredients. Les barreges niades es mostren desplegades i es conserven les dades no modificades. «Guardar» valida i desa; «Cancel·lar» descarta els canvis. L'edició es pot desfer i refer. Els envasats anteriors mantenen els seus lots i les còpies històriques dels ingredients.

Els suggeriments de productes, ingredients, proveïdors i orígens ignoren majúscules i accents, admeten fragments del text i ofereixen coincidències aproximades si hi ha petites errades. Per exemple, «Marialluisa» suggereix «Marialluïsa» i «Reloj» suggereix «El Reloj». Les coincidències exactes tenen prioritat i es conserven els noms originals i els lots individuals.

## Comprovar i obrir el programa amb doble clic

Fes doble clic a `run_and_verify.bat`.

Aquesta és l'única manera recomanada: primer valida el programa (proves + compilació) i, si tot està correcte, obre l'aplicació automàticament.

## Notes

Aquest programa està pensat per a un ús pràctic i senzill en equips modestos. Si vols, el següent pas és adaptar-lo a la teva impressora Epson ColorWorks CW-C4000e i a la configuració concreta del teu procés de producció.
