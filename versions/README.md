# Historial de versions

Cada versió publicada té una carpeta pròpia. No s'han de sobreescriure les versions arxivades.

```text
versions/
  0.1.0-alpha.1/
    windows/erola-produccio-0.1.0-alpha.1-windows.exe
    linux/erola-produccio-0.1.0-alpha.1-linux-fonts.zip
    SHA256SUMS.txt
    release.json
```

L'executable de Windows és portable. El ZIP de Linux conté una còpia de les fonts, les proves i el constructor `build_linux.sh`; cal extreure'l i construir-lo en Linux seguint el README inclòs. No és un executable Linux precompilat ni implica compatibilitat nativa verificada.

No s'hi inclouen bases de dades, receptes de l'usuari, etiquetes ni exportacions de sessions. Les dades s'han de conservar separadament. `SHA256SUMS.txt` permet comprovar la integritat dels dos fitxers i `release.json` identifica la versió i la data de construcció.

## Crear una nova versió

1. Actualitza `VERSION`, per exemple a `0.1.0-alpha.2`.
2. Afegeix les novetats en aquest historial.
3. Executa `./build_release.ps1` des de PowerShell a Windows, amb les dependències i PyInstaller instal·lats a `.venv`.

El script executa les proves abans de construir els paquets i es nega a sobreescriure una carpeta de versió existent. `./build_release.ps1 -ValidateOnly` comprova els requisits i els noms sense construir res.

## 0.1.0-alpha.2 - 2026-10-15

- Avís de producte fora del catàleg amb suggeriments seleccionables amb doble clic i alta manual amb només el nom obligatori.
- Exportació CSV del catàleg complet amb modificacions i productes nous; botons d'exportació i revisió a la dreta del resum de modificacions.
- Gestió de proveïdors i orígens amb unificació de totes les referències, inclosos ingredients niats, i desfer/refer.
- Checkbox per silenciar els avisos d'impressora durant la sessió, mantenint la generació i els intents d'impressió.
- Intro del teclat numèric equivalent a l'Intro normal.
- Selecció de receptes a Envasat amb Tab, fletxa avall o clic; ingredients visibles sense doble clic.
- Retirada de textos d'ajuda, alineació dels cercadors i millor aprofitament vertical dels ingredients.
- Separadors plans de 2 píxels, amb marges sensibles de 6 píxels a cada costat i guia fina d'arrossegament.

Verificació del codi: 145 proves superades. El ZIP de Linux conté les fonts i el constructor; la construcció i verificació natives en Linux continuen pendents.

## 0.1.0-alpha.1 - 2026-10-15

Primera versió arxivada de la fase alfa. Inclou l'estat actual del programa:

- Gestió de recepcions, catàleg, receptes, preparacions i envasats amb traçabilitat d'ingredients niats.
- Impressió del full de producció des de cada fila de l'historial de preparacions.
- Cercadors amb suggeriments aproximats i cerca pels ingredients als historials.
- Pes per unitat a les etiquetes, als historials d'envasats i al CSV de sessió.
- Panells redimensionables amb guia d'arrossegament i mínim de tres ingredients visibles a Envasat.
- Avisos breus d'impressió amb un enllaç al gestor de fitxers del sistema.
- Ajustos del mapatge dels diàlegs d'edició i base inicial «Registre 1.db».

Verificació del codi: 123 proves superades. La verificació nativa en Linux continua pendent.