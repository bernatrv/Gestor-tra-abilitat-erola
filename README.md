# Gestor de traçabilitat Erola

Aquest projecte és una aplicació de Windows/Linux per gestionar la traçabilitat de materials i productes envasats.

## Funcionalitats principals

- Alta de productes rebuts amb nom, lot, procedència i caducitat.
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

La impressora d'etiquetes es configura des del programa en la pestanya d'envasat si et cal. Si la impressora està connectada i disponible al sistema, el programa intenta enviar la feina de impressió. En cas contrari, la etiqueta es genera a disc a la carpeta `output/labels` i pots imprimir-la manualment.

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
5. Exporta la sessió amb el botó de la pestanya d'historial.

### Barreges i envasat posterior

A Envasat, afegeix els ingredients i confirma el producte final. En confirmar la quantitat amb Intro, una barreja de diversos ingredients es desa com a preparació i es pregunta si vols imprimir-ne l'etiqueta de traçabilitat. Deixa la quantitat buida per preparar-la sense envasar; si introdueixes una quantitat, també se'n registra l'envasat i s'imprimeix una etiqueta de producte per unitat.

El codi de la barreja sense envasar és diferent del codi dels productes envasats. Pots escanejar-lo a Envasat o trobar la preparació pels seus nom o SKU al mateix cercador dels productes rebuts. Si s'utilitza en una altra barreja, es conserva la traçabilitat dels ingredients originals.

A la part inferior de Receptes apareix l'historial de preparacions amb nom, SKU, lot, caducitat i data. Selecciona una preparació i prem Intro, o fes-hi doble clic, per passar al camp Quantitat. Introdueix un enter positiu i prem Intro o el botó «Imprimir i registrar envasat». Es manté el lot de la preparació, es registra l'envasat i s'imprimeix el nombre indicat d'etiquetes de producte. Si falla la impressió, el registre es conserva i es mostra un avís.

## Comprovar i obrir el programa amb doble clic

Fes doble clic a `run_and_verify.bat`.

Aquesta és l'única manera recomanada: primer valida el programa (proves + compilació) i, si tot està correcte, obre l'aplicació automàticament.

## Notes

Aquest programa està pensat per a un ús pràctic i senzill en equips modestos. Si vols, el següent pas és adaptar-lo a la teva impressora Epson ColorWorks CW-C4000e i a la configuració concreta del teu procés de producció.
