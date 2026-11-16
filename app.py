"""Twitch Highlight Finder – local Streamlit dashboard.

Start with:  streamlit run app.py     (or ./run.sh)
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from highlight_finder import ai, cache, clipper, pipeline, twitch_cli
from highlight_finder.analysis import AnalysisResult, Highlight, annotate_messages
from highlight_finder.config import AnalysisSettings
from highlight_finder.errors import HighlightFinderError
from highlight_finder.logging_setup import get_logger, setup_logging, tail_log
from highlight_finder.reactions import CATEGORY_LABELS
from highlight_finder.timeutil import format_duration, format_time, parse_time
from highlight_finder.vod import VodInfo, parse_vod_list, vod_url

setup_logging()
log = get_logger("app")

BEST_QUALITY = "Bedste (kilde)"
STAGES = {  # stage -> (start of overall progress, share, label)
    "info": (0.00, 0.05, "VOD-information"),
    "chat": (0.05, 0.70, "Henter chat"),
    "parse": (0.75, 0.15, "Læser chat"),
    "analyze": (0.90, 0.10, "Analyse"),
}
# Chart colours: a validated colour-blind safe pair; neutral greys that read on dark and light.
PALETTE = {"activity": "#3987e5", "peak": "#d95926", "muted": "rgba(150,150,150,0.95)", "grid": "rgba(128,128,128,0.18)"}


# --------------------------------------------------------------------------
# Cached data access
# --------------------------------------------------------------------------
def _messages_mtime(vod_id: str) -> float:
    path = cache.messages_path(vod_id)
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_resource(max_entries=3, show_spinner=False)
def load_annotated(vod_id: str, mtime: float) -> pd.DataFrame:
    return annotate_messages(cache.load_messages(vod_id))


@st.cache_data(max_entries=24, show_spinner=False)
def compute_result(vod_id: str, mtime: float, settings_json: str, language: str) -> AnalysisResult:
    info = cache.load_info(vod_id)
    settings = AnalysisSettings.from_dict(json.loads(settings_json))
    return pipeline.run_analysis(info, load_annotated(vod_id, mtime), settings, language)


def get_result(vod_id: str, settings: AnalysisSettings, language: str) -> AnalysisResult:
    return compute_result(vod_id, _messages_mtime(vod_id), json.dumps(asdict(settings), sort_keys=True), language)


@st.cache_data(ttl=30, show_spinner=False)
def ollama_models() -> list[str]:
    return ai.available_models()


def theme_palette() -> dict:
    surface = st.get_option("theme.backgroundColor") or ("#ffffff" if st.get_option("theme.base") == "light" else "#0e1117")
    return {**PALETTE, "surface": surface}


def show_error(exc: HighlightFinderError, prefix: str = "") -> None:
    st.error(f"{prefix}{exc}")
    if exc.hint:
        st.caption(f"💡 {exc.hint}")
    if exc.details:
        with st.expander("Tekniske detaljer"):
            st.code(exc.details)


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
def sidebar() -> tuple[AnalysisSettings, str, str | None]:
    with st.sidebar:
        st.header("⚙️ Analyse")
        sensitivity = st.slider(
            "Følsomhed", 1, 10, 5,
            help="Lav = kun de helt store peaks. Høj = flere og svagere highlights.",
        )
        pre_roll = st.slider(
            "Start klip før chatreaktionen (sek.)", 0, 60, 20, step=5,
            help="Chatten reagerer altid lidt efter selve øjeblikket, så klippet starter tidligere.",
        )
        post_roll = st.slider("Fortsæt efter reaktionen (sek.)", 0, 60, 10, step=5)
        min_len, max_len = st.slider("Kliplængde (sek.)", 10, 300, (30, 90), step=5)
        max_highlights = st.slider("Maks. antal highlights", 5, 50, 20)
        with st.expander("Avanceret"):
            bin_seconds = st.select_slider("Interval for optælling (sek.)", [5, 10, 15, 20, 30], 10)
            window_min = st.select_slider(
                "Normalniveau beregnes over (min.)", [5, 10, 15, 20, 30], 10,
                help="Aktiviteten sammenlignes med medianen i dette tidsrum omkring hvert tidspunkt.",
            )
        language = st.radio(
            "Sprog for titelforslag", ["da", "en"], horizontal=True,
            format_func=lambda x: {"da": "Dansk", "en": "English"}[x],
        )

        st.header("🧠 AI-titler (valgfrit)")
        ai_model = None
        if st.toggle("Brug lokal AI (Ollama)", value=False, help="Gratis og lokalt. Kræver at Ollama kører på din Mac."):
            models = ollama_models()
            if models:
                default = next((i for i, m in enumerate(models) if m.startswith(ai.DEFAULT_MODEL)), 0)
                ai_model = st.selectbox("Model", models, index=default)
            else:
                st.warning("Ollama blev ikke fundet. Appen virker fint uden.")
                st.code(f"brew install ollama\nollama serve &\nollama pull {ai.DEFAULT_MODEL}", language="bash")

        st.header("🩺 System")
        cli, ffmpeg = twitch_cli.find_cli(), twitch_cli.find_ffmpeg()
        st.markdown(f"{'✅' if cli else '❌'} TwitchDownloaderCLI  \n{'✅' if ffmpeg else '❌'} FFmpeg")
        if not cli or not ffmpeg:
            st.caption("Installér det manglende med:")
            st.code("./scripts/setup_macos.sh", language="bash")

    settings = AnalysisSettings(
        bin_seconds=bin_seconds, sensitivity=sensitivity, pre_roll=pre_roll, post_roll=post_roll,
        min_clip_seconds=min_len, max_clip_seconds=max_len, max_highlights=max_highlights,
        baseline_window_seconds=window_min * 60,
    ).clamped()
    return settings, language, ai_model


# --------------------------------------------------------------------------
# Import / analysis
# --------------------------------------------------------------------------
def analyze_links(text: str, force: bool, settings: AnalysisSettings, language: str) -> None:
    ids, errors = parse_vod_list(text)
    reports: list[dict] = []   # shown again after later reruns
    st.session_state["last_reports"] = reports
    for bad, message in errors:
        st.error(f"**{bad}** – {message}")
        reports.append({"ok": False, "text": f"**{bad}** – {message}"})
    if not ids:
        if not errors:
            st.warning("Indsæt mindst ét Twitch VOD-link, fx https://www.twitch.tv/videos/1234567890")
        return
    done: list[str] = []
    for number, vod_id in enumerate(ids, start=1):
        label = f"VOD {vod_id}" + (f" ({number}/{len(ids)})" if len(ids) > 1 else "")
        with st.status(f"Analyserer {label}…", expanded=True) as status:
            bar = st.progress(0.0, text="Starter…")

            def report(stage: str, fraction: float, message: str, _bar=bar) -> None:
                start, share, name = STAGES[stage]
                _bar.progress(min(1.0, start + share * max(0.0, min(1.0, fraction))), text=f"{name}: {message}")

            try:
                info, messages, from_cache = pipeline.fetch_chat(vod_id, report, force=force)
                if force:
                    load_annotated.clear()
                    compute_result.clear()
                report("analyze", 0.2, "Finder peaks i chatten…")
                result = get_result(vod_id, settings, language)
                report("analyze", 1.0, "Færdig")
                source = "gemt chat genbrugt" if from_cache else "chat hentet"
                status.update(
                    label=f"✅ {info.label} – {len(result.highlights)} highlights ({source})",
                    state="complete", expanded=False,
                )
                done.append(vod_id)
                reports.append({"ok": True, "text": f"{info.label} – {len(result.highlights)} highlights ({source})"})
            except HighlightFinderError as exc:
                log.error("VOD %s: %s", vod_id, exc)
                status.update(label=f"❌ {label}: {exc}", state="error", expanded=True)
                show_error(exc)
                reports.append({"ok": False, "text": f"{label}: {exc}" + (f" – {exc.hint}" if exc.hint else "")})
            except Exception as exc:  # never let one VOD take down the whole run
                log.exception("Uventet fejl for VOD %s", vod_id)
                status.update(label=f"❌ {label}: uventet fejl", state="error", expanded=True)
                st.error(f"Uventet fejl: {exc}")
                st.caption("💡 Se loggen nederst på siden for detaljer.")
                reports.append({"ok": False, "text": f"{label}: uventet fejl: {exc}"})
    if done:
        st.session_state["current_vod"] = done[0]


def import_section(settings: AnalysisSettings, language: str) -> None:
    with st.form("import"):
        links = st.text_area(
            "Twitch VOD-link",
            placeholder="https://www.twitch.tv/videos/1234567890\n(flere streams: ét link pr. linje)",
            height=90,
        )
        col_a, col_b, col_c = st.columns([1.2, 1.2, 4])
        submit = col_a.form_submit_button("Analyze Stream", type="primary", width="stretch")
        demo = col_b.form_submit_button("Prøv demo-data", width="stretch", help="Syntetisk 6-timers stream – kræver ikke Twitch.")
        force = col_c.checkbox("Hent chatten igen, selvom den allerede er gemt")
    if submit:
        analyze_links(links, force, settings, language)
    elif demo:
        with st.spinner("Genererer demo-stream…"):
            demo_run = pipeline.load_demo(settings, language)
        st.session_state["current_vod"] = demo_run.info.vod_id
        st.session_state["last_reports"] = [{"ok": True, "text": f"Demo-stream klar – {len(demo_run.result.highlights)} highlights."}]
        st.success(st.session_state["last_reports"][0]["text"])
    else:
        for report in st.session_state["last_reports"]:
            (st.success if report["ok"] else st.error)(report["text"])


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
def timeline_figure(result: AnalysisResult, shown: list[Highlight], selected: Highlight | None, colors: dict) -> go.Figure:
    bins = result.bins
    size = result.settings.bin_seconds
    x = pd.to_datetime(bins["t"], unit="s")
    times = [format_time(t) for t in bins["t"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=bins["raw"], mode="lines", name=f"Chatbeskeder pr. {size} sek.",
        line=dict(color=colors["activity"], width=1.5, shape="hv"),
        fill="tozeroy", fillcolor=_rgba(colors["activity"], 0.18),
        customdata=list(zip(times, bins["unique"].astype(int))),
        hovertemplate="<b>%{customdata[0]}</b><br>%{y:.0f} beskeder · %{customdata[1]} chattere<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=bins["baseline"], mode="lines", name="Normalniveau",
        line=dict(color=colors["muted"], width=1.5), hoverinfo="skip",
    ))
    if shown:
        by_time = bins.set_index("t")["raw"]
        labelled = {h.id for h in sorted(shown, key=lambda h: h.rank)[:12]}
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([h.peak_time for h in shown], unit="s"),
            y=[float(by_time.get(h.peak_time, h.peak_messages)) for h in shown],
            mode="markers+text", name="Highlights (nr. = rang)",
            marker=dict(color=colors["peak"], size=[16 if selected and h.id == selected.id else 11 for h in shown],
                        line=dict(color=colors["surface"], width=2)),
            text=[f"<b>{h.rank}</b>" if h.id in labelled else "" for h in shown],
            textposition="top center", textfont=dict(size=12),
            customdata=[[h.id, h.rank, format_time(h.peak_time), h.score, CATEGORY_LABELS[h.kind], h.messages, int(h.increase_pct)]
                        for h in shown],
            hovertemplate=("<b>#%{customdata[1]} · %{customdata[2]}</b><br>Score %{customdata[3]} · %{customdata[4]}"
                           "<br>%{customdata[5]} beskeder · +%{customdata[6]}%<extra></extra>"),
        ))
    if selected is not None:
        fig.add_vrect(
            x0=pd.to_datetime(selected.clip_start, unit="s"), x1=pd.to_datetime(selected.clip_end, unit="s"),
            fillcolor=colors["peak"], opacity=0.22, line_width=0,
        )
    fig.update_layout(
        height=380, margin=dict(l=10, r=10, t=10, b=10), hovermode="closest", dragmode="zoom",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(tickformat="%H:%M:%S", title=None, showgrid=False),
        yaxis=dict(title=None, gridcolor=colors["grid"], gridwidth=1, rangemode="tozero", zeroline=False),
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i: i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def highlight_table(shown: list[Highlight]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "#": h.rank,
            "Tidspunkt": format_time(h.peak_time),
            "Score": h.score,
            "Type": CATEGORY_LABELS[h.kind],
            "Beskeder": h.messages,
            "Stigning": h.increase_pct,
            "Chattere": h.unique_users,
            "Foreslået klip": f"{format_time(h.clip_start)} – {format_time(h.clip_end)} ({format_duration(h.duration)})",
            "Reaktioner": "  ".join(f"{t} ×{n}" for t, n in h.top_reactions[:3]) or "–",
        }
        for h in shown
    ])


def stat_row(items: list[tuple[str, str]]) -> None:
    """Compact label/value tiles that wrap instead of truncating."""
    tiles = "".join(
        f'<div style="min-width:6.5rem"><div style="font-size:0.8rem;opacity:0.7">{label}</div>'
        f'<div style="font-size:1.6rem;font-weight:600;line-height:1.3;white-space:nowrap">{value}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div style="display:flex;flex-wrap:wrap;gap:0.4rem 2rem;margin-bottom:0.4rem">{tiles}</div>', unsafe_allow_html=True)


def get_override(vod_id: str, h: Highlight) -> dict:
    return st.session_state["overrides"].get((vod_id, h.id), {})


def clip_times(vod_id: str, h: Highlight) -> tuple[float, float, str]:
    """Start, end and title for a highlight, including the user's adjustments."""
    o = get_override(vod_id, h)
    return o.get("start", h.clip_start), o.get("end", h.clip_end), o.get("title") or (h.titles[0] if h.titles else "")


