# 🎬 Twitch Highlight Finder

Et lokalt program til din Mac, der finder de mest interessante øjeblikke i en lang Twitch-stream ved at
analysere **chataktiviteten** – og klipper dem ud som MP4-filer (16:9 og 9:16 til Shorts/TikTok).

Du indsætter et VOD-link, programmet henter **kun chatten** (ikke videoen), tæller beskeder pr. 10 sekunder,
finder usædvanlige udsving, giver hvert øjeblik en score fra 0–100 og foreslår et klip, der starter
20 sekunder før chatten reagerer. Først når du eksporterer, hentes de få sekunders video, du har valgt.

Alt kører lokalt og gratis: Python, [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader),
FFmpeg og Streamlit. Ingen betalte API'er.

> ⚖️ Download og klip kun indhold, som du har rettigheder eller tilladelse til at bruge.

---

## Installation (macOS, Apple Silicon)

Kræver [Homebrew](https://brew.sh). Åbn Terminal i projektmappen og kør:

```bash
./scripts/setup_macos.sh
```

Scriptet gør fire ting:

1. installerer **FFmpeg** (`brew install ffmpeg`), hvis det mangler,
2. finder eller installerer **Python 3.10+** og opretter et virtuelt miljø i `.venv`,
3. installerer Python-pakkerne fra `requirements.txt`,
4. henter nyeste **TwitchDownloaderCLI** (`…-MacOSArm64.zip` på M1/M2/M3) til `./bin`, gør den kørbar og
   fjerner macOS' karantæne-flag.

<details>
<summary>Manuel installation (hvis du hellere vil gøre det selv)</summary>

```bash
brew install ffmpeg python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# TwitchDownloaderCLI: hent "TwitchDownloaderCLI-<version>-MacOSArm64.zip" fra
# https://github.com/lay295/TwitchDownloader/releases/latest og pak den ud i ./bin
mkdir -p bin && unzip ~/Downloads/TwitchDownloaderCLI-*-MacOSArm64.zip -d bin
chmod +x bin/TwitchDownloaderCLI
xattr -dr com.apple.quarantine bin
```
</details>

## Start programmet

```bash
./run.sh
```

…eller dobbeltklik på **`Start Highlight Finder.command`** i Finder. Dashboardet åbner på
<http://localhost:8501>. Stop med `Ctrl+C` i terminalen.

## Sådan bruger du det

1. **Indsæt et Twitch VOD-link** (`https://www.twitch.tv/videos/1234567890`). Flere streams: ét link pr. linje.
2. Tryk **Analyze Stream**. Du ser fremdrift for VOD-info, chat-download og analyse. Fejl (ugyldigt link,
   slettet VOD, ingen chat, netværk) vises med en forklaring.
3. **Tidslinjen** viser chatbeskeder pr. 10 sekunder for hele streamen, normalniveauet og de fundne peaks
   (tallet er rangeringen). Klik på en markering for at vælge den.
4. **Highlight-listen** viser tidspunkt, score, type, antal beskeder, procentvis stigning, unikke chattere,
   foreslået start/slut og de hyppigste reaktioner. Filtrér på type og mindste score.
5. Under **Valgt highlight** kan du se, hvad chatten skrev, justere start/slut, vælge eller skrive en titel
   og trykke **Eksportér klip**. *Se på Twitch* åbner VOD'en ved klippets start, så du kan tjekke det først.
6. **Flere på én gang:** sæt flueben ud for rækker i listen og tryk **Eksportér valgte**.

Vil du bare se, hvordan det virker, så tryk **Prøv demo-data** (syntetisk 6-timers stream, kræver ikke Twitch).

### Indstillinger (venstre side)

| Indstilling | Standard | Betydning |
|---|---|---|
| Følsomhed | 5 | 1 = kun de helt store peaks, 10 = mange og svagere |
| Start klip før chatreaktionen | 20 sek. | Chatten reagerer efter øjeblikket – klippet starter tidligere |
| Fortsæt efter reaktionen | 10 sek. | Ekstra tid efter chatten er faldet til ro |
| Kliplængde | 30–90 sek. | Mindste og største længde på et foreslået klip |
| Maks. antal highlights | 20 | De bedste N efter score |
| Avanceret | 10 sek. / 10 min. | Optællingsinterval og vindue for "normal aktivitet" |

Ændringer genberegnes med det samme ud fra den gemte chat – der hentes ikke noget igen.

### Hvor ender filerne?

```
clips/<streamer>/<vod-id>_<streamtitel>/
    01h23m45s_score87_<titel>_16x9.mp4     ← original video og lyd, uændret
    01h23m45s_score87_<titel>_9x16.mp4     ← 1080×1920 til Shorts/TikTok
    clips.csv                              ← oversigt over eksporterede klip
data/vods/<vod-id>/                        ← gemt chat + analyse (genbruges næste gang)
logs/highlight_finder.log                  ← log (vises også nederst i appen)
```

16:9-filen er præcis det udsnit, TwitchDownloader henter – ingen genkodning. 9:16 laves med FFmpeg enten som
**beskæring til midten** eller **hele billedet med sløret baggrund**; lyden kopieres uændret.

## Sådan virker analysen

Koden ligger i `highlight_finder/analysis.py`.

1. **Rensning** – kendte bots (Nightbot, StreamElements …), `!kommandoer` og konti, der gentager den samme
   lange besked, tælles ikke med.
2. **Optælling** – beskeder pr. 10 sekunder. Hver bruger tæller højst 2 gange pr. interval, så én person,
   der spammer, ikke kan skabe et peak.
3. **Normalniveau** – en glidende median over 10 minutter omkring hvert tidspunkt (og den normale spredning
   via MAD). En lang periode med generelt travl chat hæver normalniveauet og er derfor *ikke* et peak – kun
   pludselige udsving over det lokale niveau tæller.
4. **Peaks** – intervaller, hvor både robust z-score og relativ stigning overstiger følsomhedens tærskler
   (og mindst 6 beskeder fra 4 forskellige personer). Naboliggende intervaller samles til ét øjeblik.
5. **Score 0–100** – vægtet sum af: hvor usædvanligt udsvinget er (25 %), relativ stigning (20 %), unikke
   chattere (20 %), andel reaktionsbeskeder som LOL/KEKW/WTF/OMG/Pog (15 %), mængde i forhold til streamens
   største øjeblik (10 %) og varighed (10 %). Udsving båret af få personer trækkes ned.
6. **Klip** – start = hvor chatten begynder at stige minus 20 sekunder; slut = når chatten er faldet til ro
   plus 10 sekunder. Klip, der overlapper, slås sammen (hvis de kan være i ét klip) eller det svageste
   flyttes/fjernes, så du ikke får næsten ens klip.
7. **Type og titler** – reaktionsordene afgør typen (😂 sjov situation, 🔥 spændende øjeblik, 😱 stor
   reaktion, 💀 fail, 💬 chat-peak). Titelforslag bygges af type, reaktioner og de ord, der er typiske for
   netop det øjeblik. "Interessante beskeder" er længere beskeder, der bruger de ord.

**Synkronisering:** chattens tidsstempler (`content_offset_seconds`) er sekunder fra VOD'ens start – samme
tidslinje som TwitchDownloader bruger, når videoen klippes med `-b`/`-e`.

## Valgfrit: AI-titler med lokal model

Slå *Brug lokal AI (Ollama)* til i venstre side for at få knappen **Foreslå titel med AI**. Det kører lokalt
og gratis via [Ollama](https://ollama.com) – og er helt frivilligt; alt andet virker uden.

```bash
brew install ollama
ollama serve &
ollama pull llama3.2
```

## Fra terminalen (uden dashboard)

```bash
.venv/bin/python scripts/analyze_cli.py https://www.twitch.tv/videos/1234567890
.venv/bin/python scripts/analyze_cli.py <link> --export 3 --vertical   # eksportér de 3 bedste, også 9:16
.venv/bin/python scripts/analyze_cli.py demo                           # syntetiske data
```

## Fejlfinding

| Problem | Løsning |
|---|---|
| ❌ TwitchDownloaderCLI / FFmpeg under *System* | Kør `./scripts/setup_macos.sh` igen |
| macOS: "kan ikke åbnes, fordi udvikleren ikke kan bekræftes" | `xattr -dr com.apple.quarantine bin && chmod +x bin/TwitchDownloaderCLI` |
| ARM64-udgaven vil ikke starte | Brug Intel-udgaven via Rosetta: `softwareupdate --install-rosetta` og derefter `THF_CLI_ASSET=MacOS-x64 ./scripts/setup_macos.sh --update` |
| "VOD'en findes ikke eller er ikke længere tilgængelig" | VOD'en er slettet/udløbet (Twitch gemmer dem kun i en periode) |
| "VOD'en kræver adgang" (kun for abonnenter) | `export THF_TWITCH_OAUTH=<dit token>` før `./run.sh` – del aldrig tokenet |
| "Ingen chatbeskeder" | Streamen har ingen gemt chat – så er der intet at analysere |
| For få eller for mange highlights | Skru på *Følsomhed* |
| Opdatér TwitchDownloaderCLI | `./scripts/setup_macos.sh --update` |

Miljøvariabler: `THF_DATA_DIR`, `THF_CLIPS_DIR`, `THF_LOG_DIR` (flyt mapper), `THF_TWITCHDOWNLOADER_CLI`,
`THF_FFMPEG` (egne stier), `THF_KEEP_RAW_CHAT=1` (behold den rå chat-JSON), `THF_PORT`, `THF_OLLAMA_URL`.

## Projektstruktur

```
app.py                       Streamlit-dashboardet
highlight_finder/
    vod.py                   validering af links, VOD-metadata
    twitch_cli.py            kald af TwitchDownloaderCLI (info, chat, videoudsnit) med fremdrift og fejl
    chat_parser.py           strømmende indlæsning af chat-JSON (konstant hukommelse)
    analysis.py              optælling, normalniveau, peaks, score, klipvinduer
    reactions.py             ordbog over reaktioner (LOL, KEKW, WTF, Pog …) og typer
    insights.py              reaktioner, typiske ord, interessante beskeder, titelforslag
    clipper.py               eksport til MP4 (16:9 og 9:16) med FFmpeg
    cache.py                 gemt chat og analyse pr. VOD
    pipeline.py              hele forløbet: link → chat → analyse
    ai.py                    valgfri titler via lokal Ollama
    sample_data.py           syntetisk chat til demo og tests
scripts/setup_macos.sh       installation
scripts/analyze_cli.py       analyse fra terminalen
tests/                       automatiske tests (inkl. en simuleret TwitchDownloaderCLI)
```

## Tests

```bash
.venv/bin/python -m pytest
```

Testene dækker linkvalidering, chat-indlæsning, analysen på en realistisk 6-timers chat med plantede
højdepunkter (alle skal findes med rigtig type; bots, spam og travle perioder må ikke give highlights),
ydeevne med ca. 500.000 beskeder, cache, fejlhåndtering, eksport i 16:9 og 9:16 med rigtig FFmpeg samt
dashboardet fra link til eksporteret klip.

I testene erstattes TwitchDownloaderCLI af `tests/fake_cli/TwitchDownloaderCLI`, som tager de samme
argumenter og skriver fremdrift i samme format, men laver syntetisk chat og en testvideo i stedet for at
kontakte Twitch.
