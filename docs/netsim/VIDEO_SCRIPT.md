# Video script — sensor-network design study (target 2:40, hard cap 3:00)

Draft 2, 2026-09-10. Every number is read off `netsim/out/RESULTS.md`; re-check
after any re-run. The narration is ~340 words, about 115 words/min over 2:58; `python -m netsim.make_subtitles` regenerates `VIDEO_SCRIPT.srt` from this table.

| t | Screen | Voice |
|---|---|---|
| 0:00–0:25 | Title card, then the platform README's one-paragraph description; cut to a photo/illustration of an insect antennal-lobe recording | Insect neurons are cheap, sensitive, and already used as biosensors. Everyone asks how early one neuron can warn you. Nobody asks the deployment question: how many do you need, where, and how should they vote? That is what this study answers. |
| 0:25–0:55 | `python -m netsim.cli describe` output; highlight `twin_params.json` provenance line and the two Zenodo DOIs; then the pipeline diagram from `netsim/README.md` | The only empirical input is real: gamma-renewal fits to sixteen cockroach and locust units from two open Zenodo datasets. Around them we simulate a toxic front spreading at one and a half units per second, sensors on a grid, at random, or clustered, one-second spike counts with measurement noise, and three detectors times three network rules. Every pipeline is calibrated to the same false-alarm rate on null runs. |
| 0:55–1:35 | Terminal: `python -m netsim.cli run --n 16 --topology grid --detector cusum --rule pool --seed 1`; show JSON: origin, t_symptom, t_alarm, lead, loc_err. Then `bash netsim/reproduce.sh --quick` scrolling, then the sweep progress lines | Here is one run. Sixteen sensors on a grid, CUSUM with nearest-neighbour pooling. The source collapses at sixty-six seconds; the network alarms at sixty, six seconds early, and places the source within eight percent of the domain diagonal. The full study repeats this fifty-four times over sensor count, layout and noise, twenty seeds each, nine pipelines. One command reproduces everything. |
| 1:35–2:15 | `docs/netsim/design_curves_noise0.15.png`, zoom on the grid column, then the clustered column, then the bocpd+max line | Three results. First, pooling beats every other rule: seventy-five percent detection and about seven seconds of lead on average, versus sixty-three percent for the naive max rule at the same false-alarm rate. Second, layout matters as much as count: sixteen grid sensors reach ninety-five percent detection; random placement needs thirty-six; clustered placement never reaches one hundred percent. Third, a warning: the max rule collapses as you add sensors when the statistic is bounded. BOCPD-plus-max drops from eighty to ten percent detection between nine and a hundred sensors. Pooled BOCPD stays at one hundred. |
| 2:15–2:35 | MCP client window calling `design_sweep`; the returned table | The simulator is also an MCP server, so a research agent can run its own sweeps and get back the same numbers the command line produces. |
| 2:35–2:50 | `netsim/README.md` Limitations section on screen | What this is not: the front, the decay and the collapse endpoint are simulated. This is a network-design study, not a biological lead time. Rate is the only feature. Next is validation on recordings with a real pharmacological intervention. |
| 2:50–2:58 | Repo URL, MIT licence, `bash netsim/reproduce.sh` | Code, data pipeline and the one-page abstract are in the repository. Thank you. |

## Recording checklist

- Terminal font ≥ 18 pt, dark theme, 1920×1080; pre-run every command so nothing waits on screen.
- Trim to ≤ 2:55 in the editor; verify with `ffprobe -show_entries format=duration -v quiet -of csv=p=0 final.mp4` < 180.
- Burn subtitles; judges may watch muted.
- No music under the voice; no logo animation at the start.