def run_export(info: VodInfo, jobs: list[tuple[Highlight, float, float, str]], formats: list[str], mode: str, quality: str | None) -> None:
    if not formats:
        st.warning("Vælg mindst ét format (16:9 eller 9:16).")
        return
    exported, failed = [], 0
    with st.status(f"Eksporterer {len(jobs)} klip…", expanded=True) as status:
        bar = st.progress(0.0, text="Starter…")
        for i, (h, start, end, title) in enumerate(jobs):
            def report(fraction: float, message: str, _i=i) -> None:
                bar.progress(min(1.0, (_i + fraction) / len(jobs)), text=f"Klip {_i + 1}/{len(jobs)} – {message}")
            try:
                exported += clipper.export_clip(
                    info, start, end, title=title, formats=tuple(formats), vertical_mode=mode,
                    quality=quality, score=h.score, progress=report,
                )
            except HighlightFinderError as exc:
                failed += 1
                show_error(exc, prefix=f"Klip #{h.rank} ({format_time(start)}): ")
            except Exception as exc:
                failed += 1
                log.exception("Uventet fejl under eksport")
                st.error(f"Klip #{h.rank}: uventet fejl: {exc}")
        bar.progress(1.0, text="Færdig")
        if exported:
            status.update(label=f"✅ {len(exported)} fil(er) gemt" + (f" · {failed} fejlede" if failed else ""),
                          state="complete" if not failed else "error", expanded=bool(failed))
        else:
            status.update(label="❌ Ingen klip blev eksporteret", state="error", expanded=True)
    st.session_state["exports"] = [
        {"path": str(c.path), "format": c.format, "title": c.title, "start": c.start, "end": c.end} for c in exported
    ] + st.session_state["exports"]


