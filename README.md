# PDF Renamer

Prosta aplikacja do szybkiego **nazywania i dzielenia skanów PDF**. Widzisz podgląd stron, wpisujesz nazwę, naciskasz Enter i od razu przechodzisz do następnego pliku.

Działa w całości na Twoim komputerze. Pliki nigdzie nie są wysyłane.

## Instalacja (jednorazowo)

1. Zainstaluj **Pythona** ze strony https://www.python.org/downloads/.
   W pierwszym oknie instalatora **zaznacz „Add python.exe to PATH”**.
2. Pobierz aplikację: zielony przycisk **Code → Download ZIP** na górze tej strony, a potem rozpakuj ZIP-a, np. na Pulpit.

## Uruchamianie

Kliknij dwukrotnie **`start.bat`** w rozpakowanym folderze.

- Za pierwszym razem instalują się biblioteki, co trwa około minuty i wymaga internetu.
- Aplikacja otworzy się w przeglądarce (adres http://127.0.0.1:8765).
- Aby zakończyć, zamknij czarne okno.

Jeśli Windows pokaże ostrzeżenie „System Windows ochronił komputer”, kliknij **Więcej informacji → Uruchom mimo to**.

## Jak używać

1. **Źródło:** kliknij „Wybierz…” i wskaż folder ze skanami PDF.
   **Cel** (opcjonalnie): folder, do którego mają trafiać nowe pliki. Puste pole oznacza ten sam folder.
2. Z listy po lewej wybierz plik. Obok widać miniaturki wszystkich stron, a po prawej duży podgląd.
3. **Zmiana nazwy całego pliku:** wpisz nową nazwę (bez `.pdf`) i naciśnij **Enter**.
4. **Dzielenie skanu na kilka dokumentów:**
   - zaznacz strony w polach **od–do**, kliknij miniaturkę (jedna strona) albo użyj `Shift`+kliknięcia (zakres),
   - wpisz nazwę i naciśnij **Enter**. Te strony zapiszą się jako nowy PDF,
   - zaznaczenie samo przeskoczy na kolejne strony, więc powtarzasz dla następnego dokumentu.

   Szybki rytm: numer ostatniej strony → `Enter` → nazwa → `Enter`.
5. **„Podziel na pojedyncze strony”:** każda strona trafia do osobnego pliku `nazwa_01.pdf`, `nazwa_02.pdf`…

Gdy wszystkie strony skanu zostaną zapisane, oryginał przenosi się do podfolderu **`_przetworzone`**, więc niczego nie tracisz.

### Skróty klawiszowe

| Klawisz | Działanie |
|---|---|
| `Enter` (w polu nazwy) | zapisz |
| `Enter` (w polu „od” / „do”) | przejdź do następnego pola |
| `Ctrl+↓` / `Ctrl+↑` | następny / poprzedni plik |
| `PgDn` / `PgUp` | przewiń podgląd |
| `Esc` | przywróć poprzednią nazwę |

## Dobrze wiedzieć

- Aplikacja **nigdy nie nadpisuje** istniejących plików. Gdy nazwa jest zajęta, pokaże komunikat.
- Pliki zabezpieczone hasłem i uszkodzone są oznaczone i można je tylko pominąć.
- Postęp pracy zapisuje się w ukrytym pliku `.pdf_renamer.json` w folderze ze skanami, dzięki czemu można przerwać i wrócić później. Usuń ten plik, żeby zacząć od nowa.
- Nie zamykaj czarnego okna w trakcie pracy, bo to ono obsługuje aplikację.

## Dla programistów

Ręczne uruchomienie (np. w terminalu VS Code):

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

Całość jest w `app.py`: FastAPI + PyMuPDF po stronie serwera i jedna strona HTML/JS jako interfejs.