def reveal_in_file_manager(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        st.warning(f"Kunne ikke åbne mappen: {exc}")


def reset_highlight(vod_id: str, h: Highlight, key: str) -> None:
    """Button callback: back to the suggested times and title."""
    st.session_state["overrides"].pop((vod_id, h.id), None)
    st.session_state[f"start_{key}"] = format_time(h.clip_start)
    st.session_state[f"end_{key}"] = format_time(h.clip_end)
    if h.titles:
        st.session_state[f"title_{key}"] = h.titles[0]


def ai_title(info: VodInfo, h: Highlight, key: str, model: str, language: str) -> None:
    """Button callback: ask the local model and select its title."""
    suggestion = ai.generate_title(h, info, model, language)
    if not suggestion:
        st.session_state["ai_failed"] = True
        return
    entry = st.session_state["overrides"].setdefault((info.vod_id, h.id), {})
    entry["ai_titles"] = [suggestion] + [t for t in entry.get("ai_titles", []) if t != suggestion]
    entry["title"] = suggestion
    st.session_state[f"title_{key}"] = suggestion


def detail_panel(info: VodInfo, h: Highlight, ai_model: str | None, language: str, export_opts: dict) -> None:
    vod_id = info.vod_id
    key = f"{vod_id}_{h.id}"
    override = get_override(vod_id, h)
    left, right = st.columns([1.15, 1])

    with left:
        st.subheader(f"#{h.rank} · {format_time(h.peak_time)} · {CATEGORY_LABELS[h.kind]}")
        stat_row([
            ("Score", f"{h.score} / 100"),
            ("Chatbeskeder", f"{h.messages}"),
            ("Stigning", f"+{int(h.increase_pct)}%"),
            ("Unikke chattere", f"{h.unique_users}"),
        ])
        st.caption(
            f"Travleste interval: {int(h.peak_messages)} beskeder (normalt ca. {h.baseline:g}) · "
            f"chatreaktionen varer {format_time(h.reaction_start)}–{format_time(h.reaction_end)}"
        )
        if h.top_reactions:
            st.markdown("**Hyppigste reaktioner:** " + " · ".join(f"`{t}` ×{n}" for t, n in h.top_reactions))
        if h.top_words:
            st.markdown("**Hyppige ord:** " + ", ".join(f"{w} ({n})" for w, n in h.top_words))
        if h.repeated_messages:
            st.markdown("**Chatten spammede:** " + " · ".join(f"“{t}” ({n} personer)" for t, n in h.repeated_messages))
        if h.notable_messages:
            st.markdown("**Interessante beskeder**")
            for msg in h.notable_messages:
                st.markdown(f"`{format_time(msg['time'])}` **{msg['user']}**: {msg['text']}")
        with st.expander("Sådan er scoren beregnet"):
            names = {"intensity": "Hvor usædvanligt (z-score)", "increase": "Relativ stigning", "users": "Unikke chattere",
                     "volume": "Mængde ift. streamens største", "reactions": "Andel reaktioner", "sustain": "Varighed"}
            st.dataframe(
                pd.DataFrame([{"Faktor": names[k], "Værdi (0–1)": v} for k, v in h.components.items()]),
                hide_index=True, column_config={"Værdi (0–1)": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")},
            )

    with right:
        st.subheader("Justér og eksportér")
        c1, c2 = st.columns(2)
        start_text = c1.text_input("Start (t:mm:ss)", value=format_time(override.get("start", h.clip_start)), key=f"start_{key}")
        end_text = c2.text_input("Slut (t:mm:ss)", value=format_time(override.get("end", h.clip_end)), key=f"end_{key}")
        problem = None
        start = end = 0.0
        try:
            start, end = parse_time(start_text), parse_time(end_text)
            if end <= start:
                problem = "Sluttidspunktet skal ligge efter starttidspunktet."
            elif info.length_seconds and start >= info.length_seconds:
                problem = f"Starttidspunktet ligger efter streamens slutning ({format_time(info.length_seconds)})."
            elif end - start > clipper.MAX_CLIP_SECONDS:
                problem = f"Klippet er for langt (maks. {clipper.MAX_CLIP_SECONDS // 60} minutter)."
        except ValueError as exc:
            problem = str(exc)

        options = list(dict.fromkeys(([override["title"]] if override.get("title") else []) + override.get("ai_titles", []) + h.titles))
        title = st.selectbox(
            "Titel (vælg et forslag eller skriv din egen)", options, index=0 if options else None,
            accept_new_options=True, key=f"title_{key}",
        ) or (h.titles[0] if h.titles else f"Highlight {format_time(h.peak_time)}")

        if problem:
            st.error(problem)
        else:
            new = dict(override)
            new.update({"start": start, "end": end, "title": title})
            changed = (int(start), int(end)) != (int(h.clip_start), int(h.clip_end)) or title != (h.titles[0] if h.titles else "")
            if changed or override:
                st.session_state["overrides"][(vod_id, h.id)] = new
            st.caption(
                f"Kliplængde: **{format_duration(end - start)}** · reaktionen i chatten starter {format_time(h.reaction_start)}"
                + (" · ✏️ justeret" if (int(start), int(end)) != (int(h.clip_start), int(h.clip_end)) else "")
            )

        b1, b2, b3 = st.columns(3)
        do_export = b1.button("⬇️ Eksportér klip", type="primary", disabled=bool(problem) or info.is_demo, width="stretch", key=f"export_{key}")
        if not info.is_demo:
            b2.link_button("▶️ Se på Twitch", vod_url(vod_id, start if not problem else h.clip_start), width="stretch",
                           help="Åbner VOD'en ved klippets start, så du kan tjekke øjeblikket før eksport.")
        b3.button("↩️ Nulstil", width="stretch", key=f"reset_{key}", help="Tilbage til de foreslåede tider og titel.",
                  on_click=reset_highlight, args=(vod_id, h, key))
        if ai_model:
            st.button("✨ Foreslå titel med AI", key=f"ai_{key}", on_click=ai_title, args=(info, h, key, ai_model, language),
                      help="Kan tage et øjeblik – modellen kører lokalt på din Mac.")
            if st.session_state.pop("ai_failed", False):
                st.warning("AI-modellen svarede ikke – brug et af de almindelige forslag.")
        if info.is_demo:
            st.info("Demo-streamen har ingen video. Analysér en rigtig VOD for at eksportere klip.")
        if do_export:
            run_export(info, [(h, start, end, title)], export_opts["formats"], export_opts["mode"], export_opts["quality"])


def exports_section() -> None:
    exports = st.session_state["exports"]
    if not exports:
        return
    st.subheader(f"📁 Eksporterede klip ({len(exports)})")
    st.caption(f"Gemmes i `{Path(exports[0]['path']).parent}`")
    for i, item in enumerate(exports[:12]):
        path = Path(item["path"])
        with st.expander(f"{item['format']} · {item['title']} · {format_time(item['start'])}–{format_time(item['end'])}", expanded=i == 0):
            if not path.exists():
                st.warning("Filen findes ikke længere.")
                continue
            video_col, info_col = st.columns([1 if item["format"] == "9:16" else 2, 2])
            video_col.video(str(path))
            info_col.code(str(path), language=None)
            info_col.caption(f"{path.stat().st_size / 1e6:.1f} MB")
            if info_col.button("Vis i Finder", key=f"reveal_{i}_{path.name}"):
                reveal_in_file_manager(path)


def results_section(settings: AnalysisSettings, language: str, ai_model: str | None) -> None:
    vods = cache.list_cached_vods()
    if not vods:
        st.info("Indsæt et Twitch VOD-link ovenfor og tryk **Analyze Stream** – eller prøv demo-dataene.")
        return
    by_id = {v.vod_id: v for v in vods}
    if st.session_state.get("current_vod") not in by_id:
        st.session_state["current_vod"] = vods[0].vod_id

    st.divider()
    pick_col, delete_col = st.columns([6, 1], vertical_alignment="bottom")
    vod_id = pick_col.selectbox(
        f"Analyserede streams ({len(vods)})", list(by_id), key="current_vod",
        format_func=lambda v: f"{by_id[v].label}  ·  {format_duration(by_id[v].length_seconds)}",
    )
    if delete_col.button("🗑️ Fjern", help="Sletter den gemte chat og analyse for denne stream.", width="stretch"):
        cache.delete_vod(vod_id)
        st.session_state.pop("current_vod", None)
        st.rerun()
    info = by_id[vod_id]

    try:
        result = get_result(vod_id, settings, language)
    except HighlightFinderError as exc:
        show_error(exc)
        return
    stats = result.stats

    st.subheader(info.title or f"VOD {vod_id}")
    meta = [info.streamer, info.game, info.created_at[:10], "demo-data" if info.is_demo else f"[åbn på Twitch]({info.url})"]
    st.caption(" · ".join(m for m in meta if m))
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Varighed", format_duration(stats["duration"]))
    k2.metric("Chatbeskeder", f"{stats['counted_messages']:,}".replace(",", "."),
              help=f"{stats['ignored_messages']} beskeder fra bots, kommandoer og spam er ikke talt med.")
    k3.metric("Unikke chattere", f"{stats['unique_chatters']:,}".replace(",", "."))
    k4.metric("Beskeder pr. minut", f"{stats['messages_per_minute']:.0f}")
    k5.metric("Highlights", len(result.highlights))

    if not result.highlights:
        st.warning(stats.get("note") or "Ingen tydelige peaks fundet. Prøv at øge følsomheden i venstre side.")

    # Filters -------------------------------------------------------------
    kinds_present = [k for k in CATEGORY_LABELS if any(h.kind == k for h in result.highlights)]
    f1, f2, f3 = st.columns([4, 1.2, 1.2])
    kinds = f1.pills("Type", kinds_present, selection_mode="multi", default=kinds_present,
                     format_func=lambda k: CATEGORY_LABELS[k], key=f"kinds_{vod_id}") if kinds_present else []
    min_score = f2.slider("Mindste score", 0, 100, 0, step=5, key=f"min_score_{vod_id}")
    order = f3.radio("Sortér efter", ["Score", "Tid"], horizontal=True, key=f"order_{vod_id}")
    shown = [h for h in result.highlights if h.kind in kinds and h.score >= min_score]
    shown.sort(key=(lambda h: h.rank) if order == "Score" else (lambda h: h.peak_time))
    shown_by_id = {h.id: h for h in shown}

    # Timeline ------------------------------------------------------------
    chart_key = f"timeline_{vod_id}"
    try:
        points = st.session_state[chart_key]["selection"]["points"]
    except (KeyError, TypeError):
        points = []
    clicked = next((p["customdata"][0] for p in points if p.get("curve_number") == 2 and p.get("customdata")), None)
    if clicked and clicked != st.session_state.get("last_chart_pick") and clicked in shown_by_id:
        st.session_state["selected_highlight"] = clicked
    st.session_state["last_chart_pick"] = clicked
    if shown and st.session_state.get("selected_highlight") not in shown_by_id:
        st.session_state["selected_highlight"] = shown[0].id
    selected = shown_by_id.get(st.session_state.get("selected_highlight"))

    st.markdown(f"**Chataktivitet gennem hele streamen** – {len(shown)} highlights vist. Klik på en markering for at vælge den.")
    st.plotly_chart(
        timeline_figure(result, shown, selected, theme_palette()), key=chart_key, on_select="rerun",
        selection_mode="points", config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
    )
    if not shown:
        if result.highlights:
            st.info("Ingen highlights matcher filtrene.")
        exports_section()
        return

    # Highlight list --------------------------------------------------------
    st.markdown("**Highlights** – sæt flueben ud for dem, du vil eksportere samlet.")
    table = st.dataframe(
        highlight_table(shown), hide_index=True, on_select="rerun", selection_mode="multi-row", key=f"table_{vod_id}",
        height=min(38 + 35 * len(shown), 420),
        column_config={
            "#": st.column_config.NumberColumn(width=40),
            "Tidspunkt": st.column_config.TextColumn(width=80, help="Travleste interval i chatten"),
            "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d", width=90),
            "Beskeder": st.column_config.NumberColumn(width=70),
            "Chattere": st.column_config.NumberColumn(width=70, help="Unikke chattere under reaktionen"),
            "Foreslået klip": st.column_config.TextColumn(width=185),
            "Stigning": st.column_config.NumberColumn(format="+%d%%", width=70, help="Travleste interval i forhold til normal aktivitet"),
        },
    )
    chosen = [shown[i] for i in table.selection.rows if i < len(shown)]

    with st.container(border=True):
        o1, o2, o3, o4 = st.columns([1.3, 2, 1.5, 1.8], vertical_alignment="bottom")
        formats = o1.pills("Format", [clipper.FORMAT_WIDE, clipper.FORMAT_VERTICAL], selection_mode="multi",
                           default=[clipper.FORMAT_WIDE], key="export_formats",
                           help="16:9 = original video og lyd. 9:16 = lodret til Shorts/TikTok.")
        mode = o2.selectbox("9:16-udsnit", list(clipper.VERTICAL_MODES), format_func=clipper.VERTICAL_MODES.get,
                            disabled=clipper.FORMAT_VERTICAL not in formats, key="export_mode")
        quality = o3.selectbox("Kvalitet", [BEST_QUALITY] + info.qualities, key=f"quality_{vod_id}")
        batch = o4.button(f"⬇️ Eksportér valgte ({len(chosen)})", disabled=not chosen or info.is_demo, width="stretch")
        st.caption("⚖️ Download og klip kun indhold, som du har rettigheder eller tilladelse til at bruge.")
    export_opts = {"formats": list(formats), "mode": mode, "quality": None if quality == BEST_QUALITY else quality}
    if batch:
        jobs = [(h, *clip_times(vod_id, h)) for h in sorted(chosen, key=lambda h: h.clip_start)]
        run_export(info, jobs, export_opts["formats"], export_opts["mode"], export_opts["quality"])

    # Detail ----------------------------------------------------------------
    st.divider()
    chosen_id = st.selectbox(
        "Valgt highlight", list(shown_by_id), key="selected_highlight",
        format_func=lambda i: f"#{shown_by_id[i].rank} · {format_time(shown_by_id[i].peak_time)} · score {shown_by_id[i].score} · "
                              f"{CATEGORY_LABELS[shown_by_id[i].kind]}",
    )
    detail_panel(info, shown_by_id[chosen_id], ai_model, language, export_opts)
    exports_section()


def main() -> None:
    st.set_page_config(page_title="Twitch Highlight Finder", page_icon="🎬", layout="wide")
    st.session_state.setdefault("overrides", {})
    st.session_state.setdefault("exports", [])
    st.session_state.setdefault("last_reports", [])
    settings, language, ai_model = sidebar()

    st.title("🎬 Twitch Highlight Finder")
    st.caption("Finder de bedste øjeblikke i en Twitch-stream ud fra chataktiviteten – og klipper dem ud som MP4.")
    import_section(settings, language)
    results_section(settings, language, ai_model)

    with st.expander("📜 Log"):
        st.code(tail_log(150) or "Ingen logbeskeder endnu.", language=None)


main()
